from __future__ import annotations

import asyncio
import logging
import os
import re
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any

import discord
from discord.ext import commands

from services.permissions import evaluate_access

log = logging.getLogger("mommy-exe.child-vc")

DEFAULT_STRONG_PHRASES = [
    "bean wants to tell you something",
    "my kid wants to tell you something",
    "my child wants to tell you something",
    "my son wants to tell you something",
    "my daughter wants to tell you something",
    "my kid wants to say hi",
    "my child wants to say hi",
    "my son wants to say hi",
    "my daughter wants to say hi",
    "my kid wants to talk to you",
    "my child wants to talk to you",
    "my son wants to talk to you",
    "my daughter wants to talk to you",
]

# These are deliberately context clues rather than assertions about age.
# Several must occur close together before an automatic disconnect.
WEAK_CONTEXT_PATTERNS = [
    re.compile(r"\b(?:go|you need to) (?:back )?to bed\b", re.I),
    re.compile(r"\bget ready for school\b", re.I),
    re.compile(r"\bgo ask (?:your )?(?:mom|mommy|dad|daddy)\b", re.I),
    re.compile(r"\b(?:put|set) that down\b", re.I),
    re.compile(r"\bcome here (?:buddy|sweetie|baby|kiddo)\b", re.I),
    re.compile(r"\bgo tell (?:mom|mommy|dad|daddy)\b", re.I),
    re.compile(r"\b(?:my|the) (?:kid|child|son|daughter) (?:is|was|keeps|wants|said)\b", re.I),
]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").casefold().replace("’", "'")).strip()


async def _strict_admin(interaction: discord.Interaction) -> bool:
    member = interaction.user
    guild = interaction.guild
    allowed = bool(
        guild
        and isinstance(member, discord.Member)
        and (member.id == guild.owner_id or member.guild_permissions.administrator)
    )
    if allowed:
        return True
    text = "Only the server owner or an Administrator can use these 18+ VC controls."
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True)
    else:
        await interaction.response.send_message(text, ephemeral=True)
    return False


async def _moderator(interaction: discord.Interaction, bot) -> bool:
    if interaction.guild is None:
        return False
    decision = await evaluate_access(interaction, bot.database, minimum="moderator", enforce_channel=False)
    if decision.allowed:
        return True
    text = decision.reason or "You do not have permission to review this appeal."
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True)
    else:
        await interaction.response.send_message(text, ephemeral=True)
    return False


class ChildVCGuardianCog(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        self._weak_hits: dict[tuple[int, int], deque[datetime]] = defaultdict(lambda: deque(maxlen=12))
        self._context: dict[tuple[int, int], deque[str]] = defaultdict(lambda: deque(maxlen=8))
        self._cooldowns: dict[tuple[int, int], datetime] = {}

    async def _mommy_database(self):
        """Return Mommy's Mongo database even when this helper is invoked by Daddy in DMs."""
        database = self.bot.database
        db = database._database  # noqa: SLF001
        if db is None:
            raise RuntimeError("MongoDB is not connected")
        mommy_name = (os.getenv("MOMMY_MONGODB_DATABASE", "mommy_exe").strip() or "mommy_exe")
        if getattr(database, "_database_name", None) == mommy_name:
            return db
        client = getattr(database, "_client", None)
        if client is None:
            raise RuntimeError("MongoDB client is not connected")
        return client[mommy_name]

    async def _collection(self):
        db = await self._mommy_database()
        coll = db["vc_child_disconnects"]
        await asyncio.to_thread(coll.create_index, [("guild_id", 1), ("user_id", 1), ("created_at", -1)])
        await asyncio.to_thread(coll.create_index, "expires_at", expireAfterSeconds=0)
        return coll

    async def _guild_profile(self, guild_id: int) -> dict[str, Any]:
        database = self.bot.database
        mommy_name = (os.getenv("MOMMY_MONGODB_DATABASE", "mommy_exe").strip() or "mommy_exe")
        if getattr(database, "_database_name", None) == mommy_name:
            return await database.get_guild_profile(guild_id) or {}
        db = await self._mommy_database()
        return await asyncio.to_thread(db["guilds"].find_one, {"guild_id": int(guild_id)}) or {}

    async def ensure_defaults(self, guild: discord.Guild) -> dict[str, Any]:
        profile = await self.bot.database.ensure_guild_profile(guild)
        cfg = dict(profile.get("child_vc_guard") or {})
        defaults: dict[str, Any] = {
            "enabled": True,
            "exempt_user_ids": [],
            "custom_phrases": [],
            "weak_hits_required": 3,
            "weak_window_seconds": 60,
            "appeal_channel_id": None,
            "retention_days": 30,
        }
        changed = False
        for key, value in defaults.items():
            if key not in cfg:
                cfg[key] = value
                await self.bot.database.set_config_path(guild.id, f"child_vc_guard.{key}", value)
                changed = True
        if changed:
            log.info("Backfilled child VC guard defaults for guild %s", guild.id)
        return cfg

    async def is_exempt(self, member: discord.Member, cfg: dict[str, Any]) -> bool:
        if member.bot or member.id == member.guild.owner_id:
            return True
        ids: set[int] = set()
        for value in cfg.get("exempt_user_ids") or []:
            try:
                ids.add(int(value))
            except (TypeError, ValueError):
                pass
        return member.id in ids

    def _strong_match(self, text: str, cfg: dict[str, Any]) -> str | None:
        normal = _norm(text)
        phrases = list(DEFAULT_STRONG_PHRASES)
        phrases.extend(str(x).strip() for x in (cfg.get("custom_phrases") or []) if str(x).strip())
        for phrase in phrases:
            if _norm(phrase) and _norm(phrase) in normal:
                return phrase
        # Flexible variants for names other than Bean.
        patterns = [
            r"\b\w+ wants to tell you something\b",
            r"\b(?:my|our) (?:kid|child|son|daughter) wants to (?:talk|say hi|tell you something)\b",
            r"\b(?:say|tell) hi to (?:mommy|daddy|everyone|them)\b",
        ]
        for pattern in patterns:
            if re.search(pattern, normal, re.I):
                return "child-introduction context"
        return None

    @staticmethod
    def _weak_match(text: str) -> str | None:
        for pattern in WEAK_CONTEXT_PATTERNS:
            match = pattern.search(text or "")
            if match:
                return match.group(0)
        return None

    async def process_transcript(
        self,
        guild: discord.Guild,
        user_id: int,
        text: str,
        *,
        voice_channel_id: int | None = None,
    ) -> None:
        member = guild.get_member(user_id)
        if member is None or not text:
            return
        cfg = await self.ensure_defaults(guild)
        if not cfg.get("enabled", True) or await self.is_exempt(member, cfg):
            return
        key = (guild.id, member.id)
        self._context[key].append(text[:350])

        now = utcnow()
        cooldown_until = self._cooldowns.get(key)
        if cooldown_until and cooldown_until > now:
            return

        strong = self._strong_match(text, cfg)
        if strong:
            await self.disconnect_for_child(
                member,
                reason=f"Strong child-context phrase: {strong}",
                transcript=text,
                voice_channel_id=voice_channel_id,
                manual=False,
            )
            return

        weak = self._weak_match(text)
        if not weak:
            return
        hits = self._weak_hits[key]
        hits.append(now)
        window = max(15, int(cfg.get("weak_window_seconds", 60) or 60))
        cutoff = now - timedelta(seconds=window)
        while hits and hits[0] < cutoff:
            hits.popleft()
        required = max(2, int(cfg.get("weak_hits_required", 3) or 3))
        if len(hits) >= required:
            hits.clear()
            await self.disconnect_for_child(
                member,
                reason=f"Repeated child-directed context ({required} signals/{window}s)",
                transcript=text,
                voice_channel_id=voice_channel_id,
                manual=False,
            )

    async def disconnect_for_child(
        self,
        member: discord.Member,
        *,
        reason: str,
        transcript: str = "",
        voice_channel_id: int | None = None,
        manual: bool,
        moderator_id: int | None = None,
    ) -> tuple[bool, str]:
        if not member.voice or not member.voice.channel:
            return False, f"{member.display_name} is not currently in VC."
        cfg = await self.ensure_defaults(member.guild)
        if not manual and await self.is_exempt(member, cfg):
            return False, f"{member.display_name} is exempt from automatic child-VC detection."

        key = (member.guild.id, member.id)
        before_channel = member.voice.channel
        context = list(self._context.get(key, []))[-6:]
        if transcript and (not context or context[-1] != transcript[:350]):
            context.append(transcript[:350])

        try:
            await member.move_to(None, reason=f"18+ VC guard: {reason}"[:500])
        except (discord.Forbidden, discord.HTTPException) as exc:
            log.exception("Could not disconnect %s from VC", member.id)
            return False, f"Discord would not let Mommy disconnect {member.display_name}: {type(exc).__name__}."

        now = utcnow()
        self._cooldowns[key] = now + timedelta(seconds=30)
        coll = await self._collection()
        doc = {
            "guild_id": member.guild.id,
            "guild_name": member.guild.name,
            "user_id": member.id,
            "display_name": member.display_name,
            "voice_channel_id": voice_channel_id or before_channel.id,
            "voice_channel_name": before_channel.name,
            "reason": reason,
            "manual": bool(manual),
            "moderator_id": moderator_id,
            "context": context,
            "created_at": now,
            "expires_at": now + timedelta(days=max(7, int(cfg.get("retention_days", 30) or 30))),
            "appeal_status": "not_submitted",
            "appealed_at": None,
            "resolved_at": None,
            "resolved_by": None,
        }
        result = await asyncio.to_thread(coll.insert_one, doc)
        record_id = str(result.inserted_id)

        dm_text = (
            f"🔞 **Disconnected from VC — {member.guild.name}**\n\n"
            "Mommy.exe detected context that may indicate a child/minor was audible in an **18+ voice channel**, "
            "so you were disconnected from VC. This does **not** ban, kick, or timeout you from the server.\n\n"
            f"**Reason detected:** {reason}\n\n"
            "If this was wrong, press **Appeal Disconnect** below. You can also DM **`!mommy`** or **`!daddy`** to reopen your appeal button."
        )
        dm_sent = False
        try:
            await member.send(dm_text, view=ChildAppealDMView(self.bot, record_id=record_id))
            dm_sent = True
        except (discord.Forbidden, discord.HTTPException):
            pass

        if not dm_sent:
            try:
                await before_channel.send(
                    f"{member.mention} you were disconnected by the 18+ VC guard. "
                    "DM `!mommy` or `!daddy` to appeal. If this was a false detection, contact an admin.",
                    delete_after=30,
                )
            except Exception:
                pass

        await self._log_disconnect(member.guild, doc, record_id)
        return True, f"Disconnected {member.display_name} from VC for the 18+ child/minor rule."

    async def _appeal_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        profile = await self._guild_profile(guild.id)
        child_cfg = profile.get("child_vc_guard") or {}
        automod_cfg = profile.get("vc_automod") or {}
        channel_id = child_cfg.get("appeal_channel_id") or automod_cfg.get("log_channel_id") or (profile.get("logs") or {}).get("channel_id")
        channel = guild.get_channel(int(channel_id)) if channel_id else None
        if isinstance(channel, discord.TextChannel):
            return channel
        return None

    async def _log_disconnect(self, guild: discord.Guild, doc: dict[str, Any], record_id: str) -> None:
        channel = await self._appeal_channel(guild)
        if channel is None:
            return
        kind = "Manual" if doc.get("manual") else "Automatic"
        embed = discord.Embed(
            title="🔞 18+ VC Disconnect",
            description=(
                f"**Member:** <@{doc['user_id']}> (`{doc['user_id']}`)\n"
                f"**VC:** <#{doc['voice_channel_id']}>\n"
                f"**Type:** {kind}\n"
                f"**Reason:** {doc['reason']}\n\n"
                "The member was disconnected from VC only."
            ),
        )
        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def latest_appealable(self, user_id: int) -> dict[str, Any] | None:
        coll = await self._collection()
        return await asyncio.to_thread(
            coll.find_one,
            {"user_id": int(user_id), "appeal_status": {"$in": ["not_submitted", "submitted"]}},
            sort=[("created_at", -1)],
        )

    async def submit_appeal(self, user_id: int, record_id: str | None = None) -> tuple[bool, str]:
        from bson import ObjectId

        coll = await self._collection()
        query: dict[str, Any] = {"user_id": int(user_id)}
        if record_id:
            try:
                query["_id"] = ObjectId(record_id)
            except Exception:
                return False, "That appeal record is no longer valid."
        else:
            query["appeal_status"] = {"$in": ["not_submitted", "submitted"]}
        doc = await asyncio.to_thread(coll.find_one, query, sort=[("created_at", -1)])
        if not doc:
            return False, "I couldn't find a recent 18+ VC disconnect to appeal."
        if doc.get("appeal_status") == "submitted":
            return True, "Your appeal is already waiting for staff review."
        if doc.get("appeal_status") not in {"not_submitted", None}:
            return False, f"That appeal has already been resolved as **{doc.get('appeal_status')}**."

        guild = self.bot.get_guild(int(doc["guild_id"]))
        if guild is None:
            return False, "I can't reach that server right now."
        channel = await self._appeal_channel(guild)
        if channel is None:
            return False, "That server has not configured an appeal/mod channel yet."

        now = utcnow()
        await asyncio.to_thread(
            coll.update_one,
            {"_id": doc["_id"]},
            {"$set": {"appeal_status": "submitted", "appealed_at": now}},
        )
        doc["appeal_status"] = "submitted"
        doc["appealed_at"] = now
        embed = discord.Embed(
            title="🔞 VC Disconnect Appeal",
            description=(
                f"**Member:** <@{doc['user_id']}> (`{doc['user_id']}`)\n"
                f"**Original VC:** <#{doc.get('voice_channel_id')}>\n"
                f"**Disconnect:** {'manual' if doc.get('manual') else 'automatic'}\n"
                f"**Detection reason:** {doc.get('reason', 'unknown')}\n\n"
                "Review the context, then **Approve + Exempt**, **Warn**, or **Dismiss**."
            ),
        )
        await channel.send(embed=embed, view=ChildAppealModView(self.bot, record_id=str(doc["_id"])))
        return True, "✅ Your appeal was sent to the moderation channel for review."

    async def resolve_appeal(self, guild: discord.Guild, record_id: str, *, action: str, moderator_id: int) -> tuple[bool, str, dict[str, Any] | None]:
        from bson import ObjectId

        coll = await self._collection()
        try:
            oid = ObjectId(record_id)
        except Exception:
            return False, "That appeal record is invalid.", None
        doc = await asyncio.to_thread(coll.find_one, {"_id": oid, "guild_id": guild.id})
        if not doc:
            return False, "That appeal no longer exists.", None
        if doc.get("appeal_status") not in {"submitted", "not_submitted"}:
            return False, f"This appeal was already resolved as {doc.get('appeal_status')}.", doc

        status_map = {"approve": "approved_exempt", "warn": "warned", "dismiss": "dismissed"}
        status = status_map[action]
        if action == "approve":
            profile = await self.bot.database.get_guild_profile(guild.id) or {}
            ids = []
            for value in (profile.get("child_vc_guard") or {}).get("exempt_user_ids") or []:
                try:
                    ids.append(int(value))
                except (TypeError, ValueError):
                    pass
            if int(doc["user_id"]) not in ids:
                ids.append(int(doc["user_id"]))
                await self.bot.database.set_config_path(guild.id, "child_vc_guard.exempt_user_ids", ids)

        await asyncio.to_thread(coll.update_one, {"_id": oid}, {"$set": {
            "appeal_status": status,
            "resolved_at": utcnow(),
            "resolved_by": int(moderator_id),
        }})

        user = self.bot.get_user(int(doc["user_id"]))
        if user is None:
            try:
                user = await self.bot.fetch_user(int(doc["user_id"]))
            except Exception:
                user = None
        if user:
            if action == "approve":
                message = (
                    f"✅ **Your 18+ VC appeal in {guild.name} was approved.**\n"
                    "You were added to the child-VC detection exemption list, so the automatic detector will skip you going forward."
                )
            elif action == "warn":
                message = (
                    f"⚠️ **Your 18+ VC appeal in {guild.name} was reviewed.**\n"
                    "You were not added to the exemption list. Please keep children/minor audio out of the server's 18+ voice channels."
                )
            else:
                message = f"Your 18+ VC appeal in **{guild.name}** was reviewed and dismissed. You were not added to the exemption list."
            try:
                await user.send(message)
            except Exception:
                pass
        return True, status, doc


class ChildAppealDMView(discord.ui.View):
    def __init__(self, bot, *, record_id: str | None = None) -> None:
        super().__init__(timeout=86400)
        self.bot = bot
        self.record_id = record_id

    @discord.ui.button(label="Appeal Disconnect", emoji="📨", style=discord.ButtonStyle.primary)
    async def appeal(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        cog = self.bot.get_cog("ChildVCGuardianCog")
        if cog is None:
            # Daddy can render this view too; use a lightweight helper cog facade.
            cog = ChildVCGuardianCog.__new__(ChildVCGuardianCog)
            cog.bot = self.bot
            cog._weak_hits = defaultdict(lambda: deque(maxlen=12))
            cog._context = defaultdict(lambda: deque(maxlen=8))
            cog._cooldowns = {}
        await interaction.response.defer(ephemeral=True, thinking=True)
        ok, text = await cog.submit_appeal(interaction.user.id, self.record_id)
        await interaction.followup.send(text, ephemeral=True)


class ChildAppealModView(discord.ui.View):
    def __init__(self, bot, *, record_id: str) -> None:
        super().__init__(timeout=604800)
        self.bot = bot
        self.record_id = record_id

    async def _cog(self):
        cog = self.bot.get_cog("ChildVCGuardianCog")
        if cog is not None:
            return cog
        temp = ChildVCGuardianCog.__new__(ChildVCGuardianCog)
        temp.bot = self.bot
        temp._weak_hits = defaultdict(lambda: deque(maxlen=12))
        temp._context = defaultdict(lambda: deque(maxlen=8))
        temp._cooldowns = {}
        return temp

    async def _resolve(self, interaction: discord.Interaction, action: str) -> None:
        if not await _moderator(interaction, self.bot):
            return
        cog = await self._cog()
        ok, status, doc = await cog.resolve_appeal(interaction.guild, self.record_id, action=action, moderator_id=interaction.user.id)
        if not ok:
            await interaction.response.send_message(status, ephemeral=True)
            return
        label = {"approve": "✅ Approved + Exempt", "warn": "⚠️ Warned", "dismiss": "🗑️ Dismissed"}[action]
        embed = interaction.message.embeds[0] if interaction.message and interaction.message.embeds else discord.Embed(title="VC Disconnect Appeal")
        embed.add_field(name="Resolution", value=f"{label}\nBy {interaction.user.mention}", inline=False)
        await interaction.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="Approve + Exempt", emoji="✅", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._resolve(interaction, "approve")

    @discord.ui.button(label="Warn", emoji="⚠️", style=discord.ButtonStyle.danger)
    async def warn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._resolve(interaction, "warn")

    @discord.ui.button(label="Dismiss", emoji="🗑️", style=discord.ButtonStyle.secondary)
    async def dismiss(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._resolve(interaction, "dismiss")

    @discord.ui.button(label="View Context", emoji="🧾", style=discord.ButtonStyle.secondary)
    async def context(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _moderator(interaction, self.bot):
            return
        from bson import ObjectId
        cog = await self._cog()
        coll = await cog._collection()
        try:
            doc = await asyncio.to_thread(coll.find_one, {"_id": ObjectId(self.record_id)})
        except Exception:
            doc = None
        if not doc:
            await interaction.response.send_message("Context is no longer available.", ephemeral=True)
            return
        lines = [f"• {x}" for x in (doc.get("context") or [])]
        await interaction.response.send_message("**Recent transcript context:**\n" + ("\n".join(lines) or "No transcript context was stored."), ephemeral=True)


class ChildExemptUserSelect(discord.ui.UserSelect):
    def __init__(self, parent, *, action: str):
        super().__init__(placeholder="Choose a member…", min_values=1, max_values=1)
        self.parent_view = parent
        self.action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _strict_admin(interaction):
            return
        selected = self.values[0]
        member = interaction.guild.get_member(int(selected.id)) if interaction.guild else None
        if member is None:
            await interaction.response.send_message("That member is not in this server.", ephemeral=True)
            return
        profile = await self.parent_view.bot.database.get_guild_profile(interaction.guild.id) or {}
        ids = []
        for value in (profile.get("child_vc_guard") or {}).get("exempt_user_ids") or []:
            try:
                ids.append(int(value))
            except (TypeError, ValueError):
                pass
        if self.action == "add":
            if member.id not in ids:
                ids.append(member.id)
            msg = f"✅ {member.mention} is now exempt from **automatic** child-VC detection. Manual admin disconnect still works."
        else:
            ids = [uid for uid in ids if uid != member.id]
            msg = f"Removed {member.mention} from the child-VC exemption list."
        await self.parent_view.bot.database.set_config_path(interaction.guild.id, "child_vc_guard.exempt_user_ids", ids)
        await interaction.response.send_message(msg, ephemeral=True)


class ChildExemptSelectView(discord.ui.View):
    def __init__(self, bot, *, action: str):
        super().__init__(timeout=300)
        self.bot = bot
        self.add_item(ChildExemptUserSelect(self, action=action))


class ManualChildDisconnectSelect(discord.ui.UserSelect):
    def __init__(self, parent):
        super().__init__(placeholder="Choose a member currently in VC…", min_values=1, max_values=1)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _strict_admin(interaction):
            return
        member = interaction.guild.get_member(int(self.values[0].id)) if interaction.guild else None
        if member is None or not member.voice or not member.voice.channel:
            await interaction.response.send_message("That member is not currently in a voice channel.", ephemeral=True)
            return
        cog = self.parent_view.bot.get_cog("ChildVCGuardianCog")
        if cog is None:
            await interaction.response.send_message("The 18+ VC guard is not loaded.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        ok, msg = await cog.disconnect_for_child(
            member,
            reason="Manually flagged by an administrator for suspected child/minor audio",
            voice_channel_id=member.voice.channel.id,
            transcript="",
            manual=True,
            moderator_id=interaction.user.id,
        )
        await interaction.followup.send(msg, ephemeral=True)


class ManualChildDisconnectView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=300)
        self.bot = bot
        self.add_item(ManualChildDisconnectSelect(self))


class AppealChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, parent):
        super().__init__(placeholder="Choose moderation/appeal channel…", channel_types=[discord.ChannelType.text], min_values=1, max_values=1)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _strict_admin(interaction):
            return
        channel = interaction.guild.get_channel(int(self.values[0].id)) if interaction.guild else None
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Choose a text channel.", ephemeral=True)
            return
        await self.parent_view.bot.database.set_config_path(interaction.guild.id, "child_vc_guard.appeal_channel_id", channel.id)
        await interaction.response.send_message(f"Appeals and child-VC notices will go to {channel.mention}.", ephemeral=True)


class AppealChannelView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=300)
        self.bot = bot
        self.add_item(AppealChannelSelect(self))


class AddChildPhraseModal(discord.ui.Modal, title="Add Child-Context Phrase"):
    phrase = discord.ui.TextInput(
        label="Phrase",
        placeholder="Example: Bean wants to tell you something",
        min_length=3,
        max_length=160,
    )

    def __init__(self, bot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _strict_admin(interaction):
            return
        profile = await self.bot.database.get_guild_profile(interaction.guild.id) or {}
        phrases = [str(x).strip() for x in (profile.get("child_vc_guard") or {}).get("custom_phrases") or [] if str(x).strip()]
        value = str(self.phrase).strip()
        if value.casefold() not in {x.casefold() for x in phrases}:
            phrases.append(value)
        await self.bot.database.set_config_path(interaction.guild.id, "child_vc_guard.custom_phrases", phrases)
        await interaction.response.send_message(f"Added child-context phrase: `{value}`", ephemeral=True)


class RemovePhraseSelect(discord.ui.Select):
    def __init__(self, parent, phrases: list[str]):
        options = [discord.SelectOption(label=p[:100], value=str(i)) for i, p in enumerate(phrases[:25])]
        super().__init__(placeholder="Choose a custom phrase to remove…", options=options, min_values=1, max_values=1)
        self.parent_view = parent
        self.phrases = phrases

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _strict_admin(interaction):
            return
        idx = int(self.values[0])
        removed = self.phrases[idx]
        remaining = [p for i, p in enumerate(self.phrases) if i != idx]
        await self.parent_view.bot.database.set_config_path(interaction.guild.id, "child_vc_guard.custom_phrases", remaining)
        await interaction.response.send_message(f"Removed `{removed}`.", ephemeral=True)


class RemovePhraseView(discord.ui.View):
    def __init__(self, bot, phrases: list[str]):
        super().__init__(timeout=300)
        self.bot = bot
        self.add_item(RemovePhraseSelect(self, phrases))


class ChildVCAdminView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=600)
        self.bot = bot

    @discord.ui.button(label="Enable / Disable", emoji="🔞", style=discord.ButtonStyle.primary, row=0)
    async def toggle(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _strict_admin(interaction):
            return
        profile = await self.bot.database.get_guild_profile(interaction.guild.id) or {}
        current = bool((profile.get("child_vc_guard") or {}).get("enabled", True))
        await self.bot.database.set_config_path(interaction.guild.id, "child_vc_guard.enabled", not current)
        await interaction.response.edit_message(embed=await child_vc_admin_embed(self.bot, interaction.guild), view=self)

    @discord.ui.button(label="Disconnect for Minor in VC", emoji="⛔", style=discord.ButtonStyle.danger, row=0)
    async def manual(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _strict_admin(interaction):
            return
        await interaction.response.send_message(
            "Choose the participant to disconnect for suspected child/minor audio. This is VC-only; it does not kick or ban them from the server.",
            view=ManualChildDisconnectView(self.bot),
            ephemeral=True,
        )

    @discord.ui.button(label="Add Exemption", emoji="➕", style=discord.ButtonStyle.success, row=1)
    async def add_exempt(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _strict_admin(interaction): return
        await interaction.response.send_message("Choose the member to exempt from automatic detection.", view=ChildExemptSelectView(self.bot, action="add"), ephemeral=True)

    @discord.ui.button(label="Remove Exemption", emoji="➖", style=discord.ButtonStyle.secondary, row=1)
    async def remove_exempt(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _strict_admin(interaction): return
        await interaction.response.send_message("Choose the member to remove from exemptions.", view=ChildExemptSelectView(self.bot, action="remove"), ephemeral=True)

    @discord.ui.button(label="Context Phrases", emoji="🗣️", style=discord.ButtonStyle.secondary, row=1)
    async def phrases(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _strict_admin(interaction): return
        profile = await self.bot.database.get_guild_profile(interaction.guild.id) or {}
        custom = [str(x).strip() for x in (profile.get("child_vc_guard") or {}).get("custom_phrases") or [] if str(x).strip()]
        view = ChildPhraseMenuView(self.bot, custom)
        desc = "**Built-in strong phrases include:**\n• `Bean wants to tell you something`\n• `my kid/child/son/daughter wants to say hi/talk/tell you something`\n\n**Custom phrases:**\n" + ("\n".join(f"• `{x}`" for x in custom[:20]) or "None yet.")
        await interaction.response.send_message(embed=discord.Embed(title="18+ VC Guard • Context Phrases", description=desc), view=view, ephemeral=True)

    @discord.ui.button(label="Appeal Channel", emoji="#️⃣", style=discord.ButtonStyle.secondary, row=2)
    async def channel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _strict_admin(interaction): return
        await interaction.response.send_message("Choose the private mod channel that should receive disconnect notices and appeals.", view=AppealChannelView(self.bot), ephemeral=True)


class ChildPhraseMenuView(discord.ui.View):
    def __init__(self, bot, phrases: list[str]):
        super().__init__(timeout=300)
        self.bot = bot
        self.phrases = phrases

    @discord.ui.button(label="Add Phrase", emoji="➕", style=discord.ButtonStyle.success)
    async def add(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _strict_admin(interaction): return
        await interaction.response.send_modal(AddChildPhraseModal(self.bot))

    @discord.ui.button(label="Remove Phrase", emoji="➖", style=discord.ButtonStyle.danger)
    async def remove(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await _strict_admin(interaction): return
        if not self.phrases:
            await interaction.response.send_message("There are no custom phrases to remove.", ephemeral=True)
            return
        await interaction.response.send_message("Choose a phrase to remove.", view=RemovePhraseView(self.bot, self.phrases), ephemeral=True)


async def child_vc_admin_embed(bot, guild: discord.Guild) -> discord.Embed:
    cog = bot.get_cog("ChildVCGuardianCog")
    cfg = await cog.ensure_defaults(guild) if cog else ((await bot.database.get_guild_profile(guild.id) or {}).get("child_vc_guard") or {})
    exempt_ids = [int(v) for v in (cfg.get("exempt_user_ids") or []) if str(v).isdigit()]
    channel_id = cfg.get("appeal_channel_id")
    return discord.Embed(
        title="Mommy.exe • Auto Mod • 18+ VC Guard",
        description=(
            f"**Automatic detection:** {'ON' if cfg.get('enabled', True) else 'OFF'}\n"
            f"**Exempt members:** {len(exempt_ids)}\n"
            f"**Custom child-context phrases:** {len(cfg.get('custom_phrases') or [])}\n"
            f"**Appeal/mod channel:** {f'<#{channel_id}>' if channel_id else 'not set'}\n\n"
            "Automatic detections disconnect from **voice only**. No server kick, ban, or timeout is applied.\n"
            "A strong phrase such as **“Bean wants to tell you something”** can trigger immediately; weaker parent-to-child context must repeat before Mommy disconnects.\n\n"
            "**Manual Disconnect** works even for exempt members."
        ),
    )


async def maybe_send_child_appeal_menu(bot, message: discord.Message) -> bool:
    """Handle !mommy / !daddy in DMs as an appeal-menu shortcut."""
    if message.guild is not None:
        return False
    text = message.content.strip().casefold()
    if text not in {"!mommy", "!daddy"}:
        return False
    cog = bot.get_cog("ChildVCGuardianCog")
    if cog is None:
        cog = ChildVCGuardianCog.__new__(ChildVCGuardianCog)
        cog.bot = bot
        cog._weak_hits = defaultdict(lambda: deque(maxlen=12))
        cog._context = defaultdict(lambda: deque(maxlen=8))
        cog._cooldowns = {}
    doc = await cog.latest_appealable(message.author.id)
    if not doc:
        await message.channel.send("I don't see a recent 18+ VC disconnect waiting for an appeal.")
        return True
    guild_name = doc.get("guild_name") or "the server"
    status = doc.get("appeal_status") or "not_submitted"
    await message.channel.send(
        f"🔞 **18+ VC disconnect — {guild_name}**\nStatus: **{status.replace('_', ' ')}**\n\nPress below to send/reopen the appeal workflow.",
        view=ChildAppealDMView(bot, record_id=str(doc["_id"])),
    )
    return True


async def setup(bot) -> None:
    await bot.add_cog(ChildVCGuardianCog(bot))
