"""Gateway process entrypoint.

Discord command wiring is deliberately isolated here; upstream and renderer failures
are represented as unavailable integrations rather than process failures.
"""
from __future__ import annotations

import os
import time

import aiohttp

from .logs import logger

TEMPORARILY_UNAVAILABLE = "This feature is temporarily unavailable."


def validate_settings() -> None:
    for name in ("DISCORD_TOKEN", "DATABASE_URL", "UPSTREAM_DATA_URL", "IMAGE_RENDERER_URL"):
        if not os.environ.get(name, "").strip():
            raise RuntimeError(f"Missing required setting: {name}")
    if os.environ.get("BATTLEVIVE_API_KEY"):
        raise RuntimeError("Gateway must not receive BATTLEVIVE_API_KEY")


def command_guild_id() -> int | None:
    value = os.environ.get("DISCORD_COMMAND_GUILD_ID", "").strip()
    if not value:
        return None
    if not value.isdecimal() or int(value) <= 0:
        raise RuntimeError("DISCORD_COMMAND_GUILD_ID must be a positive snowflake")
    return int(value)


def main() -> None:
    validate_settings()
    from .gateway_bot import create_bot
    while True:
        logger.info("starting gateway")
        bot = create_bot(
            database_url=os.environ["DATABASE_URL"],
            command_guild_id=command_guild_id(),
            upstream_data_url=os.environ["UPSTREAM_DATA_URL"],
            image_renderer_url=os.environ["IMAGE_RENDERER_URL"],
        )
        try:
            bot.run(os.environ["DISCORD_TOKEN"], log_handler=None)
            return
        except (aiohttp.ClientError, OSError):
            # Discord availability is independent from private integrations; retry
            # instead of allowing a temporary resolver/network outage to stop gateway.
            logger.warning("Discord connection interrupted; retrying in five seconds")
            time.sleep(5)


if __name__ == "__main__":
    main()
