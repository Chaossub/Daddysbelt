from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import discord
from discord.ext import commands, tasks
from pymongo import ASCENDING

log = logging.getLogger("mommy-exe.vc-automod")

DEFAULT_TRIGGER = {
    "id": "nword",
    "label": "N-word",
    "enabled": True,
    "builtin": True,
    "threshold": 3,
    "window_minutes": 10,
    "quote_lenient": True,
}

# Deliberately kept in code instead of the dashboard/config output.
_NWORD_RE = re.compile(r"(?<![a-z0-9])n+[i1!|]+[gq]+[gq]+(?:[e3]+r|[a@]+)?s?(?![a-z0-9])", re.I)
_QUOTE_HINT_RE = re.compile(
    r"\b(?:he|she|they|you|someone|somebody)\s+(?:said|called|wrote)|\b(?:quote|quoting|quoted|repeat(?:ing|ed)?)\b|\bsaid\s+the\s+word\b",
    re.I,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize(text: str) -> str:
    text = (text or "").casefold()
    text = text.replace("’", "'")
    text = re.sub(r"[\s._-]+", " ", text)
    return text


class VCAutomodCog(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        self._indexes_ready = False
        self.restore_due_roles.start()

    def cog_unload(self) -> None:
        self.restore_due_roles.cancel()

    async def _collections(self):
        db = self.bot.database._database  # noqa: SLF001 - same-project integration
        if db is None:
            raise RuntimeError("MongoDB is not connected")
        events = db["vc_automod_events"]
        punishments = db["vc_automod_punishments"]
        if not self._indexes_ready:
            await asyncio.to_thread(events.create_index, [("guild_id", ASCENDING), ("user_id", ASCENDING), ("created_at", ASCENDING)])
            await asyncio.to_thread(events.create_index, "expires_at", expireAfterSeconds=0)
            await asyncio.to_thread(punishments.create_index, [("guild_id", ASCENDING), ("user_id", ASCENDING), ("created_at", ASCENDING)])
            await asyncio.to_thread(punishments.create_index, "history_expires_at", expireAfterSeconds=0)
            self._indexes_ready = True
        return events, punishments

    async def ensure_defaults(self, guild: discord.Guild) -> dict[str, Any]:
        profile = await self.bot.database.ensure_guild_profile(guild)
        cfg = dict(profile.get("vc_automod") or {})
        triggers = list(cfg.get("triggers") or [])
        if not any(str(t.get("id")) == "nword" for t in triggers):
            triggers.insert(0, dict(DEFAULT_TRIGGER))
            await self.bot.database.set_config_path(guild.id, "vc_automod.triggers", triggers)
            cfg["triggers"] = triggers
        return cfg

    @staticmethod
    def _matches_trigger(trigger: dict[str, Any], text: str) -> bool:
        if str(trigger.get("id")) == "nword":
            compact = re.sub(r"[^a-z0-9@!|]+", "", normalize(text))
            return bool(_NWORD_RE.search(normalize(text)) or _NWORD_RE.search(compact))
        phrase = normalize(str(trigger.get("phrase") or "")).strip()
        if not phrase:
            return False
        return phrase in normalize(text)

    @staticmethod
    def _looks_quoted(text: str) -> bool:
        return bool(_QUOTE_HINT_RE.search(text or ""))

    async def is_exempt(self, member: discord.Member, cfg: dict[str, Any]) -> bool:
        if member.bot or member.id == member.guild.owner_id:
            return True
        exception_ids = {int(v) for v in cfg.get("exception_role_ids") or []}
        return any(role.id in exception_ids for role in member.roles)

    async def process_transcript(self, guild: discord.Guild, user_id: int, text: str, *, voice_channel_id: int | None = None) -> None:
        member = guild.get_member(user_id)
        if member is None or not text:
            return
        cfg = await self.ensure_defaults(guild)
        if not cfg.get("enabled", True) or await self.is_exempt(member, cfg):
            return

        retention_days = max(1, int(cfg.get("retention_days", 7) or 7))
        events, punishments = await self._collections()
        now = utcnow()

        for trigger in cfg.get("triggers") or []:
            if not trigger.get("enabled", True) or not self._matches_trigger(trigger, text):
                continue
            quoted = bool(trigger.get("quote_lenient", True)) and self._looks_quoted(text)
            event = {
                "guild_id": guild.id,
                "user_id": member.id,
                "display_name": member.display_name,
                "trigger_id": str(trigger.get("id") or "custom"),
                "trigger_label": str(trigger.get("label") or "Custom trigger"),
                "created_at": now,
                "expires_at": now + timedelta(days=retention_days),
                "voice_channel_id": voice_channel_id,
                "possible_quote": quoted,
                "consumed": False,
                # Store the transcript for mod review, but never send it publicly here.
                "transcript": text[:1000],
            }
            await asyncio.to_thread(events.insert_one, event)
            if quoted:
                log.info("VC automod possible quote logged for %s in guild %s", member.id, guild.id)
                continue

            window_minutes = max(1, int(trigger.get("window_minutes", cfg.get("detection_window_minutes", 10)) or 10))
            threshold = max(1, int(trigger.get("threshold", 3) or 3))
            cutoff = now - timedelta(minutes=window_minutes)

            def _recent():
                return list(events.find({
                    "guild_id": guild.id,
                    "user_id": member.id,
                    "trigger_id": str(trigger.get("id") or "custom"),
                    "created_at": {"$gte": cutoff},
                    "possible_quote": False,
                    "consumed": False,
                }).sort("created_at", 1))

            recent = await asyncio.to_thread(_recent)
            if len(recent) >= threshold:
                ids = [doc["_id"] for doc in recent]
                await asyncio.to_thread(events.update_many, {"_id": {"$in": ids}}, {"$set": {"consumed": True}})
                await self._punish(member, trigger, cfg, punishments)

    async def _punish(self, member: discord.Member, trigger: dict[str, Any], cfg: dict[str, Any], punishments) -> None:
        now = utcnow()
        retention_days = max(1, int(cfg.get("retention_days", 7) or 7))
        cutoff = now - timedelta(days=retention_days)
        previous = await asyncio.to_thread(
            punishments.count_documents,
            {"guild_id": member.guild.id, "user_id": member.id, "created_at": {"$gte": cutoff}},
        )
        ladder = [max(1, int(v)) for v in (cfg.get("punishment_minutes") or [5, 15, 60, 1440])]
        duration = ladder[min(previous, len(ladder) - 1)]
        restore_at = now + timedelta(minutes=duration)

        profile = await self.bot.database.get_guild_profile(member.guild.id) or {}
        verification = profile.get("verification") or {}
        role_id = verification.get("house_trained_role_id")
        removed_role_id = None
        role = member.guild.get_role(int(role_id)) if role_id else None
        reason = f"Mommy.exe VC automod: {trigger.get('label', 'configured trigger')}"

        if role is not None and role in member.roles:
            try:
                await member.remove_roles(role, reason=reason)
                removed_role_id = role.id
            except (discord.Forbidden, discord.HTTPException):
                log.exception("Could not remove House Trained from %s", member.id)
        elif role is None:
            # Safe fallback until House Trained is configured.
            try:
                await member.timeout(timedelta(minutes=duration), reason=reason)
            except (discord.Forbidden, discord.HTTPException):
                log.exception("Could not apply fallback timeout to %s", member.id)

        doc = {
            "guild_id": member.guild.id,
            "user_id": member.id,
            "display_name": member.display_name,
            "trigger_id": str(trigger.get("id") or "custom"),
            "trigger_label": str(trigger.get("label") or "Configured trigger"),
            "created_at": now,
            "history_expires_at": now + timedelta(days=retention_days),
            "restore_at": restore_at,
            "removed_role_id": removed_role_id,
            "restored": False,
            "duration_minutes": duration,
            "level": previous + 1,
        }
        await asyncio.to_thread(punishments.insert_one, doc)

        vc = member.guild.voice_client
        if vc and vc.is_connected():
            voice_cog = self.bot.get_cog("VoiceRecordingCog")
            if voice_cog and hasattr(voice_cog, "play_punishment_sound") and cfg.get("punishment_sound_enabled", True):
                try:
                    await voice_cog.play_punishment_sound(member.guild)
                except Exception:
                    log.exception("Punishment sound failed")

        # Disconnect from voice after the sound gets a chance to play.
        if member.voice and member.voice.channel:
            try:
                await member.move_to(None, reason=reason)
            except (discord.Forbidden, discord.HTTPException):
                pass

        log.warning("VC automod punished %s in %s for %s minute(s)", member.id, member.guild.id, duration)

    @tasks.loop(seconds=30)
    async def restore_due_roles(self) -> None:
        if not self.bot.is_ready() or not self.bot.database.connected:
            return
        _, punishments = await self._collections()
        now = utcnow()

        def _due():
            return list(punishments.find({"restored": False, "restore_at": {"$lte": now}}).limit(100))

        for item in await asyncio.to_thread(_due):
            guild = self.bot.get_guild(int(item["guild_id"]))
            member = guild.get_member(int(item["user_id"])) if guild else None
            role_id = item.get("removed_role_id")
            restored = True
            if guild and member and role_id:
                role = guild.get_role(int(role_id))
                if role and role not in member.roles:
                    try:
                        await member.add_roles(role, reason="Mommy.exe punishment expired")
                    except (discord.Forbidden, discord.HTTPException):
                        restored = False
                        log.exception("Could not restore House Trained to %s", member.id)
            if restored:
                await asyncio.to_thread(punishments.update_one, {"_id": item["_id"]}, {"$set": {"restored": True, "restored_at": now}})

    @restore_due_roles.before_loop
    async def before_restore(self) -> None:
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.guild_id is None or payload.user_id == getattr(self.bot.user, "id", None):
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        cfg = (await self.bot.database.get_guild_profile(guild.id) or {}).get("verification") or {}
        if not cfg.get("enabled"):
            return
        if int(cfg.get("rules_message_id") or 0) != payload.message_id:
            return
        configured = str(cfg.get("rules_emoji") or "✅")
        actual = str(payload.emoji)
        if actual != configured and payload.emoji.name != configured:
            return
        member = guild.get_member(payload.user_id)
        role = guild.get_role(int(cfg.get("house_trained_role_id") or 0))
        if member is None or member.bot or role is None or role in member.roles:
            return
        try:
            await member.add_roles(role, reason="Accepted server rules")
        except (discord.Forbidden, discord.HTTPException):
            log.exception("Could not grant House Trained to %s", payload.user_id)

    async def history_for(self, guild_id: int, user_id: int, limit: int = 12) -> list[dict[str, Any]]:
        events, _ = await self._collections()
        return await asyncio.to_thread(lambda: list(events.find({"guild_id": guild_id, "user_id": user_id}).sort("created_at", -1).limit(limit)))

    async def forgive(self, guild_id: int, user_id: int) -> None:
        events, _ = await self._collections()
        await asyncio.to_thread(events.delete_many, {"guild_id": guild_id, "user_id": user_id})

    async def restore_member(self, member: discord.Member) -> str:
        profile = await self.bot.database.get_guild_profile(member.guild.id) or {}
        role_id = (profile.get("verification") or {}).get("house_trained_role_id")
        if not role_id:
            return "House Trained is not configured."
        role = member.guild.get_role(int(role_id))
        if role is None:
            return "The configured House Trained role no longer exists."
        if role not in member.roles:
            await member.add_roles(role, reason="Manual Mommy.exe restore")
        _, punishments = await self._collections()
        await asyncio.to_thread(punishments.update_many, {"guild_id": member.guild.id, "user_id": member.id, "restored": False}, {"$set": {"restored": True, "restored_at": utcnow(), "manual": True}})
        return f"Restored {role.mention} to {member.mention}."


async def setup(bot) -> None:
    await bot.add_cog(VCAutomodCog(bot))
