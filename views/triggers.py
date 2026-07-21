from __future__ import annotations

from typing import Any

import discord

from services.permissions import can_manage_bot, deny_access
from views.common import AuthorizedView
from views.dashboard import (
    BOT_VERSION,
    DashboardView,
    dashboard_embed,
)


MATCH_TYPES = {"contains", "exact", "starts", "ends"}


def trigger_embed(
    guild: discord.Guild,
    profile: dict[str, Any],
) -> discord.Embed:
    triggers = profile.get("triggers", [])
    enabled = sum(
        1 for trigger in triggers
        if trigger.get("enabled", True)
    )

    embed = discord.Embed(
        title="🎭 Custom Triggers",
        description=(
            f"**Managing:** "
            f"{discord.utils.escape_markdown(guild.name)}\n\n"
            "Automatically reply when members use configured words "
            "or phrases."
        ),
    )
    embed.add_field(name="Enabled", value=str(enabled))
    embed.add_field(name="Total Saved", value=str(len(triggers)))
    embed.add_field(
        name="Match Types",
        value="Contains · Exact · Starts with · Ends with",
        inline=False,
    )
    embed.add_field(
        name="Chance",
        value=(
            "Each trigger can be set from 1% to 100%. "
            "Use 100% to always fire."
        ),
        inline=False,
    )
    embed.add_field(
        name="Random Replies",
        value=(
            "Separate multiple replies with `|||` and Daddy's Belt "
            "will choose one randomly."
        ),
        inline=False,
    )
    embed.add_field(
        name="Reply Placeholders",
        value=(
            "`{mention}` `{username}` `{display_name}` "
            "`{server}` `{channel}`"
        ),
        inline=False,
    )
    embed.set_footer(text=f"Daddy's Belt v{BOT_VERSION}")
    return embed


async def return_to_triggers(
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
        embed=trigger_embed(interaction.guild, profile),
        view=TriggersView(
            bot=bot,
            guild_id=guild_id,
            requester_id=requester_id,
            database_connected=database_connected,
        ),
    )


class TriggerModal(discord.ui.Modal):
    def __init__(
        self,
        *,
        bot,
        guild_id: int,
        requester_id: int,
        database_connected: bool,
        channel_id: int | None,
        trigger_id: str | None = None,
        existing: dict[str, Any] | None = None,
        ping_author: bool | None = None,
    ) -> None:
        super().__init__(
            title=(
                "Edit Custom Trigger"
                if trigger_id
                else "Create Custom Trigger"
            )
        )
        self.bot = bot
        self.guild_id = guild_id
        self.requester_id = requester_id
        self.database_connected = database_connected
        self.channel_id = channel_id
        self.trigger_id = trigger_id
        existing = existing or {}
        self.existing_ping_author = (
            bool(existing.get("ping_author", False))
            if ping_author is None
            else bool(ping_author)
        )

        self.phrase = discord.ui.TextInput(
            label="Trigger word or phrase",
            placeholder="good morning",
            default=str(existing.get("phrase", "")) or None,
            min_length=1,
            max_length=200,
        )
        self.responses = discord.ui.TextInput(
            label="Replies — separate random replies with |||",
            style=discord.TextStyle.paragraph,
            placeholder=(
                "Morning, {mention}.|||Another day, another questionable choice."
            ),
            default="|||".join(
                str(item)
                for item in existing.get("responses", [])
            ) or None,
            min_length=1,
            max_length=1800,
        )
        self.match_type = discord.ui.TextInput(
            label="Match type",
            placeholder="contains, exact, starts, or ends",
            default=str(
                existing.get("match_type", "contains")
            ),
            min_length=4,
            max_length=8,
        )
        self.cooldown = discord.ui.TextInput(
            label="Cooldown in seconds",
            placeholder="10",
            default=str(
                existing.get("cooldown_seconds", 10)
            ),
            min_length=1,
            max_length=6,
        )
        self.chance = discord.ui.TextInput(
            label="Chance to fire — 1 to 100 percent",
            placeholder="100",
            default=str(
                existing.get("chance_percent", 100)
            ),
            min_length=1,
            max_length=3,
        )

        self.add_item(self.phrase)
        self.add_item(self.responses)
        self.add_item(self.match_type)
        self.add_item(self.cooldown)
        self.add_item(self.chance)

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

        match_type = str(self.match_type.value).strip().lower()
        aliases = {
            "starts with": "starts",
            "ends with": "ends",
        }
        match_type = aliases.get(match_type, match_type)

        if match_type not in MATCH_TYPES:
            await interaction.response.send_message(
                "Match type must be `contains`, `exact`, `starts`, or `ends`.",
                ephemeral=True,
            )
            return

        try:
            cooldown = int(str(self.cooldown.value).strip())
        except ValueError:
            await interaction.response.send_message(
                "Cooldown must be a whole number of seconds.",
                ephemeral=True,
            )
            return

        if cooldown < 0 or cooldown > 86400:
            await interaction.response.send_message(
                "Cooldown must be between 0 and 86400 seconds.",
                ephemeral=True,
            )
            return

        try:
            chance_percent = int(
                str(self.chance.value).strip()
            )
        except ValueError:
            await interaction.response.send_message(
                "Chance must be a whole number from 1 to 100.",
                ephemeral=True,
            )
            return

        if chance_percent < 1 or chance_percent > 100:
            await interaction.response.send_message(
                "Chance must be between 1 and 100.",
                ephemeral=True,
            )
            return

        responses = [
            response.strip()
            for response in str(self.responses.value).split("|||")
            if response.strip()
        ]
        if not responses:
            await interaction.response.send_message(
                "Add at least one reply.",
                ephemeral=True,
            )
            return

        kwargs = dict(
            phrase=str(self.phrase.value).strip(),
            responses=responses,
            match_type=match_type,
            cooldown_seconds=cooldown,
            chance_percent=chance_percent,
            channel_id=self.channel_id,
            ping_author=bool(
                self.existing_ping_author
            ),
        )

        if self.trigger_id:
            profile = await self.bot.database.update_trigger(
                self.guild_id,
                self.trigger_id,
                updated_by=interaction.user.id,
                **kwargs,
            )
        else:
            profile = await self.bot.database.add_trigger(
                self.guild_id,
                created_by=interaction.user.id,
                **kwargs,
            )

        if profile is None:
            await interaction.response.send_message(
                "I couldn't save that trigger.",
                ephemeral=True,
            )
            return

        await interaction.response.edit_message(
            embed=trigger_embed(interaction.guild, profile),
            view=TriggersView(
                bot=self.bot,
                guild_id=self.guild_id,
                requester_id=self.requester_id,
                database_connected=self.database_connected,
            ),
        )


class TriggerChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, parent: "TriggerSetupView") -> None:
        super().__init__(
            placeholder="Optional: restrict trigger to one channel",
            min_values=0,
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
        self.parent_ref.channel_id = (
            self.values[0].id if self.values else None
        )
        text = (
            f"Restricted to <#{self.parent_ref.channel_id}>."
            if self.parent_ref.channel_id
            else "Channel restriction removed."
        )
        await interaction.response.send_message(
            text,
            ephemeral=True,
        )


class TriggerSetupView(AuthorizedView):
    def __init__(
        self,
        *,
        bot,
        guild_id: int,
        requester_id: int,
        database_connected: bool,
        trigger_id: str | None = None,
        existing: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            guild_id=guild_id,
            requester_id=requester_id,
        )
        self.bot = bot
        self.database_connected = database_connected
        self.trigger_id = trigger_id
        self.existing = existing or {}
        self.channel_id = self.existing.get("channel_id")
        self.ping_author = bool(
            self.existing.get("ping_author", False)
        )
        self.add_item(TriggerChannelSelect(self))

    @discord.ui.button(
        label="Toggle Author Ping",
        emoji="🔔",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def toggle_author_ping(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        self.ping_author = not self.ping_author
        await interaction.response.send_message(
            (
                "Author ping is now **enabled**."
                if self.ping_author
                else "Author ping is now **disabled**."
            ),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Continue",
        emoji="➡️",
        style=discord.ButtonStyle.success,
        row=1,
    )
    async def continue_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await interaction.response.send_modal(
            TriggerModal(
                bot=self.bot,
                guild_id=self.guild_id,
                requester_id=self.requester_id,
                database_connected=self.database_connected,
                channel_id=(
                    int(self.channel_id)
                    if self.channel_id is not None
                    else None
                ),
                trigger_id=self.trigger_id,
                existing=self.existing,
                ping_author=self.ping_author,
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
        await return_to_triggers(
            interaction,
            bot=self.bot,
            guild_id=self.guild_id,
            requester_id=self.requester_id,
            database_connected=self.database_connected,
        )


class TriggerSelect(discord.ui.Select):
    def __init__(
        self,
        parent: "ManageTriggersView",
        triggers: list[dict[str, Any]],
    ) -> None:
        options = []
        for trigger in triggers[:25]:
            phrase = str(trigger.get("phrase", ""))
            options.append(
                discord.SelectOption(
                    label=phrase[:80] or "Untitled trigger",
                    description=(
                        f"{trigger.get('match_type', 'contains')} · "
                        f"{trigger.get('chance_percent', 100)}% chance · "
                        f"{trigger.get('cooldown_seconds', 0)}s cooldown · "
                        f"{'Enabled' if trigger.get('enabled', True) else 'Paused'}"
                    )[:100],
                    value=str(trigger["_id"]),
                    emoji=(
                        "🎭"
                        if trigger.get("enabled", True)
                        else "⏸️"
                    ),
                )
            )

        super().__init__(
            placeholder="Select a trigger",
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


class ManageTriggersView(AuthorizedView):
    def __init__(
        self,
        *,
        bot,
        guild_id: int,
        requester_id: int,
        database_connected: bool,
        triggers: list[dict[str, Any]],
    ) -> None:
        super().__init__(
            guild_id=guild_id,
            requester_id=requester_id,
        )
        self.bot = bot
        self.database_connected = database_connected
        self.triggers = triggers
        self.selected_id: str | None = None
        self.add_item(TriggerSelect(self, triggers))

    def selected_trigger(self) -> dict[str, Any] | None:
        for trigger in self.triggers:
            if str(trigger.get("_id")) == self.selected_id:
                return trigger
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
        trigger = self.selected_trigger()
        if trigger is None:
            await interaction.response.send_message(
                "Select a trigger first.",
                ephemeral=True,
            )
            return

        await interaction.response.edit_message(
            embed=discord.Embed(
                title="✏️ Edit Trigger",
                description=(
                    "Optionally choose a restricted channel, "
                    "then press Continue."
                ),
            ),
            view=TriggerSetupView(
                bot=self.bot,
                guild_id=self.guild_id,
                requester_id=self.requester_id,
                database_connected=self.database_connected,
                trigger_id=str(trigger["_id"]),
                existing=trigger,
            ),
        )

    @discord.ui.button(
        label="Test",
        emoji="👀",
        style=discord.ButtonStyle.primary,
        row=1,
    )
    async def test(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        trigger = self.selected_trigger()
        if trigger is None:
            await interaction.response.send_message(
                "Select a trigger first.",
                ephemeral=True,
            )
            return

        replies = trigger.get("responses", [])
        preview = replies[0] if replies else "(No reply saved)"
        await interaction.response.send_message(
            (
                f"**Phrase:** `{trigger.get('phrase')}`\n"
                f"**Match:** `{trigger.get('match_type')}`\n"
                f"**Chance:** `{trigger.get('chance_percent', 100)}%`\n"
                f"**First reply preview:**\n{preview}"
            ),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Pause / Resume",
        emoji="⏯️",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def toggle(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        trigger = self.selected_trigger()
        if trigger is None or interaction.guild is None:
            await interaction.response.send_message(
                "Select a trigger first.",
                ephemeral=True,
            )
            return

        profile = await self.bot.database.toggle_trigger(
            self.guild_id,
            str(trigger["_id"]),
            not bool(trigger.get("enabled", True)),
        )
        await interaction.response.edit_message(
            embed=trigger_embed(interaction.guild, profile),
            view=TriggersView(
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
        trigger = self.selected_trigger()
        if trigger is None or interaction.guild is None:
            await interaction.response.send_message(
                "Select a trigger first.",
                ephemeral=True,
            )
            return

        profile = await self.bot.database.delete_trigger(
            self.guild_id,
            str(trigger["_id"]),
        )
        await interaction.response.edit_message(
            embed=trigger_embed(interaction.guild, profile),
            view=TriggersView(
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
        await return_to_triggers(
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


class TriggersView(AuthorizedView):
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
        label="Create Trigger",
        emoji="➕",
        style=discord.ButtonStyle.success,
        row=0,
    )
    async def create(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="➕ Create Trigger",
                description=(
                    "Optionally restrict it to one channel, "
                    "then press Continue."
                ),
            ),
            view=TriggerSetupView(
                bot=self.bot,
                guild_id=self.guild_id,
                requester_id=self.requester_id,
                database_connected=self.database_connected,
            ),
        )

    @discord.ui.button(
        label="Manage Triggers",
        emoji="📋",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def manage(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        assert interaction.guild is not None
        profile = await self.bot.database.ensure_guild_profile(
            interaction.guild
        )
        triggers = profile.get("triggers", [])

        if not triggers:
            await interaction.response.send_message(
                "There are no triggers yet.",
                ephemeral=True,
            )
            return

        lines = []
        for index, trigger in enumerate(triggers[:25], start=1):
            channel = (
                f"<#{trigger['channel_id']}>"
                if trigger.get("channel_id")
                else "All channels"
            )
            lines.append(
                f"**{index}.** `{trigger.get('phrase')}`\n"
                f"{trigger.get('match_type', 'contains')} · "
                f"{trigger.get('chance_percent', 100)}% chance · "
                f"{channel} · "
                f"{'Enabled' if trigger.get('enabled', True) else 'Paused'}"
            )

        await interaction.response.edit_message(
            embed=discord.Embed(
                title="📋 Manage Triggers",
                description="\n\n".join(lines),
            ),
            view=ManageTriggersView(
                bot=self.bot,
                guild_id=self.guild_id,
                requester_id=self.requester_id,
                database_connected=self.database_connected,
                triggers=triggers,
            ),
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
