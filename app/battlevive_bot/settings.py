from pathlib import Path
import os
from urllib.parse import urlsplit

from dotenv import load_dotenv


load_dotenv()


# Paths
APP_DIR = Path(__file__).resolve().parents[1]
ASSETS_DIR = APP_DIR / "assets"
DATA_DIR = APP_DIR / "data"
LOG_DIR = APP_DIR / "logs"
BATTLEVIVE_TOKEN_PATH = Path(
    os.getenv(
        "BATTLEVIVE_TOKEN_PATH",
        DATA_DIR / "battlevive_tokens.json",
    )
)


# Environment
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DISCORD_BOT_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
BATTLEVIVE_URL = os.getenv("BATTLEVIVE_URL", "").strip()
BATTLEVIVE_BOOTSTRAP_JWT = os.getenv("BOOTSTRAP_JWT")
BATTLEVIVE_BOOTSTRAP_REFRESH_TOKEN = os.getenv("BOOTSTRAP_REFRESH_TOKEN")
SUPABASE_API_KEY = os.getenv("SUPABASE_API_KEY", "").strip()
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()


# Discord
LEADERBOARD_MAX_ENTRIES = 50


class SettingsError(ValueError):
    """Raised when runtime configuration is missing or unsafe."""


def parse_command_guild_id(value: str | None) -> int | None:
    """Return a development guild snowflake, or None for global sync."""
    if value is None or not value.strip():
        return None
    normalized = value.strip()
    if not normalized.isdecimal():
        raise SettingsError(
            "DISCORD_COMMAND_GUILD_ID must be a positive Discord snowflake."
        )
    try:
        guild_id = int(normalized, 10)
    except ValueError as error:
        raise SettingsError(
            "DISCORD_COMMAND_GUILD_ID must be a positive Discord snowflake."
        ) from error
    if guild_id <= 0 or guild_id >= 2**64:
        raise SettingsError(
            "DISCORD_COMMAND_GUILD_ID must be a positive Discord snowflake."
        )
    return guild_id


DISCORD_COMMAND_GUILD_ID = parse_command_guild_id(
    os.getenv("DISCORD_COMMAND_GUILD_ID")
)


def _is_https_url(value: str) -> bool:
    parsed = urlsplit(value)
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
    )


def validate_runtime_settings() -> None:
    """Validate required settings without including secret values in errors."""
    missing = [
        name
        for name, value in (
            ("DATABASE_URL", DATABASE_URL),
            ("DISCORD_TOKEN", DISCORD_BOT_TOKEN),
            ("SUPABASE_API_KEY", SUPABASE_API_KEY),
            ("SUPABASE_URL", SUPABASE_URL),
        )
        if not value
    ]
    if missing:
        raise SettingsError(
            "Missing required setting(s): " + ", ".join(missing) + "."
        )
    if not _is_https_url(SUPABASE_URL):
        raise SettingsError("SUPABASE_URL must be an HTTPS URL without credentials.")
