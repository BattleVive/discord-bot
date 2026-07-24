from pathlib import Path
import os

from dotenv import load_dotenv


load_dotenv()


# Paths
APP_DIR = Path(__file__).resolve().parents[1]
ASSETS_DIR = APP_DIR / "assets"
DATA_DIR = APP_DIR / "data"
LOG_DIR = APP_DIR / "logs"


# Environment
DATABASE_URL = os.getenv("DATABASE_URL")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_TOKEN")
BATTLEVIVE_URL = os.getenv("BATTLEVIVE_URL")
BATTLEVIVE_BOOTSTRAP_JWT = os.getenv("BOOTSTRAP_JWT")
BATTLEVIVE_BOOTSTRAP_REFRESH_TOKEN = os.getenv("BOOTSTRAP_REFRESH_TOKEN")
SUPABASE_API_KEY = os.getenv("SUPABASE_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")


# Discord
COMMAND_SYNC_GUILD_ID = 1524804820098224240
LEADERBOARD_MAX_ENTRIES = 50
