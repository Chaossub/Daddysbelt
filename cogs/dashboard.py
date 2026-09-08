from __future__ import annotations

import discord
from discord.ext import commands

from core.bot import DaddysBeltBot
from views.dashboard import DashboardView, dashboard_embed


def _ids(values) -> set[int]:
    result: set[int] = set()
    for value in values or []:
        try:
            result.add(int(value))
        except (TypeError, ValueError):
            continue
    return result


async def _can_open_staff_dashboard(
    bot: DaddysBeltBot,
    ctx: commands.Context,
) -> tuple[bool, str | None]:
    """Daddy's main dashboard is staff-only: owner, admins, or configured moderators."""
    guild = ctx.guild
    member = ctx.author
    if guild is None or not isinstance(member, discord.Member):
        return False, "Daddy's dashboard can only be opened inside a server."

    profile = await bot.database.get_guild_profile(guild.id)
    if profile is None:
        profile = await bot.database.ensure_guild_profile(guild)

    config = profile.get("access_control", {})
    role_ids = {role.id for role in member.roles}

    if member.id in _ids(config.get("blocked_user_ids")):
        return False, "You are blocked from using Daddy's Belt in this server."
    if role_ids & _ids(config.get("blocked_role_ids")):
        return False, "One of your roles is blocked from using Daddy's Belt."

    channel_id = getattr(ctx.channel, "id", None)
    if channel_id is not None:
        blocked_channels = _ids(config.get("blocked_channel_ids"))
        allowed_channels = _ids(config.get("allowed_channel_ids"))
        if channel_id in blocked_channels:
            return False, "Daddy's dashboard is blocked in this channel."
        if allowed_channels and channel_id not in allowed_channels:
            return False, "Daddy's dashboard is only available in approved channels."

    permissions = member.guild_permissions
    is_owner = member.id == guild.owner_id
    is_admin = permissions.administrator or permissions.manage_guild
    is_moderator = bool(role_ids & _ids(config.get("moderator_role_ids")))

    if is_owner or is_admin or is_moderator:
        return True, None

    return False, "Daddy's dashboard is staff-only (moderators, administrators, and the server owner)."


class DashboardCog(commands.Cog):
    def __init__(self, bot: DaddysBeltBot) -> None:
        self.bot = bot

    @commands.command(name="daddy")
    @commands.guild_only()
    async def daddy(self, ctx: commands.Context) -> None:
        """Open Daddy's Belt's private staff dashboard with !daddy."""
        allowed, reason = await _can_open_staff_dashboard(self.bot, ctx)
        if not allowed:
            # Keep the denial short and avoid exposing the panel to regular members.
            await ctx.reply(reason or "You do not have permission to use this dashboard.", mention_author=False)
            return

        guild = ctx.guild
        assert guild is not None

        await self.bot.database.ensure_guild_profile(guild)
        await self.bot.database.increment_stat(guild.id, "commands_used")

        # Prefix commands cannot create Discord ephemeral messages, so keep the
        # panel requester-locked. Only the person who opened it can use its buttons.
        await ctx.reply(
            embed=dashboard_embed(
                guild,
                database_connected=self.bot.database.connected,
            ),
            view=DashboardView(
                bot=self.bot,
                guild_id=guild.id,
                requester_id=ctx.author.id,
                database_connected=self.bot.database.connected,
            ),
            mention_author=False,
        )


async def setup(bot: DaddysBeltBot) -> None:
    await bot.add_cog(DashboardCog(bot))
