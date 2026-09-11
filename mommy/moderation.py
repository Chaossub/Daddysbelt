from __future__ import annotations

import asyncio
import logging
import re
import random
from datetime import datetime, timedelta, timezone
from typing import Any

import discord
from discord.ext import commands, tasks
from pymongo import ASCENDING

log = logging.getLogger("mommy-exe.vc-automod")

DEFAULT_PUNISHMENT_LADDER = [
    {"threshold": 1, "action": "remove_timeout", "duration_minutes": 5, "play_sound": True, "reset_score": False},
    {"threshold": 2, "action": "remove_timeout", "duration_minutes": 15, "play_sound": True, "reset_score": False},
    {"threshold": 3, "action": "remove_timeout", "duration_minutes": 60, "play_sound": True, "reset_score": False},
    {"threshold": 4, "action": "remove_timeout", "duration_minutes": 1440, "play_sound": True, "reset_score": False},
]

VALID_PUNISHMENT_ACTIONS = {"warning", "remove_role", "timeout", "remove_timeout"}

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
        # Remove the legacy hard-wired built-in trigger from older builds.
        # Admins can add any word or phrase they want through the dashboard instead.
        cleaned_triggers = [
            t for t in triggers
            if not (str(t.get("id")) == "nword" and bool(t.get("builtin")))
        ]
        changed = cleaned_triggers != triggers
        triggers = cleaned_triggers
        if changed:
            await self.bot.database.set_config_path(guild.id, "vc_automod.triggers", triggers)
            cfg["triggers"] = triggers
        # Backfill new per-trigger fields without destroying existing settings.
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
            if "scope_mode" not in item:
                item["scope_mode"] = "everyone"; changed = True
            if not isinstance(item.get("scope_user_ids"), list):
                item["scope_user_ids"] = []; changed = True
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

        # Automatic server bans/kicks are disabled. Migrate any older ladder
        # levels that used kick/temp_ban/ban into remove+timeout. A former temp
        # ban keeps its configured duration (24h by default); zero-duration
        # kick/ban levels become a 24h timeout so they cannot silently do nothing.
        ladder_changed = False
        for row in ladder:
            action = str(row.get("action") or "").strip().lower()
            if action in {"kick", "temp_ban", "ban"}:
                row["action"] = "remove_timeout"
                try:
                    duration = int(row.get("duration_minutes", 0) or 0)
                except (TypeError, ValueError):
                    duration = 0
                if duration <= 0:
                    row["duration_minutes"] = 1440
                # Keep reset_score as configured so a previous level-4 temp-ban
                # can still restart the score cycle after the timeout succeeds.
                row["play_sound"] = bool(row.get("play_sound", True))
                ladder_changed = True
        if ladder_changed:
            await self.bot.database.set_config_path(guild.id, "vc_automod.punishment_ladder", ladder)
            cfg["punishment_ladder"] = ladder
        return cfg

    @staticmethod
    def _matches_trigger(trigger: dict[str, Any], text: str) -> bool:
        phrase = normalize(str(trigger.get("phrase") or "")).strip()
        if not phrase:
            return False
        return phrase in normalize(text)

    @staticmethod
    def _looks_quoted(text: str) -> bool:
        return bool(_QUOTE_HINT_RE.search(text or ""))

    @staticmethod
    def _trigger_applies_to_member(trigger: dict[str, Any], member: discord.Member) -> bool:
        mode = str(trigger.get("scope_mode") or "everyone").strip().lower()
        selected: set[int] = set()
        for value in trigger.get("scope_user_ids") or []:
            try:
                selected.add(int(value))
            except (TypeError, ValueError):
                continue
        if mode == "only_selected":
            return member.id in selected
        if mode == "except_selected":
            return member.id not in selected
        return True

    async def is_exempt(self, member: discord.Member, cfg: dict[str, Any]) -> bool:
        if member.bot:
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
            if not trigger.get("enabled", True):
                continue
            if not self._trigger_applies_to_member(trigger, member):
                continue
            if not self._matches_trigger(trigger, text):
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
        now = utcnow()

        # Punishment history is scoped to the CURRENT score cycle. When a
        # successful reset-score action (such as a temp ban) clears the active
        # infractions, the next infraction starts a fresh 1 -> 2 -> 3 -> 4
        # ladder. Old punishments from the previous cycle must not block it.
        def _oldest_active_infraction():
            return infractions.find_one(
                {
                    "guild_id": member.guild.id,
                    "user_id": member.id,
                    "expires_at": {"$gt": now},
                },
                sort=[("created_at", 1)],
            )

        oldest = await asyncio.to_thread(_oldest_active_infraction)
        cycle_start = oldest.get("created_at") if oldest else now

        # Only a punishment that actually succeeded can consume a ladder level.
        # Failed Discord actions remain retryable on the next qualifying hit.
        def _previous_threshold():
            doc = punishments.find_one(
                {
                    "guild_id": member.guild.id,
                    "user_id": member.id,
                    "created_at": {"$gte": cycle_start},
                    "succeeded": True,
                },
                sort=[("score_threshold", -1)],
            )
            return int(doc.get("score_threshold", 0) or 0) if doc else 0

        previous_threshold = await asyncio.to_thread(_previous_threshold)
        if step["threshold"] <= previous_threshold:
            return
        await self._apply_punishment(member, trigger, cfg, punishments, infractions, score, step, history_days)

    async def _tempban_history_collection(self):
        db = self.bot.database._database  # noqa: SLF001
        if db is None:
            raise RuntimeError("MongoDB is not connected")
        coll = db["vc_automod_tempban_history"]
        if not self._indexes_ready:
            # _collections() normally creates the common indexes first; this is
            # just a safe fallback if this helper is reached independently.
            await asyncio.to_thread(coll.create_index, [("guild_id", ASCENDING), ("user_id", ASCENDING), ("created_at", ASCENDING)])
        return coll

    async def _temp_ban_duration(self, guild_id: int, user_id: int, cfg: dict[str, Any]) -> tuple[int, int]:
        coll = await self._tempban_history_collection()
        prior_count = await asyncio.to_thread(coll.count_documents, {"guild_id": guild_id, "user_id": user_id, "succeeded": True})
        raw = cfg.get("temp_ban_durations_minutes") or [1440, 4320, 7200, 10080]
        durations: list[int] = []
        for value in raw:
            try:
                durations.append(min(10080, max(1440, int(value))))
            except (TypeError, ValueError):
                continue
        if not durations:
            durations = [1440, 4320, 7200, 10080]
        index = min(prior_count, len(durations) - 1)
        return durations[index], prior_count

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
        temp_ban_number = None
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
            # A ladder-level warning is a real spoken warning, independent of
            # normal AI chatter/crowded-VC behavior. If the per-trigger warning
            # already spoke on this same detection, do not deliberately double it.
            if trigger.get("spoken_warning", False):
                successes.append("spoken warning already issued")
            else:
                voice_cog = self.bot.get_cog("VoiceRecordingCog") or self.bot.get_cog("VoiceRecording")
                if voice_cog is not None and hasattr(voice_cog, "speak_automod_warning"):
                    try:
                        await voice_cog.speak_automod_warning(member.guild, trigger)
                        successes.append("spoken warning issued")
                    except Exception as exc:
                        failures.append(f"spoken warning failed: {type(exc).__name__}")
                        log.exception("Could not speak ladder warning in guild %s", member.guild.id)
                else:
                    failures.append("spoken warning voice handler unavailable")
        elif action == "remove_role":
            house_role_removed = await remove_house_role()
        elif action == "timeout":
            await timeout_member()
        elif action == "remove_timeout":
            # Missing/failed House Trained removal never prevents the timeout
            # from being attempted, but it DOES suppress the punishment sound.
            house_role_removed = await remove_house_role()
            await timeout_member()

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
        should_disconnect = action != "warning"
        if action in {"remove_role", "remove_timeout"} and not house_role_removed:
            should_disconnect = False

        if should_disconnect and member.voice and member.voice.channel:
            try:
                await member.move_to(None, reason=reason)
                successes.append("disconnected from VC")
            except (discord.Forbidden, discord.HTTPException):
                failures.append("could not disconnect from VC")

        # Determine whether the intended punishment itself succeeded before
        # recording history. Cosmetic/secondary failures do not erase a real
        # successful moderation action.
        if action == "warning":
            punishment_succeeded = any("warning" in x for x in successes)
        elif action == "remove_role":
            punishment_succeeded = house_role_removed
        elif action == "timeout":
            punishment_succeeded = any(x.startswith("timed out") for x in successes)
        elif action == "remove_timeout":
            punishment_succeeded = house_role_removed and any(x.startswith("timed out") for x in successes)
        else:
            punishment_succeeded = False

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
            "temp_ban_number": temp_ban_number,
            "unban_at": None,
            "succeeded": punishment_succeeded,
        }
        await asyncio.to_thread(punishments.insert_one, doc)
        if punishment_succeeded and action in {"timeout", "remove_timeout"}:
            await self._maybe_alert_excessive_timeouts(member, punishments, duration)

        # Never wipe the score when the intended punishment failed. A successful
        # reset-score action starts a completely fresh ladder cycle.
        if step.get("reset_score", False) and punishment_succeeded:
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

    async def _maybe_alert_excessive_timeouts(self, member: discord.Member, punishments, duration_minutes: int) -> None:
        """Escalate repeated timeouts to staff; never convert them into a ban."""
        now = utcnow()
        window_days = 7
        since = now - timedelta(days=window_days)

        def _recent():
            return list(punishments.find({
                "guild_id": member.guild.id,
                "user_id": member.id,
                "succeeded": True,
                "action": {"$in": ["timeout", "remove_timeout"]},
                "created_at": {"$gte": since},
            }).sort("created_at", -1).limit(25))

        recent = await asyncio.to_thread(_recent)
        count = len(recent)
        # Alert at 3, then every two additional timeouts (5, 7, 9...).
        if count < 3 or (count > 3 and count % 2 == 0):
            return

        db = self.bot.database._database  # noqa: SLF001
        if db is None:
            return
        alerts = db["vc_timeout_escalations"]
        alert_key = f"{member.guild.id}:{member.id}:{count}"
        existing = await asyncio.to_thread(alerts.find_one, {"alert_key": alert_key})
        if existing:
            return

        doc = {
            "alert_key": alert_key,
            "guild_id": member.guild.id,
            "user_id": member.id,
            "display_name": member.display_name,
            "count": count,
            "window_days": window_days,
            "latest_duration_minutes": duration_minutes,
            "created_at": now,
        }
        await asyncio.to_thread(alerts.insert_one, doc)

        profile = await self.bot.database.get_guild_profile(member.guild.id) or {}
        cfg = profile.get("vc_automod") or {}
        lines = []
        for item in recent[:8]:
            created = item.get("created_at")
            stamp = discord.utils.format_dt(created, style="R") if isinstance(created, datetime) else "unknown time"
            lines.append(f"• {stamp} — {int(item.get('duration_minutes', 0) or 0)}m — {item.get('trigger_label', 'VC automod')}")
        message = (
            f"⚠️ **Excessive timeout review: {member}**\n"
            f"{member.mention} has received **{count} timeouts in the last {window_days} days**.\n"
            "Mommy.exe will **not** ban them automatically. A moderator/admin should review the pattern.\n\n"
            + "\n".join(lines)
        )

        # Private moderation channel, if configured.
        channel_id = cfg.get("log_channel_id") or (profile.get("logs") or {}).get("channel_id")
        channel = member.guild.get_channel(int(channel_id)) if channel_id else None
        if isinstance(channel, discord.TextChannel):
            try:
                await channel.send(message[:1900])
            except (discord.Forbidden, discord.HTTPException):
                pass

        # Server owner DM as a fallback/secondary alert.
        owner = member.guild.owner
        if owner is not None:
            try:
                await owner.send(f"**{member.guild.name}**\n{message[:1800]}")
            except (discord.Forbidden, discord.HTTPException):
                pass

    async def manual_unban(self, guild: discord.Guild, user_id: int) -> tuple[bool, str]:
        """Bans/unbans are intentionally human-only and never executed by Mommy.exe."""
        return False, "Mommy.exe does not ban or unban members. Use Discord's native moderation controls as an admin."

    async def force_clear_tempban_state(self, guild_id: int, user_id: int) -> tuple[int, int]:
        """Hard-cancel Mommy temp-ban state without requiring a live Discord ban entry.

        This is a cleanup/debug action only. It never bans or unbans anyone on Discord;
        it simply ensures no Mommy timer or punishment record remains active for the user.
        """
        guild_id = int(guild_id)
        user_id = int(user_id)
        now = utcnow()
        history_closed = 0
        punishment_closed = 0

        history = await self._tempban_history_collection()
        result = await asyncio.to_thread(
            history.update_many,
            {"guild_id": guild_id, "user_id": user_id, "succeeded": True, "unbanned": False},
            {"$set": {
                "unbanned": True,
                "unbanned_at": now,
                "manual_unban": True,
                "cancelled": True,
                "cancelled_at": now,
                "force_cleared": True,
                "unban_at": None,
            }},
        )
        history_closed = int(getattr(result, "modified_count", 0) or 0)

        _, punishments, _ = await self._collections()
        result = await asyncio.to_thread(
            punishments.update_many,
            {
                "guild_id": guild_id,
                "user_id": user_id,
                "action": "temp_ban",
                "succeeded": True,
                "temp_ban_ended": {"$ne": True},
            },
            {"$set": {
                "temp_ban_ended": True,
                "temp_ban_ended_at": now,
                "manual_unban": True,
                "force_cleared": True,
                "unban_at": None,
            }},
        )
        punishment_closed = int(getattr(result, "modified_count", 0) or 0)

        log.warning(
            "FORCE-CLEARED temp-ban state user=%s guild=%s history_closed=%s punishment_closed=%s",
            user_id, guild_id, history_closed, punishment_closed,
        )
        return history_closed, punishment_closed

    async def active_temp_bans(self, guild_id: int, limit: int = 25) -> list[dict[str, Any]]:
        """Return only temp bans that Mommy still considers active."""
        history = await self._tempban_history_collection()
        return await asyncio.to_thread(
            lambda: list(
                history.find(
                    {
                        "guild_id": int(guild_id),
                        "succeeded": True,
                        "unbanned": False,
                        "unban_at": {"$ne": None},
                    }
                ).sort("unban_at", 1).limit(int(limit))
            )
        )

    @tasks.loop(seconds=30)
    async def restore_due_roles(self) -> None:
        if not self.bot.is_ready() or not self.bot.database.connected:
            return
        _, punishments, _ = await self._collections()
        now = utcnow()

        # Automatic bans/unbans are disabled. Only reversible role restoration runs here.

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

    async def forgive(self, guild_id: int, user_id: int) -> bool:
        """Clear VC automod detections/score and immediately clear any Discord timeout.

        Returns True when a member was found and the timeout-clear request succeeded.
        Database forgiveness still happens even if Discord rejects the timeout change.
        """
        events, _, infractions = await self._collections()
        await asyncio.to_thread(events.delete_many, {"guild_id": guild_id, "user_id": user_id})
        await asyncio.to_thread(infractions.delete_many, {"guild_id": guild_id, "user_id": user_id})

        guild = self.bot.get_guild(int(guild_id))
        member = guild.get_member(int(user_id)) if guild is not None else None
        if member is None:
            return False
        try:
            await member.timeout(None, reason="Mommy.exe automod forgiveness")
            return True
        except (discord.Forbidden, discord.HTTPException):
            log.exception("Could not clear timeout while forgiving %s", user_id)
            return False

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
