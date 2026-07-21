from __future__ import annotations

import discord

from services.permissions import deny_access, evaluate_access
from views.common import AuthorizedView

FIELDS = {
    "bot_manager_role_ids": "Bot Manager Roles",
    "moderator_role_ids": "Moderator Roles",
    "allowed_user_ids": "Allowed Users",
    "blocked_user_ids": "Blocked Users",
    "blocked_role_ids": "Blocked Roles",
    "allowed_channel_ids": "Allowed Channels",
    "blocked_channel_ids": "Blocked Channels",
}


def _mention_list(guild: discord.Guild, values: list[int], kind: str) -> str:
    if not values:
        return "None"
    rendered: list[str] = []
    for raw in values:
        value = int(raw)
        if kind == "role":
            role = guild.get_role(value)
            rendered.append(role.mention if role else f"Deleted role (`{value}`)")
        elif kind == "user":
            member = guild.get_member(value)
            rendered.append(member.mention if member else f"User `{value}`")
        else:
            channel = guild.get_channel(value)
            rendered.append(channel.mention if channel else f"Deleted channel (`{value}`)")
    return ", ".join(rendered[:15]) + ("…" if len(rendered) > 15 else "")


def access_control_embed(guild: discord.Guild, profile: dict) -> discord.Embed:
    config = profile.get("access_control", {})
    embed = discord.Embed(
        title="🔐 Access Control",
        description=(
            "Choose exactly who can manage the bot and where the dashboard can be used.\n\n"
            "Server owner, **Administrator**, and **Manage Server** automatically receive full access "
            "unless locally blocked. Bot Managers receive dashboard access; Moderators receive moderation access."
        ),
    )
    embed.add_field(name="🤖 Bot Managers", value=_mention_list(guild, config.get("bot_manager_role_ids", []), "role"), inline=False)
    embed.add_field(name="🛡️ Moderators", value=_mention_list(guild, config.get("moderator_role_ids", []), "role"), inline=False)
    embed.add_field(name="✅ Allowed Users", value=_mention_list(guild, config.get("allowed_user_ids", []), "user"), inline=False)
    embed.add_field(name="⛔ Blocked Users", value=_mention_list(guild, config.get("blocked_user_ids", []), "user"), inline=False)
    embed.add_field(name="🚫 Blocked Roles", value=_mention_list(guild, config.get("blocked_role_ids", []), "role"), inline=False)
    embed.add_field(name="📍 Allowed Channels", value=_mention_list(guild, config.get("allowed_channel_ids", []), "channel"), inline=False)
    embed.add_field(name="🔇 Blocked Channels", value=_mention_list(guild, config.get("blocked_channel_ids", []), "channel"), inline=False)
    embed.set_footer(text="Selecting nothing clears that setting. Blocked entries take priority.")
    return embed


class AccessRoleSelect(discord.ui.RoleSelect):
    def __init__(self, *, field: str, placeholder: str):
        super().__init__(placeholder=placeholder, min_values=0, max_values=25, row=0)
        self.field = field

    async def callback(self, interaction: discord.Interaction) -> None:
        view: AccessEditorView = self.view  # type: ignore[assignment]
        await view.save(interaction, self.field, [role.id for role in self.values])


class AccessUserSelect(discord.ui.UserSelect):
    def __init__(self, *, field: str, placeholder: str):
        super().__init__(placeholder=placeholder, min_values=0, max_values=25, row=0)
        self.field = field

    async def callback(self, interaction: discord.Interaction) -> None:
        view: AccessEditorView = self.view  # type: ignore[assignment]
        await view.save(interaction, self.field, [user.id for user in self.values])


class AccessChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, *, field: str, placeholder: str):
        super().__init__(
            placeholder=placeholder,
            min_values=0,
            max_values=25,
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            row=0,
        )
        self.field = field

    async def callback(self, interaction: discord.Interaction) -> None:
        view: AccessEditorView = self.view  # type: ignore[assignment]
        await view.save(interaction, self.field, [channel.id for channel in self.values])


class AccessEditorView(AuthorizedView):
    def __init__(self, *, bot, guild_id: int, requester_id: int, database_connected: bool, field: str):
        super().__init__(guild_id=guild_id, requester_id=requester_id, minimum_access="admin")
        self.bot = bot
        self.database_connected = database_connected
        self.field = field
        label = FIELDS[field]
        if field in {"bot_manager_role_ids", "moderator_role_ids", "blocked_role_ids"}:
            self.add_item(AccessRoleSelect(field=field, placeholder=f"Select {label.lower()}…"))
        elif field in {"allowed_user_ids", "blocked_user_ids"}:
            self.add_item(AccessUserSelect(field=field, placeholder=f"Select {label.lower()}…"))
        else:
            self.add_item(AccessChannelSelect(field=field, placeholder=f"Select {label.lower()}…"))

    async def save(self, interaction: discord.Interaction, field: str, values: list[int]) -> None:
        assert interaction.guild is not None
        await self.bot.database.set_access_control_list(interaction.guild.id, field, values)
        profile = await self.bot.database.ensure_guild_profile(interaction.guild)
        await interaction.response.edit_message(
            embed=access_control_embed(interaction.guild, profile),
            view=AccessControlView(bot=self.bot, guild_id=self.guild_id, requester_id=self.requester_id, database_connected=self.database_connected),
        )

    @discord.ui.button(label="Cancel", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        assert interaction.guild is not None
        profile = await self.bot.database.ensure_guild_profile(interaction.guild)
        await interaction.response.edit_message(
            embed=access_control_embed(interaction.guild, profile),
            view=AccessControlView(bot=self.bot, guild_id=self.guild_id, requester_id=self.requester_id, database_connected=self.database_connected),
        )


class AccessControlView(AuthorizedView):
    def __init__(self, *, bot, guild_id: int, requester_id: int, database_connected: bool):
        super().__init__(guild_id=guild_id, requester_id=requester_id, minimum_access="admin")
        self.bot = bot
        self.database_connected = database_connected

    async def editor(self, interaction: discord.Interaction, field: str) -> None:
        assert interaction.guild is not None
        profile = await self.bot.database.ensure_guild_profile(interaction.guild)
        embed = access_control_embed(interaction.guild, profile)
        embed.description = f"Select the complete new list for **{FIELDS[field]}**. Selecting nothing clears it."
        await interaction.response.edit_message(
            embed=embed,
            view=AccessEditorView(bot=self.bot, guild_id=self.guild_id, requester_id=self.requester_id, database_connected=self.database_connected, field=field),
        )

    @discord.ui.button(label="Bot Managers", emoji="🤖", style=discord.ButtonStyle.primary, row=0)
    async def managers(self, i, b): await self.editor(i, "bot_manager_role_ids")
    @discord.ui.button(label="Moderators", emoji="🛡️", style=discord.ButtonStyle.primary, row=0)
    async def moderators(self, i, b): await self.editor(i, "moderator_role_ids")
    @discord.ui.button(label="Allowed Users", emoji="✅", style=discord.ButtonStyle.success, row=0)
    async def allowed_users(self, i, b): await self.editor(i, "allowed_user_ids")
    @discord.ui.button(label="Blocked Users", emoji="⛔", style=discord.ButtonStyle.danger, row=1)
    async def blocked_users(self, i, b): await self.editor(i, "blocked_user_ids")
    @discord.ui.button(label="Blocked Roles", emoji="🚫", style=discord.ButtonStyle.danger, row=1)
    async def blocked_roles(self, i, b): await self.editor(i, "blocked_role_ids")
    @discord.ui.button(label="Allowed Channels", emoji="📍", style=discord.ButtonStyle.secondary, row=2)
    async def allowed_channels(self, i, b): await self.editor(i, "allowed_channel_ids")
    @discord.ui.button(label="Blocked Channels", emoji="🔇", style=discord.ButtonStyle.secondary, row=2)
    async def blocked_channels(self, i, b): await self.editor(i, "blocked_channel_ids")

    @discord.ui.button(label="Dashboard", emoji="🏠", style=discord.ButtonStyle.secondary, row=3)
    async def dashboard(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        from views.dashboard import DashboardView, dashboard_embed
        assert interaction.guild is not None
        await interaction.response.edit_message(
            embed=dashboard_embed(interaction.guild, database_connected=self.database_connected),
            view=DashboardView(bot=self.bot, guild_id=self.guild_id, requester_id=self.requester_id, database_connected=self.database_connected),
        )
