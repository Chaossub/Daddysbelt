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
        # Mommy owns the voice side of the household. Daddy keeps text/admin features.
        await self.load_extension("mommy.moderation")
        await self.load_extension("mommy.voice_recording")
        await self.load_extension("mommy.staff_tools")
        await self.load_extension("mommy.memory_tools")
        await self.load_extension("mommy.member_games")

    async def on_ready(self) -> None:
        if not self.user:
            return
        log.info("Logged in as %s (%s).", self.user, self.user.id)
        await self.change_presence(activity=discord.CustomActivity(name="supervising voice chat"))
        for guild in self.guilds:
            await self.database.ensure_guild_profile(guild)
            automod = self.get_cog("VCAutomodCog")
            if automod is not None and hasattr(automod, "ensure_defaults"):
                await automod.ensure_defaults(guild)
            try:
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                log.info("Mommy ready-sync: synced %s command(s) to guild %s (%s).", len(synced), guild.name, guild.id)
            except Exception:
                log.exception("Mommy command sync failed for guild %s.", guild.id)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self.database.ensure_guild_profile(guild)
        try:
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        except Exception:
            log.exception("Mommy command sync failed for newly joined guild %s.", guild.id)

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

    async def _facts_collection(self):
        if self.database._database is None:  # noqa: SLF001
            raise RuntimeError("MongoDB is not connected")
        collection = self.database._database["mommy_user_facts"]  # noqa: SLF001
        await __import__("asyncio").to_thread(collection.create_index, [("guild_id", 1), ("user_id", 1), ("updated_at", -1)])
        return collection

    @staticmethod
    def _extract_memory_facts(text: str) -> list[str]:
        """Conservative fact extraction from messages explicitly addressed to Mommy."""
        raw = re.sub(r"\s+", " ", (text or "").strip())
        if not raw:
            return []
        patterns = [
            r"\bremember(?: that)?\s+(.+?)(?:[.!?]|$)",
            r"\bmy favorite\s+(.+?)(?:[.!?]|$)",
            r"\bi (?:really )?(?:like|love|hate|prefer)\s+(.+?)(?:[.!?]|$)",
            r"\bcall me\s+(.+?)(?:[.!?]|$)",
            r"\bmy name is\s+(.+?)(?:[.!?]|$)",
        ]
        facts: list[str] = []
        for pattern in patterns:
            for match in re.finditer(pattern, raw, flags=re.IGNORECASE):
                value = match.group(0).strip(" .!? ")
                if 3 <= len(value) <= 240 and value.casefold() not in {f.casefold() for f in facts}:
                    facts.append(value)
        return facts[:5]

    async def _remember_facts(self, guild_id: int, user_id: int, display_name: str, text: str) -> None:
        facts = self._extract_memory_facts(text)
        if not facts:
            return
        collection = await self._facts_collection()
        now = datetime.now(timezone.utc)
        for fact in facts:
            key = re.sub(r"\s+", " ", fact.casefold()).strip()
            await __import__("asyncio").to_thread(
                collection.update_one,
                {"guild_id": guild_id, "user_id": user_id, "fact_key": key},
                {"$set": {"display_name": display_name, "fact": fact, "updated_at": now}, "$setOnInsert": {"created_at": now}},
                upsert=True,
            )

    async def _load_facts(self, guild_id: int, user_id: int, *, limit: int = 30) -> list[str]:
        collection = await self._facts_collection()
        docs = await __import__("asyncio").to_thread(
            lambda: list(collection.find({"guild_id": guild_id, "user_id": user_id}).sort("updated_at", -1).limit(limit))
        )
        return [str(doc.get("fact") or "") for doc in docs if doc.get("fact")]

    async def _memory_prefs_collection(self):
        if self.database._database is None:  # noqa: SLF001
            raise RuntimeError("MongoDB is not connected")
        collection = self.database._database["mommy_memory_preferences"]  # noqa: SLF001
        await __import__("asyncio").to_thread(collection.create_index, [("guild_id", 1), ("user_id", 1)], unique=True)
        return collection

    async def memory_enabled(self, guild_id: int, user_id: int) -> bool:
        collection = await self._memory_prefs_collection()
        doc = await __import__("asyncio").to_thread(collection.find_one, {"guild_id": guild_id, "user_id": user_id})
        return bool((doc or {}).get("enabled", True))

    async def set_memory_enabled(self, guild_id: int, user_id: int, enabled: bool) -> None:
        collection = await self._memory_prefs_collection()
        await __import__("asyncio").to_thread(
            collection.update_one,
            {"guild_id": guild_id, "user_id": user_id},
            {"$set": {"enabled": bool(enabled), "updated_at": datetime.now(timezone.utc)}},
            upsert=True,
        )

    async def clear_user_memory(self, guild_id: int, user_id: int) -> tuple[int, int]:
        memory = await self._memory_collection()
        facts = await self._facts_collection()
        a = await __import__("asyncio").to_thread(memory.delete_many, {"guild_id": guild_id, "user_id": user_id})
        b = await __import__("asyncio").to_thread(facts.delete_many, {"guild_id": guild_id, "user_id": user_id})
        return int(a.deleted_count), int(b.deleted_count)

    async def generate_reply(self, message: discord.Message, user_text: str) -> str:
        if self._openai is None:
            return "I am awake, sweetheart, but my brain isn't plugged in yet."

        guild_id = message.guild.id if message.guild else 0
        remember = await self.memory_enabled(guild_id, message.author.id)
        history = await self._load_memory(guild_id, message.author.id) if remember else []
        if remember:
            await self._remember_facts(guild_id, message.author.id, message.author.display_name, user_text)
        facts = await self._load_facts(guild_id, message.author.id) if remember else []
        history_lines: list[str] = []
        for item in history:
            name = str(item.get("display_name") or "User")
            history_lines.append(f"{name}: {item.get('human_text', '')}")
            history_lines.append(f"Mommy.exe: {item.get('reply_text', '')}")

        history_text = "\n".join(history_lines[-24:])
        facts_text = "\n".join(f"- {fact}" for fact in facts)
        prompt = (
            (f"Things this person previously told you to remember or clearly stated about their preferences:\n{facts_text}\n\n" if facts_text else "")
            + (f"Recent conversations this person had directly with you:\n{history_text}\n\n" if history_text else "")
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
        if remember:
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

    @staticmethod
    def _message_admin(member: discord.abc.User) -> bool:
        if not isinstance(member, discord.Member):
            return False
        return bool(
            member.id == member.guild.owner_id
            or member.guild_permissions.administrator
            or member.guild_permissions.manage_guild
        )

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if not self.user:
            return

        mentioned = self.user in message.mentions
        content = message.content.strip()
        wake_match = re.match(r"^(?:mommy(?:\.exe)?|mommy\s+bot)\b[,:;.!?\-]*\s*(.*)$", content, flags=re.IGNORECASE)
        addressed = mentioned or wake_match is not None

        if addressed:
            if mentioned:
                user_text = self._strip_mention(message.content, self.user.id)
            else:
                user_text = (wake_match.group(1) if wake_match else "").strip()
            if user_text.lower() in {"dashboard", "panel", "controls", "settings"}:
                if self._message_admin(message.author):
                    await message.channel.send(embed=build_home_embed(), view=MommyDashboardView(self))
                else:
                    await message.reply("That control panel is admin-only.", mention_author=False)
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
