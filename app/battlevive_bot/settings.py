from pathlib import Path
import os
from urllib.parse import urlsplit

from dotenv import load_dotenv


load_dotenv()


class SettingsError(ValueError):
    """Raised when runtime configuration is missing or unsafe."""


def read_env_or_file(
    name: str,
    environ: os._Environ[str] | dict[str, str] = os.environ,
) -> str:
    """Read a setting directly or from NAME_FILE without exposing its value."""
    file_name = f"{name}_FILE"
    direct_is_set = name in environ
    file_is_set = file_name in environ
    if direct_is_set and file_is_set:
        raise SettingsError(f"{name} and {file_name} cannot both be configured.")
    if file_is_set:
        try:
            return Path(environ[file_name]).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as error:
            raise SettingsError(f"Unable to read {file_name}.") from error
    return environ.get(name, "").strip()


# Paths
APP_DIR = Path(__file__).resolve().parents[1]
ASSETS_DIR = APP_DIR / "assets"
DATA_DIR = APP_DIR / "data"
BATTLEVIVE_TOKEN_PATH = Path(
    os.getenv(
        "BATTLEVIVE_TOKEN_PATH",
        DATA_DIR / "battlevive_tokens.json",
    )
)


# Environment
DATABASE_URL = read_env_or_file("DATABASE_URL")
DISCORD_BOT_TOKEN = read_env_or_file("DISCORD_TOKEN")
BATTLEVIVE_URL = os.getenv("BATTLEVIVE_URL", "").strip()
BATTLEVIVE_BOOTSTRAP_JWT = read_env_or_file("BOOTSTRAP_JWT") or None
BATTLEVIVE_BOOTSTRAP_REFRESH_TOKEN = (
    read_env_or_file("BOOTSTRAP_REFRESH_TOKEN") or None
)
SUPABASE_API_KEY = read_env_or_file("SUPABASE_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
BATTLEVIVE_TOKEN_STORE = os.getenv("BATTLEVIVE_TOKEN_STORE", "file").strip().lower()
BATTLEVIVE_TOKEN_SSM_PARAMETER = os.getenv(
    "BATTLEVIVE_TOKEN_SSM_PARAMETER", ""
).strip()
AWS_REGION = os.getenv("AWS_REGION", "").strip()


# Discord
LEADERBOARD_MAX_ENTRIES = 50


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
    if BATTLEVIVE_TOKEN_STORE not in {"file", "ssm"}:
        raise SettingsError("BATTLEVIVE_TOKEN_STORE must be file or ssm.")
    if BATTLEVIVE_TOKEN_STORE == "ssm":
        missing_ssm = [
            name
            for name, value in (
                ("BATTLEVIVE_TOKEN_SSM_PARAMETER", BATTLEVIVE_TOKEN_SSM_PARAMETER),
                ("AWS_REGION", AWS_REGION),
            )
            if not value
        ]
        if missing_ssm:
            raise SettingsError(
                "Missing required setting(s) for SSM token storage: "
                + ", ".join(missing_ssm)
                + "."
            )
