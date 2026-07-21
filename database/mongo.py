from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import certifi
import discord
from bson import ObjectId
from pymongo import ASCENDING, MongoClient, ReturnDocument
from pymongo.collection import Collection
from pymongo.database import Database

log = logging.getLogger("daddys-belt.database")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MongoDatabase:
    def __init__(self, uri: str, database_name: str) -> None:
        self._uri = uri
        self._database_name = database_name
        self._client: MongoClient[dict[str, Any]] | None = None
        self._database: Database[dict[str, Any]] | None = None
        self.connected = False

    @property
    def guilds(self) -> Collection[dict[str, Any]]:
        if self._database is None:
            raise RuntimeError("MongoDB is not connected.")
        return self._database["guilds"]

    async def connect(self) -> None:
        def _connect() -> None:
            self._client = MongoClient(
                self._uri,
                tlsCAFile=certifi.where(),
                serverSelectionTimeoutMS=15_000,
                connectTimeoutMS=15_000,
                tz_aware=True,
            )
            self._client.admin.command("ping")
            self._database = self._client[self._database_name]
            self.guilds.create_index(
                [("guild_id", ASCENDING)],
                unique=True,
                name="unique_guild_id",
            )

        await asyncio.to_thread(_connect)
        self.connected = True
        log.info(
            "Connected to MongoDB database %r.",
            self._database_name,
        )

    async def close(self) -> None:
        if self._client is not None:
            await asyncio.to_thread(self._client.close)
        self.connected = False

    async def ensure_guild_profile(
        self,
        guild: discord.Guild,
    ) -> dict[str, Any]:
        now = utc_now()

        defaults = {
            "guild_id": guild.id,
            "created_at": now,
            "response_mode": "professional",
            "member_events": {
                "welcome": {
                    "enabled": False,
                    "channel_id": None,
                    "selection_mode": "weighted",
                    "ping_member": True,
                    "messages": [],
                },
                "goodbye": {
                    "enabled": False,
                    "channel_id": None,
                    "selection_mode": "weighted",
                    "messages": [],
                },
                "kick": {
                    "enabled": False,
                    "channel_id": None,
                    "selection_mode": "weighted",
                    "messages": [],
                },
                "ban": {
                    "enabled": False,
                    "channel_id": None,
                    "selection_mode": "weighted",
                    "messages": [],
                },
            },
            "welcome": {
                "enabled": False,
                "channel_id": None,
                "random_enabled": False,
                "messages": [],
            },
            "triggers": [],
            "scheduled_messages": {
                "timezone": "America/Los_Angeles",
                "items": [],
            },
            "pokemon_stock": {
                "enabled": False,
                "channel_id": None,
                "ping_role_id": None,
                "alerts": {
                    "in_stock": True,
                    "out_of_stock": False,
                    "price_change": False,
                },
                "products": [],
            },
            "moderation": {
                "scheduled_timeouts": [],
                "warnings": [],
                "cases": [],
            },
            "logs": {
                "channel_id": None,
                "moderation": True,
                "configuration": True,
                "deleted_messages": False,
                "edited_messages": False,
            },
            "access_control": {
                "bot_manager_role_ids": [],
                "moderator_role_ids": [],
                "allowed_user_ids": [],
                "blocked_user_ids": [],
                "blocked_role_ids": [],
                "allowed_channel_ids": [],
                "blocked_channel_ids": [],
            },
            "statistics": {
                "members_welcomed": 0,
                "triggers_fired": 0,
                "commands_used": 0,
                "warnings_issued": 0,
                "timeouts_issued": 0,
                "scheduled_messages_sent": 0,
            },
        }

        def _upsert() -> dict[str, Any]:
            self.guilds.update_one(
                {"guild_id": guild.id},
                {
                    "$setOnInsert": defaults,
                    "$set": {
                        "guild_name": guild.name,
                        "owner_id": guild.owner_id,
                        "member_count": guild.member_count,
                        "active": True,
                        "last_seen_at": now,
                        "removed_at": None,
                    },
                },
                upsert=True,
            )

            self.guilds.update_one(
                {
                    "guild_id": guild.id,
                    "scheduled_messages": {"$exists": False},
                },
                {
                    "$set": {
                        "scheduled_messages": {
                            "timezone": "America/Los_Angeles",
                            "items": [],
                        }
                    }
                },
            )

            self.guilds.update_one(
                {"guild_id": guild.id, "member_events": {"$exists": False}},
                {"$set": {"member_events": defaults["member_events"]}},
            )

            self.guilds.update_one(
                {"guild_id": guild.id, "pokemon_stock": {"$exists": False}},
                {"$set": {"pokemon_stock": defaults["pokemon_stock"]}},
            )

            self.guilds.update_one(
                {"guild_id": guild.id, "access_control": {"$exists": False}},
                {"$set": {"access_control": defaults["access_control"]}},
            )
            self.guilds.update_one(
                {"guild_id": guild.id, "access_control.allowed_user_ids": {"$exists": False}},
                {"$set": {"access_control.allowed_user_ids": []}},
            )

            # Preserve existing welcome setups from earlier phases.
            existing = self.guilds.find_one({"guild_id": guild.id}) or {}
            legacy = existing.get("welcome", {})
            events = existing.get("member_events", {})
            if legacy.get("messages") and not events.get("welcome", {}).get("messages"):
                converted = []
                for item in legacy.get("messages", []):
                    converted.append({
                        **item,
                        "weight": int(item.get("weight", 1) or 1),
                    })
                self.guilds.update_one(
                    {"guild_id": guild.id},
                    {"$set": {
                        "member_events.welcome.enabled": bool(legacy.get("enabled", False)),
                        "member_events.welcome.channel_id": legacy.get("channel_id"),
                        "member_events.welcome.selection_mode": (
                            "equal" if legacy.get("random_enabled", False) else "fixed"
                        ),
                        "member_events.welcome.ping_member": True,
                        "member_events.welcome.messages": converted,
                    }},
                )

            self.guilds.update_one(
                {
                    "guild_id": guild.id,
                    "moderation.cases": {"$exists": False},
                },
                {
                    "$set": {
                        "moderation.cases": [],
                    }
                },
            )

            self.guilds.update_one(
                {
                    "guild_id": guild.id,
                    "statistics.scheduled_messages_sent": {
                        "$exists": False
                    },
                },
                {
                    "$set": {
                        "statistics.scheduled_messages_sent": 0
                    }
                },
            )

            document = self.guilds.find_one(
                {"guild_id": guild.id}
            )
            if document is None:
                raise RuntimeError(
                    "Guild profile was not created."
                )
            return document

        return await asyncio.to_thread(_upsert)

    async def get_guild_profile(
        self,
        guild_id: int,
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self.guilds.find_one,
            {"guild_id": guild_id},
        )

    async def set_access_control_list(
        self,
        guild_id: int,
        field: str,
        values: list[int],
    ) -> dict[str, Any] | None:
        allowed_fields = {
            "bot_manager_role_ids",
            "moderator_role_ids",
            "allowed_user_ids",
            "blocked_user_ids",
            "blocked_role_ids",
            "allowed_channel_ids",
            "blocked_channel_ids",
        }
        if field not in allowed_fields:
            raise ValueError(f"Unsupported access-control field: {field}")
        clean_values = list(dict.fromkeys(int(value) for value in values))
        return await asyncio.to_thread(
            self.guilds.find_one_and_update,
            {"guild_id": guild_id},
            {
                "$set": {
                    f"access_control.{field}": clean_values,
                    "last_updated_at": utc_now(),
                }
            },
            return_document=ReturnDocument.AFTER,
        )

    async def set_welcome_channel(
        self,
        guild_id: int,
        channel_id: int,
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self.guilds.find_one_and_update,
            {"guild_id": guild_id},
            {
                "$set": {
                    "welcome.channel_id": channel_id,
                    "last_updated_at": utc_now(),
                }
            },
            return_document=ReturnDocument.AFTER,
        )

    async def set_welcome_enabled(
        self,
        guild_id: int,
        enabled: bool,
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self.guilds.find_one_and_update,
            {"guild_id": guild_id},
            {
                "$set": {
                    "welcome.enabled": enabled,
                    "last_updated_at": utc_now(),
                }
            },
            return_document=ReturnDocument.AFTER,
        )

    async def add_welcome_message(
        self,
        guild_id: int,
        content: str,
        created_by: int,
    ) -> dict[str, Any] | None:
        message = {
            "_id": ObjectId(),
            "content": content,
            "created_by": created_by,
            "created_at": utc_now(),
            "enabled": True,
            "image_url": None,
        }

        return await asyncio.to_thread(
            self.guilds.find_one_and_update,
            {"guild_id": guild_id},
            {
                "$push": {"welcome.messages": message},
                "$set": {"last_updated_at": utc_now()},
            },
            return_document=ReturnDocument.AFTER,
        )


    async def set_welcome_random(
        self,
        guild_id: int,
        enabled: bool,
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self.guilds.find_one_and_update,
            {"guild_id": guild_id},
            {
                "$set": {
                    "welcome.random_enabled": enabled,
                    "last_updated_at": utc_now(),
                }
            },
            return_document=ReturnDocument.AFTER,
        )

    async def set_response_mode(
        self,
        guild_id: int,
        mode: str,
    ) -> dict[str, Any] | None:
        normalized = mode.lower().strip()
        if normalized not in {"professional", "daddy"}:
            raise ValueError("Unsupported response mode.")

        return await asyncio.to_thread(
            self.guilds.find_one_and_update,
            {"guild_id": guild_id},
            {
                "$set": {
                    "response_mode": normalized,
                    "last_updated_at": utc_now(),
                }
            },
            return_document=ReturnDocument.AFTER,
        )

    async def update_welcome_message(
        self,
        guild_id: int,
        message_id: str,
        content: str,
        image_url: str | None,
        updated_by: int,
    ) -> dict[str, Any] | None:
        try:
            object_id = ObjectId(message_id)
        except Exception:
            return None

        return await asyncio.to_thread(
            self.guilds.find_one_and_update,
            {
                "guild_id": guild_id,
                "welcome.messages._id": object_id,
            },
            {
                "$set": {
                    "welcome.messages.$.content": content,
                    "welcome.messages.$.image_url": image_url,
                    "welcome.messages.$.updated_by": updated_by,
                    "welcome.messages.$.updated_at": utc_now(),
                    "last_updated_at": utc_now(),
                }
            },
            return_document=ReturnDocument.AFTER,
        )

    async def delete_welcome_message(
        self,
        guild_id: int,
        message_id: str,
    ) -> dict[str, Any] | None:
        try:
            object_id = ObjectId(message_id)
        except Exception:
            return None

        return await asyncio.to_thread(
            self.guilds.find_one_and_update,
            {"guild_id": guild_id},
            {
                "$pull": {
                    "welcome.messages": {
                        "_id": object_id,
                    }
                },
                "$set": {
                    "last_updated_at": utc_now(),
                },
            },
            return_document=ReturnDocument.AFTER,
        )




    @staticmethod
    def _validate_member_event(event_type: str) -> str:
        normalized = event_type.lower().strip()
        if normalized not in {"welcome", "goodbye", "kick", "ban"}:
            raise ValueError("Unsupported member event type.")
        return normalized

    async def set_member_event_enabled(self, guild_id: int, event_type: str, enabled: bool):
        event_type = self._validate_member_event(event_type)
        return await asyncio.to_thread(
            self.guilds.find_one_and_update,
            {"guild_id": guild_id},
            {"$set": {f"member_events.{event_type}.enabled": enabled, "last_updated_at": utc_now()}},
            return_document=ReturnDocument.AFTER,
        )

    async def set_member_event_channel(self, guild_id: int, event_type: str, channel_id: int):
        event_type = self._validate_member_event(event_type)
        return await asyncio.to_thread(
            self.guilds.find_one_and_update,
            {"guild_id": guild_id},
            {"$set": {f"member_events.{event_type}.channel_id": channel_id, "last_updated_at": utc_now()}},
            return_document=ReturnDocument.AFTER,
        )

    async def set_member_event_mode(self, guild_id: int, event_type: str, mode: str):
        event_type = self._validate_member_event(event_type)
        mode = mode.lower().strip()
        if mode not in {"fixed", "equal", "weighted"}:
            raise ValueError("Unsupported selection mode.")
        return await asyncio.to_thread(
            self.guilds.find_one_and_update,
            {"guild_id": guild_id},
            {"$set": {f"member_events.{event_type}.selection_mode": mode, "last_updated_at": utc_now()}},
            return_document=ReturnDocument.AFTER,
        )

    async def set_welcome_ping(self, guild_id: int, enabled: bool):
        return await asyncio.to_thread(
            self.guilds.find_one_and_update,
            {"guild_id": guild_id},
            {"$set": {"member_events.welcome.ping_member": enabled, "last_updated_at": utc_now()}},
            return_document=ReturnDocument.AFTER,
        )

    async def add_member_event_message(self, guild_id: int, event_type: str, *, content: str, image_url: str | None, weight: int, created_by: int):
        event_type = self._validate_member_event(event_type)
        message = {
            "_id": ObjectId(), "content": content, "image_url": image_url,
            "weight": max(1, min(100, int(weight))), "enabled": True,
            "created_by": created_by, "created_at": utc_now(),
        }
        return await asyncio.to_thread(
            self.guilds.find_one_and_update,
            {"guild_id": guild_id},
            {"$push": {f"member_events.{event_type}.messages": message}, "$set": {"last_updated_at": utc_now()}},
            return_document=ReturnDocument.AFTER,
        )

    async def update_member_event_message(self, guild_id: int, event_type: str, message_id: str, *, content: str, image_url: str | None, weight: int, updated_by: int):
        event_type = self._validate_member_event(event_type)
        try: object_id = ObjectId(message_id)
        except Exception: return None
        base = f"member_events.{event_type}.messages"
        return await asyncio.to_thread(
            self.guilds.find_one_and_update,
            {"guild_id": guild_id, f"{base}._id": object_id},
            {"$set": {
                f"{base}.$.content": content, f"{base}.$.image_url": image_url,
                f"{base}.$.weight": max(1, min(100, int(weight))),
                f"{base}.$.updated_by": updated_by, f"{base}.$.updated_at": utc_now(),
                "last_updated_at": utc_now(),
            }},
            return_document=ReturnDocument.AFTER,
        )

    async def delete_member_event_message(self, guild_id: int, event_type: str, message_id: str):
        event_type = self._validate_member_event(event_type)
        try: object_id = ObjectId(message_id)
        except Exception: return None
        return await asyncio.to_thread(
            self.guilds.find_one_and_update,
            {"guild_id": guild_id},
            {"$pull": {f"member_events.{event_type}.messages": {"_id": object_id}}, "$set": {"last_updated_at": utc_now()}},
            return_document=ReturnDocument.AFTER,
        )

    async def add_moderation_case(
        self,
        guild_id: int,
        *,
        action: str,
        target_id: int | None,
        moderator_id: int,
        reason: str,
        duration_minutes: int | None = None,
        message_count: int | None = None,
    ) -> dict[str, Any] | None:
        case = {
            "_id": ObjectId(),
            "action": action,
            "target_id": target_id,
            "moderator_id": moderator_id,
            "reason": reason,
            "duration_minutes": duration_minutes,
            "message_count": message_count,
            "created_at": utc_now(),
        }

        increments: dict[str, int] = {}
        if action == "warn":
            increments["statistics.warnings_issued"] = 1
        elif action == "timeout":
            increments["statistics.timeouts_issued"] = 1

        update: dict[str, Any] = {
            "$push": {
                "moderation.cases": {
                    "$each": [case],
                    "$slice": -200,
                }
            },
            "$set": {
                "last_updated_at": utc_now(),
            },
        }
        if increments:
            update["$inc"] = increments

        return await asyncio.to_thread(
            self.guilds.find_one_and_update,
            {"guild_id": guild_id},
            update,
            return_document=ReturnDocument.AFTER,
        )

    async def get_moderation_cases(
        self,
        guild_id: int,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        profile = await self.get_guild_profile(guild_id)
        if not profile:
            return []

        cases = profile.get("moderation", {}).get("cases", [])
        return list(reversed(cases[-limit:]))

    async def add_trigger(
        self,
        guild_id: int,
        *,
        phrase: str,
        responses: list[str],
        match_type: str,
        cooldown_seconds: int,
        chance_percent: int,
        channel_id: int | None,
        ping_author: bool,
        created_by: int,
    ) -> dict[str, Any] | None:
        trigger = {
            "_id": ObjectId(),
            "phrase": phrase,
            "responses": responses,
            "match_type": match_type,
            "case_sensitive": False,
            "cooldown_seconds": cooldown_seconds,
            "chance_percent": chance_percent,
            "channel_id": channel_id,
            "ping_author": ping_author,
            "enabled": True,
            "created_by": created_by,
            "created_at": utc_now(),
            "times_fired": 0,
            "last_fired_at": None,
        }

        return await asyncio.to_thread(
            self.guilds.find_one_and_update,
            {"guild_id": guild_id},
            {
                "$push": {"triggers": trigger},
                "$set": {"last_updated_at": utc_now()},
            },
            return_document=ReturnDocument.AFTER,
        )

    async def update_trigger(
        self,
        guild_id: int,
        trigger_id: str,
        *,
        phrase: str,
        responses: list[str],
        match_type: str,
        cooldown_seconds: int,
        chance_percent: int,
        channel_id: int | None,
        ping_author: bool,
        updated_by: int,
    ) -> dict[str, Any] | None:
        try:
            object_id = ObjectId(trigger_id)
        except Exception:
            return None

        return await asyncio.to_thread(
            self.guilds.find_one_and_update,
            {
                "guild_id": guild_id,
                "triggers._id": object_id,
            },
            {
                "$set": {
                    "triggers.$.phrase": phrase,
                    "triggers.$.responses": responses,
                    "triggers.$.match_type": match_type,
                    "triggers.$.cooldown_seconds": cooldown_seconds,
                    "triggers.$.chance_percent": chance_percent,
                    "triggers.$.channel_id": channel_id,
                    "triggers.$.ping_author": ping_author,
                    "triggers.$.updated_by": updated_by,
                    "triggers.$.updated_at": utc_now(),
                    "last_updated_at": utc_now(),
                }
            },
            return_document=ReturnDocument.AFTER,
        )

    async def toggle_trigger(
        self,
        guild_id: int,
        trigger_id: str,
        enabled: bool,
    ) -> dict[str, Any] | None:
        try:
            object_id = ObjectId(trigger_id)
        except Exception:
            return None

        return await asyncio.to_thread(
            self.guilds.find_one_and_update,
            {
                "guild_id": guild_id,
                "triggers._id": object_id,
            },
            {
                "$set": {
                    "triggers.$.enabled": enabled,
                    "last_updated_at": utc_now(),
                }
            },
            return_document=ReturnDocument.AFTER,
        )

    async def delete_trigger(
        self,
        guild_id: int,
        trigger_id: str,
    ) -> dict[str, Any] | None:
        try:
            object_id = ObjectId(trigger_id)
        except Exception:
            return None

        return await asyncio.to_thread(
            self.guilds.find_one_and_update,
            {"guild_id": guild_id},
            {
                "$pull": {"triggers": {"_id": object_id}},
                "$set": {"last_updated_at": utc_now()},
            },
            return_document=ReturnDocument.AFTER,
        )

    async def record_trigger_fired(
        self,
        guild_id: int,
        trigger_id: str,
    ) -> None:
        try:
            object_id = ObjectId(trigger_id)
        except Exception:
            return

        await asyncio.to_thread(
            self.guilds.update_one,
            {
                "guild_id": guild_id,
                "triggers._id": object_id,
            },
            {
                "$set": {
                    "triggers.$.last_fired_at": utc_now(),
                },
                "$inc": {
                    "triggers.$.times_fired": 1,
                    "statistics.triggers_fired": 1,
                },
            },
        )

    async def set_schedule_timezone(
        self,
        guild_id: int,
        timezone_name: str,
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self.guilds.find_one_and_update,
            {"guild_id": guild_id},
            {
                "$set": {
                    "scheduled_messages.timezone": timezone_name,
                    "last_updated_at": utc_now(),
                }
            },
            return_document=ReturnDocument.AFTER,
        )

    async def add_scheduled_message(
        self,
        guild_id: int,
        *,
        channel_id: int,
        user_id: int | None,
        target_type: str,
        header: str,
        content: str,
        repeat: str,
        next_run_at: datetime,
        created_by: int,
    ) -> dict[str, Any] | None:
        item = {
            "_id": ObjectId(),
            "channel_id": channel_id,
            "user_id": user_id,
            "target_type": target_type,
            "header": header,
            "content": content,
            "repeat": repeat,
            "active": True,
            "next_run_at": next_run_at,
            "created_by": created_by,
            "created_at": utc_now(),
            "last_sent_at": None,
            "send_count": 0,
        }

        return await asyncio.to_thread(
            self.guilds.find_one_and_update,
            {"guild_id": guild_id},
            {
                "$push": {
                    "scheduled_messages.items": item,
                },
                "$set": {
                    "last_updated_at": utc_now(),
                },
            },
            return_document=ReturnDocument.AFTER,
        )


    async def update_scheduled_message(
        self,
        guild_id: int,
        schedule_id: str,
        *,
        channel_id: int,
        user_id: int | None,
        target_type: str,
        header: str,
        content: str,
        repeat: str,
        next_run_at: datetime,
        updated_by: int,
    ) -> dict[str, Any] | None:
        try:
            object_id = ObjectId(schedule_id)
        except Exception:
            return None

        return await asyncio.to_thread(
            self.guilds.find_one_and_update,
            {
                "guild_id": guild_id,
                "scheduled_messages.items._id": object_id,
            },
            {
                "$set": {
                    "scheduled_messages.items.$.channel_id": channel_id,
                    "scheduled_messages.items.$.user_id": user_id,
                    "scheduled_messages.items.$.target_type": target_type,
                    "scheduled_messages.items.$.header": header,
                    "scheduled_messages.items.$.content": content,
                    "scheduled_messages.items.$.repeat": repeat,
                    "scheduled_messages.items.$.next_run_at": next_run_at,
                    "scheduled_messages.items.$.updated_by": updated_by,
                    "scheduled_messages.items.$.updated_at": utc_now(),
                    "last_updated_at": utc_now(),
                }
            },
            return_document=ReturnDocument.AFTER,
        )

    async def toggle_scheduled_message(
        self,
        guild_id: int,
        schedule_id: str,
        active: bool,
    ) -> dict[str, Any] | None:
        try:
            object_id = ObjectId(schedule_id)
        except Exception:
            return None

        return await asyncio.to_thread(
            self.guilds.find_one_and_update,
            {
                "guild_id": guild_id,
                "scheduled_messages.items._id": object_id,
            },
            {
                "$set": {
                    "scheduled_messages.items.$.active": active,
                    "last_updated_at": utc_now(),
                }
            },
            return_document=ReturnDocument.AFTER,
        )

    async def delete_scheduled_message(
        self,
        guild_id: int,
        schedule_id: str,
    ) -> dict[str, Any] | None:
        try:
            object_id = ObjectId(schedule_id)
        except Exception:
            return None

        return await asyncio.to_thread(
            self.guilds.find_one_and_update,
            {"guild_id": guild_id},
            {
                "$pull": {
                    "scheduled_messages.items": {
                        "_id": object_id,
                    }
                },
                "$set": {
                    "last_updated_at": utc_now(),
                },
            },
            return_document=ReturnDocument.AFTER,
        )

    async def get_due_scheduled_messages(
        self,
        now: datetime,
    ) -> list[dict[str, Any]]:
        def _get_due() -> list[dict[str, Any]]:
            documents = self.guilds.find(
                {
                    "active": True,
                    "scheduled_messages.items": {
                        "$elemMatch": {
                            "active": True,
                            "next_run_at": {"$lte": now},
                        }
                    },
                },
                {
                    "guild_id": 1,
                    "response_mode": 1,
                    "scheduled_messages": 1,
                },
            )

            due: list[dict[str, Any]] = []
            for document in documents:
                settings = document.get(
                    "scheduled_messages",
                    {},
                )
                timezone_name = settings.get(
                    "timezone",
                    "America/Los_Angeles",
                )

                for item in settings.get("items", []):
                    if (
                        item.get("active", False)
                        and item.get("next_run_at")
                        and item["next_run_at"] <= now
                    ):
                        due.append(
                            {
                                "guild_id": document["guild_id"],
                                "response_mode": document.get(
                                    "response_mode",
                                    "professional",
                                ),
                                "timezone": timezone_name,
                                "item": item,
                            }
                        )
            return due

        return await asyncio.to_thread(_get_due)

    async def complete_scheduled_message(
        self,
        guild_id: int,
        schedule_id: str,
        *,
        next_run_at: datetime | None,
    ) -> None:
        object_id = ObjectId(schedule_id)
        update: dict[str, Any] = {
            "scheduled_messages.items.$.last_sent_at": utc_now(),
            "last_updated_at": utc_now(),
        }

        if next_run_at is None:
            update[
                "scheduled_messages.items.$.active"
            ] = False
        else:
            update[
                "scheduled_messages.items.$.next_run_at"
            ] = next_run_at

        await asyncio.to_thread(
            self.guilds.update_one,
            {
                "guild_id": guild_id,
                "scheduled_messages.items._id": object_id,
            },
            {
                "$set": update,
                "$inc": {
                    "scheduled_messages.items.$.send_count": 1,
                    "statistics.scheduled_messages_sent": 1,
                },
            },
        )

    async def increment_stat(
        self,
        guild_id: int,
        stat_name: str,
        amount: int = 1,
    ) -> None:
        allowed = {
            "members_welcomed",
            "triggers_fired",
            "commands_used",
            "warnings_issued",
            "timeouts_issued",
            "scheduled_messages_sent",
        }
        if stat_name not in allowed:
            raise ValueError(
                f"Unsupported statistic: {stat_name}"
            )

        await asyncio.to_thread(
            self.guilds.update_one,
            {"guild_id": guild_id},
            {
                "$inc": {
                    f"statistics.{stat_name}": amount
                }
            },
        )

    async def set_stock_channel(self, guild_id: int, channel_id: int) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self.guilds.find_one_and_update,
            {"guild_id": guild_id},
            {"$set": {"pokemon_stock.channel_id": channel_id, "last_updated_at": utc_now()}},
            return_document=ReturnDocument.AFTER,
        )

    async def set_stock_enabled(self, guild_id: int, enabled: bool) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self.guilds.find_one_and_update,
            {"guild_id": guild_id},
            {"$set": {"pokemon_stock.enabled": enabled, "last_updated_at": utc_now()}},
            return_document=ReturnDocument.AFTER,
        )

    async def set_stock_ping_role(self, guild_id: int, role_id: int | None) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self.guilds.find_one_and_update,
            {"guild_id": guild_id},
            {"$set": {"pokemon_stock.ping_role_id": role_id, "last_updated_at": utc_now()}},
            return_document=ReturnDocument.AFTER,
        )

    async def set_stock_alert(self, guild_id: int, alert_name: str, enabled: bool) -> dict[str, Any] | None:
        if alert_name not in {"in_stock", "out_of_stock", "price_change"}:
            raise ValueError("Unsupported stock alert setting")
        return await asyncio.to_thread(
            self.guilds.find_one_and_update,
            {"guild_id": guild_id},
            {"$set": {f"pokemon_stock.alerts.{alert_name}": enabled, "last_updated_at": utc_now()}},
            return_document=ReturnDocument.AFTER,
        )

    async def add_stock_product(self, guild_id: int, *, url: str, store: str, sku: str | None, name: str, price: str | None, image_url: str | None, available: bool | None, detail: str, created_by: int) -> dict[str, Any] | None:
        product = {
            "_id": ObjectId(), "url": url, "store": store, "sku": sku, "name": name,
            "price": price, "image_url": image_url, "available": available,
            "detail": detail, "created_by": created_by, "created_at": utc_now(),
            "last_checked_at": utc_now(), "last_error": None,
        }
        return await asyncio.to_thread(
            self.guilds.find_one_and_update,
            {"guild_id": guild_id, "pokemon_stock.products.url": {"$ne": url}},
            {"$push": {"pokemon_stock.products": product}, "$set": {"last_updated_at": utc_now()}},
            return_document=ReturnDocument.AFTER,
        )

    async def remove_stock_product(self, guild_id: int, product_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(
            self.guilds.find_one_and_update,
            {"guild_id": guild_id},
            {"$pull": {"pokemon_stock.products": {"_id": ObjectId(product_id)}}, "$set": {"last_updated_at": utc_now()}},
            return_document=ReturnDocument.AFTER,
        )

    async def update_stock_product_status(self, guild_id: int, product_id: str, **values: Any) -> None:
        update = {f"pokemon_stock.products.$.{key}": value for key, value in values.items()}
        if "checked_at" in update:
            update["pokemon_stock.products.$.last_checked_at"] = update.pop("checked_at")
        if "error" in update:
            update["pokemon_stock.products.$.last_error"] = update.pop("error")
        await asyncio.to_thread(
            self.guilds.update_one,
            {"guild_id": guild_id, "pokemon_stock.products._id": ObjectId(product_id)},
            {"$set": update},
        )

    async def get_enabled_stock_configs(self) -> list[dict[str, Any]]:
        def _get() -> list[dict[str, Any]]:
            return list(self.guilds.find(
                {"active": True, "pokemon_stock.enabled": True, "pokemon_stock.channel_id": {"$ne": None}},
                {"guild_id": 1, "pokemon_stock": 1},
            ))
        return await asyncio.to_thread(_get)

    async def mark_guild_inactive(
        self,
        guild_id: int,
    ) -> None:
        await asyncio.to_thread(
            self.guilds.update_one,
            {"guild_id": guild_id},
            {
                "$set": {
                    "active": False,
                    "removed_at": utc_now(),
                }
            },
        )
