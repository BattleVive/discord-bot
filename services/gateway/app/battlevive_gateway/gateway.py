"""Gateway process entrypoint.

Discord command wiring is deliberately isolated here; upstream and renderer failures
are represented as unavailable integrations rather than process failures.
"""
from __future__ import annotations

import os
from pathlib import Path
import time

import aiohttp

from .logs import logger

TEMPORARILY_UNAVAILABLE = "This feature is temporarily unavailable."


def validate_settings() -> None:
    """Validate the gateway's required runtime settings."""
    for name in ("DISCORD_TOKEN", "DATABASE_URL", "UPSTREAM_DATA_URL", "IMAGE_RENDERER_URL"):
        if not os.environ.get(name, "").strip():
            raise RuntimeError(f"Missing required setting: {name}")
    if os.environ.get("BATTLEVIVE_API_KEY"):
        raise RuntimeError("Gateway must not receive BATTLEVIVE_API_KEY")


def load_file_settings() -> None:
    """Load deployment secrets mounted as ``<SETTING>_FILE`` exactly once."""
    for name in ("DISCORD_TOKEN", "DATABASE_URL"):
        if os.environ.get(name, "").strip():
            continue
        path = os.environ.get(f"{name}_FILE", "").strip()
        if path:
            os.environ[name] = Path(path).read_text().strip()


def command_guild_id() -> int | None:
    """Parse the optional development command-guild identifier."""
    value = os.environ.get("DISCORD_COMMAND_GUILD_ID", "").strip()
    if not value:
        return None
    if not value.isdecimal() or int(value) <= 0:
        raise RuntimeError("DISCORD_COMMAND_GUILD_ID must be a positive snowflake")
    return int(value)


def main() -> None:
    """Run the gateway command-line entry point."""
    load_file_settings()
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
