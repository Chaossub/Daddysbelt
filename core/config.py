from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    discord_token: str
    mongodb_uri: str
    mongodb_database: str
    development_guild_id: int | None

    @classmethod
    def from_environment(cls) -> "Settings":
        discord_token = os.getenv("DISCORD_TOKEN", "").strip()
        mongodb_uri = os.getenv("MONGODB_URI", "").strip()
        mongodb_database = (
            os.getenv("MONGODB_DATABASE", "daddys_belt").strip()
            or "daddys_belt"
        )

        raw_guild_id = os.getenv("GUILD_ID", "").strip()
        development_guild_id = int(raw_guild_id) if raw_guild_id else None

        missing: list[str] = []
        if not discord_token:
            missing.append("DISCORD_TOKEN")
        if not mongodb_uri:
            missing.append("MONGODB_URI")

        if missing:
            raise RuntimeError(
                "Missing required environment variable(s): "
                + ", ".join(missing)
            )

        return cls(
            discord_token=discord_token,
            mongodb_uri=mongodb_uri,
            mongodb_database=mongodb_database,
            development_guild_id=development_guild_id,
        )
