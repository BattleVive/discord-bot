#!/usr/bin/env python3
# pyrefly: ignore [missing-import]
from playwright.sync_api import sync_playwright
import os
import sys
import json
# pyrefly: ignore [missing-import]
from dotenv import load_dotenv
# pyrefly: ignore [missing-import]
from dotenv import set_key
from pathlib import Path
load_dotenv()
AUTH_FILE = "playwright/.auth/state.json"
SUPABASE_URL = os.getenv("SUPABASE_URL")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
SUPABASE_API_KEY = os.getenv("SUPABASE_API_KEY")
BATTLEVIVE_URL =os.getenv("BATTLEVIVE_URL") 

def login_discord():
    with sync_playwright() as p:
        #path=Path(AUTH_FILE) 
        #path.touch(mode=0o600, exist_ok=False)
        browser = p.chromium.launch(headless=False)
        if os.path.exists(AUTH_FILE):
                context = browser.new_context(storage_state=AUTH_FILE)
        else:

            context = browser.new_context()
        page = context.new_page()

        page.goto(f"{SUPABASE_URL}/auth/v1/authorize?provider=discord&redirect_to=https%3A%2F%2Fbattlevive.com%2Fauth%2Fcallback")

        # Click your site"s "Login with Discord" button, then log in
        # and approve the OAuth consent screen manually in the opened window.
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
                token = auth_data["access_token"]
    return token

def get_refresh_token():
    with open(AUTH_FILE, "r") as f:
        state = json.load(f)

    for origin in state.get("origins", []):
        if origin["origin"] != "https://battlevive.com":
            continue
        for item in origin.get("localStorage", []):
            if item["name"] == "sb-usfuamngimwsnnfemhsl-auth-token":
                auth_data = json.loads(item["value"])
                token = auth_data["refresh_token"]
    return token

def main():
    login_discord()
    bootstrapJWT= get_JWT_token()
    bootstrap_refresh_token=get_refresh_token()
    env_file_path = Path("../.env")
    env_file_path.touch(mode=0o600, exist_ok=True)
    set_key(dotenv_path=env_file_path, key_to_set="SUPABASE_URL", value_to_set=SUPABASE_URL)
    set_key(dotenv_path=env_file_path, key_to_set="BATTLEVIVE_URL", value_to_set=BATTLEVIVE_URL)
    set_key(dotenv_path=env_file_path, key_to_set="DISCORD_TOKEN", value_to_set=DISCORD_TOKEN)
    set_key(dotenv_path=env_file_path, key_to_set="SUPABASE_API_KEY", value_to_set=SUPABASE_API_KEY)
    set_key(dotenv_path=env_file_path, key_to_set="BOOTSTRAP_JWT", value_to_set=bootstrapJWT)

if __name__ == "__main__":
    main()