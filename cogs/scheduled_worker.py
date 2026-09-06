from __future__ import annotations

import logging
import random
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks

from services.schedule_utils import next_occurrence

log = logging.getLogger("daddys-belt.scheduler")


class ScheduledMessageWorker(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.process_schedules.start()

    def cog_unload(self) -> None:
        self.process_schedules.cancel()

    @staticmethod
    def choose_random_member(
        guild: discord.Guild,
    ) -> discord.Member | None:
        eligible = [
            member
            for member in guild.members
            if not member.bot
        ]

        if not eligible:
            return None

        return random.choice(eligible)

    def resolve_target(
        self,
        guild: discord.Guild,
        item: dict,
    ) -> discord.Member | None:
        target_type = str(
            item.get("target_type", "specific_user")
        ).lower()

        if target_type == "random_member":
            return self.choose_random_member(guild)

        try:
            user_id = int(item["user_id"])
        except (TypeError, ValueError, KeyError):
            return None

        return guild.get_member(user_id)

    @tasks.loop(seconds=30)
    async def process_schedules(self) -> None:
        now = datetime.now(timezone.utc)
        due = await self.bot.database.get_due_scheduled_messages(
            now
        )

        for record in due:
            guild_id = int(record["guild_id"])
            item = record["item"]
            schedule_id = str(item["_id"])

            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue

            try:
                channel_id = int(item["channel_id"])
            except (TypeError, ValueError, KeyError):
                await self.bot.database.toggle_scheduled_message(
                    guild_id,
                    schedule_id,
                    False,
                )
                continue

            channel = await self.bot.get_text_channel(
                guild,
                channel_id,
            )
            if channel is None:
                log.warning(
                    "Could not access scheduled-message channel %s "
                    "in guild %s.",
                    channel_id,
                    guild_id,
                )
                continue

            target = self.resolve_target(guild, item)
            if target is None:
                log.warning(
                    "Could not resolve a target for schedule %s "
                    "in guild %s.",
                    schedule_id,
                    guild_id,
                )
                continue

            header = str(item.get("header", "")).strip()
            message = str(item.get("content", "")).strip()
            repeat = str(item.get("repeat", "once"))

            ping = target.mention

            parts = [ping]

            if header:
                parts.append(f"**{header}**")

            if message:
                parts.append(message)

            body = "\n\n".join(parts)

            try:
                await channel.send(
                    body,
                    allowed_mentions=discord.AllowedMentions(
                        users=True,
                        roles=False,
                        everyone=False,
                    ),
                )
            except discord.Forbidden:
                log.warning(
                    "Missing permission to send scheduled message "
                    "in channel %s.",
                    channel_id,
                )
                continue
            except discord.HTTPException:
                log.exception(
                    "Discord rejected scheduled message %s.",
                    schedule_id,
                )
                continue

            next_run = next_occurrence(
                item["next_run_at"],
                repeat,
                str(record.get("timezone")),
            )

            await self.bot.database.complete_scheduled_message(
                guild_id,
                schedule_id,
                next_run_at=next_run,
            )

    @process_schedules.before_loop
    async def before_process_schedules(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot) -> None:
    await bot.add_cog(ScheduledMessageWorker(bot))
