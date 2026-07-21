from __future__ import annotations

from datetime import timezone

import discord


def render_member_event_message(
    template: str,
    *,
    member: discord.abc.User,
    guild: discord.Guild,
    moderator: discord.abc.User | None = None,
    reason: str | None = None,
    event_type: str = "welcome",
) -> str:
    display_name = getattr(member, "display_name", member.name)
    mention = member.mention if event_type == "welcome" else f"**{member.name}**"
    created_at = discord.utils.format_dt(member.created_at, style="F")

    joined = getattr(member, "joined_at", None)
    joined_at = (
        discord.utils.format_dt(joined, style="F")
        if joined is not None
        else "Unknown"
    )

    replacements = {
        "{mention}": mention,
        "{username}": member.name,
        "{display_name}": display_name,
        "{server}": guild.name,
        "{member_count}": str(guild.member_count or 0),
        "{moderator}": moderator.mention if moderator else "Unknown",
        "{moderator_name}": moderator.name if moderator else "Unknown",
        "{reason}": reason or "No reason provided.",
        "{created_at}": created_at,
        "{joined_at}": joined_at,
    }

    rendered = template
    for key, value in replacements.items():
        rendered = rendered.replace(key, value)
    return rendered[:4000]
