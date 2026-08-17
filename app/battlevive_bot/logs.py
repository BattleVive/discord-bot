#!/usr/bin/env python3
import logging
import os
import sys


formatter = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")


def _configure_stdout_logger(name: str, level_name: str) -> logging.Logger:
    configured_logger = logging.getLogger(name)
    configured_logger.setLevel(
        getattr(logging, os.getenv(level_name, "INFO").upper(), logging.INFO)
    )
    configured_logger.propagate = False

    for handler in tuple(configured_logger.handlers):
        if getattr(handler, "_battlevive_stdout_handler", False):
            configured_logger.removeHandler(handler)
            handler.close()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler._battlevive_stdout_handler = True  # type: ignore[attr-defined]
    configured_logger.addHandler(handler)
    return configured_logger


logger = _configure_stdout_logger("bot", "LOG_LEVEL")
discord_logger = _configure_stdout_logger("discord", "DISCORD_LOG_LEVEL")
discord_logger.setLevel(
    getattr(logging, os.getenv("DISCORD_LOG_LEVEL", "ERROR").upper(), logging.ERROR)
)
