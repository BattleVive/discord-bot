from __future__ import annotations

import importlib
import logging
from pathlib import Path

import pytest

from battlevive_bot import logs
from battlevive_bot.settings import SettingsError
from battlevive_bot.settings import read_env_or_file


def test_secret_setting_reads_file_value(tmp_path: Path) -> None:
    secret_file = tmp_path / "database-url"
    secret_file.write_text("postgresql://database/value\n", encoding="utf-8")

    assert read_env_or_file(
        "DATABASE_URL",
        {"DATABASE_URL_FILE": str(secret_file)},
    ) == "postgresql://database/value"


def test_secret_setting_rejects_value_and_file_conflict(tmp_path: Path) -> None:
    secret_file = tmp_path / "discord-token"
    secret_file.write_text("file-secret\n", encoding="utf-8")

    with pytest.raises(SettingsError, match="DISCORD_TOKEN.*_FILE") as caught:
        read_env_or_file(
            "DISCORD_TOKEN",
            {
                "DISCORD_TOKEN": "environment-secret",
                "DISCORD_TOKEN_FILE": str(secret_file),
            },
        )

    assert "environment-secret" not in str(caught.value)
    assert "file-secret" not in str(caught.value)


def test_secret_setting_reports_unreadable_file_without_secret_path(
    tmp_path: Path,
) -> None:
    path = tmp_path / "missing-private-value"

    with pytest.raises(SettingsError, match="DATABASE_URL_FILE") as caught:
        read_env_or_file("DATABASE_URL", {"DATABASE_URL_FILE": str(path)})

    assert str(path) not in str(caught.value)


def test_logging_uses_one_stdout_handler_per_logger_after_reload() -> None:
    importlib.reload(logs)
    importlib.reload(logs)

    for logger in (logs.logger, logs.discord_logger):
        owned = [
            handler
            for handler in logger.handlers
            if getattr(handler, "_battlevive_stdout_handler", False)
        ]
        assert len(owned) == 1
        assert type(owned[0]) is logging.StreamHandler
        assert not isinstance(owned[0], logging.FileHandler)


def test_logging_removes_and_closes_preinstalled_file_handler(
    tmp_path: Path,
) -> None:
    dedicated_logger = logging.getLogger("bot")
    file_handler = logging.FileHandler(tmp_path / "unexpected.log")
    dedicated_logger.addHandler(file_handler)

    importlib.reload(logs)

    assert file_handler not in logs.logger.handlers
    assert file_handler.stream is None
    assert len(logs.logger.handlers) == 1
    assert type(logs.logger.handlers[0]) is logging.StreamHandler
