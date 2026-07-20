from __future__ import annotations

import os
from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parents[1]
APP_DIR = ROOT_DIR / "app"

sys.path.insert(0, str(APP_DIR))

os.environ.setdefault("PYTHON_DOTENV_DISABLED", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://battlevive_test:battlevive_test@localhost:5432/battlevive_test")
os.environ.setdefault("DISCORD_TOKEN", "test-discord-token")
os.environ.setdefault("BATTLEVIVE_URL", "https://battlevive.test")
os.environ.setdefault("BOOTSTRAP_JWT", "test-jwt")
os.environ.setdefault("BOOTSTRAP_REFRESH_TOKEN", "test-refresh")
os.environ.setdefault("SUPABASE_API_KEY", "test-supabase-key")
os.environ.setdefault("SUPABASE_URL", "https://supabase.test")

try:
    import dotenv
except ModuleNotFoundError:
    dotenv = None

if dotenv is not None:
    dotenv.load_dotenv = lambda *args, **kwargs: False
