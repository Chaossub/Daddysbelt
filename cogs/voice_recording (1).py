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
from typing import BinaryIO

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
        self._openai = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY")) if os.getenv("OPENAI_API_KEY") else None

    async def cog_unload(self) -> None:
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
            self.sessions[guild.id] = session
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
                return segment, (getattr(result, "text", "") or "").strip()
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
