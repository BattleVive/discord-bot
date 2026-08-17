from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from battlevive_bot.battlevive import tokens as tokens_module
from battlevive_bot.battlevive.tokens import TokenPair
from battlevive_bot.battlevive.tokens import TokenStore
from battlevive_bot.battlevive.tokens import SSMTokenStore
from battlevive_bot.battlevive.tokens import build_token_store


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


def test_load_corrects_existing_token_permissions(tmp_path: Path) -> None:
    token_path = tmp_path / "tokens.json"
    token_path.write_text(
        '{"access_token":"access","refresh_token":"refresh"}',
        encoding="utf-8",
    )
    os.chmod(token_path, 0o644)

    assert TokenStore(token_path).load() == TokenPair("access", "refresh")
    assert token_path.stat().st_mode & 0o777 == 0o600


def test_load_rejects_symlink_directory_and_fifo_paths(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text(
        '{"access_token":"access","refresh_token":"refresh"}',
        encoding="utf-8",
    )
    symlink = tmp_path / "symlink.json"
    symlink.symlink_to(target)
    directory = tmp_path / "directory"
    directory.mkdir()
    fifo = tmp_path / "tokens.fifo"
    os.mkfifo(fifo)

    assert TokenStore(symlink).load() is None
    assert TokenStore(directory).load() is None
    assert TokenStore(fifo).load() is None


def test_save_refuses_unsafe_existing_path(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text("leave-me", encoding="utf-8")
    symlink = tmp_path / "tokens.json"
    symlink.symlink_to(target)

    with pytest.raises(OSError, match="unsafe token path"):
        TokenStore(symlink).save(TokenPair("access", "refresh"))

    assert target.read_text(encoding="utf-8") == "leave-me"


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


class FakeSSMClient:
    def __init__(self, *, value: str | None = None, failures: int = 0) -> None:
        self.value = value
        self.failures = failures
        self.put_calls: list[dict[str, object]] = []

    def get_parameter(self, **kwargs: object) -> dict[str, object]:
        assert kwargs == {"Name": "/tokens", "WithDecryption": True}
        if self.value is None:
            raise RuntimeError("credential provider failure with private detail")
        return {"Parameter": {"Type": "SecureString", "Value": self.value}}

    def put_parameter(self, **kwargs: object) -> dict[str, object]:
        self.put_calls.append(kwargs)
        if len(self.put_calls) <= self.failures:
            raise RuntimeError("service failure with private detail")
        return {"Version": 1}


def test_ssm_token_store_loads_valid_secure_string() -> None:
    client = FakeSSMClient(
        value='{"access_token":"access","refresh_token":"refresh"}'
    )

    assert SSMTokenStore("/tokens", client=client).load() == TokenPair(
        "access", "refresh"
    )


@pytest.mark.parametrize(
    "value",
    [
        "not-json",
        "{}",
        '[]',
        '{"access_token":"access","refresh_token":""}',
    ],
)
def test_ssm_token_store_rejects_malformed_values(value: str) -> None:
    with pytest.raises(ValueError, match="invalid Battlevive token state") as caught:
        SSMTokenStore("/tokens", client=FakeSSMClient(value=value)).load()

    assert value not in str(caught.value)


def test_ssm_token_store_rejects_malformed_response_shape() -> None:
    class Client:
        def get_parameter(self, **kwargs: object) -> dict[str, object]:
            return {"Parameter": {"Type": "SecureString"}}

    with pytest.raises(ValueError, match="invalid Battlevive token state"):
        SSMTokenStore("/tokens", client=Client()).load()


def test_ssm_token_store_retries_writes_three_times() -> None:
    client = FakeSSMClient(value="{}", failures=2)
    store = SSMTokenStore("/tokens", client=client)

    store.save(TokenPair("access", "refresh"))

    assert len(client.put_calls) == 3
    assert all(call["Type"] == "SecureString" for call in client.put_calls)
    assert all(call["Overwrite"] is True for call in client.put_calls)


def test_ssm_token_store_final_write_failure_is_secret_free() -> None:
    client = FakeSSMClient(value="{}", failures=3)

    with pytest.raises(OSError, match="three attempts") as caught:
        SSMTokenStore("/tokens", client=client).save(TokenPair("access", "refresh"))

    assert len(client.put_calls) == 3
    assert "access" not in str(caught.value)
    assert "refresh" not in str(caught.value)


def test_token_store_factory_preserves_file_default(tmp_path: Path) -> None:
    store = build_token_store(kind="file", path=tmp_path / "tokens.json")

    assert isinstance(store, TokenStore)


def test_token_store_factory_builds_ssm_store() -> None:
    client = FakeSSMClient(value='{"access_token":"a","refresh_token":"r"}')
    store = build_token_store(
        kind="ssm",
        path="unused",
        parameter_name="/tokens",
        region_name="eu-north-1",
        ssm_client=client,
    )

    assert isinstance(store, SSMTokenStore)
    assert store.load() == TokenPair("a", "r")
