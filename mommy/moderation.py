from __future__ import annotations

import asyncio
import logging
import re
import random
import unicodedata
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
    "points": 1,
    "history_days": 7,
    "quote_lenient": True,
    "spoken_warning": False,
    "warning_style": "random",
    "warning_text": "",
}

DEFAULT_PUNISHMENT_LADDER = [
    {"threshold": 1, "action": "remove_timeout", "duration_minutes": 5, "play_sound": True, "reset_score": False},
    {"threshold": 2, "action": "remove_timeout", "duration_minutes": 15, "play_sound": True, "reset_score": False},
    {"threshold": 3, "action": "remove_timeout", "duration_minutes": 60, "play_sound": True, "reset_score": False},
    {"threshold": 4, "action": "remove_timeout", "duration_minutes": 1440, "play_sound": True, "reset_score": False},
]

VALID_PUNISHMENT_ACTIONS = {"warning", "remove_role", "timeout", "remove_timeout", "kick", "ban"}

DEFAULT_WARNING_LINES = [
    "Watch it. That's your warning.",
    "Careful. You're pushing it.",
    "That's one. Don't make me count higher.",
    "Try that again and see what happens.",
    "Language. Consider yourself warned.",
    "Knock it off.",
    "You know better than that.",
    "I heard you. Don't do it again.",
    "That was your free one.",
    "Cute. Now stop.",
    "Keep going and I'll handle it.",
    "Don't test me.",
    "Behave.",
    "That's enough.",
    "I'm giving you one chance to fix that attitude.",
]

# Deliberately kept in code instead of the dashboard/config output.
# The built-in rule canonicalizes common spacing/punctuation/leet/repeated-letter
# evasions so admins do not need to add dozens of spellings manually.
_NWORD_CANON_RE = re.compile(r"^n+i+g+g+(?:(?:e+r+)|a+)?s?$")
_LEET_MAP = str.maketrans({
    "1": "i", "!": "i", "|": "i",
    "3": "e",
    "4": "a", "@": "a",
    "0": "o",
    "6": "g", "9": "g", "q": "g",
})
_CONFUSABLE_MAP = str.maketrans({
    # A small set of common look-alike Unicode characters used for evasion.
    "а": "a", "е": "e", "і": "i", "ӏ": "i", "ո": "n", "ɡ": "g",
})
_QUOTE_HINT_RE = re.compile(
    r"\b(?:he|she|they|you|someone|somebody)\s+(?:said|called|wrote)|\b(?:quote|quoting|quoted|repeat(?:ing|ed)?)\b|\bsaid\s+the\s+word\b",
    re.I,
)



def _canonical_slur_token(text: str) -> str:
    text = unicodedata.normalize("NFKD", (text or "").casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.translate(_CONFUSABLE_MAP).translate(_LEET_MAP)
    # Keep letters only; punctuation/spaces/underscores inserted between letters
    # are treated as obfuscation rather than distinct spellings.
    return re.sub(r"[^a-z]", "", text)


def _matches_nword_variant(text: str) -> bool:
    raw = text or ""
    # Check each visible token-ish span first to reduce accidental cross-word
    # joins, then check compact spans for deliberately spaced-out spellings.
    chunks = re.findall(r"[\w@!|.\-_*]+", raw, flags=re.UNICODE)
    for chunk in chunks:
        canon = _canonical_slur_token(chunk)
        if _NWORD_CANON_RE.fullmatch(canon):
            return True
    compact = _canonical_slur_token(raw)
    return bool(_NWORD_CANON_RE.search(compact))

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
        self._warning_cooldowns: dict[tuple[int, int, str], float] = {}
        self.restore_due_roles.start()

    def cog_unload(self) -> None:
        self.restore_due_roles.cancel()


    async def _collections(self):
        db = self.bot.database._database  # noqa: SLF001 - same-project integration
        if db is None:
            raise RuntimeError("MongoDB is not connected")
        events = db["vc_automod_events"]
        punishments = db["vc_automod_punishments"]
        infractions = db["vc_automod_infractions"]
        if not self._indexes_ready:
            await asyncio.to_thread(events.create_index, [("guild_id", ASCENDING), ("user_id", ASCENDING), ("created_at", ASCENDING)])
            await asyncio.to_thread(events.create_index, "expires_at", expireAfterSeconds=0)
            await asyncio.to_thread(punishments.create_index, [("guild_id", ASCENDING), ("user_id", ASCENDING), ("created_at", ASCENDING)])
            await asyncio.to_thread(punishments.create_index, "history_expires_at", expireAfterSeconds=0)
            await asyncio.to_thread(infractions.create_index, [("guild_id", ASCENDING), ("user_id", ASCENDING), ("created_at", ASCENDING)])
            await asyncio.to_thread(infractions.create_index, "expires_at", expireAfterSeconds=0)
            self._indexes_ready = True
        return events, punishments, infractions

    async def ensure_defaults(self, guild: discord.Guild) -> dict[str, Any]:
        profile = await self.bot.database.ensure_guild_profile(guild)
        cfg = dict(profile.get("vc_automod") or {})
        triggers = list(cfg.get("triggers") or [])
        if not any(str(t.get("id")) == "nword" for t in triggers):
            triggers.insert(0, dict(DEFAULT_TRIGGER))
            await self.bot.database.set_config_path(guild.id, "vc_automod.triggers", triggers)
            cfg["triggers"] = triggers
        # Backfill new per-trigger fields without destroying existing settings.
        changed = False
        for item in triggers:
            if "points" not in item:
                item["points"] = 1; changed = True
            if "history_days" not in item:
                item["history_days"] = int(cfg.get("retention_days", 7) or 7); changed = True
            if "spoken_warning" not in item:
                item["spoken_warning"] = False; changed = True
            if "warning_style" not in item:
                item["warning_style"] = "random"; changed = True
            if "warning_text" not in item:
                item["warning_text"] = ""; changed = True
        if not isinstance(cfg.get("warning_lines"), list) or not cfg.get("warning_lines"):
            await self.bot.database.set_config_path(guild.id, "vc_automod.warning_lines", list(DEFAULT_WARNING_LINES))
            cfg["warning_lines"] = list(DEFAULT_WARNING_LINES)
        if changed:
            await self.bot.database.set_config_path(guild.id, "vc_automod.triggers", triggers)
            cfg["triggers"] = triggers
        ladder = cfg.get("punishment_ladder")
        if not isinstance(ladder, list) or not ladder:
            old = cfg.get("punishment_minutes") or [5, 15, 60, 1440]
            ladder = [
                {"threshold": idx + 1, "action": "remove_timeout", "duration_minutes": max(1, int(minutes)), "play_sound": True, "reset_score": False}
                for idx, minutes in enumerate(old)
            ]
            await self.bot.database.set_config_path(guild.id, "vc_automod.punishment_ladder", ladder)
            cfg["punishment_ladder"] = ladder
        return cfg

    @staticmethod
    def _matches_trigger(trigger: dict[str, Any], text: str) -> bool:
        if str(trigger.get("id")) == "nword":
            return _matches_nword_variant(text)
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

        events, punishments, infractions = await self._collections()
        now = utcnow()

        for trigger in cfg.get("triggers") or []:
            if not trigger.get("enabled", True) or not self._matches_trigger(trigger, text):
                continue
            quoted = bool(trigger.get("quote_lenient", True)) and self._looks_quoted(text)
            history_days = max(1, int(trigger.get("history_days", cfg.get("retention_days", 7)) or 7))
            event = {
                "guild_id": guild.id,
                "user_id": member.id,
                "display_name": member.display_name,
                "trigger_id": str(trigger.get("id") or "custom"),
                "trigger_label": str(trigger.get("label") or "Custom trigger"),
                "created_at": now,
                "expires_at": now + timedelta(days=history_days),
                "voice_channel_id": voice_channel_id,
                "possible_quote": quoted,
                "consumed": False,
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

            # Optional spoken warning. This is deliberately independent of the
            # normal AI-participation rules, so Mommy can warn in a crowded VC.
            # A short per-member/per-trigger cooldown prevents one noisy phrase
            # from making her talk over herself repeatedly.
            if trigger.get("spoken_warning", False):
                key = (guild.id, member.id, str(trigger.get("id") or "custom"))
                loop_now = asyncio.get_running_loop().time()
                if loop_now >= self._warning_cooldowns.get(key, 0.0):
                    self._warning_cooldowns[key] = loop_now + 8.0
                    voice_cog = self.bot.get_cog("VoiceRecording") or self.bot.get_cog("VoiceRecordingCog")
                    if voice_cog is not None and hasattr(voice_cog, "speak_automod_warning"):
                        try:
                            await voice_cog.speak_automod_warning(guild, trigger)
                        except Exception:
                            log.exception("Could not speak VC automod warning in guild %s", guild.id)

            if len(recent) >= threshold:
                # Consume exactly one threshold-sized batch. Extra detections remain
                # available for the next batch rather than being silently swallowed.
                used = recent[:threshold]
                ids = [doc["_id"] for doc in used]
                await asyncio.to_thread(events.update_many, {"_id": {"$in": ids}}, {"$set": {"consumed": True}})
                points = max(1, int(trigger.get("points", 1) or 1))
                infraction = {
                    "guild_id": guild.id,
                    "user_id": member.id,
                    "display_name": member.display_name,
                    "trigger_id": str(trigger.get("id") or "custom"),
                    "trigger_label": str(trigger.get("label") or "Custom trigger"),
                    "points": points,
                    "created_at": now,
                    "expires_at": now + timedelta(days=history_days),
                    "voice_channel_id": voice_channel_id,
                }
                await asyncio.to_thread(infractions.insert_one, infraction)
                await self._maybe_punish(member, trigger, cfg, punishments, infractions)

    @staticmethod
    def _normalized_ladder(cfg: dict[str, Any]) -> list[dict[str, Any]]:
        raw = cfg.get("punishment_ladder") or DEFAULT_PUNISHMENT_LADDER
        rows: list[dict[str, Any]] = []
        for item in raw:
            try:
                threshold = max(1, int(item.get("threshold", 1)))
                duration = max(0, int(item.get("duration_minutes", 0) or 0))
            except (TypeError, ValueError):
                continue
            action = str(item.get("action") or "remove_timeout").strip().lower()
            if action not in VALID_PUNISHMENT_ACTIONS:
                action = "remove_timeout"
            rows.append({
                "threshold": threshold,
                "action": action,
                "duration_minutes": duration,
                "play_sound": bool(item.get("play_sound", True)),
                "reset_score": bool(item.get("reset_score", False)),
            })
        rows.sort(key=lambda r: r["threshold"])
        return rows or [dict(v) for v in DEFAULT_PUNISHMENT_LADDER]

    async def active_score(self, guild_id: int, user_id: int) -> int:
        _, _, infractions = await self._collections()
        now = utcnow()
        def _rows():
            return list(infractions.find({"guild_id": guild_id, "user_id": user_id, "expires_at": {"$gt": now}}))
        rows = await asyncio.to_thread(_rows)
        return sum(max(0, int(r.get("points", 0) or 0)) for r in rows)

    async def _maybe_punish(self, member: discord.Member, trigger: dict[str, Any], cfg: dict[str, Any], punishments, infractions) -> None:
        score = await self.active_score(member.guild.id, member.id)
        ladder = self._normalized_ladder(cfg)
        eligible = [row for row in ladder if score >= row["threshold"]]
        if not eligible:
            return
        step = eligible[-1]
        history_days = max(1, int(trigger.get("history_days", cfg.get("retention_days", 7)) or 7))
        cutoff = utcnow() - timedelta(days=history_days)
        def _previous_threshold():
            doc = punishments.find_one(
                {"guild_id": member.guild.id, "user_id": member.id, "created_at": {"$gte": cutoff}},
                sort=[("score_threshold", -1)],
            )
            return int(doc.get("score_threshold", 0) or 0) if doc else 0
        previous_threshold = await asyncio.to_thread(_previous_threshold)
        if step["threshold"] <= previous_threshold:
            return
        await self._apply_punishment(member, trigger, cfg, punishments, infractions, score, step, history_days)

    async def _send_mod_notice(self, guild: discord.Guild, message: str) -> None:
        profile = await self.bot.database.get_guild_profile(guild.id) or {}
        cfg = profile.get("vc_automod") or {}
        channel = guild.get_channel(int(cfg.get("log_channel_id") or 0)) if cfg.get("log_channel_id") else None
        if not isinstance(channel, discord.TextChannel):
            channel = guild.system_channel if isinstance(guild.system_channel, discord.TextChannel) else None
        if channel:
            try:
                await channel.send(message[:1900])
            except (discord.Forbidden, discord.HTTPException):
                pass

    async def _apply_punishment(self, member: discord.Member, trigger: dict[str, Any], cfg: dict[str, Any], punishments, infractions, score: int, step: dict[str, Any], history_days: int) -> None:
        now = utcnow()
        action = step["action"]
        duration = max(0, int(step.get("duration_minutes", 0) or 0))
        restore_at = now + timedelta(minutes=duration) if duration > 0 else now
        profile = await self.bot.database.get_guild_profile(member.guild.id) or {}
        role_id = (profile.get("verification") or {}).get("house_trained_role_id")
        role = member.guild.get_role(int(role_id)) if role_id else None
        removed_role_id = None
        reason = f"Mommy.exe VC automod: {trigger.get('label', 'configured trigger')} (score {score})"
        successes: list[str] = []
        failures: list[str] = []

        async def remove_house_role() -> bool:
            nonlocal removed_role_id
            if role is None:
                failures.append("House Trained is not configured or no longer exists")
                return False
            if role not in member.roles:
                failures.append("member did not have House Trained")
                return False
            try:
                await member.remove_roles(role, reason=reason)
                removed_role_id = role.id
                successes.append(f"removed {role.name}")
                return True
            except (discord.Forbidden, discord.HTTPException) as exc:
                failures.append(f"could not remove {role.name}: {type(exc).__name__}")
                log.exception("Could not remove House Trained from %s", member.id)
                return False

        async def timeout_member() -> None:
            if duration <= 0:
                failures.append("timeout duration is 0")
                return
            try:
                await member.timeout(timedelta(minutes=duration), reason=reason)
                successes.append(f"timed out {duration}m")
            except (discord.Forbidden, discord.HTTPException) as exc:
                failures.append(f"could not timeout: {type(exc).__name__}")
                log.exception("Could not timeout %s", member.id)

        house_role_removed = False

        if action == "warning":
            successes.append("warning recorded")
        elif action == "remove_role":
            house_role_removed = await remove_house_role()
        elif action == "timeout":
            await timeout_member()
        elif action == "remove_timeout":
            # Missing/failed House Trained removal never prevents the timeout
            # from being attempted, but it DOES suppress the punishment sound.
            house_role_removed = await remove_house_role()
            await timeout_member()
        elif action == "kick":
            try:
                await member.kick(reason=reason)
                successes.append("kicked")
            except (discord.Forbidden, discord.HTTPException) as exc:
                failures.append(f"could not kick: {type(exc).__name__}")
                log.exception("Could not kick %s", member.id)
        elif action == "ban":
            try:
                await member.ban(reason=reason, delete_message_seconds=0)
                successes.append("banned")
            except (discord.Forbidden, discord.HTTPException) as exc:
                failures.append(f"could not ban: {type(exc).__name__}")
                log.exception("Could not ban %s", member.id)

        # Punishment audio is specifically tied to successful removal of the
        # configured House Trained role. No role removal = no sound.
        if house_role_removed and step.get("play_sound", True) and cfg.get("punishment_sound_enabled", True):
            vc = member.guild.voice_client
            voice_cog = self.bot.get_cog("VoiceRecordingCog")
            if vc and vc.is_connected() and voice_cog and hasattr(voice_cog, "play_punishment_sound"):
                try:
                    await voice_cog.play_punishment_sound(member.guild)
                except Exception as exc:
                    failures.append(f"punishment sound failed: {type(exc).__name__}")
                    log.exception("Punishment sound failed")

        # Do not disguise a failed House Trained punishment as a successful one
        # by merely disconnecting the member from VC. For role-based actions,
        # only disconnect after House Trained was actually removed.
        should_disconnect = action not in {"warning", "kick", "ban"}
        if action in {"remove_role", "remove_timeout"} and not house_role_removed:
            should_disconnect = False

        if should_disconnect and member.voice and member.voice.channel:
            try:
                await member.move_to(None, reason=reason)
                successes.append("disconnected from VC")
            except (discord.Forbidden, discord.HTTPException):
                failures.append("could not disconnect from VC")

        doc = {
            "guild_id": member.guild.id,
            "user_id": member.id,
            "display_name": member.display_name,
            "trigger_id": str(trigger.get("id") or "custom"),
            "trigger_label": str(trigger.get("label") or "Configured trigger"),
            "created_at": now,
            "history_expires_at": now + timedelta(days=history_days),
            "restore_at": restore_at,
            "removed_role_id": removed_role_id,
            "restored": not bool(removed_role_id),
            "duration_minutes": duration,
            "score": score,
            "score_threshold": int(step["threshold"]),
            "action": action,
            "successes": successes,
            "failures": failures,
        }
        await asyncio.to_thread(punishments.insert_one, doc)

        if step.get("reset_score", False):
            await asyncio.to_thread(infractions.delete_many, {"guild_id": member.guild.id, "user_id": member.id})

        if failures:
            await self._send_mod_notice(
                member.guild,
                f"⚠️ Mommy.exe automod punishment for {member.mention} was only partially applied. "
                f"Action: **{action}** • score {score}. "
                f"Succeeded: {', '.join(successes) or 'nothing'}. Failed: {', '.join(failures)}. "
                "Check Mommy's permissions and role hierarchy if Discord rejected an action.",
            )
        log.warning("VC automod action=%s member=%s guild=%s score=%s successes=%s failures=%s", action, member.id, member.guild.id, score, successes, failures)

    @tasks.loop(seconds=30)
    async def restore_due_roles(self) -> None:
        if not self.bot.is_ready() or not self.bot.database.connected:
            return
        _, punishments, _ = await self._collections()
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
        events, _, _ = await self._collections()
        return await asyncio.to_thread(lambda: list(events.find({"guild_id": guild_id, "user_id": user_id}).sort("created_at", -1).limit(limit)))

    async def forgive(self, guild_id: int, user_id: int) -> None:
        events, _, infractions = await self._collections()
        await asyncio.to_thread(events.delete_many, {"guild_id": guild_id, "user_id": user_id})
        await asyncio.to_thread(infractions.delete_many, {"guild_id": guild_id, "user_id": user_id})

    async def clear_latest_infraction(self, guild_id: int, user_id: int) -> bool:
        _, _, infractions = await self._collections()
        latest = await asyncio.to_thread(lambda: infractions.find_one({"guild_id": guild_id, "user_id": user_id}, sort=[("created_at", -1)]))
        if not latest:
            return False
        await asyncio.to_thread(infractions.delete_one, {"_id": latest["_id"]})
        return True

    async def clear_score(self, guild_id: int, user_id: int) -> None:
        _, _, infractions = await self._collections()
        await asyncio.to_thread(infractions.delete_many, {"guild_id": guild_id, "user_id": user_id})

    async def infraction_history(self, guild_id: int, user_id: int, limit: int = 12) -> list[dict[str, Any]]:
        _, _, infractions = await self._collections()
        return await asyncio.to_thread(lambda: list(infractions.find({"guild_id": guild_id, "user_id": user_id}).sort("created_at", -1).limit(limit)))

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
        _, punishments, _ = await self._collections()
        await asyncio.to_thread(punishments.update_many, {"guild_id": member.guild.id, "user_id": member.id, "restored": False}, {"$set": {"restored": True, "restored_at": utcnow(), "manual": True}})
        return f"Restored {role.mention} to {member.mention}."


async def setup(bot) -> None:
    await bot.add_cog(VCAutomodCog(bot))
