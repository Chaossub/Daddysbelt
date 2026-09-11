from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import discord
from bson import ObjectId
from discord.ext import commands

log = logging.getLogger("mommy-exe.appeals")

APPEAL_WINDOW_DAYS = 30
ADVERSE_DADDY_ACTIONS = {
    "warn", "warning", "timeout", "timeout_changed", "kick", "ban",
    "remove_role", "remove_timeout", "disconnect", "vc_disconnect",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _fmt_dt(value: Any) -> str:
    if isinstance(value, datetime):
        try:
            return discord.utils.format_dt(value, style="R")
        except Exception:
            pass
    return "recently"


def _label_action(action: str) -> str:
    return (action or "moderation action").replace("_", " ").strip().title()


class AppealSelect(discord.ui.Select):
    def __init__(self, cog: "AppealsCog", actions: list[dict[str, Any]]) -> None:
        self.cog = cog
        self.actions = {str(item["key"]): item for item in actions}
        options: list[discord.SelectOption] = []
        for item in actions[:25]:
            action = _label_action(str(item.get("action") or "action"))
            guild_name = str(item.get("guild_name") or "Server")[:40]
            status = str(item.get("appeal_status") or "not appealed").replace("_", " ")
            reason = str(item.get("reason") or "No reason provided.").replace("\n", " ")[:90]
            options.append(
                discord.SelectOption(
                    label=f"{action} • {guild_name}"[:100],
                    value=str(item["key"])[:100],
                    description=f"{status} • {reason}"[:100],
                )
            )
        super().__init__(
            placeholder="Choose the action you want to appeal…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        item = self.actions.get(self.values[0])
        if item is None:
            await interaction.response.send_message("That moderation action is no longer available.", ephemeral=True)
            return
        await interaction.response.send_modal(AppealReasonModal(self.cog, item))


class AppealMenuView(discord.ui.View):
    def __init__(self, cog: "AppealsCog", actions: list[dict[str, Any]]) -> None:
        super().__init__(timeout=900)
        self.add_item(AppealSelect(cog, actions))


class AppealReasonModal(discord.ui.Modal, title="Appeal Moderation Action"):
    reason = discord.ui.TextInput(
        label="Why should staff review this action?",
        style=discord.TextStyle.paragraph,
        placeholder="Explain what happened or why you think the action was incorrect.",
        max_length=1200,
    )

    def __init__(self, cog: "AppealsCog", item: dict[str, Any]) -> None:
        super().__init__()
        self.cog = cog
        self.item = item

    async def on_submit(self, interaction: discord.Interaction) -> None:
        ok, text = await self.cog.submit_appeal(interaction.user, self.item, str(self.reason).strip())
        await interaction.response.send_message(text, ephemeral=True)


class AppealReviewView(discord.ui.View):
    def __init__(self, cog: "AppealsCog", appeal_id: str) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.appeal_id = appeal_id

    async def _authorized(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return False
        allowed = bool(
            interaction.user.id == interaction.guild.owner_id
            or interaction.user.guild_permissions.administrator
            or interaction.user.guild_permissions.manage_guild
            or interaction.user.guild_permissions.moderate_members
        )
        if not allowed:
            await interaction.response.send_message("You do not have permission to review appeals.", ephemeral=True)
        return allowed

    async def _resolve(self, interaction: discord.Interaction, decision: str) -> None:
        if not await self._authorized(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        ok, message, appeal = await self.cog.resolve_appeal(self.appeal_id, decision, interaction.user.id)
        await interaction.followup.send(message, ephemeral=True)
        if ok and interaction.message:
            embed = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed(title="Moderation Appeal")
            embed = embed.copy()
            embed.add_field(
                name="Resolution",
                value=f"**{decision.title()}** by <@{interaction.user.id}>",
                inline=False,
            )
            try:
                await interaction.message.edit(embed=embed, view=None)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Approve", emoji="✅", style=discord.ButtonStyle.success, custom_id="appeal:approve")
    async def approve(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._resolve(interaction, "approved")

    @discord.ui.button(label="Deny", emoji="❌", style=discord.ButtonStyle.danger, custom_id="appeal:deny")
    async def deny(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._resolve(interaction, "denied")

    @discord.ui.button(label="View Details", emoji="🧾", style=discord.ButtonStyle.secondary, custom_id="appeal:details")
    async def details(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self._authorized(interaction):
            return
        appeal = await self.cog.get_appeal(self.appeal_id)
        if not appeal:
            await interaction.response.send_message("That appeal no longer exists.", ephemeral=True)
            return
        text = (
            f"**Original action:** {_label_action(str(appeal.get('action') or 'action'))}\n"
            f"**Original reason:** {appeal.get('original_reason') or 'No reason provided.'}\n"
            f"**Member appeal:** {appeal.get('appeal_reason') or 'No explanation provided.'}\n"
            f"**Source:** {appeal.get('source_kind') or 'unknown'}\n"
            f"**Source ID:** `{appeal.get('source_id') or 'unknown'}`"
        )
        await interaction.response.send_message(text[:1900], ephemeral=True)


class AppealsCog(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        self._indexes_ready = False

    async def _appeals(self):
        db = self.bot.database._database  # noqa: SLF001
        if db is None:
            raise RuntimeError("Mommy MongoDB is not connected")
        coll = db["moderation_appeals"]
        if not self._indexes_ready:
            await asyncio.to_thread(coll.create_index, [("user_id", 1), ("created_at", -1)])
            await asyncio.to_thread(coll.create_index, [("source_kind", 1), ("source_id", 1), ("user_id", 1)])
            self._indexes_ready = True
        return coll

    async def _daddy_db(self):
        # Mommy and Daddy may use separate databases on the same Mongo client.
        client = self.bot.database._client  # noqa: SLF001
        if client is None:
            raise RuntimeError("MongoDB client is not connected")
        name = os.getenv("MONGODB_DATABASE", "daddys_belt").strip() or "daddys_belt"
        return client[name]

    async def get_appeal(self, appeal_id: str) -> dict[str, Any] | None:
        try:
            oid = ObjectId(appeal_id)
        except Exception:
            return None
        coll = await self._appeals()
        return await asyncio.to_thread(coll.find_one, {"_id": oid})

    async def _existing_status(self, user_id: int, source_kind: str, source_id: str) -> str | None:
        coll = await self._appeals()
        row = await asyncio.to_thread(
            coll.find_one,
            {"user_id": int(user_id), "source_kind": source_kind, "source_id": str(source_id)},
            sort=[("created_at", -1)],
        )
        return str(row.get("status")) if row else None

    async def list_actions(self, user_id: int) -> list[dict[str, Any]]:
        cutoff = utcnow() - timedelta(days=APPEAL_WINDOW_DAYS)
        actions: list[dict[str, Any]] = []

        # Mommy VC automod punishments.
        mommy_db = self.bot.database._database  # noqa: SLF001
        if mommy_db is not None:
            rows = await asyncio.to_thread(
                lambda: list(
                    mommy_db["vc_automod_punishments"]
                    .find({"user_id": int(user_id), "succeeded": True, "created_at": {"$gte": cutoff}})
                    .sort("created_at", -1)
                    .limit(50)
                )
            )
            for row in rows:
                guild = self.bot.get_guild(int(row.get("guild_id", 0) or 0))
                if guild is None:
                    continue
                sid = str(row.get("_id"))
                status = await self._existing_status(user_id, "mommy_vc_automod", sid)
                actions.append({
                    "key": f"mommy:{sid}",
                    "source_kind": "mommy_vc_automod",
                    "source_id": sid,
                    "guild_id": guild.id,
                    "guild_name": guild.name,
                    "action": str(row.get("action") or "moderation"),
                    "reason": str(row.get("trigger_label") or "VC automod action"),
                    "created_at": row.get("created_at"),
                    "appeal_status": status,
                })

            # 18+ VC Guard compatibility if that module/collection is installed.
            child_rows = await asyncio.to_thread(
                lambda: list(
                    mommy_db["vc_child_disconnects"]
                    .find({"user_id": int(user_id), "created_at": {"$gte": cutoff}})
                    .sort("created_at", -1)
                    .limit(30)
                )
            )
            for row in child_rows:
                guild = self.bot.get_guild(int(row.get("guild_id", 0) or 0))
                if guild is None:
                    continue
                sid = str(row.get("_id"))
                status = await self._existing_status(user_id, "child_vc_disconnect", sid)
                actions.append({
                    "key": f"child:{sid}",
                    "source_kind": "child_vc_disconnect",
                    "source_id": sid,
                    "guild_id": guild.id,
                    "guild_name": guild.name,
                    "action": "18+ VC disconnect",
                    "reason": str(row.get("reason") or row.get("detection_reason") or "Possible minor/child audio in 18+ VC"),
                    "created_at": row.get("created_at"),
                    "appeal_status": status or row.get("appeal_status"),
                })

        # Daddy dashboard/manual moderation records.
        daddy_db = await self._daddy_db()
        mutual_ids = [guild.id for guild in self.bot.guilds if guild.get_member(int(user_id)) is not None]
        if mutual_ids:
            profiles = await asyncio.to_thread(
                lambda: list(daddy_db["guilds"].find({"guild_id": {"$in": mutual_ids}}, {"guild_id": 1, "moderation.cases": 1}))
            )
            for profile in profiles:
                guild = self.bot.get_guild(int(profile.get("guild_id", 0) or 0))
                if guild is None:
                    continue
                for case in reversed(((profile.get("moderation") or {}).get("cases") or [])):
                    if int(case.get("target_id") or 0) != int(user_id):
                        continue
                    created = case.get("created_at")
                    if isinstance(created, datetime) and created < cutoff:
                        continue
                    action = str(case.get("action") or "").strip().lower()
                    if action not in ADVERSE_DADDY_ACTIONS:
                        continue
                    sid = str(case.get("_id"))
                    status = await self._existing_status(user_id, "daddy_moderation", sid)
                    actions.append({
                        "key": f"daddy:{sid}",
                        "source_kind": "daddy_moderation",
                        "source_id": sid,
                        "guild_id": guild.id,
                        "guild_name": guild.name,
                        "action": action,
                        "reason": str(case.get("reason") or "No reason provided."),
                        "created_at": created,
                        "appeal_status": status,
                    })

        actions.sort(key=lambda x: x.get("created_at") if isinstance(x.get("created_at"), datetime) else datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return actions[:25]

    async def send_dm_menu(self, message: discord.Message) -> bool:
        if message.guild is not None or message.author.bot:
            return False
        raw = (message.content or "").strip().casefold()
        if raw not in {
            "!mommy", "!mommy appeal", "!mommy appeals",
            "!daddy", "!daddy appeal", "!daddy appeals",
        }:
            return False
        actions = await self.list_actions(message.author.id)
        if not actions:
            await message.channel.send(
                f"I don't see any appealable moderation actions from the last {APPEAL_WINDOW_DAYS} days for your account."
            )
            return True
        lines = []
        for item in actions[:8]:
            status = str(item.get("appeal_status") or "not appealed").replace("_", " ")
            lines.append(
                f"• **{_label_action(str(item.get('action') or 'action'))}** in **{item.get('guild_name')}** — {_fmt_dt(item.get('created_at'))} — *{status}*"
            )
        extra = len(actions) - min(8, len(actions))
        description = (
            "Choose the specific moderation action you want staff to review. You can appeal warnings, timeouts, VC automod actions, role-removal punishments, and 18+ VC disconnects when present.\n\n"
            + "\n".join(lines)
            + (f"\n\n…and **{extra}** more in the selector." if extra > 0 else "")
        )
        embed = discord.Embed(title="📨 Mommy.exe • Appeals", description=description[:4000])
        await message.channel.send(embed=embed, view=AppealMenuView(self, actions))
        return True

    async def _appeal_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        profile = await self.bot.database.get_guild_profile(guild.id) or {}
        child = profile.get("child_vc_guard") or {}
        automod = profile.get("vc_automod") or {}
        logs = profile.get("logs") or {}
        channel_id = child.get("appeal_channel_id") or automod.get("log_channel_id") or logs.get("channel_id")
        # Daddy's dashboard/log settings live in Daddy's separate database.
        # Use that private log channel as a fallback when Mommy does not yet
        # have her own appeal/log channel configured.
        if not channel_id:
            try:
                daddy_db = await self._daddy_db()
                daddy_profile = await asyncio.to_thread(daddy_db["guilds"].find_one, {"guild_id": guild.id}) or {}
                channel_id = (daddy_profile.get("logs") or {}).get("channel_id")
            except Exception:
                log.exception("Could not read Daddy log-channel fallback for guild %s", guild.id)
        if not channel_id:
            return None
        channel = guild.get_channel(int(channel_id))
        return channel if isinstance(channel, discord.TextChannel) else None

    async def submit_appeal(self, user: discord.abc.User, item: dict[str, Any], reason: str) -> tuple[bool, str]:
        guild = self.bot.get_guild(int(item.get("guild_id", 0) or 0))
        if guild is None:
            return False, "I can no longer access that server, so I can't submit this appeal."
        channel = await self._appeal_channel(guild)
        if channel is None:
            return False, "That server has not configured a private moderation/appeal channel yet."

        coll = await self._appeals()
        existing = await asyncio.to_thread(
            coll.find_one,
            {
                "user_id": int(user.id),
                "source_kind": str(item["source_kind"]),
                "source_id": str(item["source_id"]),
                "status": "submitted",
            },
        )
        if existing:
            return True, "✅ That action already has an appeal waiting for staff review."

        now = utcnow()
        doc = {
            "guild_id": guild.id,
            "guild_name": guild.name,
            "user_id": int(user.id),
            "display_name": getattr(user, "display_name", None) or getattr(user, "name", str(user.id)),
            "source_kind": str(item["source_kind"]),
            "source_id": str(item["source_id"]),
            "action": str(item.get("action") or "moderation action"),
            "original_reason": str(item.get("reason") or "No reason provided."),
            "original_created_at": item.get("created_at"),
            "appeal_reason": reason or "No explanation provided.",
            "status": "submitted",
            "created_at": now,
            "resolved_at": None,
            "resolved_by": None,
        }
        result = await asyncio.to_thread(coll.insert_one, doc)
        appeal_id = str(result.inserted_id)

        embed = discord.Embed(title="📨 Moderation Appeal", description=f"<@{user.id}> appealed a moderation action.")
        embed.add_field(name="Action", value=_label_action(doc["action"]), inline=True)
        embed.add_field(name="Original action", value=_fmt_dt(doc.get("original_created_at")), inline=True)
        embed.add_field(name="Original reason", value=doc["original_reason"][:1024], inline=False)
        embed.add_field(name="Member's appeal", value=doc["appeal_reason"][:1024], inline=False)
        embed.set_footer(text=f"Appeal ID {appeal_id} • Source {doc['source_kind']}")
        await channel.send(embed=embed, view=AppealReviewView(self, appeal_id))
        return True, "✅ Your appeal was sent to staff for review. Mommy will DM you when a decision is made."

    async def _reverse_if_possible(self, appeal: dict[str, Any]) -> str:
        guild = self.bot.get_guild(int(appeal.get("guild_id", 0) or 0))
        if guild is None:
            return "The appeal was approved, but the server is unavailable so nothing could be reversed automatically."
        member = guild.get_member(int(appeal.get("user_id", 0) or 0))
        if member is None:
            return "The appeal was approved, but the member is no longer in the server."

        action = str(appeal.get("action") or "").lower()
        source = str(appeal.get("source_kind") or "")
        notes: list[str] = []

        if "timeout" in action:
            try:
                await member.timeout(None, reason="Moderation appeal approved")
                notes.append("timeout cleared")
            except (discord.Forbidden, discord.HTTPException):
                notes.append("could not clear timeout")

        if action in {"remove_role", "remove_timeout"} or (source == "mommy_vc_automod" and action == "remove_timeout"):
            profile = await self.bot.database.get_guild_profile(guild.id) or {}
            role_id = (profile.get("verification") or {}).get("house_trained_role_id")
            role = guild.get_role(int(role_id)) if role_id else None
            if role is not None and role not in member.roles:
                try:
                    await member.add_roles(role, reason="Moderation appeal approved")
                    notes.append(f"restored {role.name}")
                except (discord.Forbidden, discord.HTTPException):
                    notes.append(f"could not restore {role.name}")

        if source == "child_vc_disconnect":
            profile = await self.bot.database.get_guild_profile(guild.id) or {}
            ids = []
            for value in (profile.get("child_vc_guard") or {}).get("exempt_user_ids") or []:
                try:
                    ids.append(int(value))
                except (TypeError, ValueError):
                    pass
            if member.id not in ids:
                ids.append(member.id)
                await self.bot.database.set_config_path(guild.id, "child_vc_guard.exempt_user_ids", ids)
                notes.append("added to 18+ VC automatic-detection exemptions")

        return ", ".join(notes) if notes else "No active Discord state needed to be reversed."

    async def resolve_appeal(self, appeal_id: str, decision: str, moderator_id: int) -> tuple[bool, str, dict[str, Any] | None]:
        appeal = await self.get_appeal(appeal_id)
        if not appeal:
            return False, "That appeal no longer exists.", None
        if appeal.get("status") != "submitted":
            return False, f"That appeal was already resolved as **{appeal.get('status')}**.", appeal

        reversal = ""
        if decision == "approved":
            reversal = await self._reverse_if_possible(appeal)

        coll = await self._appeals()
        now = utcnow()
        await asyncio.to_thread(
            coll.update_one,
            {"_id": appeal["_id"], "status": "submitted"},
            {"$set": {"status": decision, "resolved_at": now, "resolved_by": int(moderator_id), "reversal_result": reversal}},
        )
        appeal["status"] = decision

        user = self.bot.get_user(int(appeal.get("user_id", 0) or 0))
        if user is None:
            try:
                user = await self.bot.fetch_user(int(appeal.get("user_id", 0) or 0))
            except (discord.NotFound, discord.HTTPException):
                user = None
        if user is not None:
            try:
                if decision == "approved":
                    text = f"✅ **Your moderation appeal in {appeal.get('guild_name', 'the server')} was approved.**\n{reversal}"
                else:
                    text = f"❌ **Your moderation appeal in {appeal.get('guild_name', 'the server')} was denied.** The original moderation action remains on record."
                await user.send(text[:1900])
            except (discord.Forbidden, discord.HTTPException):
                pass

        return True, f"Appeal **{decision}**." + (f" {reversal}" if reversal else ""), appeal


async def setup(bot) -> None:
    await bot.add_cog(AppealsCog(bot))
