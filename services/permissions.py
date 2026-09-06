from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import discord

AccessLevel = Literal["none", "moderator", "manager", "admin", "owner"]

_LEVELS: dict[AccessLevel, int] = {
    "none": 0,
    "moderator": 1,
    "manager": 2,
    "admin": 3,
    "owner": 4,
}


@dataclass(frozen=True, slots=True)
class AccessDecision:
    allowed: bool
    level: AccessLevel
    reason: str | None = None


def _ids(values: Any) -> set[int]:
    result: set[int] = set()
    for value in values or []:
        try:
            result.add(int(value))
        except (TypeError, ValueError):
            continue
    return result


async def evaluate_access(
    interaction: discord.Interaction,
    database,
    *,
    minimum: AccessLevel = "manager",
    enforce_channel: bool = True,
) -> AccessDecision:
    guild = interaction.guild
    if guild is None or not isinstance(interaction.user, discord.Member):
        return AccessDecision(False, "none", "This can only be used inside a server.")

    profile = await database.get_guild_profile(guild.id)
    if profile is None:
        profile = await database.ensure_guild_profile(guild)

    config = profile.get("access_control", {})
    user_id = interaction.user.id
    role_ids = {role.id for role in interaction.user.roles}
    channel_id = interaction.channel_id

    if user_id in _ids(config.get("blocked_user_ids")):
        return AccessDecision(False, "none", "You are blocked from using this bot in this server.")

    if role_ids & _ids(config.get("blocked_role_ids")):
        return AccessDecision(False, "none", "One of your roles is blocked from using this bot.")

    if enforce_channel and channel_id is not None:
        blocked_channels = _ids(config.get("blocked_channel_ids"))
        allowed_channels = _ids(config.get("allowed_channel_ids"))
        if channel_id in blocked_channels:
            return AccessDecision(False, "none", "The bot dashboard is blocked in this channel.")
        if allowed_channels and channel_id not in allowed_channels:
            return AccessDecision(False, "none", "The bot dashboard is only available in approved channels.")

    permissions = interaction.user.guild_permissions
    if user_id == guild.owner_id:
        level: AccessLevel = "owner"
    elif permissions.administrator or permissions.manage_guild:
        level = "admin"
    elif user_id in _ids(config.get("allowed_user_ids")):
        level = "manager"
    elif role_ids & _ids(config.get("bot_manager_role_ids")):
        level = "manager"
    elif role_ids & _ids(config.get("moderator_role_ids")):
        level = "moderator"
    else:
        level = "none"

    if _LEVELS[level] < _LEVELS[minimum]:
        return AccessDecision(False, level, "You do not have permission to use this section.")
    return AccessDecision(True, level)


async def can_manage_bot(interaction: discord.Interaction, database) -> bool:
    return (await evaluate_access(interaction, database, minimum="manager")).allowed


async def deny_access(
    interaction: discord.Interaction,
    message: str | None = None,
) -> None:
    text = message or (
        "You are not authorized to use this control.\n"
        "Ask the server owner or an administrator to update Access Control."
    )
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True)
    else:
        await interaction.response.send_message(text, ephemeral=True)
