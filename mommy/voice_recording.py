from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import shutil
import subprocess
import threading
import time
import wave
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, BinaryIO, Callable

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, voice_recv
from openai import AsyncOpenAI

from mommy.bot import MommyExeBot
from mommy.personality import MOMMY_SYSTEM_PROMPT, sanitize_output
from services.permissions import deny_access, evaluate_access

log = logging.getLogger("mommy-exe.voice-recording")


def _contains_non_english_script(text: str) -> bool:
    """Reject CJK/Hangul/Japanese-script hallucinations from English-only VC audio."""
    return bool(re.search(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]", text or ""))


def _clean_english_transcript(text: str) -> str:
    text = (text or "").strip()
    if not text or _contains_non_english_script(text):
        return ""
    return text


SAMPLE_RATE = 48_000
CHANNELS = 2
SAMPLE_WIDTH = 2
FRAME_BYTES = CHANNELS * SAMPLE_WIDTH
BYTES_PER_SECOND = SAMPLE_RATE * FRAME_BYTES
SEGMENT_GAP_SECONDS = float(os.getenv("MOMMY_SEGMENT_GAP_SECONDS", "0.45"))
SEGMENT_MAX_SECONDS = 24.0


@dataclass(slots=True)
class SpeechSegment:
    user_id: int
    display_name: str
    start_offset: float
    raw_path: Path
    byte_count: int = 0
    handle: BinaryIO | None = None
    transcript: str | None = None
    ai_processed: bool = False


@dataclass(slots=True)
class SpeakerTrack:
    user_id: int
    display_name: str
    raw_path: Path
    handle: BinaryIO
    written_bytes: int = 0
    last_packet_at: float | None = None
    active_segment: SpeechSegment | None = None


class SessionSink(voice_recv.AudioSink):
    """Writes time-aligned per-user PCM tracks and short STT segments."""

    def __init__(self, session: "RecordingSession") -> None:
        super().__init__()
        self.session = session

    def wants_opus(self) -> bool:
        return False

    def write(self, user: discord.Member | discord.User | None, data: voice_recv.VoiceData) -> None:
        if user is None or getattr(user, "bot", False) or not data.pcm:
            return
        self.session.write_pcm(user, data.pcm)

    def cleanup(self) -> None:
        self.session.close_audio_files()


@dataclass(slots=True)
class RecordingSession:
    guild_id: int
    voice_channel_id: int
    output_channel_id: int | None
    started_by_id: int
    base_dir: Path
    auto_started: bool = False
    join_reason: str = "manual"
    join_target_id: int | None = None
    automod_enabled: bool = True
    ai_enabled: bool = True
    record_enabled: bool = True
    clips_enabled: bool = True
    captions_enabled: bool = True
    archive_on_disconnect: bool = False
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_mono: float = field(default_factory=time.perf_counter)
    tracks: dict[int, SpeakerTrack] = field(default_factory=dict)
    segments: list[SpeechSegment] = field(default_factory=list)
    lock: threading.RLock = field(default_factory=threading.RLock)
    closed: bool = False
    ended_mono: float | None = None
    loop: asyncio.AbstractEventLoop | None = None
    segment_ready_callback: Callable[["RecordingSession", SpeechSegment], Awaitable[None]] | None = None
    packet_callback: Callable[["RecordingSession", int], None] | None = None

    def _aligned(self, value: int) -> int:
        return value - (value % FRAME_BYTES)

    def _new_track(self, user: discord.Member | discord.User) -> SpeakerTrack:
        display = getattr(user, "display_name", None) or user.name
        path = self.base_dir / f"user_{user.id}.pcm"
        handle = path.open("ab", buffering=0)
        track = SpeakerTrack(user.id, display, path, handle)
        self.tracks[user.id] = track
        return track

    def _close_segment(self, track: SpeakerTrack) -> None:
        segment = track.active_segment
        if segment is None:
            return
        if segment.handle is not None:
            try:
                segment.handle.close()
            except Exception:
                pass
            segment.handle = None
        if segment.byte_count >= int(BYTES_PER_SECOND * 0.12):
            self.segments.append(segment)
            if (
                not self.closed
                and self.loop is not None
                and self.segment_ready_callback is not None
                and not segment.ai_processed
            ):
                segment.ai_processed = True
                callback = self.segment_ready_callback
                self.loop.call_soon_threadsafe(asyncio.create_task, callback(self, segment))
        else:
            try:
                segment.raw_path.unlink(missing_ok=True)
            except OSError:
                pass
        track.active_segment = None

    def _new_segment(self, track: SpeakerTrack, elapsed: float) -> SpeechSegment:
        seg_dir = self.base_dir / "segments"
        seg_dir.mkdir(exist_ok=True)
        path = seg_dir / f"{track.user_id}_{len(self.segments):06d}_{time.time_ns()}.pcm"
        seg = SpeechSegment(
            user_id=track.user_id,
            display_name=track.display_name,
            start_offset=max(0.0, elapsed),
            raw_path=path,
            handle=path.open("wb", buffering=0),
        )
        track.active_segment = seg
        return seg

    def write_pcm(self, user: discord.Member | discord.User, pcm: bytes) -> None:
        now = time.perf_counter()
        elapsed = now - self.started_mono
        with self.lock:
            if self.closed:
                return
            track = self.tracks.get(user.id) or self._new_track(user)

            target = self._aligned(int(elapsed * BYTES_PER_SECOND))
            if target > track.written_bytes:
                silence = target - track.written_bytes
                zero_chunk = b"\x00" * min(1024 * 1024, silence)
                remaining = silence
                while remaining > 0:
                    chunk = zero_chunk if remaining >= len(zero_chunk) else b"\x00" * remaining
                    track.handle.write(chunk)
                    remaining -= len(chunk)
                track.written_bytes += silence
            track.handle.write(pcm)
            track.written_bytes += len(pcm)

            needs_new = track.active_segment is None
            if track.last_packet_at is not None and (now - track.last_packet_at) >= SEGMENT_GAP_SECONDS:
                self._close_segment(track)
                needs_new = True
            if track.active_segment is not None:
                segment_seconds = track.active_segment.byte_count / BYTES_PER_SECOND
                if segment_seconds >= SEGMENT_MAX_SECONDS:
                    self._close_segment(track)
                    needs_new = True
            if needs_new:
                self._new_segment(track, elapsed)

            segment = track.active_segment
            if segment is not None and segment.handle is not None:
                segment.handle.write(pcm)
                segment.byte_count += len(pcm)
            track.last_packet_at = now
            if self.packet_callback is not None:
                self.packet_callback(self, user.id)

    def write_generated_pcm(
        self,
        user_id: int,
        display_name: str,
        pcm: bytes,
        *,
        start_offset: float,
        transcript: str,
    ) -> None:
        """Add audio generated by the bot to the aligned master recording + transcript."""
        if not pcm:
            return
        with self.lock:
            if self.closed:
                return
            track = self.tracks.get(user_id)
            if track is None:
                path = self.base_dir / f"user_{user_id}.pcm"
                handle = path.open("ab", buffering=0)
                track = SpeakerTrack(user_id, display_name, path, handle)
                self.tracks[user_id] = track

            target = self._aligned(int(max(0.0, start_offset) * BYTES_PER_SECOND))
            if target > track.written_bytes:
                silence = target - track.written_bytes
                zero_chunk = b"\x00" * min(1024 * 1024, silence)
                remaining = silence
                while remaining > 0:
                    chunk = zero_chunk if remaining >= len(zero_chunk) else b"\x00" * remaining
                    track.handle.write(chunk)
                    remaining -= len(chunk)
                track.written_bytes += silence

            # If this bot track somehow already extends past the intended start,
            # don't move backwards; append from its current position.
            actual_start = track.written_bytes / BYTES_PER_SECOND
            track.handle.write(pcm)
            track.written_bytes += len(pcm)

            seg_dir = self.base_dir / "segments"
            seg_dir.mkdir(exist_ok=True)
            seg_path = seg_dir / f"{user_id}_{len(self.segments):06d}_{time.time_ns()}.pcm"
            seg_path.write_bytes(pcm)
            self.segments.append(
                SpeechSegment(
                    user_id=user_id,
                    display_name=display_name,
                    start_offset=actual_start,
                    raw_path=seg_path,
                    byte_count=len(pcm),
                    transcript=transcript.strip() or None,
                    ai_processed=True,
                )
            )

    def close_active_segment(self, user_id: int) -> None:
        with self.lock:
            if self.closed:
                return
            track = self.tracks.get(user_id)
            if track is not None:
                self._close_segment(track)

    def close_audio_files(self) -> None:
        with self.lock:
            if self.closed:
                return
            self.closed = True
            self.ended_mono = time.perf_counter()
            for track in self.tracks.values():
                self._close_segment(track)
                try:
                    track.handle.close()
                except Exception:
                    pass

    @property
    def duration(self) -> float:
        end = self.ended_mono if self.ended_mono is not None else time.perf_counter()
        return max(0.0, end - self.started_mono)


def _write_wav_from_raw(raw_path: Path, wav_path: Path, *, pad_to_bytes: int | None = None) -> None:
    with raw_path.open("rb") as src, wave.open(str(wav_path), "wb") as dst:
        dst.setnchannels(CHANNELS)
        dst.setsampwidth(SAMPLE_WIDTH)
        dst.setframerate(SAMPLE_RATE)
        copied = 0
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            dst.writeframesraw(chunk)
            copied += len(chunk)
        if pad_to_bytes and copied < pad_to_bytes:
            remaining = pad_to_bytes - copied
            zero = b"\x00" * min(1024 * 1024, remaining)
            while remaining > 0:
                chunk = zero if remaining >= len(zero) else b"\x00" * remaining
                dst.writeframesraw(chunk)
                remaining -= len(chunk)
        dst.writeframes(b"")


def _mix_tracks(session: RecordingSession) -> Path:
    """Mix per-user aligned PCM tracks into one stereo WAV without loading it all in RAM."""
    import audioop

    master = session.base_dir / "master.wav"
    target_bytes = session._aligned(int(session.duration * BYTES_PER_SECOND))
    handles = [track.raw_path.open("rb") for track in session.tracks.values()]
    chunk_size = 192_000  # 0.5 sec of stereo 48 kHz 16-bit audio.
    try:
        with wave.open(str(master), "wb") as out:
            out.setnchannels(CHANNELS)
            out.setsampwidth(SAMPLE_WIDTH)
            out.setframerate(SAMPLE_RATE)
            position = 0
            while position < target_bytes:
                take = min(chunk_size, target_bytes - position)
                mixed = b"\x00" * take
                for handle in handles:
                    data = handle.read(take)
                    if len(data) < take:
                        data += b"\x00" * (take - len(data))
                    mixed = audioop.add(mixed, data, SAMPLE_WIDTH)
                out.writeframesraw(mixed)
                position += take
            out.writeframes(b"")
    finally:
        for handle in handles:
            handle.close()
    return master


def _mix_recent_clip(session: RecordingSession, seconds: int = 180) -> Path:
    """Mix only the most recent slice of all aligned speaker tracks."""
    import audioop

    end_bytes = session._aligned(int(session.duration * BYTES_PER_SECOND))
    clip_bytes = session._aligned(int(max(1, seconds) * BYTES_PER_SECOND))
    start_bytes = max(0, end_bytes - clip_bytes)
    take_total = max(0, end_bytes - start_bytes)
    clip_wav = session.base_dir / f"clip_{int(time.time())}.wav"
    handles = []
    try:
        for track in session.tracks.values():
            handle = track.raw_path.open("rb")
            handle.seek(start_bytes)
            handles.append(handle)
        with wave.open(str(clip_wav), "wb") as out:
            out.setnchannels(CHANNELS)
            out.setsampwidth(SAMPLE_WIDTH)
            out.setframerate(SAMPLE_RATE)
            position = 0
            chunk_size = 192_000
            while position < take_total:
                take = min(chunk_size, take_total - position)
                mixed = b"\x00" * take
                for handle in handles:
                    data = handle.read(take)
                    if len(data) < take:
                        data += b"\x00" * (take - len(data))
                    mixed = audioop.add(mixed, data, SAMPLE_WIDTH)
                out.writeframesraw(mixed)
                position += take
            out.writeframes(b"")
    finally:
        for handle in handles:
            handle.close()
    return clip_wav


def _ffmpeg_executable() -> str | None:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg")


def _audio_file_to_pcm(path: Path) -> bytes:
    """Decode an audio file to the same PCM format used by Discord receive."""
    ffmpeg = _ffmpeg_executable()
    if not ffmpeg:
        return b""
    proc = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-f",
            "s16le",
            "-ar",
            str(SAMPLE_RATE),
            "-ac",
            str(CHANNELS),
            "pipe:1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        log.warning("Could not decode generated bot audio for the master recording: %s", proc.stderr.decode(errors="ignore")[:500])
        return b""
    return proc.stdout


def _compress_master(master_wav: Path) -> Path:
    ffmpeg = _ffmpeg_executable()
    if not ffmpeg:
        return master_wav
    mp3 = master_wav.with_suffix(".mp3")
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(master_wav),
            "-ac",
            "2",
            "-ar",
            "48000",
            "-b:a",
            "96k",
            str(mp3),
        ],
        check=True,
    )
    return mp3


def _split_audio_for_discord(audio_path: Path, max_bytes: int) -> list[Path]:
    if audio_path.stat().st_size <= max_bytes:
        return [audio_path]
    ffmpeg = _ffmpeg_executable()
    if not ffmpeg or audio_path.suffix.lower() != ".mp3":
        return []

    # 96 kbps CBR; stay comfortably below Discord's upload ceiling.
    seconds = max(60, int((max_bytes * 8 / 96_000) * 0.88))
    pattern = audio_path.with_name("master_part_%03d.mp3")
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(audio_path),
            "-f",
            "segment",
            "-segment_time",
            str(seconds),
            "-c",
            "copy",
            str(pattern),
        ],
        check=True,
    )
    return sorted(audio_path.parent.glob("master_part_*.mp3"))


def _segment_to_wav(segment: SpeechSegment) -> Path:
    wav_path = segment.raw_path.with_suffix(".wav")
    _write_wav_from_raw(segment.raw_path, wav_path)
    return wav_path


def _clock(offset: float) -> str:
    total_ms = max(0, int(offset * 1000))
    ms = total_ms % 1000
    total = total_ms // 1000
    sec = total % 60
    minutes = (total // 60) % 60
    hours = total // 3600
    if hours:
        return f"{hours:02d}:{minutes:02d}:{sec:02d}.{ms:03d}"
    return f"{minutes:02d}:{sec:02d}.{ms:03d}"


class VoiceRecordingCog(commands.Cog):
    record = app_commands.Group(
        name="record",
        description="Record and transcribe voice calls.",
        default_permissions=discord.Permissions(administrator=True),
    )

    def __init__(self, bot: MommyExeBot) -> None:
        self.bot = bot
        self.sessions: dict[int, RecordingSession] = {}
        self._auto_locks: dict[int, asyncio.Lock] = {}
        self._solo_silence_tasks: dict[tuple[int, int], asyncio.Task] = {}
        self._segment_watchdogs: dict[int, asyncio.Task] = {}
        self._solo_reply_locks: dict[int, asyncio.Lock] = {}
        self._solo_history: dict[int, list[tuple[str, str]]] = {}
        self._active_conversations: dict[tuple[int, int], float] = {}
        # A spoken hush/stop command silences Mommy guild-wide until somebody
        # explicitly addresses her again. This also blocks replies that were
        # already queued or still generating when the hush command arrived.
        self._vc_hushed_guilds: set[int] = set()
        # Short-lived VC conversation context. This is RAM-only and is never
        # written to MongoDB unless the speaker explicitly addresses Mommy.
        self._vc_context: dict[tuple[int, int], list[tuple[str, str]]] = {}
        self._openai = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY")) if os.getenv("OPENAI_API_KEY") else None

    async def cog_unload(self) -> None:
        for task in self._solo_silence_tasks.values():
            task.cancel()
        self._solo_silence_tasks.clear()
        for task in self._segment_watchdogs.values():
            task.cancel()
        self._segment_watchdogs.clear()
        for guild_id in list(self.sessions):
            session = self.sessions.pop(guild_id)
            session.close_audio_files()

    async def _recording_config(self, guild: discord.Guild) -> dict:
        profile = await self.bot.database.get_guild_profile(guild.id)
        if profile is None:
            profile = await self.bot.database.ensure_guild_profile(guild)
        return profile.get("voice_recording", {})

    async def _tts_voice(self, guild_id: int) -> str:
        profile = await self.bot.database.get_guild_profile(guild_id)
        configured = ((profile or {}).get("voice_recording", {}) or {}).get("tts_voice")
        return str(configured or os.getenv("MOMMY_TTS_VOICE", "shimmer")).strip().lower()

    def _piper_enabled(self) -> bool:
        return bool(os.getenv("PIPER_TTS_URL", "").strip())

    async def _synthesize_speech(
        self,
        text: str,
        path: Path,
        *,
        guild_id: int | None = None,
        openai_voice: str | None = None,
        instructions: str | None = None,
    ) -> str:
        """Synthesize speech with Piper first, then OpenAI fallback."""
        piper_url = os.getenv("PIPER_TTS_URL", "").strip()
        if piper_url:
            headers = {"Accept": "audio/wav", "Content-Type": "application/json"}
            piper_key = os.getenv("PIPER_API_KEY", "").strip()
            if piper_key:
                headers["Authorization"] = f"Bearer {piper_key}"
            try:
                timeout = aiohttp.ClientTimeout(total=120)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(piper_url, headers=headers, json={"text": text}) as response:
                        if response.status >= 400:
                            detail = (await response.text())[:500]
                            raise RuntimeError(f"Piper TTS failed ({response.status}): {detail}")
                        audio = await response.read()
                        if not audio:
                            raise RuntimeError("Piper TTS returned an empty audio response")
                        path.write_bytes(audio)
                return "Piper"
            except Exception as exc:
                log.exception("Piper TTS failed; trying fallback provider: %s", exc)

        if self._openai is None:
            raise RuntimeError("No working TTS provider is configured. Piper failed and OpenAI is unavailable.")
        voice = openai_voice
        if not voice and guild_id is not None:
            voice = await self._tts_voice(guild_id)
        voice = voice or os.getenv("MOMMY_TTS_VOICE", "shimmer").strip().lower()
        async with self._openai.audio.speech.with_streaming_response.create(
            model=os.getenv("SOLO_AI_TTS_MODEL", "gpt-4o-mini-tts"),
            voice=voice,
            input=text,
            instructions=instructions or (
                "Adult feminine voice. Calm, confident, warm, motherly, dry and lightly sassy. "
                "Natural Discord conversation, not seductive and not an announcer or customer-service voice."
            ),
            response_format="mp3",
        ) as response:
            await response.stream_to_file(path)
        return "OpenAI"

    async def preview_tts_voice(self, guild: discord.Guild, member: discord.Member, voice: str) -> str:
        """Play a temporary voice sample without changing the saved voice."""
        voice = (voice or "").strip().lower()
        using_piper = self._piper_enabled()
        if not using_piper:
            if self._openai is None:
                return "No TTS provider is configured."
            allowed = {"alloy", "ash", "ballad", "coral", "echo", "fable", "onyx", "nova", "sage", "shimmer", "verse", "marin", "cedar"}
            if voice not in allowed:
                return "That voice is not in Mommy's supported OpenAI voice list."

        vc = guild.voice_client
        connected_here = False
        if not isinstance(vc, voice_recv.VoiceRecvClient) or not vc.is_connected():
            channel = getattr(getattr(member, "voice", None), "channel", None)
            if not isinstance(channel, discord.VoiceChannel):
                return "Join a voice channel first so I have somewhere to preview it."
            try:
                vc = await channel.connect(cls=voice_recv.VoiceRecvClient)
                connected_here = True
            except Exception as exc:
                log.exception("Voice preview connect failed")
                return f"I couldn't join your VC for the preview: {type(exc).__name__}."
        else:
            bot_channel = getattr(vc, "channel", None)
            member_channel = getattr(getattr(member, "voice", None), "channel", None)
            if member_channel is not None and bot_channel is not None and member_channel.id != bot_channel.id:
                return f"I'm already busy in {getattr(bot_channel, 'mention', 'another VC')}. Join me there to preview voices."

        ffmpeg = _ffmpeg_executable()
        if not ffmpeg:
            if connected_here:
                await vc.disconnect(force=True)
            return "ffmpeg is unavailable, so I can't play the preview."

        tmp_dir = Path(os.getenv("RECORDING_TMP_DIR", "/tmp/mommy-exe-recordings"))
        tmp_dir.mkdir(parents=True, exist_ok=True)
        label = "piper" if using_piper else voice
        path = tmp_dir / f"voice_preview_{label}_{time.time_ns()}.mp3"
        try:
            provider = await self._synthesize_speech(
                "Behave, sweetheart. Mommy is testing a new voice.",
                path,
                guild_id=guild.id,
                openai_voice=voice,
            )

            while vc.is_playing():
                await asyncio.sleep(0.05)
            done = asyncio.Event()

            def _after(error: Exception | None) -> None:
                if error:
                    log.error("Voice preview playback failed: %r", error)
                self.bot.loop.call_soon_threadsafe(done.set)

            vc.play(discord.FFmpegPCMAudio(str(path), executable=ffmpeg), after=_after)
            try:
                await asyncio.wait_for(done.wait(), timeout=15)
            except asyncio.TimeoutError:
                pass
            if provider == "Piper":
                return "Previewed the configured **Piper** voice. Mommy is already using this voice for live TTS."
            return f"Previewed **{voice}**. This did not change the saved voice."
        except Exception as exc:
            log.exception("Voice preview failed for %s", label)
            return f"Voice preview failed: {type(exc).__name__}."
        finally:
            path.unlink(missing_ok=True)
            if connected_here and isinstance(vc, voice_recv.VoiceRecvClient) and vc.is_connected():
                try:
                    await vc.disconnect(force=True)
                except Exception:
                    pass

    async def _check_control_channel(self, interaction: discord.Interaction) -> bool:
        assert interaction.guild is not None
        config = await self._recording_config(interaction.guild)
        control_id = config.get("control_channel_id")
        if control_id and interaction.channel_id != int(control_id):
            await interaction.response.send_message(
                f"Recording controls can only be used in <#{int(control_id)}>.",
                ephemeral=True,
            )
            return False
        return True

    def _auto_lock(self, guild_id: int) -> asyncio.Lock:
        lock = self._auto_locks.get(guild_id)
        if lock is None:
            lock = asyncio.Lock()
            self._auto_locks[guild_id] = lock
        return lock

    @staticmethod
    def _human_members(channel: discord.VoiceChannel) -> list[discord.Member]:
        return [member for member in channel.members if not member.bot]

    def _solo_reply_lock(self, guild_id: int) -> asyncio.Lock:
        lock = self._solo_reply_locks.get(guild_id)
        if lock is None:
            lock = asyncio.Lock()
            self._solo_reply_locks[guild_id] = lock
        return lock

    def _arm_solo_silence_timer(self, session: RecordingSession, user_id: int) -> None:
        if session.loop is None:
            return
        key = (session.guild_id, user_id)

        def schedule() -> None:
            old = self._solo_silence_tasks.pop(key, None)
            if old is not None:
                old.cancel()
            self._solo_silence_tasks[key] = asyncio.create_task(
                self._solo_silence_timeout(session, user_id)
            )

        session.loop.call_soon_threadsafe(schedule)

    async def _solo_silence_timeout(self, session: RecordingSession, user_id: int) -> None:
        try:
            await asyncio.sleep(float(os.getenv("SOLO_AI_SILENCE_SECONDS", "0.65")))
            if self.sessions.get(session.guild_id) is session and not session.closed:
                session.close_active_segment(user_id)
        except asyncio.CancelledError:
            pass
        finally:
            self._solo_silence_tasks.pop((session.guild_id, user_id), None)

    def _start_segment_watchdog(self, session: RecordingSession) -> None:
        old = self._segment_watchdogs.pop(session.guild_id, None)
        if old is not None:
            old.cancel()
        self._segment_watchdogs[session.guild_id] = asyncio.create_task(
            self._segment_watchdog(session)
        )

    async def _segment_watchdog(self, session: RecordingSession) -> None:
        """Close speech chunks after silence so live captions fire reliably."""
        silence_seconds = float(os.getenv("LIVE_CAPTION_SILENCE_SECONDS", "0.65"))
        try:
            while self.sessions.get(session.guild_id) is session and not session.closed:
                await asyncio.sleep(0.20)
                now = time.perf_counter()
                ready: list[int] = []
                with session.lock:
                    for user_id, track in session.tracks.items():
                        if (
                            track.active_segment is not None
                            and track.last_packet_at is not None
                            and (now - track.last_packet_at) >= silence_seconds
                        ):
                            ready.append(user_id)
                for user_id in ready:
                    session.close_active_segment(user_id)
        except asyncio.CancelledError:
            pass
        finally:
            current = self._segment_watchdogs.get(session.guild_id)
            if current is asyncio.current_task():
                self._segment_watchdogs.pop(session.guild_id, None)

    async def _transcribe_for_solo(self, segment: SpeechSegment) -> str:
        if segment.transcript is not None:
            return segment.transcript
        if self._openai is None:
            return ""
        wav_path = await asyncio.to_thread(_segment_to_wav, segment)
        try:
            with wav_path.open("rb") as audio:
                result = await self._openai.audio.transcriptions.create(
                    model=os.getenv("TRANSCRIPTION_MODEL", "gpt-4o-mini-transcribe"),
                    file=audio,
                    response_format="json",
                    # This server is English-only. Force STT to stay in English instead
                    # of auto-detecting short/slang-heavy clips as Korean/Japanese/etc.
                    language="en",
                    prompt=(
                        "English-only casual Discord voice chat. Transcribe exactly what is spoken in English. "
                        "Preserve profanity, slang, usernames, racial slurs, and offensive words verbatim when spoken. "
                        "Do not translate speech and do not output Korean, Japanese, Chinese, or other non-English scripts. "
                        "For unclear short words, prefer the most plausible English word from the surrounding audio/context."
                    ),
                )
            text = _clean_english_transcript(getattr(result, "text", "") or "")

            # If the model still hallucinated Japanese/Korean/Chinese script despite language="en",
            # retry once with an even stricter English-only instruction.
            if not text and getattr(result, "text", ""):
                with wav_path.open("rb") as audio:
                    retry = await self._openai.audio.transcriptions.create(
                        model=os.getenv("TRANSCRIPTION_MODEL", "gpt-4o-mini-transcribe"),
                        file=audio,
                        response_format="json",
                        language="en",
                        prompt=(
                            "The speakers are American English speakers in a Discord voice channel. "
                            "Return ONLY an English transcription using Latin letters. Never translate. "
                            "Preserve slang, profanity, usernames, racial slurs, and offensive words exactly as spoken. "
                            "If audio is unclear, choose the most plausible English wording; never output CJK, Hangul, hiragana, or katakana."
                        ),
                    )
                text = _clean_english_transcript(getattr(retry, "text", "") or "")

            # Guard against common low-audio hallucinations / prompt echoes.
            hallucination_phrases = (
                "casual discord voice chat",
                "preserve profanity, slang, usernames",
                "use surrounding context to avoid guessing common words incorrectly",
                "do not sanitize speech",
            )
            lowered = text.lower()
            if any(phrase in lowered for phrase in hallucination_phrases):
                text = ""

            segment.transcript = text
            return text
        finally:
            wav_path.unlink(missing_ok=True)

    async def _generate_solo_reply(
        self,
        guild_id: int,
        user_id: int,
        display_name: str,
        user_text: str,
        *,
        human_count: int,
    ) -> str:
        if self._openai is None:
            return ""

        # Long-term memory is intentionally only the conversations that actually
        # involve Mommy.exe, never the entire VC transcript.
        remember = await self.bot.memory_enabled(guild_id, user_id)
        history_docs = await self.bot._load_memory(guild_id, user_id, limit=10) if remember else []  # noqa: SLF001
        history_lines: list[str] = []
        for item in history_docs:
            name = str(item.get("display_name") or display_name or "User")
            history_lines.append(f"{name}: {item.get('human_text', '')}")
            history_lines.append(f"Mommy.exe: {item.get('reply_text', '')}")
        recent = "\n".join(history_lines[-20:])

        extra = ""
        joey_id = os.getenv("MOMMY_JOEY_USER_ID", "").strip()
        if human_count == 1 and joey_id.isdigit() and int(joey_id) == user_id:
            extra = (
                " This speaker is Joey and he is alone with you. You can tease him a little more than usual, especially "
                "if he is rude to you, but keep it playful and do not relentlessly roast or demean him."
            )

        live_context = self._vc_recent_context(guild_id, user_id)
        prompt = (
            (f"Things remembered from earlier direct conversations with this person:\n{recent}\n\n" if recent else "")
            + (f"Temporary context from the current VC conversation (do not treat this as long-term memory):\n{live_context}\n\n" if live_context else "")
            + f"There are currently {human_count} human(s) in the voice channel.\n"
            + f"{display_name}: {user_text}\nMommy.exe:"
        )
        response = await self._openai.responses.create(
            model=os.getenv("MOMMY_AI_MODEL", "gpt-5.6-luna"),
            instructions=(
                MOMMY_SYSTEM_PROMPT
                + " ALWAYS reply in English only. Never switch languages and never output Korean, Japanese, Chinese, or other non-English scripts, even if a transcript contains them."
                + " Keep spoken VC replies very concise for low-latency voice chat, usually one sentence and rarely over 25 words."
                + " If someone is rude, insulting, dismissive, or shit-talks you, you may sass them back playfully, but stay warm and do not escalate into cruelty."
                + extra
            ),
            input=prompt,
            max_output_tokens=int(os.getenv("MOMMY_AI_MAX_OUTPUT_TOKENS", "90")),
        )
        return sanitize_output((response.output_text or "").strip())[:600].strip()

    @staticmethod
    def _is_addressed_to_mommy(text: str) -> bool:
        lowered = (text or "").casefold()
        if re.search(r"\bmommy(?:\.exe)?\b|\bmommy\s+bot\b", lowered):
            return True
        # In a two-person VC, clear insults aimed at the bot also count as addressing her.
        insult = r"(?:stupid|dumb|annoying|bitch|shut\s+up|fuck\s+you|hate\s+you|useless|idiot)"
        return bool(re.search(rf"\b(?:you|bot|mommy)\b.{0,28}\b{insult}\b|\b{insult}\b.{0,28}\b(?:you|bot|mommy)\b", lowered))

    @staticmethod
    def _is_stop_talking_request(text: str) -> bool:
        lowered = (text or "").casefold().strip()
        patterns = (
            r"\bshut\s+up\b",
            r"\bstop\s+talking\b",
            r"\bbe\s+quiet\b",
            r"\bquiet\s+down\b",
            r"\bshush\b",
            r"\bshh+\b",
            r"\bhush\b",
            r"\bstop\s+speaking\b",
            r"\bdon['’]?t\s+talk\b",
        )
        return any(re.search(pattern, lowered) for pattern in patterns)

    def _silence_conversation(self, guild: discord.Guild, user_id: int) -> None:
        # Hush is intentionally guild-wide: if one person tells Mommy to stop,
        # she must not finish a queued reply for somebody else a second later.
        self._vc_hushed_guilds.add(guild.id)
        for key in [k for k in self._active_conversations if k[0] == guild.id]:
            self._active_conversations.pop(key, None)
        for key in [k for k in self._vc_context if k[0] == guild.id]:
            self._vc_context.pop(key, None)
        vc = guild.voice_client
        if vc is not None and vc.is_connected() and vc.is_playing():
            try:
                vc.stop()
            except Exception:
                log.exception("Could not stop Mommy.exe VC playback in guild %s", guild.id)

    async def _maybe_post_presence_message(self, guild: discord.Guild, session: RecordingSession, *, joined: bool) -> None:
        """Post at most one generic clock-in/clock-out message in a staff-visible text channel.

        Never mention recording, automod, follow targets, or listening status.
        """
        config = await self._recording_config(guild)
        routes = config.get("caption_routes") or {}
        candidate_ids = [
            routes.get(str(session.voice_channel_id)),
            config.get("caption_channel_id"),
            config.get("control_channel_id"),
            session.output_channel_id,
        ]
        seen: set[int] = set()
        target_channel: discord.TextChannel | None = None
        for raw in candidate_ids:
            try:
                cid = int(raw)
            except (TypeError, ValueError):
                continue
            if cid in seen:
                continue
            seen.add(cid)
            channel = guild.get_channel(cid)
            if isinstance(channel, discord.TextChannel):
                target_channel = channel
                break
        if target_channel is None:
            return
        content = "**Mommy has clocked in. Be funny.**" if joined else "**Mommy has clocked out. Try to behave.**"
        try:
            await target_channel.send(content)
        except Exception:
            log.exception("Could not post Mommy.exe presence message in guild %s", guild.id)

    def _conversation_is_active(self, guild_id: int, user_id: int) -> bool:
        return self._active_conversations.get((guild_id, user_id), 0.0) >= time.monotonic()

    def _touch_conversation(self, guild_id: int, user_id: int) -> None:
        self._active_conversations[(guild_id, user_id)] = time.monotonic() + float(
            os.getenv("MOMMY_CONVERSATION_WINDOW_SECONDS", "60")
        )

    def _vc_recent_context(self, guild_id: int, user_id: int) -> str:
        turns = self._vc_context.get((guild_id, user_id), [])[-8:]
        lines: list[str] = []
        for human_text, mommy_text in turns:
            lines.append(f"User: {human_text}")
            lines.append(f"Mommy.exe: {mommy_text}")
        return "\n".join(lines)

    def _remember_vc_turn(self, guild_id: int, user_id: int, human_text: str, mommy_text: str) -> None:
        key = (guild_id, user_id)
        turns = self._vc_context.setdefault(key, [])
        turns.append((human_text.strip()[:700], mommy_text.strip()[:700]))
        if len(turns) > 8:
            del turns[:-8]

    def _clear_expired_vc_context(self, guild_id: int, user_id: int) -> None:
        if not self._conversation_is_active(guild_id, user_id):
            self._vc_context.pop((guild_id, user_id), None)

    async def _speak_solo_reply(self, guild: discord.Guild, session: RecordingSession, text: str) -> str | None:
        if not text or self._openai is None or guild.id in self._vc_hushed_guilds:
            return None
        # Final speech-layer guard: Mommy.exe must never speak this name,
        # even if a model response somehow contains it.
        import re
        text = re.sub(r"\bfallon(?:'s|’s)?\b", "you", text, flags=re.IGNORECASE)
        channel = guild.get_channel(session.voice_channel_id)
        if not isinstance(channel, discord.VoiceChannel) or len(self._human_members(channel)) < 1:
            return None
        vc = guild.voice_client
        if not isinstance(vc, voice_recv.VoiceRecvClient) or not vc.is_connected():
            return None

        tts_path = session.base_dir / f"solo_reply_{time.time_ns()}.mp3"
        await self._synthesize_speech(
            text,
            tts_path,
            guild_id=guild.id,
            instructions=(
                "Adult feminine voice. Calm, confident, warm, motherly, dry and lightly sassy. "
                "Natural Discord conversation, not seductive and not an announcer or customer-service voice."
            ),
        )

        try:
            if guild.id in self._vc_hushed_guilds:
                return None
            channel = guild.get_channel(session.voice_channel_id)
            if not isinstance(channel, discord.VoiceChannel) or len(self._human_members(channel)) < 1:
                return None
            while vc.is_playing():
                if guild.id in self._vc_hushed_guilds:
                    return None
                await asyncio.sleep(0.05)
            if guild.id in self._vc_hushed_guilds:
                return None
            ffmpeg = _ffmpeg_executable()
            if not ffmpeg:
                log.warning("Solo AI could not speak because ffmpeg is unavailable.")
                return None
            # discord-ext-voice-recv only receives audio coming FROM users; it does
            # not loop the bot's own playback back into the receive sink. Decode the
            # TTS file and explicitly add it to the aligned recording track so the
            # final master audio contains Mommy.exe too.
            bot_pcm = await asyncio.to_thread(_audio_file_to_pcm, tts_path)
            playback_start = time.perf_counter() - session.started_mono
            if bot_pcm and self.bot.user is not None:
                session.write_generated_pcm(
                    self.bot.user.id,
                    "Mommy.exe",
                    bot_pcm,
                    start_offset=playback_start,
                    transcript=text,
                )

            finished = asyncio.Event()

            def after_playback(error: Exception | None) -> None:
                if error:
                    log.error("Solo AI playback failed: %r", error)
                self.bot.loop.call_soon_threadsafe(finished.set)

            vc.play(discord.FFmpegPCMAudio(str(tts_path), executable=ffmpeg), after=after_playback)
            await finished.wait()
            return text
        finally:
            tts_path.unlink(missing_ok=True)

    async def speak_automod_warning(self, guild: discord.Guild, trigger: dict | None = None) -> None:
        """Speak a short automod warning regardless of how many humans are in VC.

        This bypasses Mommy's normal 1-2-human AI reply gate. It is only called
        after a configured trigger is credibly detected and its spoken-warning
        toggle is enabled.
        """
        if guild.id in self._vc_hushed_guilds:
            return
        vc = guild.voice_client
        if not isinstance(vc, voice_recv.VoiceRecvClient) or not vc.is_connected():
            return
        ffmpeg = _ffmpeg_executable()
        if not ffmpeg:
            return

        async with self._solo_reply_lock(guild.id):
            profile = await self.bot.database.get_guild_profile(guild.id) or {}
            cfg = profile.get("vc_automod") or {}
            trigger = trigger or {}
            style = str(trigger.get("warning_style") or "random").strip().lower()
            custom = str(trigger.get("warning_text") or "").strip()[:180]
            pool = [str(x).strip()[:180] for x in (cfg.get("warning_lines") or []) if str(x).strip()]
            if not pool:
                pool = [
                    "Watch it. That's your warning.",
                    "Careful. You're pushing it.",
                    "Knock it off.",
                    "You know better than that.",
                    "Don't test me.",
                    "Behave.",
                    "That's enough.",
                ]
            text = custom if style == "custom" and custom else random.choice(pool)

            session = self.sessions.get(guild.id)
            tmp_dir = session.base_dir if session is not None else Path(os.getenv("RECORDING_TMP_DIR", "/tmp/mommy-exe-recordings"))
            tmp_dir.mkdir(parents=True, exist_ok=True)
            path = tmp_dir / f"automod_warning_{time.time_ns()}.mp3"
            try:
                await self._synthesize_speech(
                    text,
                    path,
                    guild_id=guild.id,
                    instructions=(
                        "Adult feminine voice. Firm, clear, authoritative warning. "
                        "Short and direct; not playful, seductive, or conversational."
                    ),
                )

                # Voice state can change while TTS is being generated (for example,
                # a punishment may disconnect Mommy from VC). Re-check before we
                # touch FFmpeg/playback so a stale VoiceClient cannot throw
                # ``ClientException: Not connected to voice``.
                current_vc = guild.voice_client
                if (
                    current_vc is not vc
                    or not isinstance(current_vc, voice_recv.VoiceRecvClient)
                    or not current_vc.is_connected()
                ):
                    log.info(
                        "Skipping VC automod warning in guild %s because voice disconnected during TTS",
                        guild.id,
                    )
                    return

                while vc.is_playing():
                    if not vc.is_connected():
                        log.info(
                            "Skipping VC automod warning in guild %s because voice disconnected before playback",
                            guild.id,
                        )
                        return
                    await asyncio.sleep(0.05)

                # Include the warning in an active recording/master track when one exists.
                if session is not None and self.sessions.get(guild.id) is session and not session.closed:
                    try:
                        bot_pcm = await asyncio.to_thread(_audio_file_to_pcm, path)
                        playback_start = time.perf_counter() - session.started_mono
                        if bot_pcm and self.bot.user is not None:
                            session.write_generated_pcm(
                                self.bot.user.id,
                                "Mommy.exe",
                                bot_pcm,
                                start_offset=playback_start,
                                transcript=text,
                            )
                    except Exception:
                        log.exception("Could not add automod warning to the recording master")

                if not vc.is_connected():
                    log.info(
                        "Skipping VC automod warning in guild %s because voice disconnected before vc.play",
                        guild.id,
                    )
                    return

                done = asyncio.Event()
                def _after(error: Exception | None) -> None:
                    if error:
                        log.error("Automod warning playback failed: %r", error)
                    self.bot.loop.call_soon_threadsafe(done.set)
                vc.play(discord.FFmpegPCMAudio(str(path), executable=ffmpeg), after=_after)
                try:
                    await asyncio.wait_for(done.wait(), timeout=15)
                except asyncio.TimeoutError:
                    pass
            finally:
                path.unlink(missing_ok=True)

    async def play_punishment_sound(self, guild: discord.Guild) -> None:
        """Play the guild's configured punishment sound in the current VC.

        Modes:
        - ``tts``: synthesize the configured short line (default: "You're banned.")
        - ``url``: download and play a configured MP3/WAV/OGG URL
        - ``off``: no sound

        ``MOMMY_PUNISHMENT_SOUND_PATH`` remains a private deployment-level fallback.
        """
        vc = guild.voice_client
        if not isinstance(vc, voice_recv.VoiceRecvClient) or not vc.is_connected():
            return
        ffmpeg = _ffmpeg_executable()
        if not ffmpeg:
            return

        profile = await self.bot.database.get_guild_profile(guild.id) or {}
        cfg = profile.get("vc_automod") or {}
        mode = str(cfg.get("punishment_sound_mode") or "tts").strip().lower()
        if mode == "off" or not cfg.get("punishment_sound_enabled", True):
            return

        if mode == "soundboard":
            sound_id = cfg.get("punishment_soundboard_id")
            if not sound_id:
                raise RuntimeError("No server Soundboard sound is selected")
            try:
                sound_id = int(sound_id)
            except (TypeError, ValueError) as exc:
                raise RuntimeError("Saved Soundboard sound ID is invalid") from exc
            channel = getattr(vc, "channel", None)
            if channel is None or not hasattr(channel, "send_sound"):
                raise RuntimeError("This discord.py build does not support Soundboard playback")
            sound = guild.get_soundboard_sound(sound_id)
            if sound is None:
                try:
                    sound = await guild.fetch_soundboard_sound(sound_id)
                except Exception as exc:
                    raise RuntimeError("The selected Soundboard sound no longer exists or cannot be accessed") from exc
            if not getattr(sound, "available", True):
                raise RuntimeError("The selected Soundboard sound is currently unavailable")
            await channel.send_sound(sound)
            return

        temp_path: Path | None = None
        path: Path | None = None

        if mode == "url":
            sound_url = str(cfg.get("punishment_sound_url") or "").strip()
            if sound_url:
                suffix = Path(sound_url.split("?", 1)[0]).suffix.lower()
                if suffix not in {".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac"}:
                    suffix = ".audio"
                temp_path = Path(os.getenv("RECORDING_TMP_DIR", "/tmp/mommy-exe-recordings")) / f"punish_{time.time_ns()}{suffix}"
                temp_path.parent.mkdir(parents=True, exist_ok=True)
                timeout = aiohttp.ClientTimeout(total=30)
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152.0 Safari/537.36",
                    "Accept": "audio/mpeg,audio/*;q=0.9,*/*;q=0.5",
                    "Referer": "https://www.myinstants.com/",
                }
                last_error: Exception | None = None
                for attempt in range(2):
                    try:
                        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                            async with session.get(sound_url, allow_redirects=True) as response:
                                if response.status >= 400:
                                    detail = (await response.text(errors="ignore"))[:300]
                                    raise RuntimeError(
                                        f"Punishment sound download failed ({response.status}) from {response.url}: {detail}"
                                    )
                                data = await response.read()
                                if not data:
                                    raise RuntimeError("Punishment sound URL returned an empty file")
                                if len(data) > 12 * 1024 * 1024:
                                    raise RuntimeError("Punishment sound is too large (max 12 MB)")
                                temp_path.write_bytes(data)
                                break
                    except Exception as exc:
                        last_error = exc
                        if attempt == 0:
                            await asyncio.sleep(0.35)
                else:
                    raise RuntimeError(f"Could not download punishment sound: {last_error}") from last_error
                path = temp_path

        if path is None:
            configured = os.getenv("MOMMY_PUNISHMENT_SOUND_PATH", "").strip()
            local = Path(configured) if configured else None
            if local is not None and local.exists():
                path = local
            else:
                if not self._piper_enabled() and self._openai is None:
                    return
                temp_path = Path(os.getenv("RECORDING_TMP_DIR", "/tmp/mommy-exe-recordings")) / f"punish_{time.time_ns()}.mp3"
                temp_path.parent.mkdir(parents=True, exist_ok=True)
                line = str(cfg.get("punishment_tts_text") or "You're banned.").strip()[:180] or "You're banned."
                await self._synthesize_speech(
                    line,
                    temp_path,
                    guild_id=guild.id,
                    instructions="Adult feminine voice. Dry, decisive, amused, very short delivery.",
                )
                path = temp_path

        try:
            while vc.is_playing():
                await asyncio.sleep(0.05)
            done = asyncio.Event()
            def _after(error):
                if error:
                    log.error("Punishment sound playback failed: %r", error)
                self.bot.loop.call_soon_threadsafe(done.set)
            raw_volume = cfg.get("punishment_sound_volume", 3.0)
            try:
                volume = max(0.25, min(4.0, float(raw_volume)))
            except (TypeError, ValueError):
                volume = 3.0
            audio = discord.FFmpegPCMAudio(
                str(path),
                executable=ffmpeg,
                options=f"-vn -filter:a volume={volume:.2f}",
            )
            vc.play(audio, after=_after)
            try:
                await asyncio.wait_for(done.wait(), timeout=10)
            except asyncio.TimeoutError:
                pass
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    async def _handle_segment_ready(self, session: RecordingSession, segment: SpeechSegment) -> None:
        """Post live captions when configured, then run solo AI when eligible."""
        guild = self.bot.get_guild(session.guild_id)
        if guild is None or self.sessions.get(guild.id) is not session:
            return

        config = await self._recording_config(guild)
        routes = config.get("caption_routes") or {}
        caption_id = routes.get(str(session.voice_channel_id)) or config.get("caption_channel_id")
        caption_channel: discord.TextChannel | None = None
        if session.captions_enabled and caption_id:
            candidate = guild.get_channel(int(caption_id))
            if isinstance(candidate, discord.TextChannel):
                caption_channel = candidate

        text = ""
        if self._openai is not None and segment.byte_count >= int(BYTES_PER_SECOND * 0.20):
            try:
                text = await self._transcribe_for_solo(segment)
                if caption_channel is not None and text and len(text.strip()) >= 2:
                    await caption_channel.send(f"**{segment.display_name}:** {text}")
            except Exception:
                log.exception("Live transcription failed in guild %s", guild.id)

        # VC automod sees every finalized transcript even when live captions are off.
        if text and session.automod_enabled:
            automod = self.bot.get_cog("VCAutomodCog")
            if automod is not None and hasattr(automod, "process_transcript"):
                try:
                    await automod.process_transcript(
                        guild, segment.user_id, text, voice_channel_id=session.voice_channel_id
                    )
                except Exception:
                    log.exception("VC automod processing failed in guild %s", guild.id)

        if session.ai_enabled:
            await self._handle_solo_segment(session, segment, caption_channel)

    async def _handle_solo_segment(
        self,
        session: RecordingSession,
        segment: SpeechSegment,
        caption_channel: discord.TextChannel | None = None,
    ) -> None:
        if self._openai is None:
            return
        guild = self.bot.get_guild(session.guild_id)
        if guild is None or self.sessions.get(guild.id) is not session:
            return
        channel = guild.get_channel(session.voice_channel_id)
        if not isinstance(channel, discord.VoiceChannel):
            return
        humans = self._human_members(channel)
        if len(humans) < 1:
            return
        if segment.user_id not in {member.id for member in humans}:
            return
        if segment.byte_count < int(BYTES_PER_SECOND * 0.35):
            return

        # Transcribe before taking the reply lock so a spoken stop command
        # can interrupt Mommy even while an earlier reply is playing.
        try:
            user_text = await self._transcribe_for_solo(segment)
        except Exception:
            log.exception("Mommy.exe VC transcription failed in guild %s", guild.id)
            return
        if not user_text or len(user_text.strip()) < 2:
            return

        addressed = self._is_addressed_to_mommy(user_text)
        continuing = self._conversation_is_active(guild.id, segment.user_id)
        vc = guild.voice_client
        mommy_is_speaking = bool(vc is not None and vc.is_connected() and vc.is_playing())

        # Stop phrases are treated as immediate VC commands. Do not require the
        # speaker to say Mommy's name first; "shush" needs to work while she is
        # talking, generating, or waiting to speak.
        if self._is_stop_talking_request(user_text):
            self._silence_conversation(guild, segment.user_id)
            return

        # Once hushed, only a fresh explicit address wakes her back up. Normal
        # follow-up chatter must not reopen the old conversation window.
        if guild.id in self._vc_hushed_guilds:
            if not addressed:
                return
            self._vc_hushed_guilds.discard(guild.id)

        async with self._solo_reply_lock(guild.id):
            if self.sessions.get(guild.id) is not session:
                return
            channel = guild.get_channel(session.voice_channel_id)
            if not isinstance(channel, discord.VoiceChannel):
                return
            humans = self._human_members(channel)
            if len(humans) < 1:
                return

            addressed = self._is_addressed_to_mommy(user_text)
            continuing = self._conversation_is_active(guild.id, segment.user_id)
            if guild.id in self._vc_hushed_guilds:
                if not addressed:
                    return
                self._vc_hushed_guilds.discard(guild.id)
            if len(humans) > 1 and not addressed and not continuing:
                self._clear_expired_vc_context(guild.id, segment.user_id)
                return

            member = guild.get_member(segment.user_id)
            display_name = member.display_name if member else segment.display_name
            try:
                reply = await self._generate_solo_reply(
                    guild.id,
                    segment.user_id,
                    display_name,
                    user_text,
                    human_count=len(humans),
                )
                if not reply:
                    return

                # A hush may have arrived while the model was generating this
                # reply. Never let an already-generated response speak afterward.
                if guild.id in self._vc_hushed_guilds:
                    return

                # If the speaker resumed talking while the AI response was being
                # generated, do not cut them off.
                track = session.tracks.get(segment.user_id)
                if track is not None and track.active_segment is not None:
                    return

                spoken_text = await self._speak_solo_reply(guild, session, reply)
                if not spoken_text:
                    return

                self._touch_conversation(guild.id, segment.user_id)
                self._remember_vc_turn(guild.id, segment.user_id, user_text, spoken_text)
                if addressed and await self.bot.memory_enabled(guild.id, segment.user_id):
                    await self.bot._remember_facts(guild.id, segment.user_id, display_name, user_text)  # noqa: SLF001
                    await self.bot._save_memory(  # noqa: SLF001
                        guild_id=guild.id,
                        user_id=segment.user_id,
                        display_name=display_name,
                        human_text=user_text,
                        reply_text=spoken_text,
                    )
                if caption_channel is not None:
                    try:
                        await caption_channel.send(f"**Mommy.exe:** {spoken_text}")
                    except Exception:
                        log.exception("Could not post Mommy.exe reply caption in guild %s", guild.id)
            except Exception:
                log.exception("Mommy.exe VC AI reply failed in guild %s", guild.id)

    async def _start_auto_session(
        self,
        guild: discord.Guild,
        target: discord.VoiceChannel,
        started_by_id: int,
        *,
        behavior: dict | None = None,
        reason: str = "auto-channel",
        join_target_id: int | None = None,
    ) -> None:
        async with self._auto_lock(guild.id):
            if guild.id in self.sessions:
                return

            config = await self._recording_config(guild)
            defaults = self._join_behavior(config.get("default_join_behavior"))
            selected = self._join_behavior(behavior or defaults)
            output_id = config.get("output_channel_id")
            output = guild.get_channel(int(output_id)) if output_id else None
            if selected["record"] and not isinstance(output, discord.TextChannel):
                log.warning("Auto-join recording is enabled in guild %s but no valid archive/output channel is configured; joining without final archive.", guild.id)
                selected["record"] = False
            # Rolling clips are global for every active VC session. They are not
            # a per-followed-member behavior flag.
            if not isinstance(output, discord.TextChannel):
                log.warning("No archive/output channel is configured in guild %s; rolling clips cannot be posted until Recording Setup is completed.", guild.id)

            root = Path(os.getenv("RECORDING_TMP_DIR", "/tmp/mommy-exe-recordings"))
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            base_dir = root / str(guild.id) / f"{stamp}_{target.id}"
            base_dir.mkdir(parents=True, exist_ok=True)

            existing = guild.voice_client
            if existing is not None:
                try:
                    await existing.disconnect(force=True)
                except Exception:
                    log.exception("Could not disconnect an existing voice client before auto-join")

            try:
                vc = await target.connect(cls=voice_recv.VoiceRecvClient, self_deaf=False)
            except Exception:
                log.exception("Could not auto-connect to voice channel %s in guild %s", target.id, guild.id)
                shutil.rmtree(base_dir, ignore_errors=True)
                return

            session = RecordingSession(
                guild_id=guild.id,
                voice_channel_id=target.id,
                output_channel_id=output.id if isinstance(output, discord.TextChannel) else None,
                started_by_id=started_by_id,
                base_dir=base_dir,
                auto_started=True,
                join_reason=reason,
                join_target_id=join_target_id,
                automod_enabled=selected["automod"],
                ai_enabled=selected["ai"],
                record_enabled=selected["record"],
                clips_enabled=True,
                captions_enabled=selected["captions"],
                archive_on_disconnect=selected["record"],
            )
            session.loop = asyncio.get_running_loop()
            session.segment_ready_callback = self._handle_segment_ready
            session.packet_callback = self._arm_solo_silence_timer
            self.sessions[guild.id] = session
            self._start_segment_watchdog(session)
            sink = SessionSink(session)
            vc.listen(sink, after=lambda err: log.error("Voice receive stopped: %r", err) if err else None)
            await self._maybe_post_presence_message(guild, session, joined=True)


    @staticmethod
    def _join_behavior(value: dict | None) -> dict[str, bool]:
        raw = value if isinstance(value, dict) else {}
        return {
            "automod": bool(raw.get("automod", True)),
            "ai": bool(raw.get("ai", True)),
            "record": bool(raw.get("record", False)),
            "captions": bool(raw.get("captions", True)),
        }

    @staticmethod
    def _behavior_score(behavior: dict | None) -> int:
        clean = VoiceRecordingCog._join_behavior(behavior)
        return 1 if clean.get("record") else 0

    def _follow_targets(self, config: dict) -> list[dict]:
        targets = config.get("follow_targets") or []
        if targets:
            return [item for item in targets if isinstance(item, dict) and item.get("user_id")]
        default = self._join_behavior(config.get("default_join_behavior"))
        return [{"user_id": int(uid), "behavior": default} for uid in (config.get("follow_user_ids") or [])]

    def _auto_join_targets(self, config: dict) -> list[dict]:
        targets = config.get("auto_join_channels") or []
        if targets:
            return [item for item in targets if isinstance(item, dict) and item.get("channel_id")]
        legacy = config.get("auto_voice_channel_id")
        if legacy:
            return [{"channel_id": int(legacy), "behavior": self._join_behavior(config.get("default_join_behavior"))}]
        return []

    async def _best_auto_target(self, guild: discord.Guild, config: dict) -> tuple[discord.VoiceChannel, dict, str, int] | None:
        # Followed members always beat configured rooms. Within each category,
        # a record-enabled target wins over an automod-only target.
        followed: list[tuple[int, int, discord.VoiceChannel, dict, int]] = []
        for order, item in enumerate(self._follow_targets(config)):
            try:
                uid = int(item.get("user_id"))
            except (TypeError, ValueError):
                continue
            member = guild.get_member(uid)
            channel = member.voice.channel if member and member.voice else None
            if isinstance(channel, discord.VoiceChannel):
                behavior = self._join_behavior(item.get("behavior"))
                followed.append((-self._behavior_score(behavior), order, channel, behavior, uid))
        if followed:
            followed.sort(key=lambda row: (row[0], row[1]))
            _, _, channel, behavior, uid = followed[0]
            return channel, behavior, "follow-member", uid

        rooms: list[tuple[int, int, discord.VoiceChannel, dict, int]] = []
        for order, item in enumerate(self._auto_join_targets(config)):
            try:
                cid = int(item.get("channel_id"))
            except (TypeError, ValueError):
                continue
            channel = guild.get_channel(cid)
            if isinstance(channel, discord.VoiceChannel) and self._human_members(channel):
                behavior = self._join_behavior(item.get("behavior"))
                rooms.append((-self._behavior_score(behavior), order, channel, behavior, cid))
        if rooms:
            rooms.sort(key=lambda row: (row[0], row[1]))
            _, _, channel, behavior, cid = rooms[0]
            return channel, behavior, "auto-channel", cid
        return None

    async def _apply_session_behavior(self, session: RecordingSession, behavior: dict) -> None:
        clean = self._join_behavior(behavior)
        session.automod_enabled = clean["automod"]
        session.ai_enabled = clean["ai"]
        session.record_enabled = clean["record"]
        session.archive_on_disconnect = session.archive_on_disconnect or clean["record"]
        # Clipping is global for everyone in any active Mommy VC session.
        session.clips_enabled = True
        session.captions_enabled = clean["captions"]

    async def _reconcile_auto_join(self, guild: discord.Guild) -> None:
        config = await self._recording_config(guild)
        best = await self._best_auto_target(guild, config)
        session = self.sessions.get(guild.id)

        # Never hijack a manually started VC session.
        if session is not None and not session.auto_started:
            return

        if best is None:
            if session is not None and session.auto_started:
                await self._stop_auto_session(guild, session)
            return

        target, behavior, reason, target_id = best
        if session is None:
            await self._start_auto_session(
                guild, target, target_id, behavior=behavior, reason=reason, join_target_id=target_id
            )
            return

        if session.voice_channel_id == target.id:
            session.join_reason = reason
            session.join_target_id = target_id
            await self._apply_session_behavior(session, behavior)
            return

        vc = guild.voice_client
        if isinstance(vc, voice_recv.VoiceRecvClient) and vc.is_connected():
            try:
                await vc.move_to(target)
                session.voice_channel_id = target.id
                session.join_reason = reason
                session.join_target_id = target_id
                await self._apply_session_behavior(session, behavior)
                return
            except Exception:
                log.exception("Could not move Mommy.exe to preferred auto-join target %s", target.id)

        # Recover a stale voice connection/session.
        self.sessions.pop(guild.id, None)
        try:
            session.close_audio_files()
        except Exception:
            pass
        await self._start_auto_session(
            guild, target, target_id, behavior=behavior, reason=reason, join_target_id=target_id
        )

    async def _stop_auto_session(self, guild: discord.Guild, session: RecordingSession) -> None:
        async with self._auto_lock(guild.id):
            current = self.sessions.get(guild.id)
            if current is not session or not session.auto_started:
                return

            self.sessions.pop(guild.id, None)
            self._solo_history.pop(guild.id, None)
            self._vc_hushed_guilds.discard(guild.id)
            watchdog = self._segment_watchdogs.pop(guild.id, None)
            if watchdog is not None:
                watchdog.cancel()
            should_archive = bool(session.record_enabled or session.archive_on_disconnect)
            for key, task in list(self._solo_silence_tasks.items()):
                if key[0] == guild.id:
                    task.cancel()
                    self._solo_silence_tasks.pop(key, None)
            vc = guild.voice_client
            try:
                if isinstance(vc, voice_recv.VoiceRecvClient) and vc.is_listening():
                    vc.stop_listening()
                session.close_audio_files()
                if vc is not None:
                    await vc.disconnect(force=True)
            except Exception:
                log.exception("Error while disconnecting after automatic recording")
                session.close_audio_files()

            output = guild.get_channel(int(session.output_channel_id)) if session.output_channel_id else None

            await self._maybe_post_presence_message(guild, session, joined=False)
            try:
                if should_archive and isinstance(output, discord.TextChannel):
                    await self._finish_and_post(guild, output, session)
                elif should_archive:
                    log.warning("Could not post automatic recording for guild %s because the output channel is missing.", guild.id)
            except Exception:
                log.exception("Failed to finalize automatic voice recording")
                if isinstance(output, discord.TextChannel):
                    try:
                        await output.send("⚠️ An automatic VC recording ended, but final processing failed. Check the bot logs.")
                    except Exception:
                        pass
            finally:
                shutil.rmtree(session.base_dir, ignore_errors=True)

    async def dashboard_set_active_flag(self, guild: discord.Guild, flag: str, enabled: bool) -> str:
        session = self.sessions.get(guild.id)
        if session is None:
            return "Mommy isn't in a VC right now."
        mapping = {
            "automod": "automod_enabled",
            "ai": "ai_enabled",
            "record": "record_enabled",
            "captions": "captions_enabled",
        }
        attr = mapping.get(flag)
        if attr is None:
            return "Unknown VC setting."
        setattr(session, attr, bool(enabled))
        label = {"automod":"VC Automod","ai":"AI replies","record":"Full recording/archive","captions":"Live captions"}[flag]
        return f"{label} is now **{'ON' if enabled else 'OFF'}** for the current VC session."

    def active_session_status(self, guild: discord.Guild) -> str:
        session = self.sessions.get(guild.id)
        if session is None:
            return "Mommy isn't currently connected through the recording/listening system."
        reason = session.join_reason.replace('-', ' ')
        return (
            f"**VC:** <#{session.voice_channel_id}>\n"
            f"**Joined because:** {reason}\n"
            f"**Automod:** {'ON' if session.automod_enabled else 'OFF'}\n"
            f"**AI replies:** {'ON' if session.ai_enabled else 'OFF'}\n"
            f"**Full recording:** {'ON' if session.record_enabled else 'OFF'}\n"
            f"**Rolling clips:** {'ON' if session.clips_enabled else 'OFF'}\n"
            f"**Live captions:** {'ON' if session.captions_enabled else 'OFF'}"
        )

    async def dashboard_start(self, guild: discord.Guild, user: discord.Member | discord.User, target: discord.VoiceChannel) -> str:
        """Button-dashboard equivalent of /record start."""
        if guild.id in self.sessions:
            session = self.sessions[guild.id]
            return f"Mommy is already listening in <#{session.voice_channel_id}>."
        config = await self._recording_config(guild)
        output_id = config.get("output_channel_id")
        if not output_id:
            return "Use **Voice → Recording Setup** first so I know where recordings and clips should go."
        output = guild.get_channel(int(output_id))
        if not isinstance(output, discord.TextChannel):
            return "The configured recording archive/output channel is missing. Run Recording Setup again."
        await self._start_auto_session(guild, target, user.id)
        session = self.sessions.get(guild.id)
        if session is None:
            return f"I couldn't join {target.mention}. Check my VC permissions and the Render logs."
        # Dashboard/manual sessions should not be auto-stopped by empty-room logic and record by default.
        session.auto_started = False
        session.join_reason = "manual"
        session.automod_enabled = True
        session.ai_enabled = True
        session.record_enabled = True
        session.clips_enabled = True
        session.captions_enabled = True
        return f"🔴 I'm listening/recording in **{target.name}**. Use the dashboard to clip the last 3 minutes or stop/post the full session."

    async def dashboard_stop(self, guild: discord.Guild, user: discord.Member | discord.User) -> str:
        """Stop an active session and post the normal finished recording/transcript."""
        session = self.sessions.pop(guild.id, None)
        if session is None:
            return "There isn't an active VC session to stop."
        watchdog = self._segment_watchdogs.pop(guild.id, None)
        if watchdog is not None:
            watchdog.cancel()
        self._solo_history.pop(guild.id, None)
        for key, task in list(self._solo_silence_tasks.items()):
            if key[0] == guild.id:
                task.cancel()
                self._solo_silence_tasks.pop(key, None)
        vc = guild.voice_client
        try:
            if isinstance(vc, voice_recv.VoiceRecvClient) and vc.is_listening():
                vc.stop_listening()
            session.close_audio_files()
            if vc is not None:
                await vc.disconnect(force=True)
        except Exception:
            log.exception("Error while dashboard-stopping recording")
            session.close_audio_files()

        output = guild.get_channel(session.output_channel_id)
        if not session.record_enabled:
            shutil.rmtree(session.base_dir, ignore_errors=True)
            return "⏹️ Stopped the VC session. Full recording/archive was OFF, so nothing was posted."
        if not isinstance(output, discord.TextChannel):
            shutil.rmtree(session.base_dir, ignore_errors=True)
            return "Stopped the session, but the configured archive/output channel is missing."
        try:
            await self._finish_and_post(guild, output, session)
        except Exception as exc:
            log.exception("Dashboard finalization failed")
            try:
                await output.send(f"⚠️ Recording ended, but final processing failed: `{type(exc).__name__}: {exc}`")
            except Exception:
                pass
            return f"Stopped the session, but processing failed: {type(exc).__name__}."
        finally:
            shutil.rmtree(session.base_dir, ignore_errors=True)
        return f"⏹️ Stopped and posted the finished recording/transcript to {output.mention}."

    async def _build_recent_clip_transcript(
        self,
        guild: discord.Guild,
        session: RecordingSession,
        *,
        seconds: int = 180,
    ) -> Path:
        """Create a speaker-labeled transcript for the same rolling window used by a clip."""
        # Flush any speech currently sitting in an open segment so the clip transcript
        # includes what was just said immediately before the staff member pressed Clip.
        with session.lock:
            for track in session.tracks.values():
                if track.active_segment is not None:
                    session._close_segment(track)

        clip_end = session.duration
        clip_start = max(0.0, clip_end - float(seconds))

        with session.lock:
            recent_segments = [
                seg
                for seg in session.segments
                if (seg.start_offset + (seg.byte_count / BYTES_PER_SECOND)) >= clip_start
                and seg.start_offset <= clip_end
            ]

        transcript_rows: list[tuple[float, str, str]] = []

        if self._openai is not None and recent_segments:
            sem = asyncio.Semaphore(int(os.getenv("TRANSCRIPTION_CONCURRENCY", "3")))
            results = await asyncio.gather(
                *(self._transcribe_segment(seg, sem) for seg in recent_segments),
                return_exceptions=True,
            )
            for item in results:
                if isinstance(item, Exception):
                    log.warning("A clip speech segment failed transcription", exc_info=item)
                    continue
                segment, transcript = item
                if transcript:
                    relative_offset = max(0.0, segment.start_offset - clip_start)
                    transcript_rows.append((relative_offset, segment.display_name, transcript))

        transcript_rows.sort(key=lambda row: row[0])

        stamp = int(time.time() * 1000)
        transcript_path = session.base_dir / f"clip_transcript_{stamp}.txt"
        lines = [
            "Discord VC clip transcript",
            f"Server: {guild.name}",
            f"Voice channel ID: {session.voice_channel_id}",
            f"Clip duration: {_clock(min(float(seconds), clip_end))}",
            "",
        ]

        if self._openai is None:
            lines.append("Transcription unavailable: OPENAI_API_KEY is not configured.")
        elif not transcript_rows:
            lines.append("No transcribable speech was detected in this clip.")
        else:
            for offset, speaker, transcript in transcript_rows:
                lines.append(f"[{_clock(offset)}] {speaker}: {transcript}")

        transcript_path.write_text("\n".join(lines), encoding="utf-8")
        return transcript_path

    async def dashboard_clip(self, guild: discord.Guild, user: discord.Member | discord.User) -> str:
        """Save the previous 180 seconds from the active aligned session."""
        session = self.sessions.get(guild.id)
        if not session or not session.tracks:
            return "I don't have an active voice buffer to clip right now."
        try:
            clip_wav = await asyncio.to_thread(_mix_recent_clip, session, 180)
            clip_audio = await asyncio.to_thread(_compress_master, clip_wav)
            config = await self._recording_config(guild)
            output = guild.get_channel(int(config.get("output_channel_id") or session.output_channel_id))
            if not isinstance(output, discord.TextChannel):
                return "The configured clip output channel is missing."
            duration = min(180.0, session.duration)
            transcript_path = await self._build_recent_clip_transcript(guild, session, seconds=180)
            try:
                await output.send(
                    f"✂️ **Last {_clock(duration)} clipped from <#{session.voice_channel_id}>** by <@{user.id}>\n"
                    "📝 Transcript attached with speaker labels and clip-relative timestamps.",
                    files=[
                        discord.File(
                            clip_audio,
                            filename=f"mommy_clip_{int(time.time())}.mp3"
                            if clip_audio.suffix.lower() == ".mp3"
                            else "mommy_clip.wav",
                        ),
                        discord.File(transcript_path, filename="clip_transcript.txt"),
                    ],
                )
            finally:
                transcript_path.unlink(missing_ok=True)
            return f"Saved and transcribed the previous {_clock(duration)} to {output.mention}."
        except Exception as exc:
            log.exception("Dashboard clip failed")
            return f"I couldn't make that clip: {type(exc).__name__}: {exc}"

    async def follow_member_now(self, guild: discord.Guild, member: discord.Member) -> str:
        """Reconcile auto-join immediately after a followed-member change."""
        voice_state = member.voice
        target = voice_state.channel if voice_state else None
        if not isinstance(target, discord.VoiceChannel):
            return f"Following **{member.display_name}**. I'll join when they enter a voice channel."
        await self._reconcile_auto_join(guild)
        session = self.sessions.get(guild.id)
        if session and session.voice_channel_id == target.id:
            if session.automod_enabled and session.record_enabled:
                mode = "Automod + Record"
            elif session.record_enabled:
                mode = "Record Only"
            elif session.automod_enabled:
                mode = "Automod Only"
            else:
                mode = "Follow Only"
            return f"Following **{member.display_name}** — I joined **{target.name}** ({mode}; clipping available for everyone)."
        return f"Following **{member.display_name}**, but I couldn't switch to **{target.name}** right now."

    async def _reconcile_followed_members(self) -> None:
        """Restore configured followed-member / auto-channel behavior after restart."""
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            try:
                await self._reconcile_auto_join(guild)
            except Exception:
                log.exception("Could not reconcile voice auto-join in guild %s", guild.id)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if getattr(self, "_follow_reconcile_started", False):
            return
        self._follow_reconcile_started = True
        asyncio.create_task(self._reconcile_followed_members())

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        # If Mommy is disconnected externally (manual Discord disconnect,
        # connection drop, etc.), finalize an active followed/auto recording
        # instead of silently abandoning its audio and transcript.
        if self.bot.user is not None and member.id == self.bot.user.id:
            if before.channel is not None and after.channel is None:
                session = self.sessions.get(member.guild.id)
                if session is not None and session.auto_started:
                    try:
                        await self._stop_auto_session(member.guild, session)
                    except Exception:
                        log.exception(
                            "Could not finalize auto session after Mommy was disconnected in guild %s",
                            member.guild.id,
                        )
            return

        if member.bot or before.channel == after.channel:
            return
        await asyncio.sleep(0.6)
        try:
            await self._reconcile_auto_join(member.guild)
        except Exception:
            log.exception("Auto-join reconciliation failed after voice-state update in guild %s", member.guild.id)

    @record.command(name="auto", description="Automatically record when someone joins a chosen voice channel.")
    @app_commands.guild_only()
    async def auto_recording(
        self,
        interaction: discord.Interaction,
        voice_channel: discord.VoiceChannel,
    ) -> None:
        if interaction.guild is None:
            return
        decision = await evaluate_access(interaction, self.bot.database, minimum="owner", enforce_channel=False)
        if not decision.allowed:
            await deny_access(interaction, decision.reason)
            return
        config = await self._recording_config(interaction.guild)
        if not config.get("output_channel_id"):
            await interaction.response.send_message("Run `/record setup` first so I know where to post recordings.", ephemeral=True)
            return
        await self.bot.database.set_voice_recording_auto_channel(interaction.guild.id, voice_channel.id)
        await interaction.response.send_message(
            f"Automatic recording is now enabled for {voice_channel.mention}. I’ll join when the first non-bot user enters and stop when the room is empty.",
            ephemeral=True,
        )

    @record.command(name="autooff", description="Turn off automatic voice-channel recording.")
    @app_commands.guild_only()
    async def auto_recording_off(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        decision = await evaluate_access(interaction, self.bot.database, minimum="owner", enforce_channel=False)
        if not decision.allowed:
            await deny_access(interaction, decision.reason)
            return
        await self.bot.database.set_voice_recording_auto_channel(interaction.guild.id, None)
        await interaction.response.send_message("Automatic VC recording is disabled. Any recording already running will continue until stopped.", ephemeral=True)

    @record.command(name="followadd", description="Have Mommy.exe automatically follow a member into voice chat.")
    @app_commands.guild_only()
    async def follow_add(self, interaction: discord.Interaction, member: discord.Member) -> None:
        if interaction.guild is None:
            return
        decision = await evaluate_access(interaction, self.bot.database, minimum="owner", enforce_channel=False)
        if not decision.allowed:
            await deny_access(interaction, decision.reason)
            return
        config = await self._recording_config(interaction.guild)
        targets = list(config.get("follow_targets") or [])
        if not targets:
            default = self._join_behavior(config.get("default_join_behavior"))
            targets = [{"user_id": int(v), "behavior": default} for v in (config.get("follow_user_ids") or [])]
        if member.id not in {int(item.get("user_id", 0)) for item in targets}:
            targets.append({"user_id": member.id, "behavior": self._join_behavior(config.get("default_join_behavior"))})
            await self.bot.database.set_voice_recording_follow_targets(interaction.guild.id, targets)
        result = await self.follow_member_now(interaction.guild, member)
        await interaction.response.send_message(result, ephemeral=True)

    @record.command(name="followremove", description="Stop automatically following a member into voice chat.")
    @app_commands.guild_only()
    async def follow_remove(self, interaction: discord.Interaction, member: discord.Member) -> None:
        if interaction.guild is None:
            return
        decision = await evaluate_access(interaction, self.bot.database, minimum="owner", enforce_channel=False)
        if not decision.allowed:
            await deny_access(interaction, decision.reason)
            return
        config = await self._recording_config(interaction.guild)
        targets = list(config.get("follow_targets") or [])
        if not targets:
            default = self._join_behavior(config.get("default_join_behavior"))
            targets = [{"user_id": int(v), "behavior": default} for v in (config.get("follow_user_ids") or [])]
        targets = [item for item in targets if int(item.get("user_id", 0)) != member.id]
        await self.bot.database.set_voice_recording_follow_targets(interaction.guild.id, targets)
        await self._reconcile_auto_join(interaction.guild)
        await interaction.response.send_message(f"I won’t automatically follow **{member.display_name}** anymore.", ephemeral=True)

    @record.command(name="followlist", description="Show members Mommy.exe is configured to follow into voice chat.")
    @app_commands.guild_only()
    async def follow_list(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        decision = await evaluate_access(interaction, self.bot.database, minimum="owner", enforce_channel=False)
        if not decision.allowed:
            await deny_access(interaction, decision.reason)
            return
        config = await self._recording_config(interaction.guild)
        ids = [int(v) for v in (config.get("follow_user_ids") or [])]
        if not ids:
            await interaction.response.send_message("I’m not following anyone automatically yet.", ephemeral=True)
            return
        names = []
        for user_id in ids:
            member = interaction.guild.get_member(user_id)
            names.append(f"• {member.mention if member else f'<@{user_id}>'}")
        await interaction.response.send_message("**Auto-follow members:**\n" + "\n".join(names), ephemeral=True)

    @record.command(name="autostatus", description="Show the voice channel configured for automatic recording.")
    @app_commands.guild_only()
    async def auto_recording_status(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        decision = await evaluate_access(interaction, self.bot.database, minimum="owner", enforce_channel=False)
        if not decision.allowed:
            await deny_access(interaction, decision.reason)
            return
        config = await self._recording_config(interaction.guild)
        target_id = config.get("auto_voice_channel_id")
        if not target_id:
            await interaction.response.send_message("Automatic VC recording is not configured.", ephemeral=True)
            return
        await interaction.response.send_message(f"Automatic VC recording target: <#{int(target_id)}>", ephemeral=True)

    @record.command(name="captions", description="Route one voice channel's live captions to a text channel.")
    @app_commands.guild_only()
    async def captions_channel(
        self,
        interaction: discord.Interaction,
        voice_channel: discord.VoiceChannel,
        channel: discord.TextChannel,
    ) -> None:
        if interaction.guild is None:
            return
        decision = await evaluate_access(interaction, self.bot.database, minimum="owner", enforce_channel=False)
        if not decision.allowed:
            await deny_access(interaction, decision.reason)
            return
        if self._openai is None:
            await interaction.response.send_message(
                "Live captions need `OPENAI_API_KEY` configured on the bot first.",
                ephemeral=True,
            )
            return
        await self.bot.database.set_voice_recording_caption_route(
            interaction.guild.id, voice_channel.id, channel.id
        )
        await interaction.response.send_message(
            f"Live captions from {voice_channel.mention} will post in {channel.mention}.",
            ephemeral=True,
        )

    @record.command(name="captionsoff", description="Turn off live captions for one voice channel.")
    @app_commands.guild_only()
    async def captions_off(
        self,
        interaction: discord.Interaction,
        voice_channel: discord.VoiceChannel,
    ) -> None:
        if interaction.guild is None:
            return
        decision = await evaluate_access(interaction, self.bot.database, minimum="owner", enforce_channel=False)
        if not decision.allowed:
            await deny_access(interaction, decision.reason)
            return
        await self.bot.database.set_voice_recording_caption_route(
            interaction.guild.id, voice_channel.id, None
        )
        await interaction.response.send_message(
            f"Live captions are disabled for {voice_channel.mention}.", ephemeral=True
        )

    @record.command(name="setup", description="Choose the private control and archive channels.")
    @app_commands.guild_only()
    async def setup_recording(
        self,
        interaction: discord.Interaction,
        control_channel: discord.TextChannel,
        output_channel: discord.TextChannel,
    ) -> None:
        if interaction.guild is None:
            return
        decision = await evaluate_access(interaction, self.bot.database, minimum="owner", enforce_channel=False)
        if not decision.allowed:
            await deny_access(interaction, decision.reason)
            return
        await self.bot.database.set_voice_recording_config(
            interaction.guild.id,
            control_channel_id=control_channel.id,
            output_channel_id=output_channel.id,
        )
        await interaction.response.send_message(
            f"Recording controls: {control_channel.mention}\nRecording archive: {output_channel.mention}",
            ephemeral=True,
        )

    @record.command(name="start", description="Start a full recording of a voice channel.")
    @app_commands.guild_only()
    async def start_recording(
        self,
        interaction: discord.Interaction,
        voice_channel: discord.VoiceChannel | None = None,
    ) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return

        # Acknowledge Discord immediately so DB/voice work cannot expire the slash interaction.
        await interaction.response.defer(ephemeral=True, thinking=True)

        decision = await evaluate_access(interaction, self.bot.database, minimum="owner", enforce_channel=False)
        if not decision.allowed:
            await interaction.followup.send(decision.reason or "You don't have permission to use that command.", ephemeral=True)
            return

        config = await self._recording_config(interaction.guild)
        control_id = config.get("control_channel_id")
        if control_id and interaction.channel_id != int(control_id):
            await interaction.followup.send(
                f"Recording controls can only be used in <#{int(control_id)}>.", ephemeral=True
            )
            return

        if interaction.guild.id in self.sessions:
            await interaction.followup.send("A recording is already running in this server.", ephemeral=True)
            return

        output_id = config.get("output_channel_id")
        if not output_id:
            await interaction.followup.send("Run `/record setup` first so I know where to post recordings.", ephemeral=True)
            return

        target = voice_channel
        if target is None and interaction.user.voice and isinstance(interaction.user.voice.channel, discord.VoiceChannel):
            target = interaction.user.voice.channel
        if target is None:
            await interaction.followup.send("Join a voice channel first or choose one in the command.", ephemeral=True)
            return

        output = interaction.guild.get_channel(int(output_id))
        if not isinstance(output, discord.TextChannel):
            await interaction.followup.send("The configured recording archive channel no longer exists.", ephemeral=True)
            return

        root = Path(os.getenv("RECORDING_TMP_DIR", "/tmp/mommy-exe-recordings"))
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        base_dir = root / str(interaction.guild.id) / f"{stamp}_{target.id}"
        base_dir.mkdir(parents=True, exist_ok=True)

        existing = interaction.guild.voice_client
        if existing is not None:
            await existing.disconnect(force=True)

        try:
            vc = await target.connect(cls=voice_recv.VoiceRecvClient, self_deaf=False)
        except Exception as exc:
            log.exception("Could not connect to voice channel")
            await interaction.followup.send(f"I couldn't join {target.mention}: `{exc}`", ephemeral=True)
            return

        session = RecordingSession(
            guild_id=interaction.guild.id,
            voice_channel_id=target.id,
            output_channel_id=output.id,
            started_by_id=interaction.user.id,
            base_dir=base_dir,
        )
        self.sessions[interaction.guild.id] = session
        session.loop = asyncio.get_running_loop()
        session.segment_ready_callback = self._handle_segment_ready
        session.packet_callback = self._arm_solo_silence_timer
        self._start_segment_watchdog(session)
        sink = SessionSink(session)
        vc.listen(sink, after=lambda err: log.error("Voice receive stopped: %r", err) if err else None)

        await interaction.followup.send(
            f"🔴 Recording **{target.name}**. Use `/record stop` here when you're done.\nFinished audio and transcript will go to {output.mention}.",
            ephemeral=True,
        )

    @record.command(name="clip", description="Save the previous three minutes of the active VC session.")
    @app_commands.guild_only()
    async def clip_recent(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        decision = await evaluate_access(interaction, self.bot.database, minimum="owner", enforce_channel=False)
        if not decision.allowed:
            await interaction.followup.send(decision.reason or "You don't have permission to clip voice chat.", ephemeral=True)
            return
        session = self.sessions.get(interaction.guild.id)
        if not session or not session.tracks:
            await interaction.followup.send("I don't have an active voice buffer to clip right now.", ephemeral=True)
            return
        try:
            clip_wav = await asyncio.to_thread(_mix_recent_clip, session, 180)
            clip_audio = await asyncio.to_thread(_compress_master, clip_wav)
            config = await self._recording_config(interaction.guild)
            output = interaction.guild.get_channel(int(config.get("output_channel_id") or session.output_channel_id))
            if not isinstance(output, discord.TextChannel):
                await interaction.followup.send("The configured clip output channel is missing.", ephemeral=True)
                return
            duration = min(180.0, session.duration)
            transcript_path = await self._build_recent_clip_transcript(interaction.guild, session, seconds=180)
            try:
                await output.send(
                    f"✂️ **Last {_clock(duration)} clipped from <#{session.voice_channel_id}>** by {interaction.user.mention}\n"
                    "📝 Transcript attached with speaker labels and clip-relative timestamps.",
                    files=[
                        discord.File(
                            clip_audio,
                            filename=f"mommy_clip_{int(time.time())}.mp3"
                            if clip_audio.suffix.lower() == ".mp3"
                            else "mommy_clip.wav",
                        ),
                        discord.File(transcript_path, filename="clip_transcript.txt"),
                    ],
                )
            finally:
                transcript_path.unlink(missing_ok=True)
            await interaction.followup.send(
                f"Saved and transcribed the last {_clock(duration)} to {output.mention}.",
                ephemeral=True,
            )
        except Exception as exc:
            log.exception("Could not create rolling VC clip")
            await interaction.followup.send(f"I couldn't make that clip: `{type(exc).__name__}: {exc}`", ephemeral=True)

    @record.command(name="status", description="Show whether a voice recording is active.")
    @app_commands.guild_only()
    async def status(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        decision = await evaluate_access(interaction, self.bot.database, minimum="owner", enforce_channel=False)
        if not decision.allowed:
            await deny_access(interaction, decision.reason)
            return
        session = self.sessions.get(interaction.guild.id)
        if not session:
            await interaction.response.send_message("No recording is currently running.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"🔴 Recording <#{session.voice_channel_id}> — {_clock(session.duration)} elapsed — {len(session.tracks)} speaker(s) captured.",
            ephemeral=True,
        )

    @record.command(name="stop", description="Stop recording and post the audio + transcript.")
    @app_commands.guild_only()
    async def stop_recording(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return

        # Acknowledge immediately; stopping/DB checks may take longer than Discord's interaction window.
        await interaction.response.defer(ephemeral=True, thinking=True)

        decision = await evaluate_access(interaction, self.bot.database, minimum="owner", enforce_channel=False)
        if not decision.allowed:
            await interaction.followup.send(decision.reason or "You don't have permission to use that command.", ephemeral=True)
            return

        config = await self._recording_config(interaction.guild)
        control_id = config.get("control_channel_id")
        if control_id and interaction.channel_id != int(control_id):
            await interaction.followup.send(
                f"Recording controls can only be used in <#{int(control_id)}>.", ephemeral=True
            )
            return

        session = self.sessions.get(interaction.guild.id)
        if not session:
            await interaction.followup.send("There isn't an active recording to stop.", ephemeral=True)
            return

        self.sessions.pop(interaction.guild.id, None)
        watchdog = self._segment_watchdogs.pop(interaction.guild.id, None)
        if watchdog is not None:
            watchdog.cancel()
        vc = interaction.guild.voice_client
        try:
            if isinstance(vc, voice_recv.VoiceRecvClient) and vc.is_listening():
                vc.stop_listening()
            session.close_audio_files()
            if vc is not None:
                await vc.disconnect(force=True)
        except Exception:
            log.exception("Error while disconnecting after recording")
            session.close_audio_files()

        output = interaction.guild.get_channel(session.output_channel_id)
        if not isinstance(output, discord.TextChannel):
            await interaction.followup.send("Recording stopped, but the configured archive channel is missing.", ephemeral=True)
            return

        await interaction.followup.send(
            f"⏹️ Recording stopped at {_clock(session.duration)}. I'm processing the recording and transcript now.",
            ephemeral=True,
        )

        try:
            await self._finish_and_post(interaction.guild, output, session)
        except Exception as exc:
            log.exception("Failed to finalize voice recording")
            await output.send(
                f"⚠️ The recording ended, but final processing failed: `{type(exc).__name__}: {exc}`"
            )
        finally:
            shutil.rmtree(session.base_dir, ignore_errors=True)

    async def _transcribe_segment(self, segment: SpeechSegment, semaphore: asyncio.Semaphore) -> tuple[SpeechSegment, str]:
        if segment.transcript is not None:
            return segment, segment.transcript
        if self._openai is None:
            return segment, ""
        async with semaphore:
            wav_path = await asyncio.to_thread(_segment_to_wav, segment)
            try:
                with wav_path.open("rb") as audio:
                    result = await self._openai.audio.transcriptions.create(
                        model=os.getenv("TRANSCRIPTION_MODEL", "gpt-4o-mini-transcribe"),
                        file=audio,
                        response_format="json",
                        # This server is English-only. Force STT to stay in English instead
                        # of auto-detecting short/slang-heavy clips as Korean/Japanese/etc.
                        language="en",
                        prompt=(
                            "English-only casual Discord voice chat. Transcribe exactly what is spoken in English. "
                            "Preserve profanity, slang, usernames, racial slurs, and offensive words verbatim when spoken. "
                            "Do not translate speech and do not output Korean, Japanese, Chinese, or other non-English scripts. "
                            "For unclear short words, prefer the most plausible English word from the surrounding audio/context."
                        ),
                    )
                text = _clean_english_transcript(getattr(result, "text", "") or "")
                if not text and getattr(result, "text", ""):
                    with wav_path.open("rb") as audio:
                        retry = await self._openai.audio.transcriptions.create(
                            model=os.getenv("TRANSCRIPTION_MODEL", "gpt-4o-mini-transcribe"),
                            file=audio,
                            response_format="json",
                            language="en",
                            prompt=(
                                "The speakers are American English speakers in a Discord voice channel. "
                                "Return ONLY an English transcription using Latin letters. Never translate. "
                                "Preserve slang, profanity, usernames, racial slurs, and offensive words exactly as spoken. "
                                "If audio is unclear, choose the most plausible English wording; never output CJK, Hangul, hiragana, or katakana."
                            ),
                        )
                    text = _clean_english_transcript(getattr(retry, "text", "") or "")
                segment.transcript = text
                return segment, segment.transcript
            finally:
                wav_path.unlink(missing_ok=True)

    async def _finish_and_post(self, guild: discord.Guild, output: discord.TextChannel, session: RecordingSession) -> None:
        if not session.tracks:
            await output.send(
                f"🎙️ **VC recording finished** — <#{session.voice_channel_id}>\n"
                f"Duration: `{_clock(session.duration)}`\nNo user audio was captured."
            )
            return

        master_wav = await asyncio.to_thread(_mix_tracks, session)
        audio_file = await asyncio.to_thread(_compress_master, master_wav)

        transcript_rows: list[tuple[float, int, str, str]] = []
        if self._openai is not None and session.segments:
            sem = asyncio.Semaphore(int(os.getenv("TRANSCRIPTION_CONCURRENCY", "3")))
            results = await asyncio.gather(
                *(self._transcribe_segment(seg, sem) for seg in session.segments),
                return_exceptions=True,
            )
            for item in results:
                if isinstance(item, Exception):
                    log.warning("A speech segment failed transcription", exc_info=item)
                    continue
                segment, text = item
                if text:
                    transcript_rows.append((segment.start_offset, segment.user_id, segment.display_name, text))
            transcript_rows.sort(key=lambda row: row[0])

        transcript_path = session.base_dir / "transcript.txt"
        started = session.started_at.astimezone().strftime("%Y-%m-%d %I:%M:%S %p %Z")
        lines = [
            f"Discord VC transcript",
            f"Server: {guild.name}",
            f"Voice channel ID: {session.voice_channel_id}",
            f"Started: {started}",
            f"Duration: {_clock(session.duration)}",
            "",
        ]
        if self._openai is None:
            lines.append("Transcription unavailable: OPENAI_API_KEY is not configured.")
        elif not transcript_rows:
            lines.append("No transcribable speech was detected.")
        else:
            for offset, _user_id, speaker, text in transcript_rows:
                lines.append(f"[{_clock(offset)}] {speaker}: {text}")
        transcript_path.write_text("\n".join(lines), encoding="utf-8")

        header = (
            f"🎙️ **VC recording finished** — <#{session.voice_channel_id}>\n"
            f"Duration: `{_clock(session.duration)}` • Speakers captured: `{len(session.tracks)}`\n"
            + ("Transcript generated with speaker labels." if self._openai else "Audio saved; transcription is disabled until `OPENAI_API_KEY` is configured.")
        )
        transcript_message = await output.send(header, file=discord.File(transcript_path, filename="transcript.txt"))

        # Leave headroom for multipart/form-data overhead.
        max_bytes = max(1_000_000, int(guild.filesize_limit * 0.92))
        parts = await asyncio.to_thread(_split_audio_for_discord, audio_file, max_bytes)
        if not parts:
            await output.send(
                "⚠️ The master recording is larger than this server's Discord upload limit, and I couldn't split it. "
                "The transcript above was still saved."
            )
            return
        audio_message_ids: list[int] = []
        for index, part in enumerate(parts, start=1):
            label = "Full recording" if len(parts) == 1 else f"Recording part {index}/{len(parts)}"
            msg = await output.send(label, file=discord.File(part, filename=part.name))
            audio_message_ids.append(msg.id)

        staff_cog = self.bot.get_cog("StaffToolsCog")
        if staff_cog is not None and hasattr(staff_cog, "archive_session"):
            try:
                session_id = await staff_cog.archive_session(
                    guild,
                    session,
                    transcript_rows,
                    transcript_message_id=transcript_message.id,
                    audio_message_ids=audio_message_ids,
                )
                if hasattr(staff_cog, "attach_incidents_to_session"):
                    await staff_cog.attach_incidents_to_session(guild.id, session, session_id)
                if hasattr(staff_cog, "materialize_incidents"):
                    await staff_cog.materialize_incidents(guild, output, session, session_id, master_wav)
            except Exception:
                log.exception("Could not index recording session for staff search")


async def setup(bot: MommyExeBot) -> None:
    await bot.add_cog(VoiceRecordingCog(bot))
