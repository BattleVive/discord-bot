from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from dotenv import dotenv_values
import pytest

from battlevive_bot.battlevive.tokens import TokenPair
from battlevive_bot.battlevive.tokens import TokenStore
from utils import env_gen


def auth_state(access_token: str, refresh_token: str) -> dict[str, object]:
    return {
        "origins": [
            {
                "origin": "https://battlevive.com",
                "localStorage": [
                    {
                        "name": "sb-usfuamngimwsnnfemhsl-auth-token",
                        "value": json.dumps(
                            {
                                "access_token": access_token,
                                "refresh_token": refresh_token,
                            }
                        ),
                    }
                ],
            }
        ]
    }


def test_main_writes_the_same_pair_to_bootstrap_and_runtime_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    utils_env = tmp_path / "utils.env"
    root_env = tmp_path / "root.env"
    auth_file = tmp_path / "state.json"
    token_file = tmp_path / "data/tokens.json"
    utils_env.write_text(
        "DISCORD_TOKEN=fake-discord\nSUPABASE_URL=https://supabase.test\n",
        encoding="utf-8",
    )
    auth_file.write_text(
        json.dumps(auth_state("fresh-access", "fresh-refresh")),
        encoding="utf-8",
    )

    monkeypatch.setattr(env_gen, "UTILS_ENV", utils_env)
    monkeypatch.setattr(env_gen, "ROOT_ENV", root_env)
    monkeypatch.setattr(env_gen, "AUTH_FILE", auth_file)
    monkeypatch.setattr(env_gen, "TOKEN_FILE", token_file)
    monkeypatch.setattr(env_gen, "login_discord", lambda: None)

    env_gen.main()

    root_values = dotenv_values(root_env)
    runtime_tokens = TokenStore(token_file).load()
    assert root_values["BOOTSTRAP_JWT"] == runtime_tokens.access_token
    assert (
        root_values["BOOTSTRAP_REFRESH_TOKEN"]
        == runtime_tokens.refresh_token
    )
    assert runtime_tokens == TokenPair("fresh-access", "fresh-refresh")


def test_login_discord_restricts_auth_state_permissions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    auth_file = tmp_path / "playwright/.auth/state.json"
    visited: list[str] = []

    class Context:
        def new_page(self) -> SimpleNamespace:
            return SimpleNamespace(goto=visited.append)

        def storage_state(self, *, path: str) -> None:
            Path(path).write_text("{}", encoding="utf-8")
            Path(path).chmod(0o644)

    class Browser:
        def new_context(self, **kwargs: object) -> Context:
            return Context()

        def close(self) -> None:
            return None

    class Playwright:
        chromium = SimpleNamespace(
            launch=lambda **kwargs: Browser(),
        )

    class PlaywrightContext:
        def __enter__(self) -> Playwright:
            return Playwright()

        def __exit__(
            self,
            exc_type: object,
            exc: object,
            traceback: object,
        ) -> None:
            return None

    monkeypatch.setattr(env_gen, "AUTH_FILE", auth_file)
    monkeypatch.setattr(env_gen, "SUPABASE_URL", "https://supabase.test")
    monkeypatch.setattr(
        env_gen,
        "sync_playwright",
        lambda: PlaywrightContext(),
    )
    monkeypatch.setattr("builtins.input", lambda prompt: "")

    env_gen.login_discord()

    assert visited == [
        "https://supabase.test/auth/v1/authorize?provider=discord&"
        "redirect_to=https%3A%2F%2Fbattlevive.com%2Fauth%2Fcallback"
    ]
    assert auth_file.stat().st_mode & 0o777 == 0o600
