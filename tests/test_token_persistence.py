from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from battlevive_bot.battlevive import tokens as tokens_module
from battlevive_bot.battlevive.tokens import TokenPair
from battlevive_bot.battlevive.tokens import TokenStore


def test_token_store_round_trip_is_complete_and_private(tmp_path: Path) -> None:
    token_path = tmp_path / "battlevive_tokens.json"
    store = TokenStore(token_path)
    tokens = TokenPair("access-token", "refresh-token")

    store.save(tokens)

    assert store.load() == tokens
    assert json.loads(token_path.read_text(encoding="utf-8")) == {
        "access_token": "access-token",
        "refresh_token": "refresh-token",
    }
    assert token_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "contents",
    [
        "{not-json",
        "[]",
        "{}",
        '{"access_token": "access"}',
        '{"refresh_token": "refresh"}',
        '{"access_token": "", "refresh_token": "refresh"}',
        '{"access_token": 123, "refresh_token": "refresh"}',
    ],
)
def test_token_store_rejects_corrupt_or_partial_state(
    tmp_path: Path,
    contents: str,
) -> None:
    token_path = tmp_path / "battlevive_tokens.json"
    token_path.write_text(contents, encoding="utf-8")

    assert TokenStore(token_path).load() is None


def test_token_store_returns_none_when_state_is_missing(tmp_path: Path) -> None:
    assert TokenStore(tmp_path / "missing.json").load() is None


def test_failed_replace_preserves_last_complete_pair(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    token_path = tmp_path / "battlevive_tokens.json"
    store = TokenStore(token_path)
    original = TokenPair("old-access", "old-refresh")
    replacement = TokenPair("new-access", "new-refresh")
    store.save(original)

    def fail_replace(source: Path, target: Path) -> None:
        temporary_record = json.loads(
            Path(source).read_text(encoding="utf-8")
        )
        assert temporary_record == {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
        }
        assert Path(target) == token_path
        raise OSError("read-only filesystem")

    monkeypatch.setattr(tokens_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="read-only filesystem"):
        store.save(replacement)

    assert store.load() == original
    assert list(tmp_path.glob(".*.tmp")) == []


def test_save_restricts_permissions_when_replacing_an_existing_record(
    tmp_path: Path,
) -> None:
    token_path = tmp_path / "battlevive_tokens.json"
    token_path.write_text("old", encoding="utf-8")
    os.chmod(token_path, 0o644)

    TokenStore(token_path).save(TokenPair("access", "refresh"))

    assert token_path.stat().st_mode & 0o777 == 0o600
