from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands
from pymongo import ASCENDING, DESCENDING

from services.permissions import deny_access, evaluate_access

log = logging.getLogger("mommy-exe.staff-tools")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def clock(seconds: float | int | None) -> str:
    value = max(0, int(seconds or 0))
    h, rem = divmod(value, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


async def _staff(interaction: discord.Interaction, bot, *, minimum: str = "moderator") -> bool:
    decision = await evaluate_access(interaction, bot.database, minimum=minimum, enforce_channel=False)
    if decision.allowed:
        return True
    await deny_access(interaction, decision.reason)
    return False


class PhraseSearchModal(discord.ui.Modal, title="Search VC transcripts"):
    phrase = discord.ui.TextInput(label="Phrase or words", placeholder="Example: I never said that", max_length=200)
    user = discord.ui.TextInput(label="User ID (optional)", placeholder="Numeric Discord user ID", required=False, max_length=24)
    days = discord.ui.TextInput(label="Days back (optional)", placeholder="30", required=False, max_length=4)

    def __init__(self, cog: "StaffToolsCog") -> None:
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _staff(interaction, self.cog.bot):
            return
        uid: int | None = None
        if str(self.user).strip():
            try:
                uid = int(str(self.user).strip())
            except ValueError:
                await interaction.response.send_message("User ID must be numeric.", ephemeral=True)
                return
        days = 30
        if str(self.days).strip():
            try:
                days = max(1, min(3650, int(str(self.days).strip())))
            except ValueError:
                await interaction.response.send_message("Days must be a number.", ephemeral=True)
                return
        await interaction.response.defer(ephemeral=True, thinking=True)
        rows = await self.cog.search_transcripts(interaction.guild.id, str(self.phrase).strip(), user_id=uid, days=days)
        if not rows:
            await interaction.followup.send("No matching transcript lines found.", ephemeral=True)
            return
        embed = discord.Embed(title="Transcript Search", description=f"Matches for **{discord.utils.escape_markdown(str(self.phrase))}**", color=discord.Color.blurple())
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

    def __init__(self, cog: "StaffToolsCog") -> None:
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _staff(interaction, self.cog.bot):
            return
        ok, message = await self.cog.bookmark_active(interaction, reason=str(self.reason).strip())
        await interaction.response.send_message(message, ephemeral=True)


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
        if not await _staff(interaction, self.cog.bot): return
        await interaction.response.send_modal(AddNoteModal(self.cog, self.session_id))

    @discord.ui.button(label="Notes", emoji="📌", style=discord.ButtonStyle.secondary)
    async def notes(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _staff(interaction, self.cog.bot): return
        notes = await self.cog.notes_for(interaction.guild.id, self.session_id)
        if not notes:
            await interaction.response.send_message("No notes on this recording yet.", ephemeral=True); return
        embed = discord.Embed(title=f"Notes • {self.session_id}")
        for note in notes[:15]:
            when = f" @ {clock(note.get('offset'))}" if note.get("offset") is not None else ""
            embed.add_field(name=f"{note.get('author_name','Moderator')}{when}", value=str(note.get("text") or "")[:900], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Search This Session", emoji="🔎", style=discord.ButtonStyle.secondary)
    async def search(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _staff(interaction, self.cog.bot): return
        await interaction.response.send_modal(SessionSearchModal(self.cog, self.session_id))


class SessionSearchModal(discord.ui.Modal, title="Search this recording"):
    phrase = discord.ui.TextInput(label="Phrase or words", max_length=200)
    def __init__(self, cog: "StaffToolsCog", session_id: str) -> None:
        super().__init__(); self.cog=cog; self.session_id=session_id
    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _staff(interaction, self.cog.bot): return
        rows = await self.cog.search_transcripts(interaction.guild.id, str(self.phrase).strip(), session_id=self.session_id, days=3650)
        if not rows:
            await interaction.response.send_message("No matches in that recording.", ephemeral=True); return
        embed=discord.Embed(title=f"Search • {self.session_id}")
        for row in rows[:15]:
            embed.add_field(name=f"{row.get('display_name')} • {clock(row.get('start_offset'))}", value=str(row.get('text') or '')[:700], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class IncidentView(discord.ui.View):
    def __init__(self, cog: "StaffToolsCog", incident_id: str, session_id: str | None) -> None:
        super().__init__(timeout=900)
        self.cog = cog
        self.incident_id = incident_id
        self.session_id = session_id

    @discord.ui.button(label="Add Note", emoji="📝", style=discord.ButtonStyle.primary)
    async def note(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _staff(interaction, self.cog.bot): return
        if not self.session_id:
            await interaction.response.send_message("This incident is still waiting for its recording session to finish.", ephemeral=True); return
        await interaction.response.send_modal(AddNoteModal(self.cog, self.session_id, self.incident_id))

    @discord.ui.button(label="Mark Evidence", emoji="📌", style=discord.ButtonStyle.secondary)
    async def evidence(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _staff(interaction, self.cog.bot): return
        await self.cog.set_incident_status(interaction.guild.id, self.incident_id, "evidence")
        await interaction.response.send_message(f"📌 {self.incident_id} marked as evidence.", ephemeral=True)

    @discord.ui.button(label="Close", emoji="✅", style=discord.ButtonStyle.success)
    async def close(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _staff(interaction, self.cog.bot): return
        await self.cog.set_incident_status(interaction.guild.id, self.incident_id, "closed")
        await interaction.response.send_message(f"✅ {self.incident_id} closed.", ephemeral=True)

class StaffDashboardView(discord.ui.View):
    def __init__(self, cog: "StaffToolsCog") -> None:
        super().__init__(timeout=1200)
        self.cog = cog

    @discord.ui.button(label="Search Transcripts", emoji="🔎", style=discord.ButtonStyle.primary, row=0)
    async def search(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _staff(interaction, self.cog.bot): return
        await interaction.response.send_modal(PhraseSearchModal(self.cog))

    @discord.ui.button(label="Recent Recordings", emoji="🎙️", style=discord.ButtonStyle.secondary, row=0)
    async def recent(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _staff(interaction, self.cog.bot): return
        await self.cog.show_recent(interaction)

    @discord.ui.button(label="Bookmark Incident", emoji="🚩", style=discord.ButtonStyle.danger, row=0)
    async def bookmark(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _staff(interaction, self.cog.bot): return
        await interaction.response.send_modal(IncidentReasonModal(self.cog))

    @discord.ui.button(label="Recent Incidents", emoji="📂", style=discord.ButtonStyle.secondary, row=1)
    async def incidents(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _staff(interaction, self.cog.bot): return
        await self.cog.show_incidents(interaction)

    @discord.ui.button(label="Timeout Reviews", emoji="⏱️", style=discord.ButtonStyle.secondary, row=1)
    async def timeout_reviews(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _staff(interaction, self.cog.bot): return
        await self.cog.show_timeout_reviews(interaction)


class StaffToolsCog(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        self._indexes_ready = False

    async def _collections(self):
        db = self.bot.database._database  # noqa: SLF001
        if db is None: raise RuntimeError("MongoDB is not connected")
        sessions=db["vc_recording_sessions"]; rows=db["vc_transcript_rows"]; notes=db["vc_recording_notes"]; incidents=db["vc_incidents"]
        if not self._indexes_ready:
            await asyncio.to_thread(sessions.create_index, [("guild_id", ASCENDING),("started_at", DESCENDING)])
            await asyncio.to_thread(rows.create_index, [("guild_id", ASCENDING),("session_id", ASCENDING),("start_offset", ASCENDING)])
            await asyncio.to_thread(rows.create_index, [("guild_id", ASCENDING),("user_id", ASCENDING),("created_at", DESCENDING)])
            await asyncio.to_thread(notes.create_index, [("guild_id", ASCENDING),("session_id", ASCENDING),("created_at", ASCENDING)])
            await asyncio.to_thread(incidents.create_index, [("guild_id", ASCENDING),("created_at", DESCENDING)])
            self._indexes_ready=True
        return sessions,rows,notes,incidents

    @app_commands.command(name="staff", description="Open Mommy.exe's private VC moderation and recording dashboard.")
    @app_commands.guild_only()
    async def staff(self, interaction: discord.Interaction) -> None:
        if not await _staff(interaction, self.bot): return
        embed=discord.Embed(title="Mommy.exe • Staff Console", description="Private tools for recordings, transcript search, incident bookmarks, notes, and timeout review.", color=discord.Color.dark_teal())
        embed.add_field(name="Recording Archive", value="Search every saved end-of-session transcript or open recent recordings.", inline=False)
        embed.add_field(name="Incident Bookmark", value="Marks **5 minutes before + 2 minutes after** the current point in an active recording.", inline=False)
        embed.set_footer(text="Visible/useable only by configured moderators, admins, and the server owner.")
        await interaction.response.send_message(embed=embed, view=StaffDashboardView(self), ephemeral=True)

    @app_commands.command(name="incident-bookmark", description="Bookmark a 7-minute moderation window in the active VC recording.")
    @app_commands.guild_only()
    async def incident_bookmark(self, interaction: discord.Interaction, reason: str = "") -> None:
        if not await _staff(interaction, self.bot): return
        ok,msg=await self.bookmark_active(interaction, reason=reason)
        await interaction.response.send_message(msg, ephemeral=True)

    async def archive_session(self, guild: discord.Guild, session, transcript_rows: list[tuple[float,int,str,str]], *, transcript_message_id: int | None = None, audio_message_ids: list[int] | None = None) -> str:
        sessions,rows,_,_=await self._collections()
        session_id=f"VC-{session.started_at.strftime('%Y%m%d')}-{str(int(session.started_at.timestamp()))[-6:]}"
        doc={"guild_id":guild.id,"session_id":session_id,"voice_channel_id":session.voice_channel_id,"output_channel_id":session.output_channel_id,"started_by_id":session.started_by_id,"started_at":session.started_at,"ended_at":utcnow(),"duration_seconds":session.duration,"speaker_count":len(session.tracks),"transcript_message_id":transcript_message_id,"audio_message_ids":audio_message_ids or [],"created_at":utcnow()}
        await asyncio.to_thread(sessions.update_one,{"guild_id":guild.id,"session_id":session_id},{"$set":doc},upsert=True)
        if transcript_rows:
            docs=[]
            for offset,uid,name,text in transcript_rows:
                docs.append({"guild_id":guild.id,"session_id":session_id,"voice_channel_id":session.voice_channel_id,"user_id":uid,"display_name":name,"start_offset":float(offset),"text":text,"text_normalized":re.sub(r"\s+"," ",text.casefold()).strip(),"created_at":session.started_at+timedelta(seconds=float(offset))})
            await asyncio.to_thread(rows.delete_many,{"guild_id":guild.id,"session_id":session_id})
            await asyncio.to_thread(rows.insert_many,docs)
        return session_id

    async def search_transcripts(self,guild_id:int,phrase:str,*,user_id:int|None=None,days:int=30,session_id:str|None=None)->list[dict[str,Any]]:
        _,rows,_,_=await self._collections()
        phrase_norm=re.sub(r"\s+"," ",phrase.casefold()).strip()
        if not phrase_norm:return []
        query:dict[str,Any]={"guild_id":guild_id,"text_normalized":{"$regex":re.escape(phrase_norm)}}
        if user_id is not None:query["user_id"]=user_id
        if session_id:query["session_id"]=session_id
        else:query["created_at"]={"$gte":utcnow()-timedelta(days=days)}
        return await asyncio.to_thread(lambda:list(rows.find(query).sort("created_at",DESCENDING).limit(50)))

    async def add_note(self,*,guild_id:int,session_id:str,incident_id:str|None,author:discord.abc.User,text:str,offset:int|None)->None:
        _,_,notes,_=await self._collections()
        await asyncio.to_thread(notes.insert_one,{"guild_id":guild_id,"session_id":session_id,"incident_id":incident_id,"author_id":author.id,"author_name":getattr(author,"display_name",author.name),"text":text,"offset":offset,"created_at":utcnow()})

    async def notes_for(self,guild_id:int,session_id:str)->list[dict[str,Any]]:
        _,_,notes,_=await self._collections()
        return await asyncio.to_thread(lambda:list(notes.find({"guild_id":guild_id,"session_id":session_id}).sort("created_at",ASCENDING).limit(50)))

    async def bookmark_active(self,interaction:discord.Interaction,*,reason:str="")->tuple[bool,str]:
        vc_cog=self.bot.get_cog("VoiceRecordingCog")
        session=getattr(vc_cog,"sessions",{}).get(interaction.guild.id) if vc_cog else None
        if session is None:return False,"There isn't an active recording to bookmark right now."
        _,_,_,incidents=await self._collections()
        offset=float(session.duration); now=utcnow()
        seq=await asyncio.to_thread(incidents.count_documents,{"guild_id":interaction.guild.id})+1
        incident_id=f"INC-{seq:04d}"
        doc={"guild_id":interaction.guild.id,"incident_id":incident_id,"session_started_at":session.started_at,"session_id":None,"voice_channel_id":session.voice_channel_id,"bookmarked_by_id":interaction.user.id,"bookmarked_by_name":getattr(interaction.user,"display_name",interaction.user.name),"bookmark_offset":offset,"window_start":max(0.0,offset-300.0),"window_end":offset+120.0,"reason":reason,"status":"open","created_at":now}
        await asyncio.to_thread(incidents.insert_one,doc)
        return True,f"🚩 **{incident_id}** bookmarked. Window: `{clock(doc['window_start'])}` → `{clock(doc['window_end'])}` (5 minutes before + 2 after)."

    async def attach_incidents_to_session(self,guild_id:int,session,session_id:str)->list[dict[str,Any]]:
        _,_,_,incidents=await self._collections()
        query={"guild_id":guild_id,"session_id":None,"session_started_at":session.started_at}
        docs=await asyncio.to_thread(lambda:list(incidents.find(query)))
        if docs:
            await asyncio.to_thread(incidents.update_many, query, {"$set": {"session_id": session_id, "session_duration_seconds": session.duration}})
        return docs

    async def set_incident_status(self, guild_id: int, incident_id: str, status: str) -> None:
        _,_,_,incidents = await self._collections()
        await asyncio.to_thread(
            incidents.update_one,
            {"guild_id": guild_id, "incident_id": incident_id},
            {"$set": {"status": status, "status_updated_at": utcnow()}},
        )

    async def materialize_incidents(self, guild: discord.Guild, output: discord.TextChannel, session, session_id: str, master_wav: Path) -> None:
        """Create the bookmarked 7-minute audio clips once the source session is finalized."""
        _, rows, _, incidents = await self._collections()
        docs = await asyncio.to_thread(lambda: list(incidents.find({"guild_id": guild.id, "session_id": session_id, "clip_message_id": {"$exists": False}})))
        for doc in docs:
            start = max(0.0, float(doc.get("window_start", 0.0) or 0.0))
            end = min(float(session.duration), float(doc.get("window_end", session.duration) or session.duration))
            if end <= start:
                continue
            out_path = session.base_dir / f"{doc.get('incident_id')}.mp3"
            cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", str(start), "-to", str(end), "-i", str(master_wav), "-codec:a", "libmp3lame", "-b:a", "96k", str(out_path)]
            try:
                await asyncio.to_thread(subprocess.run, cmd, check=True, capture_output=True)
            except Exception:
                log.exception("Could not render incident clip %s", doc.get("incident_id"))
                continue
            excerpt_rows = await asyncio.to_thread(lambda: list(rows.find({
                "guild_id": guild.id,
                "session_id": session_id,
                "start_offset": {"$gte": start, "$lte": end},
            }).sort("start_offset", ASCENDING).limit(20)))
            excerpt = "\n".join(
                f"`{clock(r.get('start_offset'))}` **{r.get('display_name','Speaker')}:** {str(r.get('text') or '')[:220]}"
                for r in excerpt_rows
            )
            reason = str(doc.get("reason") or "No reason supplied")
            content = f"🚩 **{doc.get('incident_id')}** • <#{session.voice_channel_id}> • `{clock(start)}` → `{clock(end)}`\n**Reason:** {reason[:500]}"
            if excerpt:
                content += "\n\n**Transcript preview**\n" + excerpt[:1200]
            try:
                msg = await output.send(content[:1900], file=discord.File(out_path, filename=out_path.name))
                await asyncio.to_thread(incidents.update_one, {"_id": doc["_id"]}, {"$set": {"clip_message_id": msg.id, "clip_channel_id": output.id, "window_end_actual": end, "materialized_at": utcnow()}})
            except (discord.Forbidden, discord.HTTPException):
                log.exception("Could not post incident clip %s", doc.get("incident_id"))

    async def show_incident(self, interaction: discord.Interaction, incident_id: str) -> None:
        _,_,_,incidents = await self._collections()
        doc = await asyncio.to_thread(incidents.find_one, {"guild_id": interaction.guild.id, "incident_id": incident_id})
        if not doc:
            await interaction.response.send_message("Incident not found.", ephemeral=True); return
        sid = doc.get("session_id")
        desc = f"<#{int(doc.get('voice_channel_id') or 0)}> • `{clock(doc.get('window_start'))}` → `{clock(doc.get('window_end_actual', doc.get('window_end')) )}`\nStatus: **{doc.get('status','open')}**\n{str(doc.get('reason') or 'No reason supplied')[:700]}"
        embed = discord.Embed(title=str(doc.get("incident_id")), description=desc, color=discord.Color.orange())
        if doc.get("clip_message_id") and doc.get("clip_channel_id"):
            embed.add_field(name="Clip", value=f"[Open incident clip](https://discord.com/channels/{interaction.guild.id}/{doc.get('clip_channel_id')}/{doc.get('clip_message_id')})", inline=False)
        await interaction.response.send_message(embed=embed, view=IncidentView(self, incident_id, sid), ephemeral=True)

    async def show_recent(self, interaction:discord.Interaction)->None:
        sessions,_,_,_=await self._collections()
        docs=await asyncio.to_thread(lambda:list(sessions.find({"guild_id":interaction.guild.id}).sort("started_at",DESCENDING).limit(10)))
        if not docs:
            await interaction.response.send_message("No archived recording sessions yet.",ephemeral=True);return
        embed=discord.Embed(title="Recent VC Recordings")
        view=discord.ui.View(timeout=600)
        for doc in docs:
            sid=str(doc.get("session_id")); embed.add_field(name=sid,value=f"<#{int(doc.get('voice_channel_id') or 0)}> • `{clock(doc.get('duration_seconds'))}` • {doc.get('speaker_count',0)} speaker(s)",inline=False)
            if len(view.children)<5:
                b=discord.ui.Button(label=f"Open {sid[-6:]}",style=discord.ButtonStyle.secondary)
                async def cb(i:discord.Interaction,s=sid):
                    if not await _staff(i,self.bot):return
                    await self.show_session(i,s)
                b.callback=cb;view.add_item(b)
        await interaction.response.send_message(embed=embed,view=view,ephemeral=True)

    async def show_session(self,interaction:discord.Interaction,session_id:str)->None:
        sessions,rows,_,_=await self._collections()
        doc=await asyncio.to_thread(sessions.find_one,{"guild_id":interaction.guild.id,"session_id":session_id})
        if not doc:
            if interaction.response.is_done(): await interaction.followup.send("Recording not found.",ephemeral=True)
            else: await interaction.response.send_message("Recording not found.",ephemeral=True)
            return
        count=await asyncio.to_thread(rows.count_documents,{"guild_id":interaction.guild.id,"session_id":session_id})
        embed=discord.Embed(title=f"Recording • {session_id}",description=f"<#{int(doc.get('voice_channel_id') or 0)}>\nDuration: `{clock(doc.get('duration_seconds'))}` • Transcript rows: `{count}`")
        mid=doc.get("transcript_message_id");out=doc.get("output_channel_id")
        if mid and out: embed.add_field(name="Archive",value=f"[Open transcript message](https://discord.com/channels/{interaction.guild.id}/{out}/{mid})",inline=False)
        if interaction.response.is_done(): await interaction.followup.send(embed=embed,view=SessionView(self,session_id),ephemeral=True)
        else: await interaction.response.send_message(embed=embed,view=SessionView(self,session_id),ephemeral=True)

    async def show_incidents(self,interaction:discord.Interaction)->None:
        _,_,_,incidents=await self._collections()
        docs=await asyncio.to_thread(lambda:list(incidents.find({"guild_id":interaction.guild.id}).sort("created_at",DESCENDING).limit(15)))
        if not docs:
            await interaction.response.send_message("No incident bookmarks yet.",ephemeral=True);return
        embed=discord.Embed(title="Recent Incident Bookmarks")
        view=discord.ui.View(timeout=600)
        for d in docs:
            iid=str(d.get('incident_id'))
            embed.add_field(name=f"{iid} • {d.get('status','open')}",value=f"<#{int(d.get('voice_channel_id') or 0)}> • `{clock(d.get('window_start'))}`→`{clock(d.get('window_end_actual', d.get('window_end')) )}`\n{str(d.get('reason') or 'No note')[:500]}",inline=False)
            if len(view.children)<5:
                b=discord.ui.Button(label=f"Open {iid}",style=discord.ButtonStyle.secondary)
                async def cb(i:discord.Interaction, incident=iid):
                    if not await _staff(i,self.bot): return
                    await self.show_incident(i,incident)
                b.callback=cb; view.add_item(b)
        await interaction.response.send_message(embed=embed,view=view,ephemeral=True)

    async def show_timeout_reviews(self,interaction:discord.Interaction)->None:
        db=self.bot.database._database  # noqa: SLF001
        coll=db["vc_timeout_escalations"]
        docs=await asyncio.to_thread(lambda:list(coll.find({"guild_id":interaction.guild.id}).sort("created_at",DESCENDING).limit(15)))
        if not docs:
            await interaction.response.send_message("No excessive-timeout review alerts yet.",ephemeral=True);return
        embed=discord.Embed(title="Excessive Timeout Reviews")
        for d in docs:
            embed.add_field(name=f"{d.get('display_name','Member')} • {d.get('count')} timeouts",value=f"User: <@{int(d.get('user_id'))}> • window: {d.get('window_days',7)} days\nLatest duration: {d.get('latest_duration_minutes',0)}m",inline=False)
        await interaction.response.send_message(embed=embed,ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(StaffToolsCog(bot))
