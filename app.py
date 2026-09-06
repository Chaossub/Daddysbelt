from __future__ import annotations

import asyncio
import logging
import os
import threading

from flask import Flask

from core.bot import DaddysBeltBot
from core.config import Settings
from mommy.bot import MommyExeBot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

log = logging.getLogger("dual-bot.launcher")
web_app = Flask(__name__)
daddy_bot: DaddysBeltBot | None = None
mommy_bot: MommyExeBot | None = None


@web_app.get("/")
def home():
    return "Daddy's Belt and Mommy.exe are reviewing the household paperwork.", 200


@web_app.get("/health")
def health():
    daddy_ready = bool(daddy_bot and daddy_bot.is_ready())
    daddy_db = bool(daddy_bot and daddy_bot.database.connected)
    mommy_expected = mommy_bot is not None
    mommy_ready = bool(mommy_bot and mommy_bot.is_ready()) if mommy_expected else True
    mommy_db = bool(mommy_bot and mommy_bot.database.connected) if mommy_expected else True
    ready = daddy_ready and daddy_db and mommy_ready and mommy_db

    payload = {
        "status": "ready" if ready else "starting",
        "daddy": {"discord_ready": daddy_ready, "database_connected": daddy_db},
        "mommy": {
            "enabled": mommy_expected,
            "discord_ready": mommy_ready if mommy_expected else False,
            "database_connected": mommy_db if mommy_expected else False,
        },
        "version": "4.1.0-mommy-vc-migration",
    }
    return payload, 200 if ready else 503


def run_web_server() -> None:
    port = int(os.getenv("PORT", "10000"))
    web_app.run(host="0.0.0.0", port=port, use_reloader=False)


async def run_bots(settings: Settings) -> None:
    global daddy_bot, mommy_bot

    daddy_bot = DaddysBeltBot(settings)
    tasks = [
        asyncio.create_task(
            daddy_bot.start(settings.daddy_discord_token, reconnect=True),
            name="daddys-belt",
        )
    ]

    if settings.mommy_enabled:
        mommy_bot = MommyExeBot(
            mongodb_uri=settings.mongodb_uri,
            database_name=settings.mommy_mongodb_database,
        )
        tasks.append(
            asyncio.create_task(
                mommy_bot.start(settings.mommy_discord_token, reconnect=True),
                name="mommy-exe",
            )
        )
        log.info("Mommy.exe enabled; starting both Discord clients in one Render process.")
    else:
        log.warning("Mommy.exe disabled by ENABLE_MOMMY=false; starting Daddy only.")

    try:
        await asyncio.gather(*tasks)
    finally:
        for bot in (mommy_bot, daddy_bot):
            if bot and not bot.is_closed():
                await bot.close()


def main() -> None:
    settings = Settings.from_environment()
    threading.Thread(target=run_web_server, daemon=True).start()
    asyncio.run(run_bots(settings))


if __name__ == "__main__":
    main()
