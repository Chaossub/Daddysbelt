from __future__ import annotations

from dataclasses import dataclass

import discord

from views.common import AuthorizedView

BOT_VERSION = "5.2.0-member-games"


@dataclass(frozen=True, slots=True)
class Section:
    title: str
    emoji: str
    description: str


SECTIONS: dict[str, Section] = {
    "triggers": Section(
        title="Triggers",
        emoji="🎭",
        description=(
            "Build trigger replies and automations for this server."
        ),
    ),
    "moderation": Section(
        title="Moderation",
        emoji="🛡️",
        description=(
            "Manage timeouts, warnings, cleanup tools, and moderation settings."
        ),
    ),
    "logs": Section(
        title="Logs",
        emoji="📋",
        description=(
            "View server logs and moderation activity."
        ),
    ),
    "access": Section(
        title="Access Control",
        emoji="🔐",
        description=(
            "Manage who can use Daddy's tools and dashboard."
        ),
    ),
    "statistics": Section(
        title="Statistics",
        emoji="📊",
        description=(
            "Check activity, usage, and server stats."
        ),
    ),
    "settings": Section(
        title="Settings",
        emoji="⚙️",
        description=(
            "Adjust Daddy's behavior and server settings."
        ),
    ),
}


def dashboard_embed(
    guild: discord.Guild,
    *,
    database_connected: bool,
) -> discord.Embed:
    embed = discord.Embed(
        title="Daddy's Belt",
        description=(
            f"Managing **{discord.utils.escape_markdown(guild.name)}**.\n\n"
            "What are we handling?"
        ),
    )
    embed.add_field(
        name="Status",
        value=(
            "🟢 Online"
            if database_connected
            else "🔴 Offline"
        ),
    )
    embed.add_field(
        name="Build",
        value="Member Events • Phase 4B",
    )
    embed.set_footer(
        text=f"Daddy's Belt v{BOT_VERSION}"
    )
    return embed


def placeholder_embed(
    guild: discord.Guild,
    section: Section,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"{section.emoji} {section.title}",
        description=section.description,
    )
    embed.add_field(
        name="Managing",
        value=discord.utils.escape_markdown(
            guild.name
        ),
        inline=False,
    )
    embed.set_footer(
        text=f"Daddy's Belt v{BOT_VERSION}"
    )
    return embed


class DashboardView(AuthorizedView):
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
        self.database_connected = (
            database_connected
        )

    @discord.ui.button(
        label="Member Events",
        emoji="👥",
        style=discord.ButtonStyle.success,
        row=0,
    )
    async def welcome(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        from views.member_events import MemberEventsView, member_events_embed
        assert interaction.guild is not None
        profile = await self.bot.database.ensure_guild_profile(interaction.guild)
        await interaction.response.edit_message(
            embed=member_events_embed(interaction.guild, profile),
            view=MemberEventsView(
                bot=self.bot, guild_id=self.guild_id,
                requester_id=self.requester_id,
                database_connected=self.database_connected,
            ),
        )


    @discord.ui.button(
        label="Scheduled",
        emoji="⏰",
        style=discord.ButtonStyle.success,
        row=0,
    )
    async def scheduled(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        from views.scheduled import (
            ScheduledMessagesView,
            scheduled_embed,
        )

        assert interaction.guild is not None
        profile = await self.bot.database.ensure_guild_profile(
            interaction.guild
        )

        await interaction.response.edit_message(
            embed=scheduled_embed(
                interaction.guild,
                profile,
            ),
            view=ScheduledMessagesView(
                bot=self.bot,
                guild_id=self.guild_id,
                requester_id=self.requester_id,
                database_connected=self.database_connected,
            ),
        )

    async def open_placeholder(
        self,
        interaction: discord.Interaction,
        key: str,
    ) -> None:
        assert interaction.guild is not None

        await interaction.response.edit_message(
            embed=placeholder_embed(
                interaction.guild,
                SECTIONS[key],
            ),
            view=PlaceholderView(
                bot=self.bot,
                guild_id=self.guild_id,
                requester_id=self.requester_id,
                database_connected=(
                    self.database_connected
                ),
            ),
        )

    @discord.ui.button(
        label="Triggers",
        emoji="🎭",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def triggers(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        from views.triggers import (
            TriggersView,
            trigger_embed,
        )

        assert interaction.guild is not None
        profile = await self.bot.database.ensure_guild_profile(
            interaction.guild
        )

        await interaction.response.edit_message(
            embed=trigger_embed(
                interaction.guild,
                profile,
            ),
            view=TriggersView(
                bot=self.bot,
                guild_id=self.guild_id,
                requester_id=self.requester_id,
                database_connected=self.database_connected,
            ),
        )

    @discord.ui.button(
        label="Moderation",
        emoji="🛡️",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def moderation(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        from views.moderation import (
            ModerationView,
            moderation_embed,
        )

        assert interaction.guild is not None
        profile = await self.bot.database.ensure_guild_profile(
            interaction.guild
        )

        await interaction.response.edit_message(
            embed=moderation_embed(
                interaction.guild,
                profile,
            ),
            view=ModerationView(
                bot=self.bot,
                guild_id=self.guild_id,
                requester_id=self.requester_id,
                database_connected=self.database_connected,
            ),
        )

    @discord.ui.button(
        label="Logs",
        emoji="📋",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def logs(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await self.open_placeholder(
            interaction,
            "logs",
        )

    @discord.ui.button(
        label="Access Control",
        emoji="🔐",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def access(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        from services.permissions import evaluate_access, deny_access
        from views.access_control import AccessControlView, access_control_embed

        assert interaction.guild is not None
        decision = await evaluate_access(
            interaction, self.bot.database, minimum="admin"
        )
        if not decision.allowed:
            await deny_access(interaction, decision.reason)
            return
        profile = await self.bot.database.ensure_guild_profile(interaction.guild)
        await interaction.response.edit_message(
            embed=access_control_embed(interaction.guild, profile),
            view=AccessControlView(
                bot=self.bot,
                guild_id=self.guild_id,
                requester_id=self.requester_id,
                database_connected=self.database_connected,
            ),
        )

    @discord.ui.button(
        label="Statistics",
        emoji="📊",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def statistics(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await self.open_placeholder(
            interaction,
            "statistics",
        )

    @discord.ui.button(
        label="Text Automod",
        emoji="🚨",
        style=discord.ButtonStyle.primary,
        row=2,
    )
    async def text_automod(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        from views.text_automod import TextAutomodView, text_automod_embed
        assert interaction.guild is not None
        profile = await self.bot.database.ensure_guild_profile(interaction.guild)
        await interaction.response.edit_message(
            embed=text_automod_embed(interaction.guild, profile),
            view=TextAutomodView(
                bot=self.bot, guild_id=self.guild_id, requester_id=self.requester_id,
                database_connected=self.database_connected,
            ),
        )

    @discord.ui.button(
        label="Settings",
        emoji="⚙️",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def settings(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await self.open_placeholder(
            interaction,
            "settings",
        )


class PlaceholderView(AuthorizedView):
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
        self.database_connected = (
            database_connected
        )

    async def return_to_dashboard(
        self,
        interaction: discord.Interaction,
    ) -> None:
        assert interaction.guild is not None

        await interaction.response.edit_message(
            embed=dashboard_embed(
                interaction.guild,
                database_connected=(
                    self.database_connected
                ),
            ),
            view=DashboardView(
                bot=self.bot,
                guild_id=self.guild_id,
                requester_id=self.requester_id,
                database_connected=(
                    self.database_connected
                ),
            ),
        )

    @discord.ui.button(
        label="Back",
        emoji="◀️",
        style=discord.ButtonStyle.secondary,
    )
    async def back(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await self.return_to_dashboard(
            interaction
        )

    @discord.ui.button(
        label="Dashboard",
        emoji="🏠",
        style=discord.ButtonStyle.primary,
    )
    async def dashboard(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await self.return_to_dashboard(
            interaction
        )
