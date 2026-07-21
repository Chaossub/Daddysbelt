from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Any

import discord

from services.permissions import can_manage_bot, deny_access
from services.schedule_utils import (
    format_schedule_time,
    normalize_repeat,
    parse_local_datetime,
    validate_timezone,
)
from views.common import AuthorizedView
from views.dashboard import (
    BOT_VERSION,
    DashboardView,
    dashboard_embed,
)


def schedule_settings(
    profile: dict[str, Any],
) -> dict[str, Any]:
    return profile.get(
        "scheduled_messages",
        {
            "timezone": "America/Los_Angeles",
            "items": [],
        },
    )


def target_text(item: dict[str, Any]) -> str:
    if item.get("target_type") == "random_member":
        return "🎲 Random human member"

    user_id = item.get("user_id")
    return f"<@{user_id}>" if user_id else "No user selected"


def scheduled_embed(
    guild: discord.Guild,
    profile: dict[str, Any],
) -> discord.Embed:
    settings = schedule_settings(profile)
    items = settings.get("items", [])
    active = sum(
        1 for item in items if item.get("active", False)
    )

    embed = discord.Embed(
        title="⏰ Scheduled Messages",
        description=(
            f"**Managing:** "
            f"{discord.utils.escape_markdown(guild.name)}\n\n"
            "Schedule messages for a specific user or a "
            "random human member, with an optional custom header."
        ),
    )
    embed.add_field(name="Active", value=str(active))
    embed.add_field(name="Total Saved", value=str(len(items)))
    embed.add_field(
        name="Server Timezone",
        value=f"`{settings.get('timezone', 'America/Los_Angeles')}`",
        inline=False,
    )
    embed.add_field(
        name="Targets",
        value="Specific User · Random Human Member",
        inline=False,
    )
    embed.set_footer(text=f"Daddy's Belt v{BOT_VERSION}")
    return embed


async def return_to_scheduled(
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
        embed=scheduled_embed(interaction.guild, profile),
        view=ScheduledMessagesView(
            bot=bot,
            guild_id=guild_id,
            requester_id=requester_id,
            database_connected=database_connected,
        ),
    )


class TimezoneModal(
    discord.ui.Modal,
    title="Set Server Timezone",
):
    timezone_input = discord.ui.TextInput(
        label="IANA timezone",
        placeholder="America/Los_Angeles",
        min_length=3,
        max_length=64,
        required=True,
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
        if (
            interaction.guild is None
            or interaction.guild_id != self.guild_id
            or interaction.user.id != self.requester_id
        ):
            await interaction.response.send_message(
                "This form is no longer valid.",
                ephemeral=True,
            )
            return

        if not await can_manage_bot(interaction, self.bot.database):
            await deny_access(interaction)
            return

        try:
            timezone_name = validate_timezone(
                str(self.timezone_input.value)
            )
        except ValueError as error:
            await interaction.response.send_message(
                str(error),
                ephemeral=True,
            )
            return

        profile = await self.bot.database.set_schedule_timezone(
            self.guild_id,
            timezone_name,
        )

        await interaction.response.edit_message(
            embed=scheduled_embed(interaction.guild, profile),
            view=ScheduledMessagesView(
                bot=self.bot,
                guild_id=self.guild_id,
                requester_id=self.requester_id,
                database_connected=self.database_connected,
            ),
        )


class ScheduleModal(discord.ui.Modal):
    def __init__(
        self,
        *,
        title: str,
        bot,
        guild_id: int,
        requester_id: int,
        database_connected: bool,
        channel_id: int,
        user_id: int | None,
        target_type: str,
        timezone_name: str,
        schedule_id: str | None = None,
        existing_time: str = "",
        existing_repeat: str = "once",
        existing_header: str = "",
        existing_message: str = "",
    ) -> None:
        super().__init__(title=title)

        self.bot = bot
        self.guild_id = guild_id
        self.requester_id = requester_id
        self.database_connected = database_connected
        self.channel_id = channel_id
        self.user_id = user_id
        self.target_type = target_type
        self.timezone_name = timezone_name
        self.schedule_id = schedule_id

        self.date_time = discord.ui.TextInput(
            label="Date and time",
            placeholder="2026-07-20 20:00",
            default=existing_time or None,
            min_length=16,
            max_length=16,
            required=True,
        )
        self.repeat = discord.ui.TextInput(
            label="Repeat",
            placeholder="once, daily, weekly, or monthly",
            default=existing_repeat or "once",
            min_length=4,
            max_length=7,
            required=True,
        )
        self.header = discord.ui.TextInput(
            label="Optional header",
            placeholder="Daddy checked the calendar.",
            default=existing_header or None,
            min_length=0,
            max_length=200,
            required=False,
        )
        self.message = discord.ui.TextInput(
            label="Message",
            style=discord.TextStyle.paragraph,
            placeholder="Time to do the thing.",
            default=existing_message or None,
            min_length=1,
            max_length=1800,
            required=True,
        )

        self.add_item(self.date_time)
        self.add_item(self.repeat)
        self.add_item(self.header)
        self.add_item(self.message)

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if (
            interaction.guild is None
            or interaction.guild_id != self.guild_id
            or interaction.user.id != self.requester_id
        ):
            await interaction.response.send_message(
                "This form is no longer valid.",
                ephemeral=True,
            )
            return

        if not await can_manage_bot(interaction, self.bot.database):
            await deny_access(interaction)
            return

        try:
            next_run_at = parse_local_datetime(
                str(self.date_time.value),
                self.timezone_name,
            )
            repeat = normalize_repeat(
                str(self.repeat.value)
            )
        except ValueError as error:
            await interaction.response.send_message(
                str(error),
                ephemeral=True,
            )
            return

        if next_run_at <= datetime.now(timezone.utc):
            await interaction.response.send_message(
                "Choose a time in the future.",
                ephemeral=True,
            )
            return

        header = str(self.header.value or "").strip()
        content = str(self.message.value).strip()

        if self.schedule_id:
            profile = await self.bot.database.update_scheduled_message(
                self.guild_id,
                self.schedule_id,
                channel_id=self.channel_id,
                user_id=self.user_id,
                target_type=self.target_type,
                header=header,
                content=content,
                repeat=repeat,
                next_run_at=next_run_at,
                updated_by=interaction.user.id,
            )
        else:
            profile = await self.bot.database.add_scheduled_message(
                self.guild_id,
                channel_id=self.channel_id,
                user_id=self.user_id,
                target_type=self.target_type,
                header=header,
                content=content,
                repeat=repeat,
                next_run_at=next_run_at,
                created_by=interaction.user.id,
            )

        if profile is None:
            await interaction.response.send_message(
                "I couldn't save that scheduled message.",
                ephemeral=True,
            )
            return

        await interaction.response.edit_message(
            embed=scheduled_embed(interaction.guild, profile),
            view=ScheduledMessagesView(
                bot=self.bot,
                guild_id=self.guild_id,
                requester_id=self.requester_id,
                database_connected=self.database_connected,
            ),
        )


class ScheduleChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, parent: "ScheduleSetupView") -> None:
        super().__init__(
            placeholder="1. Choose the message channel",
            min_values=1,
            max_values=1,
            channel_types=[
                discord.ChannelType.text,
                discord.ChannelType.news,
            ],
            row=0,
        )
        self.parent_ref = parent

    async def callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        self.parent_ref.channel_id = self.values[0].id
        await interaction.response.send_message(
            f"Channel selected: <#{self.values[0].id}>",
            ephemeral=True,
        )


class ScheduleUserSelect(discord.ui.UserSelect):
    def __init__(self, parent: "ScheduleSetupView") -> None:
        super().__init__(
            placeholder="2. Choose a user (specific-user mode)",
            min_values=1,
            max_values=1,
            row=1,
        )
        self.parent_ref = parent

    async def callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        selected = self.values[0]

        if getattr(selected, "bot", False):
            await interaction.response.send_message(
                "Choose a human member, not a bot.",
                ephemeral=True,
            )
            return

        self.parent_ref.user_id = selected.id
        await interaction.response.send_message(
            f"User selected: <@{selected.id}>",
            ephemeral=True,
        )


class ScheduleSetupView(AuthorizedView):
    def __init__(
        self,
        *,
        bot,
        guild_id: int,
        requester_id: int,
        database_connected: bool,
        timezone_name: str,
        schedule_id: str | None = None,
        existing_item: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            guild_id=guild_id,
            requester_id=requester_id,
        )
        self.bot = bot
        self.database_connected = database_connected
        self.timezone_name = timezone_name
        self.schedule_id = schedule_id
        self.existing_item = existing_item or {}

        self.channel_id = self.existing_item.get("channel_id")
        self.user_id = self.existing_item.get("user_id")
        self.target_type = str(
            self.existing_item.get(
                "target_type",
                "specific_user",
            )
        )

        self.add_item(ScheduleChannelSelect(self))
        self.add_item(ScheduleUserSelect(self))

    @discord.ui.button(
        label="Specific / Random",
        emoji="🎯",
        style=discord.ButtonStyle.primary,
        row=2,
    )
    async def toggle_target(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        if self.target_type == "specific_user":
            self.target_type = "random_member"
            await interaction.response.send_message(
                "Target changed to **🎲 Random human member**. "
                "The user selector will be ignored.",
                ephemeral=True,
            )
        else:
            self.target_type = "specific_user"
            await interaction.response.send_message(
                "Target changed to **👤 Specific user**. "
                "Choose a user above.",
                ephemeral=True,
            )

    @discord.ui.button(
        label="Continue",
        emoji="➡️",
        style=discord.ButtonStyle.success,
        row=2,
    )
    async def continue_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        if self.channel_id is None:
            await interaction.response.send_message(
                "Choose a channel first.",
                ephemeral=True,
            )
            return

        if (
            self.target_type == "specific_user"
            and self.user_id is None
        ):
            await interaction.response.send_message(
                "Choose a specific user or switch to random-member mode.",
                ephemeral=True,
            )
            return

        existing_time = ""
        if self.existing_item.get("next_run_at"):
            existing_time = (
                self.existing_item["next_run_at"]
                .astimezone(
                    __import__("zoneinfo").ZoneInfo(
                        self.timezone_name
                    )
                )
                .strftime("%Y-%m-%d %H:%M")
            )

        await interaction.response.send_modal(
            ScheduleModal(
                title=(
                    "Edit Scheduled Message"
                    if self.schedule_id
                    else "Create Scheduled Message"
                ),
                bot=self.bot,
                guild_id=self.guild_id,
                requester_id=self.requester_id,
                database_connected=self.database_connected,
                channel_id=int(self.channel_id),
                user_id=(
                    int(self.user_id)
                    if self.user_id is not None
                    else None
                ),
                target_type=self.target_type,
                timezone_name=self.timezone_name,
                schedule_id=self.schedule_id,
                existing_time=existing_time,
                existing_repeat=str(
                    self.existing_item.get("repeat", "once")
                ),
                existing_header=str(
                    self.existing_item.get("header", "")
                ),
                existing_message=str(
                    self.existing_item.get("content", "")
                ),
            )
        )

    @discord.ui.button(
        label="Back",
        emoji="◀️",
        style=discord.ButtonStyle.secondary,
        row=3,
    )
    async def back(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await return_to_scheduled(
            interaction,
            bot=self.bot,
            guild_id=self.guild_id,
            requester_id=self.requester_id,
            database_connected=self.database_connected,
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


class ScheduleSelect(discord.ui.Select):
    def __init__(
        self,
        parent: "ManageSchedulesView",
        items: list[dict[str, Any]],
        timezone_name: str,
    ) -> None:
        options: list[discord.SelectOption] = []

        for item in items[:25]:
            when = format_schedule_time(
                item["next_run_at"],
                timezone_name,
            )
            content = str(item.get("content", "")).replace(
                "\n",
                " ",
            )

            options.append(
                discord.SelectOption(
                    label=content[:70] or "Untitled schedule",
                    description=(
                        f"{when} · {item.get('repeat', 'once')}"
                    )[:100],
                    value=str(item["_id"]),
                    emoji=(
                        "⏸️"
                        if not item.get("active", False)
                        else "⏰"
                    ),
                )
            )

        super().__init__(
            placeholder="Select a scheduled message",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )
        self.parent_ref = parent

    async def callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        self.parent_ref.selected_id = self.values[0]
        await interaction.response.send_message(
            "Selected. Use a button below.",
            ephemeral=True,
        )


class ManageSchedulesView(AuthorizedView):
    def __init__(
        self,
        *,
        bot,
        guild_id: int,
        requester_id: int,
        database_connected: bool,
        items: list[dict[str, Any]],
        timezone_name: str,
    ) -> None:
        super().__init__(
            guild_id=guild_id,
            requester_id=requester_id,
        )
        self.bot = bot
        self.database_connected = database_connected
        self.items = items
        self.timezone_name = timezone_name
        self.selected_id: str | None = None
        self.add_item(
            ScheduleSelect(
                self,
                items,
                timezone_name,
            )
        )

    def selected_item(self) -> dict[str, Any] | None:
        for item in self.items:
            if str(item.get("_id")) == self.selected_id:
                return item
        return None

    @discord.ui.button(
        label="Edit",
        emoji="✏️",
        style=discord.ButtonStyle.success,
        row=1,
    )
    async def edit(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        item = self.selected_item()
        if item is None:
            await interaction.response.send_message(
                "Select a schedule first.",
                ephemeral=True,
            )
            return

        await interaction.response.edit_message(
            embed=discord.Embed(
                title="✏️ Edit Scheduled Message",
                description=(
                    "Choose a new channel/user if needed.\n"
                    "Use **Specific / Random** to change the target.\n"
                    "Then press **Continue** to edit the header, message, "
                    "time, and repeat."
                ),
            ),
            view=ScheduleSetupView(
                bot=self.bot,
                guild_id=self.guild_id,
                requester_id=self.requester_id,
                database_connected=self.database_connected,
                timezone_name=self.timezone_name,
                schedule_id=str(item["_id"]),
                existing_item=item,
            ),
        )

    @discord.ui.button(
        label="Send Test",
        emoji="👀",
        style=discord.ButtonStyle.primary,
        row=1,
    )
    async def send_test(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        item = self.selected_item()
        if item is None or interaction.guild is None:
            await interaction.response.send_message(
                "Select a schedule first.",
                ephemeral=True,
            )
            return

        channel = await self.bot.get_text_channel(
            interaction.guild,
            int(item["channel_id"]),
        )
        if channel is None:
            await interaction.response.send_message(
                "I couldn't access the saved channel.",
                ephemeral=True,
            )
            return

        if item.get("target_type") == "random_member":
            eligible = [
                member
                for member in interaction.guild.members
                if not member.bot
            ]
            if not eligible:
                await interaction.response.send_message(
                    "There are no eligible human members.",
                    ephemeral=True,
                )
                return
            member = random.choice(eligible)
        else:
            try:
                member = interaction.guild.get_member(
                    int(item["user_id"])
                )
            except (TypeError, ValueError, KeyError):
                member = None

            if member is None:
                await interaction.response.send_message(
                    "That saved user is no longer in the server.",
                    ephemeral=True,
                )
                return

        profile = await self.bot.database.get_guild_profile(
            self.guild_id
        )
        mode = str(
            (profile or {}).get(
                "response_mode",
                "professional",
            )
        ).lower()

        header = str(item.get("header", "")).strip()
        content = str(item.get("content", "")).strip()

        parts = [member.mention]

        if header:
            parts.append(f"**{header}**")

        if content:
            parts.append(content)

        body = "\n\n".join(parts)

        try:
            await channel.send(
                "**Scheduled-message test:**\n" + body,
                allowed_mentions=discord.AllowedMentions(
                    users=True,
                    roles=False,
                    everyone=False,
                ),
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "I can see that channel but cannot send there.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"✅ Test sent to {channel.mention} and pinged "
            f"{member.mention}.",
            ephemeral=True,
        )

    @discord.ui.button(
        label="Pause / Resume",
        emoji="⏯️",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def toggle_active(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        item = self.selected_item()
        if item is None or interaction.guild is None:
            await interaction.response.send_message(
                "Select a schedule first.",
                ephemeral=True,
            )
            return

        profile = await self.bot.database.toggle_scheduled_message(
            self.guild_id,
            str(item["_id"]),
            not bool(item.get("active", False)),
        )

        await interaction.response.edit_message(
            embed=scheduled_embed(interaction.guild, profile),
            view=ScheduledMessagesView(
                bot=self.bot,
                guild_id=self.guild_id,
                requester_id=self.requester_id,
                database_connected=self.database_connected,
            ),
        )

    @discord.ui.button(
        label="Delete",
        emoji="🗑️",
        style=discord.ButtonStyle.danger,
        row=1,
    )
    async def delete(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        item = self.selected_item()
        if item is None or interaction.guild is None:
            await interaction.response.send_message(
                "Select a schedule first.",
                ephemeral=True,
            )
            return

        profile = await self.bot.database.delete_scheduled_message(
            self.guild_id,
            str(item["_id"]),
        )

        await interaction.response.edit_message(
            embed=scheduled_embed(interaction.guild, profile),
            view=ScheduledMessagesView(
                bot=self.bot,
                guild_id=self.guild_id,
                requester_id=self.requester_id,
                database_connected=self.database_connected,
            ),
        )

    @discord.ui.button(
        label="Back",
        emoji="◀️",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def back(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await return_to_scheduled(
            interaction,
            bot=self.bot,
            guild_id=self.guild_id,
            requester_id=self.requester_id,
            database_connected=self.database_connected,
        )

    @discord.ui.button(
        label="Dashboard",
        emoji="🏠",
        style=discord.ButtonStyle.primary,
        row=2,
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


class ScheduledMessagesView(AuthorizedView):
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
        )
        self.bot = bot
        self.database_connected = database_connected

    @discord.ui.button(
        label="Create Schedule",
        emoji="➕",
        style=discord.ButtonStyle.success,
        row=0,
    )
    async def create_schedule(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        assert interaction.guild is not None
        profile = await self.bot.database.ensure_guild_profile(
            interaction.guild
        )
        timezone_name = schedule_settings(profile).get(
            "timezone",
            "America/Los_Angeles",
        )

        await interaction.response.edit_message(
            embed=discord.Embed(
                title="➕ Create Scheduled Message",
                description=(
                    "Choose a channel.\n"
                    "Choose a user for specific-user mode, or press "
                    "**Specific / Random** for a random human member.\n\n"
                    f"Times use `{timezone_name}`."
                ),
            ),
            view=ScheduleSetupView(
                bot=self.bot,
                guild_id=self.guild_id,
                requester_id=self.requester_id,
                database_connected=self.database_connected,
                timezone_name=timezone_name,
            ),
        )

    @discord.ui.button(
        label="Manage Schedules",
        emoji="📋",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def manage_schedules(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        assert interaction.guild is not None
        profile = await self.bot.database.ensure_guild_profile(
            interaction.guild
        )
        settings = schedule_settings(profile)
        items = settings.get("items", [])

        if not items:
            await interaction.response.send_message(
                "There are no scheduled messages yet.",
                ephemeral=True,
            )
            return

        description = "\n\n".join(
            (
                f"**{index}.** {target_text(item)} in "
                f"<#{int(item['channel_id'])}>\n"
                f"{format_schedule_time(item['next_run_at'], settings['timezone'])}"
                f" · {item.get('repeat', 'once')} · "
                f"{'Active' if item.get('active', False) else 'Paused'}"
            )
            for index, item in enumerate(items[:25], start=1)
        )

        await interaction.response.edit_message(
            embed=discord.Embed(
                title="📋 Manage Scheduled Messages",
                description=description,
            ),
            view=ManageSchedulesView(
                bot=self.bot,
                guild_id=self.guild_id,
                requester_id=self.requester_id,
                database_connected=self.database_connected,
                items=items,
                timezone_name=settings["timezone"],
            ),
        )

    @discord.ui.button(
        label="Set Timezone",
        emoji="🌎",
        style=discord.ButtonStyle.secondary,
        row=0,
    )
    async def set_timezone(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await interaction.response.send_modal(
            TimezoneModal(
                bot=self.bot,
                guild_id=self.guild_id,
                requester_id=self.requester_id,
                database_connected=self.database_connected,
            )
        )

    @discord.ui.button(
        label="Back",
        emoji="◀️",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def back(
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

    @discord.ui.button(
        label="Dashboard",
        emoji="🏠",
        style=discord.ButtonStyle.primary,
        row=1,
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
