import logging
import os
import threading

from flask import Flask

from core.bot import DaddysBeltBot
from core.config import Settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

web_app = Flask(__name__)
discord_bot: DaddysBeltBot | None = None


@web_app.get("/")
def home():
    return "Daddy's Belt is awake and reviewing the paperwork.", 200


@web_app.get("/health")
def health():
    ready = bool(discord_bot and discord_bot.is_ready())
    database = bool(discord_bot and discord_bot.database.connected)

    payload = {
        "status": "ready" if ready and database else "starting",
        "discord_ready": ready,
        "database_connected": database,
        "version": "3.1.0-phase4a",
    }
    return payload, 200 if ready and database else 503


def run_web_server() -> None:
    port = int(os.getenv("PORT", "10000"))
    web_app.run(host="0.0.0.0", port=port, use_reloader=False)


def main() -> None:
    global discord_bot

    settings = Settings.from_environment()
    discord_bot = DaddysBeltBot(settings)

    threading.Thread(target=run_web_server, daemon=True).start()
    discord_bot.run(settings.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
