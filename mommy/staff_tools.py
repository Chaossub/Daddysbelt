from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands
from pymongo import ASCENDING, DESCENDING, ReturnDocument

from services.permissions import deny_access, evaluate_access

log = logging.getLogger("mommy-exe.staff-tools")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def clock(seconds: float | int | None) -> str:
    value = max(0, int(seconds or 0))
    h, rem = divmod(value, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _parse_ids(raw: str) -> list[int]:
    values: list[int] = []
    for token in re.findall(r"\d{15,24}", raw or ""):
        try:
            value = int(token)
        except ValueError:
            continue
        if value not in values:
            values.append(value)
    return values[:12]


async def _staff(interaction: discord.Interaction, bot, *, minimum: str = "moderator") -> bool:
    decision = await evaluate_access(interaction, bot.database, minimum=minimum, enforce_channel=False)
    if decision.allowed:
        return True
    await deny_access(interaction, decision.reason)
    return False


class PhraseSearchModal(discord.ui.Modal, title="Search VC transcripts"):
    phrase = discord.ui.TextInput(label="Phrase or words", placeholder="Example: I never said that", max_length=200)
    user = discord.ui.TextInput(label="User ID (optional)", placeholder="Numeric Discord user ID", required=False, max_length=24)
    channel = discord.ui.TextInput(label="Voice channel ID (optional)", placeholder="Numeric voice channel ID", required=False, max_length=24)
    days = discord.ui.TextInput(label="Days back", placeholder="30", required=False, max_length=4)
    mode = discord.ui.TextInput(label="Search mode", placeholder="exact or keywords (default: exact)", required=False, max_length=12)

    def __init__(self, cog: "StaffToolsCog") -> None:
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _staff(interaction, self.cog.bot):
            return
        uid: int | None = None
        channel_id: int | None = None
        if str(self.user).strip():
            try:
                uid = int(str(self.user).strip())
            except ValueError:
                await interaction.response.send_message("User ID must be numeric.", ephemeral=True)
                return
        if str(self.channel).strip():
            try:
                channel_id = int(str(self.channel).strip())
            except ValueError:
                await interaction.response.send_message("Voice channel ID must be numeric.", ephemeral=True)
                return
        days = 30
        if str(self.days).strip():
            try:
                days = max(1, min(3650, int(str(self.days).strip())))
            except ValueError:
                await interaction.response.send_message("Days must be a number.", ephemeral=True)
                return
        mode = str(self.mode).strip().casefold() or "exact"
        if mode not in {"exact", "keywords", "keyword"}:
            await interaction.response.send_message("Search mode must be `exact` or `keywords`.", ephemeral=True)
            return
        exact = mode == "exact"
        await interaction.response.defer(ephemeral=True, thinking=True)
        rows = await self.cog.search_transcripts(
            interaction.guild.id,
            str(self.phrase).strip(),
            user_id=uid,
            voice_channel_id=channel_id,
            days=days,
            exact=exact,
        )
        if not rows:
            await interaction.followup.send("No matching transcript lines found.", ephemeral=True)
            return
        label = "Exact phrase" if exact else "Keyword"
        embed = discord.Embed(
            title="Transcript Search",
            description=f"{label} matches for **{discord.utils.escape_markdown(str(self.phrase))}**",
            color=discord.Color.blurple(),
        )
        for row in rows[:10]:
            sid = row.get("session_id", "?")
            speaker = row.get("display_name") or f"User {row.get('user_id')}"
            excerpt = str(row.get("text") or "")
            if len(excerpt) > 420:
                excerpt = excerpt[:417] + "…"
            embed.add_field(
                name=f"{speaker} • {clock(row.get('start_offset'))}",
                value=f"`{sid}` • <#{int(row.get('voice_channel_id') or 0)}>\n{excerpt}",
                inline=False,
            )
        await interaction.followup.send(embed=embed, view=SearchResultsView(self.cog, rows[:10]), ephemeral=True)


class SessionSearchModal(discord.ui.Modal, title="Search this recording"):
    phrase = discord.ui.TextInput(label="Phrase or words", max_length=200)

    def __init__(self, cog: "StaffToolsCog", session_id: str) -> None:
        super().__init__()
        self.cog = cog
        self.session_id = session_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _staff(interaction, self.cog.bot):
            return
        rows = await self.cog.search_transcripts(
            interaction.guild.id,
            str(self.phrase).strip(),
            session_id=self.session_id,
            days=3650,
        )
        if not rows:
            await interaction.response.send_message("No matches in that recording.", ephemeral=True)
            return
        embed = discord.Embed(title=f"Search • {self.session_id}", color=discord.Color.blurple())
        for row in rows[:15]:
            embed.add_field(
                name=f"{row.get('display_name')} • {clock(row.get('start_offset'))}",
                value=str(row.get("text") or "")[:700],
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class AddNoteModal(discord.ui.Modal, title="Add recording note"):
    note = discord.ui.TextInput(label="Note", style=discord.TextStyle.paragraph, max_length=1200)
    timestamp = discord.ui.TextInput(label="Timestamp (optional)", placeholder="Example: 04:12", required=False, max_length=12)

    def __init__(self, cog: "StaffToolsCog", session_id: str, incident_id: str | None = None) -> None:
        super().__init__()
        self.cog = cog
        self.session_id = session_id
        self.incident_id = incident_id

    @staticmethod
    def _parse_timestamp(value: str) -> int | None:
        raw = value.strip()
        if not raw:
            return None
        try:
            parts = [int(x) for x in raw.split(":")]
        except ValueError:
            return -1
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        return -1

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _staff(interaction, self.cog.bot):
            return
        offset = self._parse_timestamp(str(self.timestamp))
        if offset == -1:
            await interaction.response.send_message("Use a timestamp like `04:12` or `01:04:12`.", ephemeral=True)
            return
        await self.cog.add_note(
            guild_id=interaction.guild.id,
            session_id=self.session_id,
            incident_id=self.incident_id,
            author=interaction.user,
            text=str(self.note).strip(),
            offset=offset,
        )
        await interaction.response.send_message("📝 Note saved.", ephemeral=True)


class IncidentReasonModal(discord.ui.Modal, title="Bookmark VC incident"):
    reason = discord.ui.TextInput(label="Reason / note (optional)", required=False, style=discord.TextStyle.paragraph, max_length=800)
    involved = discord.ui.TextInput(label="People involved (optional)", placeholder="@mention or paste user IDs", required=False, max_length=300)
    category = discord.ui.TextInput(label="Category (optional)", placeholder="harassment, threat, audio spam, argument...", required=False, max_length=40)
    severity = discord.ui.TextInput(label="Severity (optional)", placeholder="low, medium, high", required=False, max_length=12)

    def __init__(self, cog: "StaffToolsCog") -> None:
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _staff(interaction, self.cog.bot):
            return
        severity = str(self.severity).strip().casefold() or "unspecified"
        if severity not in {"low", "medium", "high", "unspecified"}:
            await interaction.response.send_message("Severity must be `low`, `medium`, or `high`.", ephemeral=True)
            return
        _, message = await self.cog.bookmark_active(
            interaction,
            reason=str(self.reason).strip(),
            involved_user_ids=_parse_ids(str(self.involved)),
            category=str(self.category).strip() or "other",
            severity=severity,
        )
        await interaction.response.send_message(message, ephemeral=True)


class ExtendIncidentModal(discord.ui.Modal, title="Extend incident window"):
    before = discord.ui.TextInput(label="Add minutes BEFORE", placeholder="Example: 2", required=False, max_length=3)
    after = discord.ui.TextInput(label="Add minutes AFTER", placeholder="Example: 2", required=False, max_length=3)

    def __init__(self, cog: "StaffToolsCog", incident_id: str) -> None:
        super().__init__()
        self.cog = cog
        self.incident_id = incident_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _staff(interaction, self.cog.bot):
            return
        try:
            before = max(0, min(30, int(str(self.before).strip() or "0")))
            after = max(0, min(30, int(str(self.after).strip() or "0")))
        except ValueError:
            await interaction.response.send_message("Use whole numbers of minutes.", ephemeral=True)
            return
        if not before and not after:
            await interaction.response.send_message("Enter minutes before, after, or both.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        message = await self.cog.extend_incident(interaction.guild, self.incident_id, before * 60, after * 60)
        await interaction.followup.send(message, ephemeral=True)


class EditIncidentModal(discord.ui.Modal, title="Edit incident details"):
    def __init__(self, cog: "StaffToolsCog", incident_id: str, doc: dict[str, Any]) -> None:
        super().__init__()
        self.cog = cog
        self.incident_id = incident_id
        self.reason = discord.ui.TextInput(
            label="Reason / note",
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=800,
            default=str(doc.get("reason") or "")[:800],
        )
        self.involved = discord.ui.TextInput(
            label="People involved",
            placeholder="@mention or paste user IDs",
            required=False,
            max_length=300,
            default=" ".join(str(x) for x in doc.get("involved_user_ids", []))[:300],
        )
        self.category = discord.ui.TextInput(
            label="Category",
            required=False,
            max_length=40,
            default=str(doc.get("category") or "other")[:40],
        )
        sev = str(doc.get("severity") or "unspecified")
        self.severity = discord.ui.TextInput(
            label="Severity",
            placeholder="low, medium, high",
            required=False,
            max_length=12,
            default="" if sev == "unspecified" else sev[:12],
        )
        for item in (self.reason, self.involved, self.category, self.severity):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _staff(interaction, self.cog.bot):
            return
        severity = str(self.severity).strip().casefold() or "unspecified"
        if severity not in {"low", "medium", "high", "unspecified"}:
            await interaction.response.send_message("Severity must be `low`, `medium`, or `high`.", ephemeral=True)
            return
        await self.cog.update_incident_details(
            interaction.guild.id,
            self.incident_id,
            reason=str(self.reason).strip(),
            involved_user_ids=_parse_ids(str(self.involved)),
            category=str(self.category).strip() or "other",
            severity=severity,
        )
        await interaction.response.send_message(f"✏️ {self.incident_id} details updated.", ephemeral=True)


class SearchResultsView(discord.ui.View):
    def __init__(self, cog: "StaffToolsCog", rows: list[dict[str, Any]]) -> None:
        super().__init__(timeout=600)
        self.cog = cog
        seen: set[str] = set()
        for row in rows:
            sid = str(row.get("session_id") or "")
            if not sid or sid in seen or len(seen) >= 5:
                continue
            seen.add(sid)
            button = discord.ui.Button(label=f"Open {sid[-6:]}", style=discord.ButtonStyle.secondary)
            button.callback = self._make_callback(sid)
            self.add_item(button)

    def _make_callback(self, session_id: str):
        async def callback(interaction: discord.Interaction) -> None:
            if not await _staff(interaction, self.cog.bot):
                return
            await self.cog.show_session(interaction, session_id)
        return callback


class SessionView(discord.ui.View):
    def __init__(self, cog: "StaffToolsCog", session_id: str) -> None:
        super().__init__(timeout=900)
        self.cog = cog
        self.session_id = session_id

    @discord.ui.button(label="Add Note", emoji="📝", style=discord.ButtonStyle.primary)
    async def add_note(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _staff(interaction, self.cog.bot):
            return
        await interaction.response.send_modal(AddNoteModal(self.cog, self.session_id))

    @discord.ui.button(label="Notes", emoji="📌", style=discord.ButtonStyle.secondary)
    async def notes(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _staff(interaction, self.cog.bot):
            return
        notes = await self.cog.notes_for(interaction.guild.id, self.session_id)
        if not notes:
            await interaction.response.send_message("No notes on this recording yet.", ephemeral=True)
            return
        embed = discord.Embed(title=f"Notes • {self.session_id}")
        for note in notes[:15]:
            when = f" @ {clock(note.get('offset'))}" if note.get("offset") is not None else ""
            embed.add_field(
                name=f"{note.get('author_name', 'Moderator')}{when}",
                value=str(note.get("text") or "")[:900],
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Participants", emoji="👥", style=discord.ButtonStyle.secondary)
    async def participants(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _staff(interaction, self.cog.bot):
            return
        participants = await self.cog.participants_for(interaction.guild.id, self.session_id)
        if not participants:
            await interaction.response.send_message("No speaker data was indexed for this recording.", ephemeral=True)
            return
        embed = discord.Embed(title=f"Participants • {self.session_id}")
        embed.description = "\n".join(
            f"• <@{uid}> — **{discord.utils.escape_markdown(name)}**" for uid, name in participants[:40]
        )[:4000]
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Search This Session", emoji="🔎", style=discord.ButtonStyle.secondary)
    async def search(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _staff(interaction, self.cog.bot):
            return
        await interaction.response.send_modal(SessionSearchModal(self.cog, self.session_id))


class NearbyIncidentsView(discord.ui.View):
    def __init__(self, cog: "StaffToolsCog", primary_id: str, candidates: list[dict[str, Any]]) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.primary_id = primary_id
        for doc in candidates[:5]:
            candidate = str(doc.get("incident_id"))
            button = discord.ui.Button(label=f"Merge {candidate}", style=discord.ButtonStyle.danger)
            button.callback = self._callback(candidate)
            self.add_item(button)

    def _callback(self, candidate_id: str):
        async def callback(interaction: discord.Interaction) -> None:
            if not await _staff(interaction, self.cog.bot):
                return
            message = await self.cog.merge_incidents(interaction.guild.id, self.primary_id, candidate_id)
            await interaction.response.send_message(message, ephemeral=True)
        return callback


class IncidentView(discord.ui.View):
    def __init__(self, cog: "StaffToolsCog", incident_id: str, session_id: str | None) -> None:
        super().__init__(timeout=1200)
        self.cog = cog
        self.incident_id = incident_id
        self.session_id = session_id

    @discord.ui.button(label="Add Note", emoji="📝", style=discord.ButtonStyle.primary, row=0)
    async def note(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _staff(interaction, self.cog.bot):
            return
        if not self.session_id:
            await interaction.response.send_message("This incident is still waiting for its recording session to finish.", ephemeral=True)
            return
        await interaction.response.send_modal(AddNoteModal(self.cog, self.session_id, self.incident_id))

    @discord.ui.button(label="Extend", emoji="↔️", style=discord.ButtonStyle.secondary, row=0)
    async def extend(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _staff(interaction, self.cog.bot):
            return
        await interaction.response.send_modal(ExtendIncidentModal(self.cog, self.incident_id))

    @discord.ui.button(label="Transcript", emoji="🧾", style=discord.ButtonStyle.secondary, row=0)
    async def transcript(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _staff(interaction, self.cog.bot):
            return
        await self.cog.show_incident_transcript(interaction, self.incident_id)

    @discord.ui.button(label="Summary", emoji="🧠", style=discord.ButtonStyle.secondary, row=0)
    async def summary(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _staff(interaction, self.cog.bot):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        text = await self.cog.case_summary(interaction.guild.id, self.incident_id)
        await interaction.followup.send(embed=discord.Embed(title=f"Case Summary • {self.incident_id}", description=text[:4000]), ephemeral=True)

    @discord.ui.button(label="Mark Evidence", emoji="📌", style=discord.ButtonStyle.secondary, row=1)
    async def evidence(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _staff(interaction, self.cog.bot):
            return
        await self.cog.set_incident_status(interaction.guild.id, self.incident_id, "evidence")
        await interaction.response.send_message(f"📌 {self.incident_id} marked as evidence. It will stay in the evidence queue until staff closes it.", ephemeral=True)

    @discord.ui.button(label="User History", emoji="👤", style=discord.ButtonStyle.secondary, row=1)
    async def user_history(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _staff(interaction, self.cog.bot):
            return
        await self.cog.show_incident_user_history(interaction, self.incident_id)

    @discord.ui.button(label="Merge Nearby", emoji="🔗", style=discord.ButtonStyle.secondary, row=1)
    async def merge(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _staff(interaction, self.cog.bot):
            return
        candidates = await self.cog.nearby_incidents(interaction.guild.id, self.incident_id)
        if not candidates:
            await interaction.response.send_message("No nearby incident bookmarks from this recording are available to merge.", ephemeral=True)
            return
        embed = discord.Embed(title=f"Merge into {self.incident_id}", description="Choose another bookmark from the same recording. The combined case keeps the widest time window and both sets of notes/people.")
        for doc in candidates[:5]:
            embed.add_field(name=str(doc.get("incident_id")), value=f"`{clock(doc.get('window_start'))}` → `{clock(doc.get('window_end_actual', doc.get('window_end')))}` • {doc.get('status', 'open')}\n{str(doc.get('reason') or 'No reason')[:350]}", inline=False)
        await interaction.response.send_message(embed=embed, view=NearbyIncidentsView(self.cog, self.incident_id, candidates), ephemeral=True)

    @discord.ui.button(label="Close", emoji="✅", style=discord.ButtonStyle.success, row=1)
    async def close(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _staff(interaction, self.cog.bot):
            return
        await self.cog.set_incident_status(interaction.guild.id, self.incident_id, "closed")
        await interaction.response.send_message(f"✅ {self.incident_id} closed.", ephemeral=True)

    @discord.ui.button(label="Edit Details", emoji="✏️", style=discord.ButtonStyle.secondary, row=2)
    async def edit_details(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _staff(interaction, self.cog.bot):
            return
        doc = await self.cog.get_incident(interaction.guild.id, self.incident_id)
        if not doc:
            await interaction.response.send_message("Incident not found.", ephemeral=True)
            return
        await interaction.response.send_modal(EditIncidentModal(self.cog, self.incident_id, doc))


class StaffDashboardView(discord.ui.View):
    def __init__(self, cog: "StaffToolsCog") -> None:
        super().__init__(timeout=1200)
        self.cog = cog

    @discord.ui.button(label="Search Transcripts", emoji="🔎", style=discord.ButtonStyle.primary, row=0)
    async def search(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _staff(interaction, self.cog.bot):
            return
        await interaction.response.send_modal(PhraseSearchModal(self.cog))

    @discord.ui.button(label="Recent Recordings", emoji="🎙️", style=discord.ButtonStyle.secondary, row=0)
    async def recent(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _staff(interaction, self.cog.bot):
            return
        await self.cog.show_recent(interaction)

    @discord.ui.button(label="Bookmark Incident", emoji="🚩", style=discord.ButtonStyle.danger, row=0)
    async def bookmark(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _staff(interaction, self.cog.bot):
            return
        await interaction.response.send_modal(IncidentReasonModal(self.cog))

    @discord.ui.button(label="Incidents", emoji="📂", style=discord.ButtonStyle.secondary, row=1)
    async def incidents(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _staff(interaction, self.cog.bot):
            return
        await self.cog.show_incidents(interaction)

    @discord.ui.button(label="Evidence", emoji="📌", style=discord.ButtonStyle.secondary, row=1)
    async def evidence(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _staff(interaction, self.cog.bot):
            return
        await self.cog.show_incidents(interaction, status="evidence")

    @discord.ui.button(label="Timeout Reviews", emoji="⏱️", style=discord.ButtonStyle.secondary, row=1)
    async def timeout_reviews(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _staff(interaction, self.cog.bot):
            return
        await self.cog.show_timeout_reviews(interaction)


class StaffToolsCog(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        self._indexes_ready = False

    async def _collections(self):
        db = self.bot.database._database  # noqa: SLF001
        if db is None:
            raise RuntimeError("MongoDB is not connected")
        sessions = db["vc_recording_sessions"]
        rows = db["vc_transcript_rows"]
        notes = db["vc_recording_notes"]
        incidents = db["vc_incidents"]
        if not self._indexes_ready:
            await asyncio.to_thread(sessions.create_index, [("guild_id", ASCENDING), ("started_at", DESCENDING)])
            await asyncio.to_thread(rows.create_index, [("guild_id", ASCENDING), ("session_id", ASCENDING), ("start_offset", ASCENDING)])
            await asyncio.to_thread(rows.create_index, [("guild_id", ASCENDING), ("user_id", ASCENDING), ("created_at", DESCENDING)])
            await asyncio.to_thread(notes.create_index, [("guild_id", ASCENDING), ("session_id", ASCENDING), ("created_at", ASCENDING)])
            await asyncio.to_thread(incidents.create_index, [("guild_id", ASCENDING), ("incident_id", ASCENDING)], unique=True)
            await asyncio.to_thread(incidents.create_index, [("guild_id", ASCENDING), ("status", ASCENDING), ("created_at", DESCENDING)])
            await asyncio.to_thread(incidents.create_index, [("guild_id", ASCENDING), ("involved_user_ids", ASCENDING), ("created_at", DESCENDING)])
            self._indexes_ready = True
        return sessions, rows, notes, incidents

    @app_commands.command(name="staff", description="Open Mommy.exe's private VC moderation and recording dashboard.")
    @app_commands.guild_only()
    async def staff(self, interaction: discord.Interaction) -> None:
        if not await _staff(interaction, self.bot):
            return
        embed = discord.Embed(
            title="Mommy.exe • Staff Console",
            description="Private VC evidence, recordings, transcript search, incident cases, notes, and timeout review.",
            color=discord.Color.dark_teal(),
        )
        embed.add_field(name="Recording Archive", value="Search every saved end-of-session transcript or open recent recordings.", inline=False)
        embed.add_field(name="Incident Cases", value="Bookmark 5 minutes before + 2 minutes after, tag people/category/severity, extend, merge, summarize, and preserve as evidence.", inline=False)
        embed.add_field(name="Human Decisions", value="Mommy can timeout/remove configured roles and collect evidence. Bans and kicks remain Discord-native staff actions only.", inline=False)
        embed.set_footer(text="Staff only • moderators, admins, and server owner")
        await interaction.response.send_message(embed=embed, view=StaffDashboardView(self), ephemeral=True)

    @app_commands.command(name="incident-bookmark", description="Bookmark a 7-minute moderation window in the active VC recording.")
    @app_commands.guild_only()
    async def incident_bookmark(self, interaction: discord.Interaction, reason: str = "") -> None:
        if not await _staff(interaction, self.bot):
            return
        _, msg = await self.bookmark_active(interaction, reason=reason)
        await interaction.response.send_message(msg, ephemeral=True)

    async def _next_incident_id(self, guild_id: int) -> str:
        db = self.bot.database._database  # noqa: SLF001
        counters = db["staff_counters"]
        doc = await asyncio.to_thread(
            counters.find_one_and_update,
            {"guild_id": guild_id, "name": "incident"},
            {"$inc": {"value": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return f"INC-{int((doc or {}).get('value', 1)):04d}"

    async def archive_session(
        self,
        guild: discord.Guild,
        session,
        transcript_rows: list[tuple[float, int, str, str]],
        *,
        transcript_message_id: int | None = None,
        audio_message_ids: list[int] | None = None,
    ) -> str:
        sessions, rows, _, _ = await self._collections()
        session_id = f"VC-{session.started_at.strftime('%Y%m%d')}-{str(int(session.started_at.timestamp()))[-6:]}"
        doc = {
            "guild_id": guild.id,
            "session_id": session_id,
            "voice_channel_id": session.voice_channel_id,
            "output_channel_id": session.output_channel_id,
            "started_by_id": session.started_by_id,
            "started_at": session.started_at,
            "ended_at": utcnow(),
            "duration_seconds": session.duration,
            "speaker_count": len(session.tracks),
            "transcript_message_id": transcript_message_id,
            "audio_message_ids": audio_message_ids or [],
            "created_at": utcnow(),
        }
        await asyncio.to_thread(sessions.update_one, {"guild_id": guild.id, "session_id": session_id}, {"$set": doc}, upsert=True)
        if transcript_rows:
            docs = []
            for offset, uid, name, text in transcript_rows:
                docs.append({
                    "guild_id": guild.id,
                    "session_id": session_id,
                    "voice_channel_id": session.voice_channel_id,
                    "user_id": uid,
                    "display_name": name,
                    "start_offset": float(offset),
                    "text": text,
                    "text_normalized": re.sub(r"\s+", " ", text.casefold()).strip(),
                    "created_at": session.started_at + timedelta(seconds=float(offset)),
                })
            await asyncio.to_thread(rows.delete_many, {"guild_id": guild.id, "session_id": session_id})
            await asyncio.to_thread(rows.insert_many, docs)
        return session_id

    async def search_transcripts(
        self,
        guild_id: int,
        phrase: str,
        *,
        user_id: int | None = None,
        voice_channel_id: int | None = None,
        days: int = 30,
        session_id: str | None = None,
        exact: bool = True,
    ) -> list[dict[str, Any]]:
        _, rows, _, _ = await self._collections()
        phrase_norm = re.sub(r"\s+", " ", phrase.casefold()).strip()
        if not phrase_norm:
            return []
        query: dict[str, Any] = {"guild_id": guild_id}
        if exact:
            query["text_normalized"] = {"$regex": re.escape(phrase_norm)}
        else:
            tokens = [t for t in re.findall(r"[\w']+", phrase_norm) if len(t) >= 2][:12]
            if not tokens:
                return []
            query["$and"] = [{"text_normalized": {"$regex": re.escape(token)}} for token in tokens]
        if user_id is not None:
            query["user_id"] = user_id
        if voice_channel_id is not None:
            query["voice_channel_id"] = voice_channel_id
        if session_id:
            query["session_id"] = session_id
        else:
            query["created_at"] = {"$gte": utcnow() - timedelta(days=days)}
        return await asyncio.to_thread(lambda: list(rows.find(query).sort("created_at", DESCENDING).limit(50)))

    async def participants_for(self, guild_id: int, session_id: str) -> list[tuple[int, str]]:
        _, rows, _, _ = await self._collections()
        docs = await asyncio.to_thread(lambda: list(rows.find({"guild_id": guild_id, "session_id": session_id}, {"user_id": 1, "display_name": 1})))
        seen: dict[int, str] = {}
        for doc in docs:
            uid = int(doc.get("user_id") or 0)
            if uid:
                seen.setdefault(uid, str(doc.get("display_name") or f"User {uid}"))
        return sorted(seen.items(), key=lambda item: item[1].casefold())

    async def add_note(self, *, guild_id: int, session_id: str, incident_id: str | None, author: discord.abc.User, text: str, offset: int | None) -> None:
        _, _, notes, _ = await self._collections()
        await asyncio.to_thread(notes.insert_one, {
            "guild_id": guild_id,
            "session_id": session_id,
            "incident_id": incident_id,
            "author_id": author.id,
            "author_name": getattr(author, "display_name", author.name),
            "text": text,
            "offset": offset,
            "created_at": utcnow(),
        })

    async def notes_for(self, guild_id: int, session_id: str, *, incident_id: str | None = None) -> list[dict[str, Any]]:
        _, _, notes, _ = await self._collections()
        query: dict[str, Any] = {"guild_id": guild_id, "session_id": session_id}
        if incident_id is not None:
            query["incident_id"] = incident_id
        return await asyncio.to_thread(lambda: list(notes.find(query).sort("created_at", ASCENDING).limit(75)))

    async def bookmark_active(
        self,
        interaction: discord.Interaction,
        *,
        reason: str = "",
        involved_user_ids: list[int] | None = None,
        category: str = "other",
        severity: str = "unspecified",
    ) -> tuple[bool, str]:
        vc_cog = self.bot.get_cog("VoiceRecordingCog")
        session = getattr(vc_cog, "sessions", {}).get(interaction.guild.id) if vc_cog else None
        if session is None:
            return False, "There isn't an active recording to bookmark right now."
        _, _, _, incidents = await self._collections()
        offset = float(session.duration)
        now = utcnow()
        incident_id = await self._next_incident_id(interaction.guild.id)
        doc = {
            "guild_id": interaction.guild.id,
            "incident_id": incident_id,
            "session_started_at": session.started_at,
            "session_id": None,
            "voice_channel_id": session.voice_channel_id,
            "bookmarked_by_id": interaction.user.id,
            "bookmarked_by_name": getattr(interaction.user, "display_name", interaction.user.name),
            "bookmark_offset": offset,
            "window_start": max(0.0, offset - 300.0),
            "window_end": offset + 120.0,
            "reason": reason,
            "category": (category or "other")[:40],
            "severity": severity,
            "involved_user_ids": list(dict.fromkeys(involved_user_ids or []))[:12],
            "status": "open",
            "created_at": now,
        }
        await asyncio.to_thread(incidents.insert_one, doc)
        people = " ".join(f"<@{uid}>" for uid in doc["involved_user_ids"]) or "not tagged yet"
        return True, (
            f"🚩 **{incident_id}** bookmarked. Window: `{clock(doc['window_start'])}` → `{clock(doc['window_end'])}` "
            f"(5 minutes before + 2 after).\nCategory: **{doc['category']}** • Severity: **{severity}** • People: {people}"
        )

    async def attach_incidents_to_session(self, guild_id: int, session, session_id: str) -> list[dict[str, Any]]:
        _, _, _, incidents = await self._collections()
        query = {"guild_id": guild_id, "session_id": None, "session_started_at": session.started_at}
        docs = await asyncio.to_thread(lambda: list(incidents.find(query)))
        if docs:
            await asyncio.to_thread(incidents.update_many, query, {"$set": {"session_id": session_id, "session_duration_seconds": session.duration}})
        return docs

    async def get_incident(self, guild_id: int, incident_id: str) -> dict[str, Any] | None:
        _, _, _, incidents = await self._collections()
        return await asyncio.to_thread(incidents.find_one, {"guild_id": guild_id, "incident_id": incident_id})

    async def update_incident_details(
        self,
        guild_id: int,
        incident_id: str,
        *,
        reason: str,
        involved_user_ids: list[int],
        category: str,
        severity: str,
    ) -> None:
        _, _, _, incidents = await self._collections()
        await asyncio.to_thread(
            incidents.update_one,
            {"guild_id": guild_id, "incident_id": incident_id},
            {"$set": {
                "reason": reason[:800],
                "involved_user_ids": list(dict.fromkeys(involved_user_ids))[:12],
                "category": (category or "other")[:40],
                "severity": severity,
                "updated_at": utcnow(),
            }},
        )

    async def set_incident_status(self, guild_id: int, incident_id: str, status: str) -> None:
        _, _, _, incidents = await self._collections()
        await asyncio.to_thread(
            incidents.update_one,
            {"guild_id": guild_id, "incident_id": incident_id},
            {"$set": {"status": status, "status_updated_at": utcnow()}},
        )

    async def _incident_rows(self, guild_id: int, doc: dict[str, Any], *, limit: int = 80) -> list[dict[str, Any]]:
        _, rows, _, _ = await self._collections()
        sid = doc.get("session_id")
        if not sid:
            return []
        start = float(doc.get("window_start", 0.0) or 0.0)
        end = float(doc.get("window_end_actual", doc.get("window_end", 0.0)) or 0.0)
        return await asyncio.to_thread(lambda: list(rows.find({
            "guild_id": guild_id,
            "session_id": sid,
            "start_offset": {"$gte": start, "$lte": end},
        }).sort("start_offset", ASCENDING).limit(limit)))

    async def _post_incident_clip(
        self,
        guild: discord.Guild,
        output: discord.TextChannel,
        doc: dict[str, Any],
        source_audio: Path,
        *,
        duration: float,
        replacement: bool = False,
    ) -> discord.Message | None:
        start = max(0.0, float(doc.get("window_start", 0.0) or 0.0))
        end = min(float(duration), float(doc.get("window_end", duration) or duration))
        if end <= start:
            return None
        with tempfile.TemporaryDirectory(prefix="mommy_incident_") as temp:
            out_path = Path(temp) / f"{doc.get('incident_id')}.mp3"
            cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-ss", str(start), "-to", str(end), "-i", str(source_audio),
                "-codec:a", "libmp3lame", "-b:a", "96k", str(out_path),
            ]
            try:
                await asyncio.to_thread(subprocess.run, cmd, check=True, capture_output=True)
            except Exception:
                log.exception("Could not render incident clip %s", doc.get("incident_id"))
                return None
            excerpt_rows = await self._incident_rows(guild.id, {**doc, "window_end_actual": end}, limit=20)
            excerpt = "\n".join(
                f"`{clock(r.get('start_offset'))}` **{r.get('display_name', 'Speaker')}:** {str(r.get('text') or '')[:220]}"
                for r in excerpt_rows
            )
            reason = str(doc.get("reason") or "No reason supplied")
            people = " ".join(f"<@{uid}>" for uid in doc.get("involved_user_ids", [])) or "not tagged"
            prefix = "♻️ **Updated incident clip**" if replacement else "🚩 **Incident clip**"
            content = (
                f"{prefix} • **{doc.get('incident_id')}** • <#{int(doc.get('voice_channel_id') or 0)}> • "
                f"`{clock(start)}` → `{clock(end)}`\n"
                f"**Category:** {doc.get('category', 'other')} • **Severity:** {doc.get('severity', 'unspecified')} • **People:** {people}\n"
                f"**Reason:** {reason[:500]}"
            )
            if excerpt:
                content += "\n\n**Transcript preview**\n" + excerpt[:1100]
            try:
                return await output.send(content[:1900], file=discord.File(out_path, filename=out_path.name))
            except (discord.Forbidden, discord.HTTPException):
                log.exception("Could not post incident clip %s", doc.get("incident_id"))
                return None

    async def materialize_incidents(self, guild: discord.Guild, output: discord.TextChannel, session, session_id: str, master_wav: Path) -> None:
        _, _, _, incidents = await self._collections()
        docs = await asyncio.to_thread(lambda: list(incidents.find({"guild_id": guild.id, "session_id": session_id, "clip_message_id": {"$exists": False}})))
        for doc in docs:
            msg = await self._post_incident_clip(guild, output, doc, master_wav, duration=float(session.duration), replacement=False)
            if msg is not None:
                end = min(float(session.duration), float(doc.get("window_end", session.duration) or session.duration))
                await asyncio.to_thread(incidents.update_one, {"_id": doc["_id"]}, {"$set": {
                    "clip_message_id": msg.id,
                    "clip_channel_id": output.id,
                    "window_end_actual": end,
                    "materialized_at": utcnow(),
                }})

    async def _download_archived_audio(self, guild: discord.Guild, session_doc: dict[str, Any], workdir: Path) -> Path | None:
        output_id = int(session_doc.get("output_channel_id") or 0)
        channel = guild.get_channel(output_id)
        if not isinstance(channel, discord.TextChannel):
            try:
                fetched = await guild.fetch_channel(output_id)
                channel = fetched if isinstance(fetched, discord.TextChannel) else None
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                channel = None
        if channel is None:
            return None
        parts: list[Path] = []
        for index, message_id in enumerate(session_doc.get("audio_message_ids") or []):
            try:
                message = await channel.fetch_message(int(message_id))
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                continue
            if not message.attachments:
                continue
            attachment = message.attachments[0]
            suffix = Path(attachment.filename).suffix or ".mp3"
            path = workdir / f"source_{index:02d}{suffix}"
            try:
                await attachment.save(path)
            except (discord.HTTPException, OSError):
                continue
            parts.append(path)
        if not parts:
            return None
        if len(parts) == 1:
            return parts[0]
        concat_file = workdir / "concat.txt"
        concat_file.write_text("\n".join(f"file '{p.as_posix()}'" for p in parts), encoding="utf-8")
        joined = workdir / "joined.mp3"
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-codec:a", "libmp3lame", "-b:a", "128k", str(joined)]
        try:
            await asyncio.to_thread(subprocess.run, cmd, check=True, capture_output=True)
            return joined
        except Exception:
            log.exception("Could not reassemble archived recording %s", session_doc.get("session_id"))
            return None

    async def extend_incident(self, guild: discord.Guild, incident_id: str, before_seconds: int, after_seconds: int) -> str:
        sessions, _, _, incidents = await self._collections()
        doc = await asyncio.to_thread(incidents.find_one, {"guild_id": guild.id, "incident_id": incident_id})
        if not doc:
            return "Incident not found."
        new_start = max(0.0, float(doc.get("window_start", 0.0)) - before_seconds)
        requested_end = float(doc.get("window_end", 0.0)) + after_seconds
        duration = float(doc.get("session_duration_seconds", 0.0) or 0.0)
        new_end = min(duration, requested_end) if duration else requested_end
        await asyncio.to_thread(incidents.update_one, {"_id": doc["_id"]}, {"$set": {
            "window_start": new_start,
            "window_end": new_end,
            "window_extended_at": utcnow(),
        }})
        doc["window_start"] = new_start
        doc["window_end"] = new_end

        if not doc.get("session_id"):
            return f"↔️ {incident_id} extended to `{clock(new_start)}` → `{clock(new_end)}`. The final clip will use the extended window when this VC recording ends."

        session_doc = await asyncio.to_thread(sessions.find_one, {"guild_id": guild.id, "session_id": doc.get("session_id")})
        if not session_doc:
            return f"↔️ Window updated to `{clock(new_start)}` → `{clock(new_end)}`, but the archived source recording could not be found for re-rendering."
        output_id = int(session_doc.get("output_channel_id") or 0)
        output = guild.get_channel(output_id)
        if not isinstance(output, discord.TextChannel):
            return f"↔️ Window updated to `{clock(new_start)}` → `{clock(new_end)}`, but the archive channel is unavailable for re-rendering."
        with tempfile.TemporaryDirectory(prefix="mommy_archive_") as temp:
            source = await self._download_archived_audio(guild, session_doc, Path(temp))
            if source is None:
                return f"↔️ Window updated to `{clock(new_start)}` → `{clock(new_end)}`, but Mommy couldn't download the archived full recording to regenerate the clip."
            msg = await self._post_incident_clip(guild, output, doc, source, duration=float(session_doc.get("duration_seconds", new_end)), replacement=True)
        if msg is None:
            return f"↔️ Window updated to `{clock(new_start)}` → `{clock(new_end)}`, but the replacement clip failed to upload."
        await asyncio.to_thread(incidents.update_one, {"_id": doc["_id"]}, {"$set": {
            "clip_message_id": msg.id,
            "clip_channel_id": output.id,
            "window_end_actual": new_end,
            "materialized_at": utcnow(),
            "clip_revision": int(doc.get("clip_revision", 0) or 0) + 1,
        }})
        return f"↔️ {incident_id} extended to `{clock(new_start)}` → `{clock(new_end)}` and a replacement clip was posted from the archived full recording."

    async def nearby_incidents(self, guild_id: int, incident_id: str) -> list[dict[str, Any]]:
        _, _, _, incidents = await self._collections()
        primary = await asyncio.to_thread(incidents.find_one, {"guild_id": guild_id, "incident_id": incident_id})
        if not primary or not primary.get("session_id"):
            return []
        center = float(primary.get("bookmark_offset", 0.0) or 0.0)
        docs = await asyncio.to_thread(lambda: list(incidents.find({
            "guild_id": guild_id,
            "session_id": primary.get("session_id"),
            "incident_id": {"$ne": incident_id},
            "status": {"$nin": ["merged"]},
            "bookmark_offset": {"$gte": max(0, center - 900), "$lte": center + 900},
        }).sort("bookmark_offset", ASCENDING).limit(10)))
        return docs

    async def merge_incidents(self, guild_id: int, primary_id: str, other_id: str) -> str:
        _, _, notes, incidents = await self._collections()
        primary = await asyncio.to_thread(incidents.find_one, {"guild_id": guild_id, "incident_id": primary_id})
        other = await asyncio.to_thread(incidents.find_one, {"guild_id": guild_id, "incident_id": other_id})
        if not primary or not other:
            return "One of those incidents no longer exists."
        if primary.get("session_id") != other.get("session_id"):
            return "Only incident bookmarks from the same recording can be merged."
        involved = list(dict.fromkeys((primary.get("involved_user_ids") or []) + (other.get("involved_user_ids") or [])))[:12]
        reasons = [str(primary.get("reason") or "").strip(), str(other.get("reason") or "").strip()]
        combined_reason = "\n---\n".join(r for r in reasons if r)[:1600]
        new_start = min(float(primary.get("window_start", 0.0)), float(other.get("window_start", 0.0)))
        new_end = max(float(primary.get("window_end", 0.0)), float(other.get("window_end", 0.0)))
        await asyncio.to_thread(incidents.update_one, {"_id": primary["_id"]}, {"$set": {
            "window_start": new_start,
            "window_end": new_end,
            "reason": combined_reason,
            "involved_user_ids": involved,
            "merged_incident_ids": list(dict.fromkeys((primary.get("merged_incident_ids") or []) + [other_id])),
            "updated_at": utcnow(),
        }})
        await asyncio.to_thread(incidents.update_one, {"_id": other["_id"]}, {"$set": {
            "status": "merged",
            "merged_into": primary_id,
            "status_updated_at": utcnow(),
        }})
        await asyncio.to_thread(notes.update_many, {"guild_id": guild_id, "incident_id": other_id}, {"$set": {"incident_id": primary_id, "merged_from_incident_id": other_id}})
        return f"🔗 **{other_id}** merged into **{primary_id}**. Combined window: `{clock(new_start)}` → `{clock(new_end)}`."

    async def show_incident_transcript(self, interaction: discord.Interaction, incident_id: str) -> None:
        _, _, _, incidents = await self._collections()
        doc = await asyncio.to_thread(incidents.find_one, {"guild_id": interaction.guild.id, "incident_id": incident_id})
        if not doc:
            await interaction.response.send_message("Incident not found.", ephemeral=True)
            return
        rows = await self._incident_rows(interaction.guild.id, doc, limit=35)
        if not rows:
            await interaction.response.send_message("No transcript rows are indexed inside this incident window yet.", ephemeral=True)
            return
        lines = [f"`{clock(r.get('start_offset'))}` **{r.get('display_name', 'Speaker')}:** {str(r.get('text') or '')[:350]}" for r in rows]
        chunks: list[str] = []
        current = ""
        for line in lines:
            if len(current) + len(line) + 1 > 3800:
                chunks.append(current)
                current = ""
            current += ("\n" if current else "") + line
        if current:
            chunks.append(current)
        await interaction.response.send_message(embed=discord.Embed(title=f"Incident Transcript • {incident_id}", description=chunks[0]), ephemeral=True)
        for chunk in chunks[1:3]:
            await interaction.followup.send(embed=discord.Embed(description=chunk), ephemeral=True)

    async def show_incident_user_history(self, interaction: discord.Interaction, incident_id: str) -> None:
        _, _, _, incidents = await self._collections()
        doc = await asyncio.to_thread(incidents.find_one, {"guild_id": interaction.guild.id, "incident_id": incident_id})
        if not doc:
            await interaction.response.send_message("Incident not found.", ephemeral=True)
            return
        user_ids = [int(x) for x in doc.get("involved_user_ids", []) if x]
        if not user_ids:
            await interaction.response.send_message("No people are tagged on this incident yet. Add their IDs when bookmarking future incidents so user history can be linked cleanly.", ephemeral=True)
            return
        db = self.bot.database._database  # noqa: SLF001
        punishments = db["vc_automod_punishments"]
        embed = discord.Embed(title=f"User History • {incident_id}", color=discord.Color.orange())
        for uid in user_ids[:6]:
            incident_count = await asyncio.to_thread(incidents.count_documents, {"guild_id": interaction.guild.id, "involved_user_ids": uid, "status": {"$ne": "merged"}})
            timeout_count = await asyncio.to_thread(punishments.count_documents, {"guild_id": interaction.guild.id, "user_id": uid, "succeeded": True, "action": {"$in": ["timeout", "remove_timeout"]}})
            recent = await asyncio.to_thread(lambda uid=uid: list(punishments.find({"guild_id": interaction.guild.id, "user_id": uid}).sort("created_at", DESCENDING).limit(3)))
            recent_text = "\n".join(f"• {p.get('action', 'action')} — {int(p.get('duration_minutes', 0) or 0)}m — {p.get('trigger_label', 'VC automod')}" for p in recent) or "No automod punishment history."
            embed.add_field(name=f"<@{uid}>", value=f"Linked incidents: **{incident_count}** • timeouts: **{timeout_count}**\n{recent_text}"[:950], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def case_summary(self, guild_id: int, incident_id: str) -> str:
        _, _, notes, incidents = await self._collections()
        doc = await asyncio.to_thread(incidents.find_one, {"guild_id": guild_id, "incident_id": incident_id})
        if not doc:
            return "Incident not found."
        rows = await self._incident_rows(guild_id, doc, limit=60)
        note_docs = await asyncio.to_thread(lambda: list(notes.find({"guild_id": guild_id, "incident_id": incident_id}).sort("created_at", ASCENDING).limit(20)))
        transcript = "\n".join(f"[{clock(r.get('start_offset'))}] {r.get('display_name', 'Speaker')}: {r.get('text', '')}" for r in rows)
        note_text = "\n".join(f"- {n.get('author_name', 'Staff')}: {n.get('text', '')}" for n in note_docs)
        fallback = (
            f"**Category:** {doc.get('category', 'other')} • **Severity:** {doc.get('severity', 'unspecified')} • **Status:** {doc.get('status', 'open')}\n"
            f"**Window:** `{clock(doc.get('window_start'))}` → `{clock(doc.get('window_end_actual', doc.get('window_end')))}`\n"
            f"**Reason:** {str(doc.get('reason') or 'No reason supplied')[:900]}\n"
            f"**People tagged:** {' '.join(f'<@{uid}>' for uid in doc.get('involved_user_ids', [])) or 'none'}\n"
            f"**Transcript lines:** {len(rows)} • **Staff notes:** {len(note_docs)}"
        )
        client = getattr(self.bot, "_openai", None)
        if client is None or not transcript:
            return fallback
        prompt = (
            f"Incident: {incident_id}\nCategory: {doc.get('category')}\nSeverity: {doc.get('severity')}\nReason: {doc.get('reason')}\n\n"
            f"Staff notes:\n{note_text or 'none'}\n\nTranscript excerpt:\n{transcript[:12000]}"
        )
        try:
            response = await client.responses.create(
                model=getattr(self.bot, "_model", "gpt-5.6-luna"),
                instructions=(
                    "Write a concise, neutral moderation case summary based only on the supplied evidence. "
                    "Do not infer motives, diagnose people, or decide guilt. Distinguish staff notes from transcript evidence. "
                    "Mention important timestamps and conflicting accounts when present. End with 'Human review required.'"
                ),
                input=prompt,
                max_output_tokens=500,
            )
            text = (response.output_text or "").strip()
            return text or fallback
        except Exception:
            log.exception("Could not generate case summary for %s", incident_id)
            return fallback

    async def show_incident(self, interaction: discord.Interaction, incident_id: str) -> None:
        _, _, notes, incidents = await self._collections()
        doc = await asyncio.to_thread(incidents.find_one, {"guild_id": interaction.guild.id, "incident_id": incident_id})
        if not doc:
            await interaction.response.send_message("Incident not found.", ephemeral=True)
            return
        sid = doc.get("session_id")
        end = doc.get("window_end_actual", doc.get("window_end"))
        people = " ".join(f"<@{uid}>" for uid in doc.get("involved_user_ids", [])) or "None tagged"
        note_count = await asyncio.to_thread(notes.count_documents, {"guild_id": interaction.guild.id, "incident_id": incident_id})
        desc = (
            f"<#{int(doc.get('voice_channel_id') or 0)}> • `{clock(doc.get('window_start'))}` → `{clock(end)}`\n"
            f"Status: **{doc.get('status', 'open')}** • Category: **{doc.get('category', 'other')}** • Severity: **{doc.get('severity', 'unspecified')}**\n"
            f"People: {people}\nNotes: **{note_count}**\n\n{str(doc.get('reason') or 'No reason supplied')[:900]}"
        )
        embed = discord.Embed(title=str(doc.get("incident_id")), description=desc, color=discord.Color.orange())
        if doc.get("clip_message_id") and doc.get("clip_channel_id"):
            embed.add_field(name="Clip", value=f"[Open incident clip](https://discord.com/channels/{interaction.guild.id}/{doc.get('clip_channel_id')}/{doc.get('clip_message_id')})", inline=False)
        if doc.get("merged_incident_ids"):
            embed.add_field(name="Merged", value=", ".join(doc.get("merged_incident_ids")[:10]), inline=False)
        await interaction.response.send_message(embed=embed, view=IncidentView(self, incident_id, sid), ephemeral=True)

    async def show_recent(self, interaction: discord.Interaction) -> None:
        sessions, _, _, _ = await self._collections()
        docs = await asyncio.to_thread(lambda: list(sessions.find({"guild_id": interaction.guild.id}).sort("started_at", DESCENDING).limit(10)))
        if not docs:
            await interaction.response.send_message("No archived recording sessions yet.", ephemeral=True)
            return
        embed = discord.Embed(title="Recent VC Recordings")
        view = discord.ui.View(timeout=600)
        for doc in docs:
            sid = str(doc.get("session_id"))
            embed.add_field(name=sid, value=f"<#{int(doc.get('voice_channel_id') or 0)}> • `{clock(doc.get('duration_seconds'))}` • {doc.get('speaker_count', 0)} speaker(s)", inline=False)
            if len(view.children) < 5:
                button = discord.ui.Button(label=f"Open {sid[-6:]}", style=discord.ButtonStyle.secondary)
                async def callback(i: discord.Interaction, s=sid):
                    if not await _staff(i, self.bot):
                        return
                    await self.show_session(i, s)
                button.callback = callback
                view.add_item(button)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def show_session(self, interaction: discord.Interaction, session_id: str) -> None:
        sessions, rows, _, incidents = await self._collections()
        doc = await asyncio.to_thread(sessions.find_one, {"guild_id": interaction.guild.id, "session_id": session_id})
        if not doc:
            if interaction.response.is_done():
                await interaction.followup.send("Recording not found.", ephemeral=True)
            else:
                await interaction.response.send_message("Recording not found.", ephemeral=True)
            return
        count = await asyncio.to_thread(rows.count_documents, {"guild_id": interaction.guild.id, "session_id": session_id})
        incident_count = await asyncio.to_thread(incidents.count_documents, {"guild_id": interaction.guild.id, "session_id": session_id, "status": {"$ne": "merged"}})
        embed = discord.Embed(title=f"Recording • {session_id}", description=f"<#{int(doc.get('voice_channel_id') or 0)}>\nDuration: `{clock(doc.get('duration_seconds'))}` • Transcript rows: `{count}` • Incidents: `{incident_count}`")
        mid = doc.get("transcript_message_id")
        out = doc.get("output_channel_id")
        if mid and out:
            embed.add_field(name="Archive", value=f"[Open transcript message](https://discord.com/channels/{interaction.guild.id}/{out}/{mid})", inline=False)
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=SessionView(self, session_id), ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, view=SessionView(self, session_id), ephemeral=True)

    async def show_incidents(self, interaction: discord.Interaction, *, status: str | None = None) -> None:
        _, _, _, incidents = await self._collections()
        query: dict[str, Any] = {"guild_id": interaction.guild.id, "status": {"$ne": "merged"}}
        if status:
            query["status"] = status
        docs = await asyncio.to_thread(lambda: list(incidents.find(query).sort("created_at", DESCENDING).limit(15)))
        if not docs:
            await interaction.response.send_message("No matching incident cases yet.", ephemeral=True)
            return
        embed = discord.Embed(title="Evidence Queue" if status == "evidence" else "Recent Incident Cases")
        view = discord.ui.View(timeout=600)
        for doc in docs:
            iid = str(doc.get("incident_id"))
            people = " ".join(f"<@{uid}>" for uid in doc.get("involved_user_ids", [])[:4]) or "No people tagged"
            embed.add_field(
                name=f"{iid} • {doc.get('status', 'open')} • {doc.get('severity', 'unspecified')}",
                value=(
                    f"<#{int(doc.get('voice_channel_id') or 0)}> • `{clock(doc.get('window_start'))}`→`{clock(doc.get('window_end_actual', doc.get('window_end')))}`\n"
                    f"{doc.get('category', 'other')} • {people}\n{str(doc.get('reason') or 'No note')[:350]}"
                ),
                inline=False,
            )
            if len(view.children) < 5:
                button = discord.ui.Button(label=f"Open {iid}", style=discord.ButtonStyle.secondary)
                async def callback(i: discord.Interaction, incident=iid):
                    if not await _staff(i, self.bot):
                        return
                    await self.show_incident(i, incident)
                button.callback = callback
                view.add_item(button)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def show_timeout_reviews(self, interaction: discord.Interaction) -> None:
        db = self.bot.database._database  # noqa: SLF001
        coll = db["vc_timeout_escalations"]
        docs = await asyncio.to_thread(lambda: list(coll.find({"guild_id": interaction.guild.id}).sort("created_at", DESCENDING).limit(15)))
        if not docs:
            await interaction.response.send_message("No excessive-timeout review alerts yet.", ephemeral=True)
            return
        embed = discord.Embed(title="Excessive Timeout Reviews")
        for doc in docs:
            embed.add_field(
                name=f"{doc.get('display_name', 'Member')} • {doc.get('count')} timeouts",
                value=f"User: <@{int(doc.get('user_id'))}> • window: {doc.get('window_days', 7)} days\nLatest duration: {doc.get('latest_duration_minutes', 0)}m",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(StaffToolsCog(bot))
