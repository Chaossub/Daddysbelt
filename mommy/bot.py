from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

import discord
from discord.ext import commands
from openai import AsyncOpenAI

from database.mongo import MongoDatabase
from mommy.dashboard import MommyDashboardView, build_home_embed
from mommy.personality import MOMMY_SYSTEM_PROMPT, sanitize_output

log = logging.getLogger("mommy-exe.bot")


class MommyExeBot(commands.Bot):
    def __init__(self, *, mongodb_uri: str, database_name: str) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.message_content = True
        intents.moderation = True
        intents.voice_states = True

        super().__init__(
            command_prefix="!",
            intents=intents,
            allowed_mentions=discord.AllowedMentions(
                everyone=False,
                roles=False,
                users=True,
                replied_user=False,
            ),
        )
        self.database = MongoDatabase(uri=mongodb_uri, database_name=database_name)
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        self._openai = AsyncOpenAI(api_key=api_key) if api_key else None
        self._model = os.getenv("MOMMY_AI_MODEL", os.getenv("SOLO_AI_MODEL", "gpt-5.6-luna")).strip()

    @property
    def ai_available(self) -> bool:
        return self._openai is not None

    async def setup_hook(self) -> None:
        await self.database.connect()

    async def on_ready(self) -> None:
        if not self.user:
            return
        log.info("Logged in as %s (%s).", self.user, self.user.id)
        await self.change_presence(activity=discord.CustomActivity(name="supervising voice chat"))
        for guild in self.guilds:
            await self.database.ensure_guild_profile(guild)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self.database.ensure_guild_profile(guild)

    async def _memory_collection(self):
        # MongoDatabase intentionally exposes the raw DB only internally, so use
        # a dedicated collection through the already-connected client database.
        if self.database._database is None:  # noqa: SLF001 - same-project integration
            raise RuntimeError("MongoDB is not connected")
        collection = self.database._database["mommy_ai_memory"]  # noqa: SLF001
        await __import__("asyncio").to_thread(collection.create_index, [("guild_id", 1), ("created_at", -1)])
        return collection

    async def _load_memory(self, guild_id: int, user_id: int, *, limit: int = 12) -> list[dict[str, Any]]:
        collection = await self._memory_collection()

        def _load() -> list[dict[str, Any]]:
            docs = list(
                collection.find({"guild_id": guild_id, "user_id": user_id})
                .sort("created_at", -1)
                .limit(limit)
            )
            docs.reverse()
            return docs

        return await __import__("asyncio").to_thread(_load)

    async def _save_memory(self, *, guild_id: int, user_id: int, display_name: str, human_text: str, reply_text: str) -> None:
        collection = await self._memory_collection()
        doc = {
            "guild_id": guild_id,
            "user_id": user_id,
            "display_name": display_name,
            "human_text": human_text,
            "reply_text": reply_text,
            "created_at": datetime.now(timezone.utc),
        }
        await __import__("asyncio").to_thread(collection.insert_one, doc)

    async def generate_reply(self, message: discord.Message, user_text: str) -> str:
        if self._openai is None:
            return "I am awake, sweetheart, but my brain isn't plugged in yet."

        guild_id = message.guild.id if message.guild else 0
        history = await self._load_memory(guild_id, message.author.id)
        history_lines: list[str] = []
        for item in history:
            name = str(item.get("display_name") or "User")
            history_lines.append(f"{name}: {item.get('human_text', '')}")
            history_lines.append(f"Mommy.exe: {item.get('reply_text', '')}")

        history_text = "\n".join(history_lines[-24:])
        prompt = (
            (f"Recent conversations this person had directly with you:\n{history_text}\n\n" if history_text else "")
            + f"{message.author.display_name}: {user_text}\nMommy.exe:"
        )
        try:
            response = await self._openai.responses.create(
                model=self._model,
                instructions=MOMMY_SYSTEM_PROMPT,
                input=prompt,
                max_output_tokens=180,
            )
            text = sanitize_output(response.output_text or "")
        except Exception:
            log.exception("Mommy.exe AI reply failed")
            return "My brain just tripped over its own heels. Try that again in a second."

        if not text:
            return "Mm. Try me again."
        await self._save_memory(
            guild_id=guild_id,
            user_id=message.author.id,
            display_name=message.author.display_name,
            human_text=user_text,
            reply_text=text,
        )
        return text

    @staticmethod
    def _strip_mention(content: str, bot_id: int) -> str:
        content = re.sub(rf"<@!?{bot_id}>", "", content or "", flags=re.IGNORECASE)
        return content.strip()

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if not self.user:
            return

        mentioned = self.user in message.mentions
        prefix_dashboard = message.content.strip().lower() in {"!mommy", "!mommy dashboard", "!mommy-dashboard"}

        if prefix_dashboard:
            await message.channel.send(embed=build_home_embed(), view=MommyDashboardView(self))
            return

        if mentioned:
            user_text = self._strip_mention(message.content, self.user.id)
            if user_text.lower() in {"dashboard", "panel", "controls", "settings"}:
                await message.channel.send(embed=build_home_embed(), view=MommyDashboardView(self))
                return
            if not user_text:
                user_text = "You were mentioned. Respond naturally and briefly."
            async with message.channel.typing():
                reply = await self.generate_reply(message, user_text)
            await message.reply(reply, mention_author=False)
            return

        await self.process_commands(message)

    async def close(self) -> None:
        await self.database.close()
        await super().close()
