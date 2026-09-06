from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    daddy_discord_token: str
    mommy_discord_token: str
    mommy_enabled: bool
    mongodb_uri: str
    mongodb_database: str
    mommy_mongodb_database: str
    development_guild_id: int | None

    @property
    def discord_token(self) -> str:
        # Backwards compatibility for the existing Daddy bot code.
        return self.daddy_discord_token

    @classmethod
    def from_environment(cls) -> "Settings":
        daddy_token = (os.getenv("DISCORD_TOKEN_DADDY") or os.getenv("DISCORD_TOKEN") or "").strip()
        mommy_token = os.getenv("DISCORD_TOKEN_MOMMY", "").strip()
        mommy_enabled = os.getenv("ENABLE_MOMMY", "true").strip().lower() not in {"0", "false", "no", "off"}
        mongodb_uri = os.getenv("MONGODB_URI", "").strip()
        mongodb_database = os.getenv("MONGODB_DATABASE", "daddys_belt").strip() or "daddys_belt"
        mommy_mongodb_database = os.getenv("MOMMY_MONGODB_DATABASE", "mommy_exe").strip() or "mommy_exe"

        raw_guild_id = os.getenv("GUILD_ID", "").strip()
        development_guild_id = int(raw_guild_id) if raw_guild_id else None

        missing: list[str] = []
        if not daddy_token:
            missing.append("DISCORD_TOKEN_DADDY (or legacy DISCORD_TOKEN)")
        if not mongodb_uri:
            missing.append("MONGODB_URI")
        if mommy_enabled and not mommy_token:
            missing.append("DISCORD_TOKEN_MOMMY")
        if missing:
            raise RuntimeError("Missing required environment variable(s): " + ", ".join(missing))

        return cls(
            daddy_discord_token=daddy_token,
            mommy_discord_token=mommy_token,
            mommy_enabled=mommy_enabled,
            mongodb_uri=mongodb_uri,
            mongodb_database=mongodb_database,
            mommy_mongodb_database=mommy_mongodb_database,
            development_guild_id=development_guild_id,
        )
