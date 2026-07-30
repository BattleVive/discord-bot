#!/usr/bin/env python3

import json
import os
import sys
from pathlib import Path
from shutil import copy2

UTILS_DIR = Path(__file__).resolve().parent
ROOT_DIR = UTILS_DIR.parent
APP_DIR = ROOT_DIR / "app"
sys.path.insert(0, str(APP_DIR))

# pyrefly: ignore [missing-import]
from playwright.sync_api import sync_playwright

# pyrefly: ignore [missing-import]
from dotenv import load_dotenv
# pyrefly: ignore [missing-import]
from dotenv import set_key

from battlevive_bot.battlevive.tokens import TokenPair
from battlevive_bot.battlevive.tokens import TokenStore


load_dotenv(UTILS_DIR / ".env")


AUTH_FILE = UTILS_DIR / "playwright/.auth/state.json"
UTILS_ENV = UTILS_DIR / ".env"
ROOT_ENV = ROOT_DIR / ".env"
TOKEN_FILE = ROOT_DIR / "app/data/battlevive_tokens.json"
SUPABASE_URL = os.getenv("SUPABASE_URL")


def login_discord() -> None:
    AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)

        if AUTH_FILE.exists():
            AUTH_FILE.chmod(0o600)
            context = browser.new_context(storage_state=str(AUTH_FILE))
        else:
            context = browser.new_context()

        page = context.new_page()

        page.goto(
            f"{SUPABASE_URL}/auth/v1/authorize?provider=discord&redirect_to=https%3A%2F%2Fbattlevive.com%2Fauth%2Fcallback"
        )

        input("Complete login in the browser, then press Enter here...")

        context.storage_state(path=str(AUTH_FILE))
        AUTH_FILE.chmod(0o600)
        browser.close()


def get_token_pair() -> TokenPair:
    if not AUTH_FILE.exists():
        raise FileNotFoundError(f"Missing Playwright auth state: {AUTH_FILE}")

    with AUTH_FILE.open("r", encoding="utf-8") as file:
        state = json.load(file)

    for origin in state.get("origins", []):
        if origin.get("origin") != "https://battlevive.com":
            continue
        for item in origin.get("localStorage", []):
            if item.get("name") == "sb-usfuamngimwsnnfemhsl-auth-token":
                auth_data = json.loads(item["value"])
                tokens = TokenPair.from_values(
                    auth_data.get("access_token"),
                    auth_data.get("refresh_token"),
                )
                if tokens is not None:
                    return tokens

    raise RuntimeError("Complete token pair not found in Playwright auth state.")


def main() -> None:
    login_discord()

    tokens = get_token_pair()

    # Always recreate .env files from utils/.env
    copy2(UTILS_ENV, ROOT_ENV)

    # Inject generated values
    set_key(ROOT_ENV, "BOOTSTRAP_JWT", tokens.access_token)
    set_key(ROOT_ENV, "BOOTSTRAP_REFRESH_TOKEN", tokens.refresh_token)
    TokenStore(TOKEN_FILE).save(tokens)


if __name__ == "__main__":
    main()
