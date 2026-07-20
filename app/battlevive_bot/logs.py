#!/usr/bin/env python3
import logging
import os
import sys

from .settings import LOG_DIR


LOG_DIR.mkdir(exist_ok=True)

formatter = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")

bot_handler = logging.FileHandler(
    filename=LOG_DIR / "bot.log",
    encoding="utf-8",
    mode="a",
)
bot_handler.setFormatter(formatter)

discord_handler = logging.FileHandler(
    filename=LOG_DIR / "discord.log",
    encoding="utf-8",
    mode="a",
)
discord_handler.setFormatter(formatter)

bot_console_handler = logging.StreamHandler(sys.stdout)
bot_console_handler.setFormatter(formatter)

discord_console_handler = logging.StreamHandler(sys.stdout)
discord_console_handler.setFormatter(formatter)

bot_log_level = getattr(
    logging,
    os.getenv("LOG_LEVEL", "INFO").upper(),
    logging.INFO,
)
discord_log_level = getattr(
    logging,
    os.getenv("DISCORD_LOG_LEVEL", "ERROR").upper(),
    logging.ERROR,
)

logger = logging.getLogger("bot")
logger.setLevel(bot_log_level)
logger.addHandler(bot_handler)
logger.addHandler(bot_console_handler)

discord_logger = logging.getLogger("discord")
discord_logger.setLevel(discord_log_level)
discord_logger.addHandler(discord_handler)
discord_logger.addHandler(discord_console_handler)
