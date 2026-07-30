#!/usr/bin/env python3
import logging
from logging.handlers import RotatingFileHandler
import os
import sys

from .settings import LOG_DIR


LOG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
os.chmod(LOG_DIR, 0o700)

formatter = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")

LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3


class PrivateRotatingFileHandler(RotatingFileHandler):
    def _open(self):
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
        flags |= getattr(os, "O_CLOEXEC", 0)
        file_descriptor = os.open(self.baseFilename, flags, 0o600)
        os.fchmod(file_descriptor, 0o600)
        return os.fdopen(
            file_descriptor,
            self.mode,
            encoding=self.encoding,
            errors=self.errors,
        )


def _private_rotating_handler(filename: str) -> PrivateRotatingFileHandler:
    path = LOG_DIR / filename
    handler = PrivateRotatingFileHandler(
        filename=path,
        encoding="utf-8",
        mode="a",
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
    )
    handler.setFormatter(formatter)
    return handler


bot_handler = _private_rotating_handler("bot.log")
discord_handler = _private_rotating_handler("discord.log")

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
