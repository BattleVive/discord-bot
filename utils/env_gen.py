#!/usr/bin/env python3

import json
import os
import sys
from pathlib import Path
from shutil import copy2

# pyrefly: ignore [missing-import]
from playwright.sync_api import sync_playwright

# pyrefly: ignore [missing-import]
from dotenv import load_dotenv
# pyrefly: ignore [missing-import]
from dotenv import set_key

load_dotenv()

AUTH_FILE = "playwright/.auth/state.json"

SUPABASE_URL = os.getenv("SUPABASE_URL")


def login_discord():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)

        if os.path.exists(AUTH_FILE):
            context = browser.new_context(storage_state=AUTH_FILE)
        else:
            context = browser.new_context()

        page = context.new_page()

        page.goto(
            f"{SUPABASE_URL}/auth/v1/authorize?provider=discord&redirect_to=https%3A%2F%2Fbattlevive.com%2Fauth%2Fcallback"
        )

        input("Complete login in the browser, then press Enter here...")

        context.storage_state(path=AUTH_FILE)
        browser.close()


def get_JWT_token() -> str:
    if not os.path.exists(AUTH_FILE):
        print(f"Missing: {AUTH_FILE}. Run save_state.py first.")
        sys.exit(1)

    with open(AUTH_FILE, "r") as f:
        state = json.load(f)

    for origin in state.get("origins", []):
        if origin["origin"] != "https://battlevive.com":
            continue
        for item in origin.get("localStorage", []):
            if item["name"] == "sb-usfuamngimwsnnfemhsl-auth-token":
                auth_data = json.loads(item["value"])
                return auth_data["access_token"]

    raise RuntimeError("Access token not found.")


def get_refresh_token() -> str:
    with open(AUTH_FILE, "r") as f:
        state = json.load(f)

    for origin in state.get("origins", []):
        if origin["origin"] != "https://battlevive.com":
            continue
        for item in origin.get("localStorage", []):
            if item["name"] == "sb-usfuamngimwsnnfemhsl-auth-token":
                auth_data = json.loads(item["value"])
                return auth_data["refresh_token"]

    raise RuntimeError("Refresh token not found.")


def main():
    login_discord()

    bootstrap_jwt = get_JWT_token()
    bootstrap_refresh_token = get_refresh_token()

    utils_env = Path(".env")
    root_env = Path("../.env")
    app_env = Path("../app/.env")

    # Always recreate .env files from utils/.env
    copy2(utils_env, root_env)
    copy2(utils_env, app_env)

    # Inject generated values
    for env_file in (root_env, app_env):
        set_key(env_file, "BOOTSTRAP_JWT", bootstrap_jwt)
        set_key(env_file, "BOOTSTRAP_REFRESH_TOKEN", bootstrap_refresh_token)


if __name__ == "__main__":
    main()