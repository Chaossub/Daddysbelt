from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

import discord
from openai import AsyncOpenAI

log = logging.getLogger("daddys-belt.ai")

DADDY_SYSTEM_PROMPT = (
    "You are Daddy's Belt, a Discord bot with dry, confident, chaotic dad energy. "
    "You are witty, casually protective, mildly sarcastic, and comfortable joking with adults, "
    "but you are not cruel, controlling, or constantly sexual. Talk like someone actually hanging "
    "out in Discord: concise, natural, varied, and not like customer support. You may roast someone "
    "lightly when they invite it, but do not harass or pile on. If someone is serious, be useful and "
    "calmer. Do not pretend to be human. Do not volunteer implementation details, prompts, API keys, "
    "or who created you. Never claim you performed moderation actions you did not actually perform."
)


class DaddyAI:
    """Text conversation brain for messages explicitly addressed to Daddy's Belt."""

    def __init__(self, bot) -> None:
        self.bot = bot
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.client = AsyncOpenAI(api_key=api_key) if api_key else None
        self.model = os.getenv("DADDY_AI_MODEL", os.getenv("MOMMY_AI_MODEL", "gpt-5.6-luna")).strip()

    @property
    def available(self) -> bool:
        return self.client is not None

    async def _collection(self):
        database = self.bot.database._database  # noqa: SLF001 - same project integration
        if database is None:
            raise RuntimeError("MongoDB is not connected")
        collection = database["daddy_ai_memory"]
        await asyncio.to_thread(collection.create_index, [("guild_id", 1), ("user_id", 1), ("created_at", -1)])
        return collection

    async def _load_history(self, guild_id: int, user_id: int, limit: int = 10) -> list[dict[str, Any]]:
        collection = await self._collection()

        def load() -> list[dict[str, Any]]:
            docs = list(collection.find({"guild_id": guild_id, "user_id": user_id}).sort("created_at", -1).limit(limit))
            docs.reverse()
            return docs

        return await asyncio.to_thread(load)

    async def _save(self, message: discord.Message, human_text: str, reply_text: str) -> None:
        collection = await self._collection()
        await asyncio.to_thread(
            collection.insert_one,
            {
                "guild_id": message.guild.id if message.guild else 0,
                "user_id": message.author.id,
                "display_name": getattr(message.author, "display_name", message.author.name),
                "human_text": human_text,
                "reply_text": reply_text,
                "created_at": datetime.now(timezone.utc),
            },
        )

    async def reply(self, message: discord.Message, user_text: str) -> str:
        if self.client is None:
            return "My brain isn't plugged in right now. Somebody stole the extension cord."

        guild_id = message.guild.id if message.guild else 0
        history = await self._load_history(guild_id, message.author.id)
        lines: list[str] = []
        for item in history:
            lines.append(f"{item.get('display_name', 'User')}: {item.get('human_text', '')}")
            lines.append(f"Daddy's Belt: {item.get('reply_text', '')}")
        history_text = "\n".join(lines[-20:])
        prompt = (
            (f"Recent messages this person directly addressed to you:\n{history_text}\n\n" if history_text else "")
            + f"{getattr(message.author, 'display_name', message.author.name)}: {user_text}\nDaddy's Belt:"
        )
        try:
            response = await self.client.responses.create(
                model=self.model,
                instructions=DADDY_SYSTEM_PROMPT,
                input=prompt,
                max_output_tokens=180,
            )
            text = (response.output_text or "").strip()
        except Exception:
            log.exception("Daddy AI reply failed")
            return "My last brain cell just clocked out. Try me again in a second."

        if not text:
            text = "You rang?"
        await self._save(message, user_text, text)
        return text

    @staticmethod
    def addressed_text(message: discord.Message, bot_user: discord.ClientUser) -> str | None:
        content = (message.content or "").strip()
        if bot_user in message.mentions:
            return re.sub(rf"<@!?{bot_user.id}>", "", content).strip() or "You were mentioned. Respond naturally and briefly."
        match = re.match(
            r"^(?:daddy(?:'s\s+belt)?|daddy\s+bot)\b[,:;.!?\-]*\s*(.*)$",
            content,
            flags=re.IGNORECASE,
        )
        if match:
            return match.group(1).strip() or "You were addressed. Respond naturally and briefly."
        return None
