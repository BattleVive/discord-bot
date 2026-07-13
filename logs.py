#!/usr/bin/env python3
import os,logging
# pyrefly: ignore [missing-import]
from dotenv import load_dotenv

load_dotenv()

os.makedirs("logs", exist_ok=True)

formatter = logging.Formatter(
    "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
)

bot_handler = logging.FileHandler(
    filename="logs/bot.log",
    encoding="utf-8",
    mode="a"
)
bot_handler.setFormatter(formatter)

discord_handler = logging.FileHandler(
    filename="logs/discord.log",
    encoding="utf-8",
    mode="a"
)
discord_handler.setFormatter(formatter)

# Read levels from .env
bot_log_level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
discord_log_level = getattr(logging, os.getenv("DISCORD_LOG_LEVEL", "ERROR").upper(), logging.ERROR)

logger = logging.getLogger("bot")
logger.setLevel(bot_log_level)
logger.addHandler(bot_handler)

discord_logger = logging.getLogger("discord")
discord_logger.setLevel(discord_log_level)
discord_logger.addHandler(discord_handler)