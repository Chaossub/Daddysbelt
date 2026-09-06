from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import discord

from services.permissions import can_manage_bot, deny_access
from views.common import AuthorizedView
from views.dashboard import (
    BOT_VERSION,
    DashboardView,
    dashboard_embed,
)


TIMEOUT_PLACEHOLDERS = (
    "`{mention}` `{username}` `{minutes}` "
    "`{reason}` `{moderator}`"
)


def moderation_embed(
    guild: discord.Guild,
    profile: dict[str, Any],
) -> discord.Embed:
    embed = discord.Embed(
        title="🛡️ Moderation",
        description=(
            f"**Managing:** "
            f"{discord.utils.escape_markdown(guild.name)}\n\n"
            "Select a member, then choose an action."
        ),
    )
    embed.add_field(
        name="Member Actions",
        value=(
            "⚠️ Warn\n"
            "⏱️ Timeout\n"
            "🕒 Change Duration\n"
            "🔓 Remove Timeout\n"
            "👢 Kick\n"
            "🔨 Ban"
        ),
        inline=True,
    )
    embed.add_field(
        name="Channel Action",
        value="🧹 Purge recent messages",
        inline=True,
    )
    embed.add_field(
        name="Timeout Announcements",
        value=(
            "Timeout and duration-change forms support an optional "
            "custom public message.\n\n"
            f"Placeholders: {TIMEOUT_PLACEHOLDERS}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Safety",
        value=(
            "Timeout, duration changes, timeout removal, kick, ban, "
            "and purge all require confirmation."
        ),
        inline=False,
    )
    embed.set_footer(text=f"Daddy's Belt v{BOT_VERSION}")
    return embed


async def return_to_moderation(
    interaction: discord.Interaction,
    *,
    bot,
    guild_id: int,
    requester_id: int,
    database_connected: bool,
) -> None:
    assert interaction.guild is not None

    profile = await bot.database.ensure_guild_profile(
        interaction.guild
    )

    await interaction.response.edit_message(
        embed=moderation_embed(
            interaction.guild,
            profile,
        ),
        view=ModerationView(
            bot=bot,
            guild_id=guild_id,
            requester_id=requester_id,
            database_connected=database_connected,
        ),
    )


def can_target(
    interaction: discord.Interaction,
    member: discord.Member,
) -> tuple[bool, str]:
    guild = interaction.guild
    if guild is None:
        return False, "This action must be used inside a server."

    if member.bot:
        return False, "Bots cannot be targeted from this panel."

    if member.id == guild.owner_id:
        return False, "The server owner cannot be targeted."

    if member.id == interaction.user.id:
        return False, "You cannot target yourself from this panel."

    bot_member = guild.me
    if bot_member is None:
        return False, "I could not determine my server role."

    if member.top_role >= bot_member.top_role:
        return (
            False,
            "That member's highest role is equal to or above mine.",
        )

    actor = interaction.user
    if isinstance(actor, discord.Member):
        if (
            actor.id != guild.owner_id
            and member.top_role >= actor.top_role
        ):
            return (
                False,
                "You cannot moderate someone with an equal or higher role.",
            )

    return True, ""


def render_timeout_announcement(
    template: str,
    *,
    member: discord.Member,
    moderator: discord.abc.User,
    minutes: int,
    reason: str,
) -> str:
    replacements = {
        "{mention}": member.mention,
        "{username}": member.name,
        "{minutes}": str(minutes),
        "{reason}": reason,
        "{moderator}": moderator.mention,
    }

    rendered = template
    for placeholder, replacement in replacements.items():
        rendered = rendered.replace(
            placeholder,
            replacement,
        )

    return rendered[:2000]


async def send_public_timeout_message(
    interaction: discord.Interaction,
    *,
    member: discord.Member,
    minutes: int,
    reason: str,
    template: str,
) -> None:
    if not template.strip():
        return

    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        return

    rendered = render_timeout_announcement(
        template,
        member=member,
        moderator=interaction.user,
        minutes=minutes,
        reason=reason,
    )

    try:
        await channel.send(
            rendered,
            allowed_mentions=discord.AllowedMentions(
                users=True,
                roles=False,
                everyone=False,
            ),
        )
    except (
        discord.Forbidden,
        discord.HTTPException,
    ):
        # The moderation action itself should still succeed even if
        # the optional announcement cannot be sent.
        return


class WarningModal(
    discord.ui.Modal,
    title="Warn Member",
):
    reason = discord.ui.TextInput(
        label="Reason",
        style=discord.TextStyle.paragraph,
        placeholder="Reason for warning",
        default="No reason provided.",
        min_length=1,
        max_length=500,
    )

    def __init__(
        self,
        *,
        bot,
        guild_id: int,
        requester_id: int,
        member_id: int,
    ) -> None:
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id
        self.requester_id = requester_id
        self.member_id = member_id

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if interaction.guild is None:
            return

        member = interaction.guild.get_member(
            self.member_id
        )
        if member is None:
            await interaction.response.send_message(
                "That member is no longer in the server.",
                ephemeral=True,
            )
            return

        allowed, error = can_target(
            interaction,
            member,
        )
        if not allowed:
            await interaction.response.send_message(
                error,
                ephemeral=True,
            )
            return

        reason = str(self.reason.value).strip()

        await self.bot.database.add_moderation_case(
            self.guild_id,
            action="warn",
            target_id=member.id,
            moderator_id=interaction.user.id,
            reason=reason,
        )

        await interaction.response.send_message(
            f"⚠️ Warned {member.mention}: {reason}",
            ephemeral=True,
        )


class TimeoutDetailsModal(discord.ui.Modal):
    def __init__(
        self,
        *,
        title: str,
        bot,
        guild_id: int,
        requester_id: int,
        database_connected: bool,
        member_id: int,
        action: str,
    ) -> None:
        super().__init__(title=title)

        self.bot = bot
        self.guild_id = guild_id
        self.requester_id = requester_id
        self.database_connected = database_connected
        self.member_id = member_id
        self.action = action

        self.minutes = discord.ui.TextInput(
            label="Duration in minutes",
            placeholder="10",
            default="10",
            min_length=1,
            max_length=5,
        )
        self.reason = discord.ui.TextInput(
            label="Reason",
            style=discord.TextStyle.paragraph,
            placeholder="Reason for timeout",
            default="No reason provided.",
            min_length=1,
            max_length=500,
        )
        self.public_message = discord.ui.TextInput(
            label="Optional public message",
            style=discord.TextStyle.paragraph,
            placeholder=(
                "{mention} has been timed out for {minutes} minutes.\n"
                "Reason: {reason}"
            ),
            required=False,
            max_length=1800,
        )

        self.add_item(self.minutes)
        self.add_item(self.reason)
        self.add_item(self.public_message)

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if interaction.guild is None:
            return

        try:
            minutes = int(
                str(self.minutes.value).strip()
            )
        except ValueError:
            await interaction.response.send_message(
                "Duration must be a whole number of minutes.",
                ephemeral=True,
            )
            return

        if minutes < 1 or minutes > 40320:
            await interaction.response.send_message(
                "Timeout duration must be between 1 minute and 28 days.",
                ephemeral=True,
            )
            return

        member = interaction.guild.get_member(
            self.member_id
        )
        if member is None:
            await interaction.response.send_message(
                "That member is no longer in the server.",
                ephemeral=True,
            )
            return

        allowed, error = can_target(
            interaction,
            member,
        )
        if not allowed:
            await interaction.response.send_message(
                error,
                ephemeral=True,
            )
            return

        is_timed_out = (
            member.timed_out_until is not None
            and member.timed_out_until
            > datetime.now(timezone.utc)
        )

        if self.action == "timeout_changed" and not is_timed_out:
            await interaction.response.send_message(
                "That member is not currently timed out.",
                ephemeral=True,
            )
            return

        reason = str(self.reason.value).strip()
        public_message = str(
            self.public_message.value or ""
        ).strip()

        action_title = (
            "Change Timeout Duration"
            if self.action == "timeout_changed"
            else "Apply Timeout"
        )

        announcement_preview = (
            render_timeout_announcement(
                public_message,
                member=member,
                moderator=interaction.user,
                minutes=minutes,
                reason=reason,
            )
            if public_message
            else "No public announcement"
        )

        await interaction.response.edit_message(
            embed=discord.Embed(
                title=f"Confirm {action_title}",
                description=(
                    f"**Target:** {member.mention}\n"
                    f"**New duration:** {minutes} minutes\n"
                    f"**Reason:** {reason}\n\n"
                    f"**Public message:**\n"
                    f"{announcement_preview}"
                ),
            ),
            view=ConfirmTimeoutView(
                bot=self.bot,
                guild_id=self.guild_id,
                requester_id=self.requester_id,
                database_connected=self.database_connected,
                member_id=member.id,
                action=self.action,
                minutes=minutes,
                reason=reason,
                public_message=public_message,
            ),
        )


class RemoveTimeoutReasonModal(
    discord.ui.Modal,
    title="Remove Timeout",
):
    reason = discord.ui.TextInput(
        label="Reason",
        style=discord.TextStyle.paragraph,
        placeholder="Appeal accepted, mistake, time served...",
        default="Timeout removed.",
        min_length=1,
        max_length=500,
    )

    def __init__(
        self,
        *,
        bot,
        guild_id: int,
        requester_id: int,
        database_connected: bool,
        member_id: int,
    ) -> None:
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id
        self.requester_id = requester_id
        self.database_connected = database_connected
        self.member_id = member_id

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if interaction.guild is None:
            return

        member = interaction.guild.get_member(
            self.member_id
        )
        if member is None:
            await interaction.response.send_message(
                "That member is no longer in the server.",
                ephemeral=True,
            )
            return

        allowed, error = can_target(
            interaction,
            member,
        )
        if not allowed:
            await interaction.response.send_message(
                error,
                ephemeral=True,
            )
            return

        is_timed_out = (
            member.timed_out_until is not None
            and member.timed_out_until
            > datetime.now(timezone.utc)
        )
        if not is_timed_out:
            await interaction.response.send_message(
                "That member is not currently timed out.",
                ephemeral=True,
            )
            return

        reason = str(self.reason.value).strip()

        await interaction.response.edit_message(
            embed=discord.Embed(
                title="Confirm Remove Timeout",
                description=(
                    f"**Target:** {member.mention}\n"
                    f"**Reason:** {reason}\n\n"
                    "This will immediately restore their ability to chat."
                ),
            ),
            view=ConfirmRemoveTimeoutView(
                bot=self.bot,
                guild_id=self.guild_id,
                requester_id=self.requester_id,
                database_connected=self.database_connected,
                member_id=member.id,
                reason=reason,
            ),
        )


class ConfirmTimeoutView(AuthorizedView):
    def __init__(
        self,
        *,
        bot,
        guild_id: int,
        requester_id: int,
        database_connected: bool,
        member_id: int,
        action: str,
        minutes: int,
        reason: str,
        public_message: str,
    ) -> None:
        super().__init__(
            guild_id=guild_id,
            requester_id=requester_id,
            minimum_access="moderator",
        )
        self.bot = bot
        self.database_connected = database_connected
        self.member_id = member_id
        self.action = action
        self.minutes = minutes
        self.reason = reason
        self.public_message = public_message

    @discord.ui.button(
        label="Confirm",
        emoji="✅",
        style=discord.ButtonStyle.danger,
    )
    async def confirm(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        assert interaction.guild is not None

        member = interaction.guild.get_member(
            self.member_id
        )
        if member is None:
            await interaction.response.send_message(
                "That member is no longer in the server.",
                ephemeral=True,
            )
            return

        allowed, error = can_target(
            interaction,
            member,
        )
        if not allowed:
            await interaction.response.send_message(
                error,
                ephemeral=True,
            )
            return

        try:
            await member.timeout(
                timedelta(minutes=self.minutes),
                reason=self.reason,
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "I do not have permission to timeout that member.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.response.send_message(
                "Discord rejected the timeout request.",
                ephemeral=True,
            )
            return

        await self.bot.database.add_moderation_case(
            self.guild_id,
            action=self.action,
            target_id=member.id,
            moderator_id=interaction.user.id,
            reason=self.reason,
            duration_minutes=self.minutes,
        )

        await send_public_timeout_message(
            interaction,
            member=member,
            minutes=self.minutes,
            reason=self.reason,
            template=self.public_message,
        )

        action_text = (
            "Changed the timeout duration for"
            if self.action == "timeout_changed"
            else "Timed out"
        )

        await interaction.response.send_message(
            f"⏱️ {action_text} {member.mention} "
            f"to {self.minutes} minutes.",
            ephemeral=True,
        )

    @discord.ui.button(
        label="Cancel",
        emoji="✖️",
        style=discord.ButtonStyle.secondary,
    )
    async def cancel(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await return_to_moderation(
            interaction,
            bot=self.bot,
            guild_id=self.guild_id,
            requester_id=self.requester_id,
            database_connected=self.database_connected,
        )


class ConfirmRemoveTimeoutView(AuthorizedView):
    def __init__(
        self,
        *,
        bot,
        guild_id: int,
        requester_id: int,
        database_connected: bool,
        member_id: int,
        reason: str,
    ) -> None:
        super().__init__(
            guild_id=guild_id,
            requester_id=requester_id,
            minimum_access="moderator",
        )
        self.bot = bot
        self.database_connected = database_connected
        self.member_id = member_id
        self.reason = reason

    @discord.ui.button(
        label="Confirm Remove Timeout",
        emoji="🔓",
        style=discord.ButtonStyle.success,
    )
    async def confirm(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        assert interaction.guild is not None

        member = interaction.guild.get_member(
            self.member_id
        )
        if member is None:
            await interaction.response.send_message(
                "That member is no longer in the server.",
                ephemeral=True,
            )
            return

        allowed, error = can_target(
            interaction,
            member,
        )
        if not allowed:
            await interaction.response.send_message(
                error,
                ephemeral=True,
            )
            return

        try:
            await member.timeout(
                None,
                reason=self.reason,
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "I do not have permission to remove that timeout.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.response.send_message(
                "Discord rejected the timeout-removal request.",
                ephemeral=True,
            )
            return

        await self.bot.database.add_moderation_case(
            self.guild_id,
            action="timeout_removed",
            target_id=member.id,
            moderator_id=interaction.user.id,
            reason=self.reason,
        )

        await interaction.response.send_message(
            f"🔓 Removed the timeout from {member.mention}.",
            ephemeral=True,
        )

    @discord.ui.button(
        label="Cancel",
        emoji="✖️",
        style=discord.ButtonStyle.secondary,
    )
    async def cancel(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await return_to_moderation(
            interaction,
            bot=self.bot,
            guild_id=self.guild_id,
            requester_id=self.requester_id,
            database_connected=self.database_connected,
        )


class MemberActionReasonModal(discord.ui.Modal):
    def __init__(
        self,
        *,
        title: str,
        bot,
        guild_id: int,
        requester_id: int,
        database_connected: bool,
        member_id: int,
        action: str,
    ) -> None:
        super().__init__(title=title)

        self.bot = bot
        self.guild_id = guild_id
        self.requester_id = requester_id
        self.database_connected = database_connected
        self.member_id = member_id
        self.action = action

        self.reason = discord.ui.TextInput(
            label="Reason",
            style=discord.TextStyle.paragraph,
            placeholder="Reason for this action",
            default="No reason provided.",
            min_length=1,
            max_length=500,
        )
        self.add_item(self.reason)

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if interaction.guild is None:
            return

        member = interaction.guild.get_member(
            self.member_id
        )
        if member is None:
            await interaction.response.send_message(
                "That member is no longer in the server.",
                ephemeral=True,
            )
            return

        allowed, error = can_target(
            interaction,
            member,
        )
        if not allowed:
            await interaction.response.send_message(
                error,
                ephemeral=True,
            )
            return

        reason = str(self.reason.value).strip()

        await interaction.response.edit_message(
            embed=discord.Embed(
                title=f"Confirm {self.action.title()}",
                description=(
                    f"**Target:** {member.mention}\n"
                    f"**Reason:** {reason}\n\n"
                    "This action cannot be undone from this panel."
                ),
            ),
            view=ConfirmMemberActionView(
                bot=self.bot,
                guild_id=self.guild_id,
                requester_id=self.requester_id,
                database_connected=self.database_connected,
                member_id=member.id,
                action=self.action,
                reason=reason,
            ),
        )


class ConfirmMemberActionView(AuthorizedView):
    def __init__(
        self,
        *,
        bot,
        guild_id: int,
        requester_id: int,
        database_connected: bool,
        member_id: int,
        action: str,
        reason: str,
    ) -> None:
        super().__init__(
            guild_id=guild_id,
            requester_id=requester_id,
            minimum_access="moderator",
        )
        self.bot = bot
        self.database_connected = database_connected
        self.member_id = member_id
        self.action = action
        self.reason = reason

    @discord.ui.button(
        label="Confirm",
        emoji="✅",
        style=discord.ButtonStyle.danger,
    )
    async def confirm(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        assert interaction.guild is not None

        member = interaction.guild.get_member(
            self.member_id
        )
        if member is None:
            await interaction.response.send_message(
                "That member is no longer in the server.",
                ephemeral=True,
            )
            return

        allowed, error = can_target(
            interaction,
            member,
        )
        if not allowed:
            await interaction.response.send_message(
                error,
                ephemeral=True,
            )
            return

        try:
            if self.action == "kick":
                await member.kick(
                    reason=self.reason
                )
            elif self.action == "ban":
                await interaction.guild.ban(
                    member,
                    reason=self.reason,
                    delete_message_seconds=0,
                )
            else:
                await interaction.response.send_message(
                    "Unknown moderation action.",
                    ephemeral=True,
                )
                return
        except discord.Forbidden:
            await interaction.response.send_message(
                f"I do not have permission to {self.action} that member.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.response.send_message(
                f"Discord rejected the {self.action} request.",
                ephemeral=True,
            )
            return

        await self.bot.database.add_moderation_case(
            self.guild_id,
            action=self.action,
            target_id=member.id,
            moderator_id=interaction.user.id,
            reason=self.reason,
        )

        await interaction.response.send_message(
            f"✅ {self.action.title()} completed for {member}.",
            ephemeral=True,
        )

    @discord.ui.button(
        label="Cancel",
        emoji="✖️",
        style=discord.ButtonStyle.secondary,
    )
    async def cancel(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await return_to_moderation(
            interaction,
            bot=self.bot,
            guild_id=self.guild_id,
            requester_id=self.requester_id,
            database_connected=self.database_connected,
        )


class PurgeDetailsModal(
    discord.ui.Modal,
    title="Purge Messages",
):
    amount = discord.ui.TextInput(
        label="Number of recent messages",
        placeholder="10",
        default="10",
        min_length=1,
        max_length=3,
    )
    reason = discord.ui.TextInput(
        label="Reason",
        placeholder="Spam cleanup",
        default="Channel cleanup",
        min_length=1,
        max_length=200,
    )

    def __init__(
        self,
        *,
        bot,
        guild_id: int,
        requester_id: int,
        database_connected: bool,
    ) -> None:
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id
        self.requester_id = requester_id
        self.database_connected = database_connected

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ) -> None:
        channel = interaction.channel
        if not isinstance(
            channel,
            discord.TextChannel,
        ):
            await interaction.response.send_message(
                "Purge must be used in a text channel.",
                ephemeral=True,
            )
            return

        try:
            amount = int(
                str(self.amount.value).strip()
            )
        except ValueError:
            await interaction.response.send_message(
                "Message count must be a whole number.",
                ephemeral=True,
            )
            return

        if amount < 1 or amount > 100:
            await interaction.response.send_message(
                "Choose between 1 and 100 messages.",
                ephemeral=True,
            )
            return

        reason = str(self.reason.value).strip()

        await interaction.response.edit_message(
            embed=discord.Embed(
                title="Confirm Purge",
                description=(
                    f"You are about to delete up to "
                    f"**{amount} recent messages** from "
                    f"{channel.mention}.\n\n"
                    f"**Reason:** {reason}\n\n"
                    "Deleted messages cannot be restored."
                ),
            ),
            view=ConfirmPurgeView(
                bot=self.bot,
                guild_id=self.guild_id,
                requester_id=self.requester_id,
                database_connected=self.database_connected,
                channel_id=channel.id,
                amount=amount,
                reason=reason,
            ),
        )


class ConfirmPurgeView(AuthorizedView):
    def __init__(
        self,
        *,
        bot,
        guild_id: int,
        requester_id: int,
        database_connected: bool,
        channel_id: int,
        amount: int,
        reason: str,
    ) -> None:
        super().__init__(
            guild_id=guild_id,
            requester_id=requester_id,
            minimum_access="moderator",
        )
        self.bot = bot
        self.database_connected = database_connected
        self.channel_id = channel_id
        self.amount = amount
        self.reason = reason

    @discord.ui.button(
        label="Confirm Purge",
        emoji="🧹",
        style=discord.ButtonStyle.danger,
    )
    async def confirm(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        assert interaction.guild is not None

        channel = interaction.guild.get_channel(
            self.channel_id
        )
        if not isinstance(
            channel,
            discord.TextChannel,
        ):
            await interaction.response.send_message(
                "That channel is no longer available.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        try:
            deleted = await channel.purge(
                limit=self.amount,
                reason=self.reason,
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "I do not have permission to manage messages there.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.followup.send(
                "Discord rejected the purge request.",
                ephemeral=True,
            )
            return

        await self.bot.database.add_moderation_case(
            self.guild_id,
            action="purge",
            target_id=None,
            moderator_id=interaction.user.id,
            reason=self.reason,
            message_count=len(deleted),
        )

        await interaction.followup.send(
            f"🧹 Deleted {len(deleted)} messages.",
            ephemeral=True,
        )

    @discord.ui.button(
        label="Cancel",
        emoji="✖️",
        style=discord.ButtonStyle.secondary,
    )
    async def cancel(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await return_to_moderation(
            interaction,
            bot=self.bot,
            guild_id=self.guild_id,
            requester_id=self.requester_id,
            database_connected=self.database_connected,
        )


class MemberSelect(discord.ui.UserSelect):
    def __init__(
        self,
        parent: "ModerationView",
    ) -> None:
        super().__init__(
            placeholder="Select a member to moderate",
            min_values=1,
            max_values=1,
            row=0,
        )
        self.parent_ref = parent

    async def callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        selected = self.values[0]
        self.parent_ref.member_id = selected.id

        await interaction.response.send_message(
            f"Selected {selected.mention}.",
            ephemeral=True,
        )


class ModerationView(AuthorizedView):
    def __init__(
        self,
        *,
        bot,
        guild_id: int,
        requester_id: int,
        database_connected: bool,
    ) -> None:
        super().__init__(
            guild_id=guild_id,
            requester_id=requester_id,
            minimum_access="moderator",
        )
        self.bot = bot
        self.database_connected = database_connected
        self.member_id: int | None = None
        self.add_item(MemberSelect(self))

    async def require_member(
        self,
        interaction: discord.Interaction,
    ) -> discord.Member | None:
        if self.member_id is None:
            await interaction.response.send_message(
                "Select a member first.",
                ephemeral=True,
            )
            return None

        assert interaction.guild is not None

        member = interaction.guild.get_member(
            self.member_id
        )
        if member is None:
            await interaction.response.send_message(
                "That member is no longer in the server.",
                ephemeral=True,
            )
            return None

        return member

    @discord.ui.button(
        label="Warn",
        emoji="⚠️",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def warn(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        member = await self.require_member(
            interaction
        )
        if member is None:
            return

        await interaction.response.send_modal(
            WarningModal(
                bot=self.bot,
                guild_id=self.guild_id,
                requester_id=self.requester_id,
                member_id=member.id,
            )
        )

    @discord.ui.button(
        label="Timeout",
        emoji="⏱️",
        style=discord.ButtonStyle.primary,
        row=1,
    )
    async def timeout_member(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        member = await self.require_member(
            interaction
        )
        if member is None:
            return

        await interaction.response.send_modal(
            TimeoutDetailsModal(
                title="Timeout Member",
                bot=self.bot,
                guild_id=self.guild_id,
                requester_id=self.requester_id,
                database_connected=self.database_connected,
                member_id=member.id,
                action="timeout",
            )
        )

    @discord.ui.button(
        label="Change Duration",
        emoji="🕒",
        style=discord.ButtonStyle.primary,
        row=1,
    )
    async def change_duration(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        member = await self.require_member(
            interaction
        )
        if member is None:
            return

        is_timed_out = (
            member.timed_out_until is not None
            and member.timed_out_until
            > datetime.now(timezone.utc)
        )
        if not is_timed_out:
            await interaction.response.send_message(
                "That member is not currently timed out.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            TimeoutDetailsModal(
                title="Change Timeout Duration",
                bot=self.bot,
                guild_id=self.guild_id,
                requester_id=self.requester_id,
                database_connected=self.database_connected,
                member_id=member.id,
                action="timeout_changed",
            )
        )

    @discord.ui.button(
        label="Remove Timeout",
        emoji="🔓",
        style=discord.ButtonStyle.success,
        row=2,
    )
    async def remove_timeout(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        member = await self.require_member(
            interaction
        )
        if member is None:
            return

        is_timed_out = (
            member.timed_out_until is not None
            and member.timed_out_until
            > datetime.now(timezone.utc)
        )
        if not is_timed_out:
            await interaction.response.send_message(
                "That member is not currently timed out.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            RemoveTimeoutReasonModal(
                bot=self.bot,
                guild_id=self.guild_id,
                requester_id=self.requester_id,
                database_connected=self.database_connected,
                member_id=member.id,
            )
        )

    @discord.ui.button(
        label="Kick",
        emoji="👢",
        style=discord.ButtonStyle.danger,
        row=2,
    )
    async def kick(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        member = await self.require_member(
            interaction
        )
        if member is None:
            return

        await interaction.response.send_modal(
            MemberActionReasonModal(
                title="Kick Member",
                bot=self.bot,
                guild_id=self.guild_id,
                requester_id=self.requester_id,
                database_connected=self.database_connected,
                member_id=member.id,
                action="kick",
            )
        )

    @discord.ui.button(
        label="Ban",
        emoji="🔨",
        style=discord.ButtonStyle.danger,
        row=2,
    )
    async def ban(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        member = await self.require_member(
            interaction
        )
        if member is None:
            return

        await interaction.response.send_modal(
            MemberActionReasonModal(
                title="Ban Member",
                bot=self.bot,
                guild_id=self.guild_id,
                requester_id=self.requester_id,
                database_connected=self.database_connected,
                member_id=member.id,
                action="ban",
            )
        )

    @discord.ui.button(
        label="Purge",
        emoji="🧹",
        style=discord.ButtonStyle.secondary,
        row=3,
    )
    async def purge(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await interaction.response.send_modal(
            PurgeDetailsModal(
                bot=self.bot,
                guild_id=self.guild_id,
                requester_id=self.requester_id,
                database_connected=self.database_connected,
            )
        )

    @discord.ui.button(
        label="Dashboard",
        emoji="🏠",
        style=discord.ButtonStyle.primary,
        row=3,
    )
    async def dashboard(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        assert interaction.guild is not None

        await interaction.response.edit_message(
            embed=dashboard_embed(
                interaction.guild,
                database_connected=self.database_connected,
            ),
            view=DashboardView(
                bot=self.bot,
                guild_id=self.guild_id,
                requester_id=self.requester_id,
                database_connected=self.database_connected,
            ),
        )
