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


def welcome_data(profile: dict[str, Any]) -> dict[str, Any]:
    return profile.get("welcome", {})


def message_label(message: dict[str, Any], index: int) -> str:
    content = str(message.get("content", "")).replace("\n", " ").strip()
    return f"{index}. {content[:75] or 'Untitled message'}"


def welcome_embed(
    guild: discord.Guild,
    profile: dict[str, Any],
) -> discord.Embed:
    welcome = welcome_data(profile)
    enabled = bool(welcome.get("enabled", False))
    random_enabled = bool(welcome.get("random_enabled", False))
    channel_id = welcome.get("channel_id")
    messages = welcome.get("messages", [])
    mode = str(profile.get("response_mode", "professional")).lower()

    embed = discord.Embed(
        title="📢 Welcome Settings",
        description=(
            f"**Managing:** {discord.utils.escape_markdown(guild.name)}\n\n"
            "Configure greetings, images, message selection, and personality."
        ),
    )
    embed.add_field(
        name="Status",
        value="🟢 Enabled" if enabled else "🔴 Disabled",
    )
    embed.add_field(
        name="Channel",
        value=f"<#{channel_id}>" if channel_id else "Not selected",
    )
    embed.add_field(name="Saved Messages", value=str(len(messages)))
    embed.add_field(
        name="Selection",
        value="🎲 Random" if random_enabled else "1️⃣ First message",
    )
    embed.add_field(
        name="Mode",
        value="😈 Daddy Mode" if mode == "daddy" else "🧾 Professional",
    )
    embed.add_field(
        name="Placeholders",
        value=(
            "`{mention}` `{username}` `{display_name}` "
            "`{server}` `{member_count}`"
        ),
        inline=False,
    )
    embed.set_footer(text=f"Daddy's Belt v{BOT_VERSION}")
    return embed


async def return_to_welcome(
    interaction: discord.Interaction,
    *,
    bot,
    guild_id: int,
    requester_id: int,
    database_connected: bool,
) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            "This control panel must be used inside a server.",
            ephemeral=True,
        )
        return

    profile = await bot.database.ensure_guild_profile(interaction.guild)
    await interaction.response.edit_message(
        embed=welcome_embed(interaction.guild, profile),
        view=WelcomeView(
            bot=bot,
            guild_id=guild_id,
            requester_id=requester_id,
            database_connected=database_connected,
        ),
    )


async def return_to_dashboard(
    interaction: discord.Interaction,
    *,
    bot,
    guild_id: int,
    requester_id: int,
    database_connected: bool,
) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            "The dashboard must be opened inside a server.",
            ephemeral=True,
        )
        return

    await bot.database.ensure_guild_profile(interaction.guild)

    await interaction.response.edit_message(
        embed=dashboard_embed(
            interaction.guild,
            database_connected=database_connected,
        ),
        view=DashboardView(
            bot=bot,
            guild_id=guild_id,
            requester_id=requester_id,
            database_connected=database_connected,
        ),
    )


class WelcomeMessageModal(discord.ui.Modal):
    def __init__(
        self,
        *,
        bot,
        guild_id: int,
        requester_id: int,
        database_connected: bool,
        message_id: str | None = None,
        existing_content: str = "",
        existing_image_url: str = "",
    ) -> None:
        super().__init__(
            title="Edit Welcome Message" if message_id else "Add Welcome Message"
        )
        self.bot = bot
        self.guild_id = guild_id
        self.requester_id = requester_id
        self.database_connected = database_connected
        self.message_id = message_id

        self.content_input = discord.ui.TextInput(
            label="Welcome message",
            style=discord.TextStyle.paragraph,
            default=existing_content[:1900] or None,
            placeholder="Welcome {mention} to {server}.",
            min_length=1,
            max_length=1900,
            required=True,
        )
        self.image_input = discord.ui.TextInput(
            label="Optional image or GIF URL",
            default=existing_image_url[:500] or None,
            placeholder="https://example.com/welcome.gif",
            min_length=0,
            max_length=500,
            required=False,
        )
        self.add_item(self.content_input)
        self.add_item(self.image_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
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

        content = str(self.content_input.value).strip()
        image_url = str(self.image_input.value or "").strip() or None

        if image_url and not image_url.lower().startswith(("http://", "https://")):
            await interaction.response.send_message(
                "The image/GIF URL must begin with `http://` or `https://`.",
                ephemeral=True,
            )
            return

        if self.message_id:
            profile = await self.bot.database.update_welcome_message(
                self.guild_id,
                self.message_id,
                content,
                image_url,
                interaction.user.id,
            )
        else:
            profile = await self.bot.database.add_welcome_message(
                self.guild_id,
                content,
                interaction.user.id,
            )
            if profile and image_url:
                created = profile.get("welcome", {}).get("messages", [])[-1]
                profile = await self.bot.database.update_welcome_message(
                    self.guild_id,
                    str(created["_id"]),
                    content,
                    image_url,
                    interaction.user.id,
                )

        if profile is None:
            await interaction.response.send_message(
                "I couldn't save that welcome message.",
                ephemeral=True,
            )
            return

        await interaction.response.edit_message(
            embed=welcome_embed(interaction.guild, profile),
            view=WelcomeView(
                bot=self.bot,
                guild_id=self.guild_id,
                requester_id=self.requester_id,
                database_connected=self.database_connected,
            ),
        )


class WelcomeChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, parent: "ChannelPickerView") -> None:
        super().__init__(
            placeholder="Choose a welcome channel",
            min_values=1,
            max_values=1,
            channel_types=[
                discord.ChannelType.text,
                discord.ChannelType.news,
            ],
        )
        self.parent_ref = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = self.values[0]
        profile = await self.parent_ref.bot.database.set_welcome_channel(
            self.parent_ref.guild_id,
            selected.id,
        )
        if profile is None or interaction.guild is None:
            await interaction.response.send_message(
                "I couldn't save that channel.",
                ephemeral=True,
            )
            return

        await interaction.response.edit_message(
            embed=welcome_embed(interaction.guild, profile),
            view=WelcomeView(
                bot=self.parent_ref.bot,
                guild_id=self.parent_ref.guild_id,
                requester_id=self.parent_ref.requester_id,
                database_connected=self.parent_ref.database_connected,
            ),
        )


class ChannelPickerView(AuthorizedView):
    def __init__(
        self,
        *,
        bot,
        guild_id: int,
        requester_id: int,
        database_connected: bool,
    ) -> None:
        super().__init__(guild_id=guild_id, requester_id=requester_id)
        self.bot = bot
        self.database_connected = database_connected
        self.add_item(WelcomeChannelSelect(self))

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
        await return_to_welcome(
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
        row=1,
    )
    async def dashboard(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await return_to_dashboard(
            interaction,
            bot=self.bot,
            guild_id=self.guild_id,
            requester_id=self.requester_id,
            database_connected=self.database_connected,
        )


class MessageSelect(discord.ui.Select):
    def __init__(
        self,
        parent: "MessageManagerView",
        messages: list[dict[str, Any]],
    ) -> None:
        options = [
            discord.SelectOption(
                label=message_label(message, index),
                value=str(message["_id"]),
                emoji="💬",
            )
            for index, message in enumerate(messages[:25], start=1)
        ]
        super().__init__(
            placeholder="Select a message to edit or delete",
            min_values=1,
            max_values=1,
            options=options,
        )
        self.parent_ref = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        self.parent_ref.selected_message_id = self.values[0]
        await interaction.response.send_message(
            "Selected. Use **Edit Selected** or **Delete Selected** below.",
            ephemeral=True,
        )


class MessageManagerView(AuthorizedView):
    def __init__(
        self,
        *,
        bot,
        guild_id: int,
        requester_id: int,
        database_connected: bool,
        messages: list[dict[str, Any]],
    ) -> None:
        super().__init__(guild_id=guild_id, requester_id=requester_id)
        self.bot = bot
        self.database_connected = database_connected
        self.messages = messages
        self.selected_message_id: str | None = None
        if messages:
            self.add_item(MessageSelect(self, messages))

    def selected_message(self) -> dict[str, Any] | None:
        for message in self.messages:
            if str(message.get("_id")) == self.selected_message_id:
                return message
        return None

    @discord.ui.button(
        label="Edit Selected",
        emoji="✏️",
        style=discord.ButtonStyle.primary,
        row=1,
    )
    async def edit_selected(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        message = self.selected_message()
        if message is None:
            await interaction.response.send_message(
                "Select a message first.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            WelcomeMessageModal(
                bot=self.bot,
                guild_id=self.guild_id,
                requester_id=self.requester_id,
                database_connected=self.database_connected,
                message_id=str(message["_id"]),
                existing_content=str(message.get("content", "")),
                existing_image_url=str(message.get("image_url") or ""),
            )
        )

    @discord.ui.button(
        label="Delete Selected",
        emoji="🗑️",
        style=discord.ButtonStyle.danger,
        row=1,
    )
    async def delete_selected(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        if not self.selected_message_id:
            await interaction.response.send_message(
                "Select a message first.",
                ephemeral=True,
            )
            return

        profile = await self.bot.database.delete_welcome_message(
            self.guild_id,
            self.selected_message_id,
        )
        if profile is None or interaction.guild is None:
            await interaction.response.send_message(
                "I couldn't delete that message.",
                ephemeral=True,
            )
            return

        await interaction.response.edit_message(
            embed=welcome_embed(interaction.guild, profile),
            view=WelcomeView(
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
        await return_to_welcome(
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
        await return_to_dashboard(
            interaction,
            bot=self.bot,
            guild_id=self.guild_id,
            requester_id=self.requester_id,
            database_connected=self.database_connected,
        )


class WelcomeView(AuthorizedView):
    def __init__(
        self,
        *,
        bot,
        guild_id: int,
        requester_id: int,
        database_connected: bool,
    ) -> None:
        super().__init__(guild_id=guild_id, requester_id=requester_id)
        self.bot = bot
        self.database_connected = database_connected

    @discord.ui.button(
        label="Choose Channel",
        emoji="📍",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def choose_channel(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="📍 Choose Welcome Channel",
                description="Select where welcome messages should be posted.",
            ),
            view=ChannelPickerView(
                bot=self.bot,
                guild_id=self.guild_id,
                requester_id=self.requester_id,
                database_connected=self.database_connected,
            ),
        )

    @discord.ui.button(
        label="Enable / Disable",
        emoji="⏯️",
        style=discord.ButtonStyle.secondary,
        row=0,
    )
    async def toggle_enabled(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        assert interaction.guild is not None
        profile = await self.bot.database.ensure_guild_profile(interaction.guild)
        welcome = welcome_data(profile)
        current = bool(welcome.get("enabled", False))

        if not current and not welcome.get("channel_id"):
            await interaction.response.send_message(
                "Choose a welcome channel first.",
                ephemeral=True,
            )
            return
        if not current and not welcome.get("messages"):
            await interaction.response.send_message(
                "Add at least one welcome message first.",
                ephemeral=True,
            )
            return

        updated = await self.bot.database.set_welcome_enabled(
            self.guild_id,
            not current,
        )
        await interaction.response.edit_message(
            embed=welcome_embed(interaction.guild, updated),
            view=WelcomeView(
                bot=self.bot,
                guild_id=self.guild_id,
                requester_id=self.requester_id,
                database_connected=self.database_connected,
            ),
        )

    @discord.ui.button(
        label="Add Message",
        emoji="➕",
        style=discord.ButtonStyle.success,
        row=1,
    )
    async def add_message(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await interaction.response.send_modal(
            WelcomeMessageModal(
                bot=self.bot,
                guild_id=self.guild_id,
                requester_id=self.requester_id,
                database_connected=self.database_connected,
            )
        )

    @discord.ui.button(
        label="Manage Messages",
        emoji="📝",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def manage_messages(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        assert interaction.guild is not None
        profile = await self.bot.database.ensure_guild_profile(interaction.guild)
        messages = welcome_data(profile).get("messages", [])

        if not messages:
            await interaction.response.send_message(
                "There are no messages to manage yet.",
                ephemeral=True,
            )
            return

        description = "\n\n".join(
            f"**{index}.** {str(message.get('content', ''))[:250]}"
            for index, message in enumerate(messages[:25], start=1)
        )
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="📝 Manage Welcome Messages",
                description=description,
            ),
            view=MessageManagerView(
                bot=self.bot,
                guild_id=self.guild_id,
                requester_id=self.requester_id,
                database_connected=self.database_connected,
                messages=messages,
            ),
        )

    @discord.ui.button(
        label="First / Random",
        emoji="🎲",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def toggle_random(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        assert interaction.guild is not None
        profile = await self.bot.database.ensure_guild_profile(interaction.guild)
        current = bool(
            welcome_data(profile).get("random_enabled", False)
        )
        updated = await self.bot.database.set_welcome_random(
            self.guild_id,
            not current,
        )
        await interaction.response.edit_message(
            embed=welcome_embed(interaction.guild, updated),
            view=WelcomeView(
                bot=self.bot,
                guild_id=self.guild_id,
                requester_id=self.requester_id,
                database_connected=self.database_connected,
            ),
        )

    @discord.ui.button(
        label="Professional / Daddy",
        emoji="😈",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def toggle_mode(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        assert interaction.guild is not None
        profile = await self.bot.database.ensure_guild_profile(interaction.guild)
        current = str(profile.get("response_mode", "professional")).lower()
        updated = await self.bot.database.set_response_mode(
            self.guild_id,
            "daddy" if current == "professional" else "professional",
        )
        await interaction.response.edit_message(
            embed=welcome_embed(interaction.guild, updated),
            view=WelcomeView(
                bot=self.bot,
                guild_id=self.guild_id,
                requester_id=self.requester_id,
                database_connected=self.database_connected,
            ),
        )

    @discord.ui.button(
        label="Test Welcome",
        emoji="👀",
        style=discord.ButtonStyle.primary,
        row=2,
    )
    async def test_welcome(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        assert interaction.guild is not None
        profile = await self.bot.database.ensure_guild_profile(interaction.guild)
        welcome = welcome_data(profile)

        channel_id = welcome.get("channel_id")
        if not channel_id:
            await interaction.response.send_message(
                "Choose a welcome channel first.",
                ephemeral=True,
            )
            return

        message = self.bot.choose_welcome_message(welcome)
        if message is None:
            await interaction.response.send_message(
                "Add at least one welcome message first.",
                ephemeral=True,
            )
            return

        try:
            channel = await self.bot.get_text_channel(
                interaction.guild,
                int(channel_id),
            )
        except (TypeError, ValueError):
            channel = None

        if channel is None:
            await interaction.response.send_message(
                "I couldn't access that channel. Make sure the bot is installed "
                "to this server and can view/send messages there.",
                ephemeral=True,
            )
            return

        member = interaction.guild.get_member(interaction.user.id)
        if member is None:
            try:
                member = await interaction.guild.fetch_member(
                    interaction.user.id
                )
            except discord.HTTPException:
                await interaction.response.send_message(
                    "I couldn't load your member profile.",
                    ephemeral=True,
                )
                return

        try:
            await self.bot.send_welcome(
                member=member,
                channel=channel,
                message=message,
                response_mode=str(
                    profile.get("response_mode", "professional")
                ),
                test=True,
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "I can see the channel but cannot send messages there.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.response.send_message(
                "Discord rejected the test message or image URL.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"✅ Test sent to {channel.mention}.",
            ephemeral=True,
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
        await return_to_dashboard(
            interaction,
            bot=self.bot,
            guild_id=self.guild_id,
            requester_id=self.requester_id,
            database_connected=self.database_connected,
        )
