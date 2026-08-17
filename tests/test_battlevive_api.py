from __future__ import annotations

import asyncio
from collections.abc import Mapping
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import aiohttp
import pytest

from battlevive_bot.battlevive.client import BattleviveClient
from battlevive_bot.battlevive.supabase import SupabaseTransport
from battlevive_bot.battlevive.tokens import TokenPair
from battlevive_bot.battlevive.tokens import TokenStore
from tests.factories import lobby_payload
from tests.factories import lobby_captain_payload
from tests.factories import lobby_draft_action_payload
from tests.factories import match_result_confirmation_payload
from tests.factories import season_rating_payload
from tests.factories import user_payload


def response_error(status: int) -> aiohttp.ClientResponseError:
    return aiohttp.ClientResponseError(
        request_info=SimpleNamespace(real_url="https://supabase.test/rest/v1/users"),
        history=(),
        status=status,
        message="request failed",
    )


class FakeTransport:
    def __init__(
        self,
        *,
        payloads: dict[str, object] | None = None,
        refreshed_tokens: TokenPair | None = None,
    ) -> None:
        self.payloads = payloads or {}
        self.refreshed_tokens = refreshed_tokens or TokenPair(
            "new-access",
            "new-refresh",
        )
        self.get_calls: list[tuple[str, str]] = []
        self.get_params_calls: list[
            tuple[
                str,
                str,
                Mapping[str, str] | Sequence[tuple[str, str]] | None,
            ]
        ] = []
        self.refresh_calls: list[str] = []
        self.closed = False

    async def get(
        self,
        endpoint: str,
        access_token: str,
        *,
        params: Mapping[str, str] | Sequence[tuple[str, str]] | None = None,
    ) -> object:
        self.get_calls.append((endpoint, access_token))
        self.get_params_calls.append((endpoint, access_token, params))
        payload = self.payloads[endpoint]
        if isinstance(payload, BaseException):
            raise payload
        return payload

    async def refresh(self, refresh_token: str) -> TokenPair:
        self.refresh_calls.append(refresh_token)
        if isinstance(self.refreshed_tokens, BaseException):
            raise self.refreshed_tokens
        return self.refreshed_tokens

    async def close(self) -> None:
        self.closed = True


def make_client(
    tmp_path: Path,
    transport: FakeTransport,
    *,
    token_path: Path | None = None,
) -> BattleviveClient:
    return BattleviveClient(
        bootstrap_access_token="bootstrap-access",
        bootstrap_refresh_token="bootstrap-refresh",
        token_path=token_path or tmp_path / "tokens.json",
        supabase_url="https://supabase.test",
        supabase_api_key="api-key",
        transport=transport,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "endpoint", "payload", "summary"),
    [
        ("get_users", "users", [user_payload()], ["PlayerOne"]),
        ("get_lobbies", "lobbies", [lobby_payload()], [101]),
        (
            "get_season_ratings",
            "season_ratings",
            [season_rating_payload()],
            [1999],
        ),
        (
            "get_user_trophies",
            "user_trophies",
            [{"kind": "champion"}],
            [{"kind": "champion"}],
        ),
    ],
)
async def test_client_parses_every_endpoint(
    tmp_path: Path,
    method_name: str,
    endpoint: str,
    payload: object,
    summary: list[object],
) -> None:
    payloads = {endpoint: payload}
    if endpoint == "lobbies":
        payloads["lobby_slots"] = [
            {"lobby_id": 101, "user_id": user_payload()["id"], "slot": "team_one"}
        ]
    transport = FakeTransport(payloads=payloads)
    client = make_client(tmp_path, transport)

    result = await getattr(client, method_name)()

    if endpoint == "users":
        actual = [item.discord_username for item in result]
    elif endpoint == "lobbies":
        actual = [item.id for item in result]
    elif endpoint == "season_ratings":
        actual = [item.mmr for item in result]
    else:
        actual = [item.json() for item in result]
    assert actual == summary
    expected_calls = [(endpoint, "bootstrap-access")]
    if endpoint == "lobbies":
        expected_calls.append(("lobby_slots", "bootstrap-access"))
    assert transport.get_calls == expected_calls


@pytest.mark.asyncio
async def test_lobbies_hydrate_ordered_rosters_from_minimal_slot_query(
    tmp_path: Path,
) -> None:
    transport = FakeTransport(
        payloads={
            "lobbies": [
                lobby_payload(team_one_roster=[], team_two_roster=[]),
            ],
            "lobby_slots": [
                {"lobby_id": 101, "user_id": "user-two", "slot": "team_one"},
                {"lobby_id": 101, "user_id": "user-one", "slot": "team_one"},
                {"lobby_id": 101, "user_id": "user-three", "slot": "team_two"},
                {"lobby_id": 999, "user_id": "orphan", "slot": "team_one"},
            ],
        }
    )
    client = make_client(tmp_path, transport)

    result = await client.get_lobbies()

    assert result[0].team_one_roster == ["user-two", "user-one"]
    assert result[0].team_two_roster == ["user-three"]
    assert transport.get_params_calls == [
        ("lobbies", "bootstrap-access", None),
        (
            "lobby_slots",
            "bootstrap-access",
            (
                ("select", "lobby_id,user_id,slot"),
                ("order", "joined_at.asc,id.asc"),
            ),
        ),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "endpoint", "payload", "params"),
    [
        (
            "get_lobby_draft_actions",
            "lobby_draft_actions",
            [lobby_draft_action_payload()],
            (
                (
                    "select",
                    "id,lobby_id,step,team_slot,action,champion,created_at",
                ),
                ("lobby_id", "eq.101"),
                ("order", "step.asc"),
            ),
        ),
        (
            "get_lobby_captains",
            "lobby_slots",
            [lobby_captain_payload()],
            (
                ("select", "user_id,slot"),
                ("lobby_id", "eq.101"),
                ("is_captain", "eq.true"),
            ),
        ),
        (
            "get_match_result_confirmations",
            "match_result_confirmations",
            [match_result_confirmation_payload()],
            (
                (
                    "select",
                    "id,lobby_id,user_id,selected_winner,created_at,captain_slot",
                ),
                ("lobby_id", "eq.101"),
                ("order", "created_at.asc"),
            ),
        ),
    ],
)
async def test_active_lobby_endpoints_use_exact_minimal_queries(
    tmp_path: Path,
    method_name: str,
    endpoint: str,
    payload: object,
    params: tuple[tuple[str, str], ...],
) -> None:
    transport = FakeTransport(payloads={endpoint: payload})
    client = make_client(tmp_path, transport)

    result = await getattr(client, method_name)(101)

    assert len(result) == 1
    assert transport.get_params_calls == [
        (endpoint, "bootstrap-access", params)
    ]


@pytest.mark.asyncio
async def test_persisted_state_takes_precedence_over_bootstrap(
    tmp_path: Path,
) -> None:
    token_path = tmp_path / "tokens.json"
    TokenStore(token_path).save(TokenPair("persisted-access", "persisted-refresh"))
    transport = FakeTransport(payloads={"users": [user_payload()]})
    client = make_client(tmp_path, transport, token_path=token_path)

    await client.get_users()
    await client.refresh_credentials()

    assert transport.get_calls == [("users", "persisted-access")]
    assert transport.refresh_calls == ["persisted-refresh"]


@pytest.mark.asyncio
@pytest.mark.parametrize("contents", [None, "{bad-json", '{"access_token": "only"}'])
async def test_missing_or_invalid_state_falls_back_to_bootstrap(
    tmp_path: Path,
    contents: str | None,
) -> None:
    token_path = tmp_path / "tokens.json"
    if contents is not None:
        token_path.write_text(contents, encoding="utf-8")
    transport = FakeTransport(payloads={"users": [user_payload()]})
    client = make_client(tmp_path, transport, token_path=token_path)

    await client.get_users()

    assert transport.get_calls == [("users", "bootstrap-access")]


@pytest.mark.asyncio
async def test_successful_refresh_updates_memory_and_persists_immediately(
    tmp_path: Path,
) -> None:
    token_path = tmp_path / "tokens.json"
    transport = FakeTransport(
        payloads={"users": [user_payload()]},
        refreshed_tokens=TokenPair("rotated-access", "rotated-refresh"),
    )
    client = make_client(tmp_path, transport, token_path=token_path)

    await client.refresh_credentials()
    await client.get_users()

    assert TokenStore(token_path).load() == TokenPair(
        "rotated-access",
        "rotated-refresh",
    )
    assert transport.get_calls == [("users", "rotated-access")]


@pytest.mark.asyncio
async def test_failed_refresh_does_not_mutate_credentials(tmp_path: Path) -> None:
    transport = FakeTransport(payloads={"users": [user_payload()]})

    async def fail_refresh(refresh_token: str) -> TokenPair:
        assert refresh_token == "bootstrap-refresh"
        raise RuntimeError("upstream unavailable")

    transport.refresh = fail_refresh
    client = make_client(tmp_path, transport)

    with pytest.raises(RuntimeError, match="upstream unavailable"):
        await client.refresh_credentials()
    await client.get_users()

    assert transport.get_calls == [("users", "bootstrap-access")]


@pytest.mark.asyncio
async def test_persistence_failure_keeps_rotated_credentials_in_memory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    transport = FakeTransport(
        payloads={"users": [user_payload()]},
        refreshed_tokens=TokenPair("rotated-access", "rotated-refresh"),
    )
    client = make_client(tmp_path, transport)

    def fail_save(tokens: TokenPair) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(client._token_store, "save", fail_save)

    await client.refresh_credentials()
    await client.get_users()

    assert transport.get_calls == [("users", "rotated-access")]
    assert client.persistence_degraded is True


@pytest.mark.asyncio
async def test_successful_persistence_recovers_degraded_state(tmp_path: Path) -> None:
    transport = FakeTransport(
        payloads={"users": [user_payload()]},
        refreshed_tokens=TokenPair("rotated-access", "rotated-refresh"),
    )
    client = make_client(tmp_path, transport)
    saves = 0

    def save(tokens: TokenPair) -> None:
        nonlocal saves
        saves += 1
        if saves == 1:
            raise OSError("temporarily unavailable")

    client._token_store.save = save

    await client.refresh_credentials()
    assert client.persistence_degraded is True
    await client.refresh_credentials()
    assert client.persistence_degraded is False


class RetryTransport(FakeTransport):
    def __init__(self, retry_error: BaseException | None = None) -> None:
        super().__init__(payloads={})
        self.retry_error = retry_error

    async def get(self, endpoint: str, access_token: str) -> object:
        self.get_calls.append((endpoint, access_token))
        if access_token == "bootstrap-access":
            raise response_error(401)
        if self.retry_error is not None:
            raise self.retry_error
        return [user_payload()]


class ActiveLobbyRetryTransport(FakeTransport):
    async def get(
        self,
        endpoint: str,
        access_token: str,
        *,
        params: Mapping[str, str] | Sequence[tuple[str, str]] | None = None,
    ) -> object:
        self.get_calls.append((endpoint, access_token))
        self.get_params_calls.append((endpoint, access_token, params))
        if access_token == "bootstrap-access":
            raise response_error(401)
        return [lobby_draft_action_payload()]


@pytest.mark.asyncio
async def test_401_refreshes_and_retries_once(tmp_path: Path) -> None:
    transport = RetryTransport()
    client = make_client(tmp_path, transport)

    users = await client.get_users()

    assert [user.discord_username for user in users] == ["PlayerOne"]
    assert transport.refresh_calls == ["bootstrap-refresh"]
    assert transport.get_calls == [
        ("users", "bootstrap-access"),
        ("users", "new-access"),
    ]


@pytest.mark.asyncio
async def test_active_lobby_query_keeps_exact_params_after_401_retry(
    tmp_path: Path,
) -> None:
    transport = ActiveLobbyRetryTransport()
    client = make_client(tmp_path, transport)

    drafts = await client.get_lobby_draft_actions(101)

    assert [draft.champion for draft in drafts] == ["Lucie"]
    assert transport.refresh_calls == ["bootstrap-refresh"]
    assert len(transport.get_params_calls) == 2
    assert transport.get_params_calls[0][2] == transport.get_params_calls[1][2]


@pytest.mark.asyncio
async def test_retry_failure_is_propagated_without_another_refresh(
    tmp_path: Path,
) -> None:
    retry_error = response_error(401)
    transport = RetryTransport(retry_error=retry_error)
    client = make_client(tmp_path, transport)

    with pytest.raises(aiohttp.ClientResponseError) as caught:
        await client.get_users()

    assert caught.value is retry_error
    assert transport.refresh_calls == ["bootstrap-refresh"]
    assert len(transport.get_calls) == 2


@pytest.mark.asyncio
async def test_non_auth_error_propagates_without_refresh(tmp_path: Path) -> None:
    error = response_error(500)
    transport = FakeTransport(payloads={"users": error})
    client = make_client(tmp_path, transport)

    with pytest.raises(aiohttp.ClientResponseError) as caught:
        await client.get_users()

    assert caught.value is error
    assert transport.refresh_calls == []


class ConcurrentRetryTransport(FakeTransport):
    def __init__(self) -> None:
        super().__init__(payloads={})
        self._old_requests = 0
        self._both_old_requests_started = asyncio.Event()

    async def get(self, endpoint: str, access_token: str) -> object:
        self.get_calls.append((endpoint, access_token))
        if access_token == "bootstrap-access":
            self._old_requests += 1
            if self._old_requests == 2:
                self._both_old_requests_started.set()
            await self._both_old_requests_started.wait()
            raise response_error(401)
        return [user_payload()]


@pytest.mark.asyncio
async def test_concurrent_401_responses_share_one_refresh(tmp_path: Path) -> None:
    transport = ConcurrentRetryTransport()
    client = make_client(tmp_path, transport)

    first, second = await asyncio.gather(
        client.get_users(),
        client.get_users(),
    )

    assert len(first) == len(second) == 1
    assert transport.refresh_calls == ["bootstrap-refresh"]
    assert transport.get_calls.count(("users", "bootstrap-access")) == 2
    assert transport.get_calls.count(("users", "new-access")) == 2


class FakeResponse:
    def __init__(self, status: int, payload: object, body: str = "") -> None:
        self.status = status
        self.payload = payload
        self.body = body
        self.raise_called = False

    async def json(self) -> object:
        return self.payload

    async def text(self) -> str:
        return self.body

    def raise_for_status(self) -> None:
        self.raise_called = True
        raise response_error(self.status)


class ResponseContext:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response

    async def __aenter__(self) -> FakeResponse:
        return self.response

    async def __aexit__(
        self,
        exc_type: object,
        exc: object,
        traceback: object,
    ) -> None:
        return None


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, object]]] = []
        self.closed = False

    def get(self, url: str, **kwargs: object) -> ResponseContext:
        self.calls.append(("GET", url, kwargs))
        return ResponseContext(self.responses.pop(0))

    def post(self, url: str, **kwargs: object) -> ResponseContext:
        self.calls.append(("POST", url, kwargs))
        return ResponseContext(self.responses.pop(0))

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint",
    ["users", "lobbies", "season_ratings", "user_trophies"],
)
async def test_transport_builds_rest_urls_and_headers(endpoint: str) -> None:
    session = FakeSession([FakeResponse(200, [])])
    transport = SupabaseTransport(
        "https://supabase.test/",
        "api-key",
        session=session,
    )

    assert await transport.get(endpoint, "access-token") == []

    method, url, kwargs = session.calls[0]
    assert method == "GET"
    assert url == f"https://supabase.test/rest/v1/{endpoint}"
    assert kwargs["headers"] == {
        "Authorization": "Bearer access-token",
        "apikey": "api-key",
        "Content-Type": "application/json",
    }
    assert isinstance(kwargs["timeout"], aiohttp.ClientTimeout)
    assert kwargs["timeout"].total == 15
    assert kwargs["timeout"].connect == 5


@pytest.mark.asyncio
async def test_transport_forwards_query_values_for_safe_encoding() -> None:
    session = FakeSession([FakeResponse(200, [])])
    transport = SupabaseTransport(
        "https://supabase.test",
        "api-key",
        session=session,
    )
    params = (
        ("select", "id,lobby_id,champion"),
        ("lobby_id", "eq.165"),
        ("order", "created_at.asc"),
    )

    await transport.get(
        "lobby_draft_actions",
        "access-token",
        params=params,
    )

    assert session.calls[0][2]["params"] == params


@pytest.mark.asyncio
async def test_transport_refresh_posts_token_and_parses_pair() -> None:
    session = FakeSession(
        [
            FakeResponse(
                200,
                {
                    "access_token": "new-access",
                    "refresh_token": "new-refresh",
                },
            )
        ]
    )
    transport = SupabaseTransport(
        "https://supabase.test",
        "api-key",
        session=session,
    )

    tokens = await transport.refresh("old-refresh")

    assert tokens == TokenPair("new-access", "new-refresh")
    method, url, kwargs = session.calls[0]
    assert method == "POST"
    assert url == "https://supabase.test/auth/v1/token?grant_type=refresh_token"
    assert kwargs["headers"] == {
        "Content-Type": "application/json",
        "apikey": "api-key",
    }
    assert kwargs["json"] == {"refresh_token": "old-refresh"}
    assert isinstance(kwargs["timeout"], aiohttp.ClientTimeout)
    assert kwargs["timeout"].total == 15


@pytest.mark.asyncio
async def test_client_closes_transport(tmp_path: Path) -> None:
    transport = FakeTransport()
    client = make_client(tmp_path, transport)

    await client.close()

    assert transport.closed is True
