from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import defaultdict, deque
from datetime import timedelta
from typing import Any

import discord
from discord.ext import commands

log = logging.getLogger("daddys-belt.text-automod")


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").casefold()).strip()


class TextAutomodCog(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        self._hits: dict[tuple[int, int, str], deque[float]] = defaultdict(deque)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not message.guild or message.author.bot or not isinstance(message.author, discord.Member):
            return
        profile = await self.bot.database.get_guild_profile(message.guild.id) or {}
        cfg = profile.get("text_automod") or {}
        if not cfg.get("enabled", False):
            return
        if message.author.id == message.guild.owner_id:
            return
        exceptions = {int(v) for v in cfg.get("exception_role_ids") or []}
        if any(r.id in exceptions for r in message.author.roles):
            return
        text = norm(message.content)
        now = time.monotonic()
        for trig in cfg.get("triggers") or []:
            if not trig.get("enabled", True):
                continue
            phrase = norm(str(trig.get("phrase") or ""))
            if not phrase or phrase not in text:
                continue
            tid = str(trig.get("id") or phrase)
            key = (message.guild.id, message.author.id, tid)
            window = max(1, int(trig.get("window_minutes", 10))) * 60
            threshold = max(1, int(trig.get("threshold", 1)))
            q = self._hits[key]
            q.append(now)
            while q and now - q[0] > window:
                q.popleft()
            try:
                await message.delete()
            except (discord.Forbidden, discord.HTTPException):
                pass
            if len(q) < threshold:
                continue
            q.clear()
            minutes = max(1, int(trig.get("timeout_minutes", 5)))
            try:
                await message.author.timeout(timedelta(minutes=minutes), reason=f"Daddy text automod: {trig.get('label') or phrase}")
            except (discord.Forbidden, discord.HTTPException):
                log.exception("Text automod could not timeout %s", message.author.id)


async def setup(bot) -> None:
    await bot.add_cog(TextAutomodCog(bot))
