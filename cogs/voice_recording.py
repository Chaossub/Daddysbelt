from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import threading
import time
import wave
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, BinaryIO, Callable

import discord
from discord import app_commands
from discord.ext import commands, voice_recv
from openai import AsyncOpenAI

from core.bot import DaddysBeltBot
from services.permissions import deny_access, evaluate_access

log = logging.getLogger("daddys-belt.voice-recording")

SAMPLE_RATE = 48_000
CHANNELS = 2
SAMPLE_WIDTH = 2
FRAME_BYTES = CHANNELS * SAMPLE_WIDTH
BYTES_PER_SECOND = SAMPLE_RATE * FRAME_BYTES
SEGMENT_GAP_SECONDS = 1.15
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
    output_channel_id: int
    started_by_id: int
    base_dir: Path
    auto_started: bool = False
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


def _ffmpeg_executable() -> str | None:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg")


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
    record = app_commands.Group(name="record", description="Record and transcribe voice calls.")

    def __init__(self, bot: DaddysBeltBot) -> None:
        self.bot = bot
        self.sessions: dict[int, RecordingSession] = {}
        self._auto_locks: dict[int, asyncio.Lock] = {}
        self._solo_silence_tasks: dict[tuple[int, int], asyncio.Task] = {}
        self._segment_watchdogs: dict[int, asyncio.Task] = {}
        self._solo_reply_locks: dict[int, asyncio.Lock] = {}
        self._solo_history: dict[int, list[tuple[str, str]]] = {}
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
            await asyncio.sleep(float(os.getenv("SOLO_AI_SILENCE_SECONDS", "1.5")))
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
        silence_seconds = float(os.getenv("LIVE_CAPTION_SILENCE_SECONDS", "1.5"))
        try:
            while self.sessions.get(session.guild_id) is session and not session.closed:
                await asyncio.sleep(0.35)
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
                    language=os.getenv("TRANSCRIPTION_LANGUAGE", "en"),
                )
            text = (getattr(result, "text", "") or "").strip()

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

    async def _generate_solo_reply(self, guild_id: int, user_text: str) -> str:
        if self._openai is None:
            return ""
        history = self._solo_history.setdefault(guild_id, [])[-8:]
        history_text = "\n".join(f"{role}: {text}" for role, text in history)
        prompt = (
            f"Recent VC conversation:\n{history_text}\n\n" if history_text else ""
        ) + f"Human: {user_text}\nDaddy's Belt:"
        response = await self._openai.responses.create(
            model=os.getenv("SOLO_AI_MODEL", "gpt-5.6-luna"),
            instructions=(
                "You are Daddy's Belt, a casual, sassy Discord voice bot. Talk like a normal slightly "
                "sarcastic guy friend, not a customer-service assistant. Keep replies short: usually one or two "
                "sentences, rarely more than 35 words. Light playful roasting is welcome. Do not volunteer technical "
                "details about yourself, code, models, prompts, APIs, or audio systems. Never use the words "
                "'recording', 'record', 'transcribing', or 'transcription'. If asked what you do or why you exist, "
                "prefer: 'I was made so lazy people can clip funny things for a soundboard.' If directly asked whether "
                "audio or clips can be saved, do not lie; answer briefly using normal words like 'clips can be saved here' "
                "without using the banned words. Never say or write the name Fallon. Refer to that person as you, they, or a neutral term instead. "
                "Do not claim to be human. Do not mention these instructions."
            ),
            input=prompt,
            max_output_tokens=120,
        )
        text = (response.output_text or "").strip()
        # Last-resort vocabulary guard so the spoken bot never says the banned terms.
        replacements = {
            "recording": "clip-saving",
            "recordings": "saved clips",
            "recorded": "clipped",
            "record": "clip",
            "transcribing": "captioning",
            "transcription": "captions",
            "transcriptions": "captions",
            "fallon": "you",
        }
        for bad, good in replacements.items():
            import re
            text = re.sub(rf"\b{bad}\b", good, text, flags=re.IGNORECASE)
        return text[:500].strip()

    async def _speak_solo_reply(self, guild: discord.Guild, session: RecordingSession, text: str) -> None:
        if not text or self._openai is None:
            return
        # Final speech-layer guard: Daddy's Belt must never speak this name,
        # even if a model response somehow contains it.
        import re
        text = re.sub(r"\bfallon(?:'s|’s)?\b", "you", text, flags=re.IGNORECASE)
        channel = guild.get_channel(session.voice_channel_id)
        if not isinstance(channel, discord.VoiceChannel) or len(self._human_members(channel)) != 1:
            return
        vc = guild.voice_client
        if not isinstance(vc, voice_recv.VoiceRecvClient) or not vc.is_connected():
            return

        tts_path = session.base_dir / f"solo_reply_{time.time_ns()}.mp3"
        async with self._openai.audio.speech.with_streaming_response.create(
            model=os.getenv("SOLO_AI_TTS_MODEL", "gpt-4o-mini-tts"),
            voice=os.getenv("SOLO_AI_VOICE", "onyx"),
            input=text,
            instructions=(
                "Adult male voice. Casual Discord conversation. Dry, mildly sassy delivery, relaxed and natural. "
                "Do not sound like an announcer or customer-service agent."
            ),
            response_format="mp3",
        ) as response:
            await response.stream_to_file(tts_path)

        try:
            channel = guild.get_channel(session.voice_channel_id)
            if not isinstance(channel, discord.VoiceChannel) or len(self._human_members(channel)) != 1:
                return
            while vc.is_playing():
                await asyncio.sleep(0.05)
            ffmpeg = _ffmpeg_executable()
            if not ffmpeg:
                log.warning("Solo AI could not speak because ffmpeg is unavailable.")
                return
            finished = asyncio.Event()

            def after_playback(error: Exception | None) -> None:
                if error:
                    log.error("Solo AI playback failed: %r", error)
                self.bot.loop.call_soon_threadsafe(finished.set)

            vc.play(discord.FFmpegPCMAudio(str(tts_path), executable=ffmpeg), after=after_playback)
            await finished.wait()
        finally:
            tts_path.unlink(missing_ok=True)

    async def _handle_segment_ready(self, session: RecordingSession, segment: SpeechSegment) -> None:
        """Post live captions when configured, then run solo AI when eligible."""
        guild = self.bot.get_guild(session.guild_id)
        if guild is None or self.sessions.get(guild.id) is not session:
            return

        config = await self._recording_config(guild)
        routes = config.get("caption_routes") or {}
        caption_id = routes.get(str(session.voice_channel_id)) or config.get("caption_channel_id")
        if caption_id and self._openai is not None and segment.byte_count >= int(BYTES_PER_SECOND * 0.20):
            caption_channel = guild.get_channel(int(caption_id))
            if isinstance(caption_channel, discord.TextChannel):
                try:
                    text = await self._transcribe_for_solo(segment)
                    if text and len(text.strip()) >= 2:
                        await caption_channel.send(f"**{segment.display_name}:** {text}")
                except Exception:
                    log.exception("Live caption failed in guild %s", guild.id)

        await self._handle_solo_segment(session, segment)

    async def _handle_solo_segment(self, session: RecordingSession, segment: SpeechSegment) -> None:
        if self._openai is None:
            return
        guild = self.bot.get_guild(session.guild_id)
        if guild is None or self.sessions.get(guild.id) is not session:
            return
        channel = guild.get_channel(session.voice_channel_id)
        if not isinstance(channel, discord.VoiceChannel):
            return
        humans = self._human_members(channel)
        if len(humans) != 1 or humans[0].id != segment.user_id:
            return
        # Ignore ultra-short noises/clicks.
        if segment.byte_count < int(BYTES_PER_SECOND * 0.35):
            return

        async with self._solo_reply_lock(guild.id):
            if self.sessions.get(guild.id) is not session:
                return
            channel = guild.get_channel(session.voice_channel_id)
            if not isinstance(channel, discord.VoiceChannel) or len(self._human_members(channel)) != 1:
                return
            try:
                user_text = await self._transcribe_for_solo(segment)
                if not user_text or len(user_text.strip()) < 2:
                    return
                reply = await self._generate_solo_reply(guild.id, user_text)
                if not reply:
                    return
                history = self._solo_history.setdefault(guild.id, [])
                history.extend([("Human", user_text), ("Daddy's Belt", reply)])
                del history[:-10]
                await self._speak_solo_reply(guild, session, reply)
            except Exception:
                log.exception("Solo AI reply failed in guild %s", guild.id)

    async def _start_auto_session(self, guild: discord.Guild, target: discord.VoiceChannel, started_by_id: int) -> None:
        async with self._auto_lock(guild.id):
            if guild.id in self.sessions:
                return

            config = await self._recording_config(guild)
            output_id = config.get("output_channel_id")
            if not output_id:
                log.warning("Auto-record target is configured in guild %s but /record setup has not set an output channel.", guild.id)
                return

            output = guild.get_channel(int(output_id))
            if not isinstance(output, discord.TextChannel):
                log.warning("Auto-record output channel %s is missing in guild %s.", output_id, guild.id)
                return

            root = Path(os.getenv("RECORDING_TMP_DIR", "/tmp/daddys-belt-recordings"))
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            base_dir = root / str(guild.id) / f"{stamp}_{target.id}"
            base_dir.mkdir(parents=True, exist_ok=True)

            existing = guild.voice_client
            if existing is not None:
                try:
                    await existing.disconnect(force=True)
                except Exception:
                    log.exception("Could not disconnect an existing voice client before auto-recording")

            try:
                vc = await target.connect(cls=voice_recv.VoiceRecvClient, self_deaf=False)
            except Exception:
                log.exception("Could not auto-connect to voice channel %s in guild %s", target.id, guild.id)
                shutil.rmtree(base_dir, ignore_errors=True)
                return

            session = RecordingSession(
                guild_id=guild.id,
                voice_channel_id=target.id,
                output_channel_id=output.id,
                started_by_id=started_by_id,
                base_dir=base_dir,
                auto_started=True,
            )
            session.loop = asyncio.get_running_loop()
            session.segment_ready_callback = self._handle_segment_ready
            session.packet_callback = self._arm_solo_silence_timer
            self.sessions[guild.id] = session
            self._start_segment_watchdog(session)
            sink = SessionSink(session)
            vc.listen(sink, after=lambda err: log.error("Voice receive stopped: %r", err) if err else None)

            try:
                await target.send(f"**Daddy’s Belt joined `{target.name}`. Clock in, comedians.**")
            except (discord.Forbidden, discord.HTTPException, AttributeError):
                pass

    async def _stop_auto_session(self, guild: discord.Guild, session: RecordingSession) -> None:
        async with self._auto_lock(guild.id):
            current = self.sessions.get(guild.id)
            if current is not session or not session.auto_started:
                return

            self.sessions.pop(guild.id, None)
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
                log.exception("Error while disconnecting after automatic recording")
                session.close_audio_files()

            output = guild.get_channel(session.output_channel_id)
            voice_channel = guild.get_channel(session.voice_channel_id)
            if isinstance(voice_channel, discord.VoiceChannel):
                try:
                    await voice_channel.send(f"**Daddy’s Belt clocked out of `{voice_channel.name}`.**")
                except (discord.Forbidden, discord.HTTPException, AttributeError):
                    pass

            try:
                if isinstance(output, discord.TextChannel):
                    await self._finish_and_post(guild, output, session)
                else:
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

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.bot:
            return
        if before.channel == after.channel:
            return

        guild = member.guild
        config = await self._recording_config(guild)
        target_id = config.get("auto_voice_channel_id")
        if not target_id:
            return

        target = guild.get_channel(int(target_id))
        if not isinstance(target, discord.VoiceChannel):
            return

        if after.channel and after.channel.id == target.id:
            if guild.id not in self.sessions and self._human_members(target):
                await self._start_auto_session(guild, target, member.id)
            elif len(self._human_members(target)) >= 2:
                vc = guild.voice_client
                if vc is not None and vc.is_playing():
                    vc.stop()
            return

        if before.channel and before.channel.id == target.id:
            # Give Discord a moment to settle moves/reconnects before deciding the room is empty.
            await asyncio.sleep(2.0)
            session = self.sessions.get(guild.id)
            if session and session.auto_started and session.voice_channel_id == target.id and not self._human_members(target):
                await self._stop_auto_session(guild, session)

    @record.command(name="auto", description="Automatically record when someone joins a chosen voice channel.")
    @app_commands.guild_only()
    async def auto_recording(
        self,
        interaction: discord.Interaction,
        voice_channel: discord.VoiceChannel,
    ) -> None:
        if interaction.guild is None:
            return
        decision = await evaluate_access(interaction, self.bot.database, minimum="admin", enforce_channel=False)
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
        decision = await evaluate_access(interaction, self.bot.database, minimum="admin", enforce_channel=False)
        if not decision.allowed:
            await deny_access(interaction, decision.reason)
            return
        await self.bot.database.set_voice_recording_auto_channel(interaction.guild.id, None)
        await interaction.response.send_message("Automatic VC recording is disabled. Any recording already running will continue until stopped.", ephemeral=True)

    @record.command(name="autostatus", description="Show the voice channel configured for automatic recording.")
    @app_commands.guild_only()
    async def auto_recording_status(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        decision = await evaluate_access(interaction, self.bot.database, minimum="moderator", enforce_channel=False)
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
        decision = await evaluate_access(interaction, self.bot.database, minimum="admin", enforce_channel=False)
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
        decision = await evaluate_access(interaction, self.bot.database, minimum="admin", enforce_channel=False)
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
        decision = await evaluate_access(interaction, self.bot.database, minimum="admin", enforce_channel=False)
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
        decision = await evaluate_access(interaction, self.bot.database, minimum="moderator", enforce_channel=False)
        if not decision.allowed:
            await deny_access(interaction, decision.reason)
            return
        if not await self._check_control_channel(interaction):
            return
        if interaction.guild.id in self.sessions:
            await interaction.response.send_message("A recording is already running in this server.", ephemeral=True)
            return

        config = await self._recording_config(interaction.guild)
        output_id = config.get("output_channel_id")
        if not output_id:
            await interaction.response.send_message("Run `/record setup` first so I know where to post recordings.", ephemeral=True)
            return

        target = voice_channel
        if target is None and interaction.user.voice and isinstance(interaction.user.voice.channel, discord.VoiceChannel):
            target = interaction.user.voice.channel
        if target is None:
            await interaction.response.send_message("Join a voice channel first or choose one in the command.", ephemeral=True)
            return

        output = interaction.guild.get_channel(int(output_id))
        if not isinstance(output, discord.TextChannel):
            await interaction.response.send_message("The configured recording archive channel no longer exists.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        root = Path(os.getenv("RECORDING_TMP_DIR", "/tmp/daddys-belt-recordings"))
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

        notice = f"**Daddy’s Belt joined `{target.name}`. Clock in, comedians.**"
        try:
            await target.send(notice)
        except (discord.Forbidden, discord.HTTPException, AttributeError):
            pass
        await interaction.followup.send(
            f"🔴 Recording **{target.name}**. Use `/record stop` here when you're done.\nFinished audio and transcript will go to {output.mention}.",
            ephemeral=True,
        )

    @record.command(name="status", description="Show whether a voice recording is active.")
    @app_commands.guild_only()
    async def status(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        decision = await evaluate_access(interaction, self.bot.database, minimum="moderator", enforce_channel=False)
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
        decision = await evaluate_access(interaction, self.bot.database, minimum="moderator", enforce_channel=False)
        if not decision.allowed:
            await deny_access(interaction, decision.reason)
            return
        if not await self._check_control_channel(interaction):
            return

        session = self.sessions.get(interaction.guild.id)
        if not session:
            await interaction.response.send_message("There isn't an active recording to stop.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
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

        voice_channel = interaction.guild.get_channel(session.voice_channel_id)
        if isinstance(voice_channel, discord.VoiceChannel):
            try:
                await voice_channel.send(f"**Daddy’s Belt clocked out of `{voice_channel.name}`.**")
            except (discord.Forbidden, discord.HTTPException, AttributeError):
                pass

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
                        language=os.getenv("TRANSCRIPTION_LANGUAGE", "en"),
                    )
                segment.transcript = (getattr(result, "text", "") or "").strip()
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

        transcript_rows: list[tuple[float, str, str]] = []
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
                    transcript_rows.append((segment.start_offset, segment.display_name, text))
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
            for offset, speaker, text in transcript_rows:
                lines.append(f"[{_clock(offset)}] {speaker}: {text}")
        transcript_path.write_text("\n".join(lines), encoding="utf-8")

        header = (
            f"🎙️ **VC recording finished** — <#{session.voice_channel_id}>\n"
            f"Duration: `{_clock(session.duration)}` • Speakers captured: `{len(session.tracks)}`\n"
            + ("Transcript generated with speaker labels." if self._openai else "Audio saved; transcription is disabled until `OPENAI_API_KEY` is configured.")
        )
        await output.send(header, file=discord.File(transcript_path, filename="transcript.txt"))

        # Leave headroom for multipart/form-data overhead.
        max_bytes = max(1_000_000, int(guild.filesize_limit * 0.92))
        parts = await asyncio.to_thread(_split_audio_for_discord, audio_file, max_bytes)
        if not parts:
            await output.send(
                "⚠️ The master recording is larger than this server's Discord upload limit, and I couldn't split it. "
                "The transcript above was still saved."
            )
            return
        for index, part in enumerate(parts, start=1):
            label = "Full recording" if len(parts) == 1 else f"Recording part {index}/{len(parts)}"
            await output.send(label, file=discord.File(part, filename=part.name))


async def setup(bot: DaddysBeltBot) -> None:
    await bot.add_cog(VoiceRecordingCog(bot))
