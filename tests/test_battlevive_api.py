from __future__ import annotations

from types import SimpleNamespace

import pytest

from battlevive_bot import battlevive_api
from tests.factories import lobby_payload
from tests.factories import season_rating_payload
from tests.factories import user_payload


class FakeRequestsResponse:
    def __init__(self, payload: dict[str, str]) -> None:
        self._payload = payload
        self.headers = {"content-type": "application/json"}
        self.status_code = 200
        self.text = "{}"
        self.raise_called = False

    def raise_for_status(self) -> None:
        self.raise_called = True

    def json(self) -> dict[str, str]:
        return self._payload


def test_revalidate_posts_refresh_token_and_returns_new_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    response = FakeRequestsResponse(
        {
            "refresh_token": "new-refresh",
            "access_token": "new-access",
        }
    )
    calls: list[dict[str, object]] = []

    def fake_post(
        url: str,
        headers: dict[str, str],
        json: dict[str, str | None],
        timeout: int,
    ) -> FakeRequestsResponse:
        calls.append(
            {
                "url": url,
                "headers": headers,
                "json": json,
                "timeout": timeout,
            }
        )
        return response

    monkeypatch.setattr(battlevive_api, "SUPABASE_URL", "https://supabase.test")
    monkeypatch.setattr(battlevive_api, "SUPABASE_API_KEY", "fake-api-key")
    monkeypatch.setattr(battlevive_api.requests, "post", fake_post)

    refresh_token, access_token = battlevive_api.BattleviveTokenManager.revalidate("old-refresh")

    assert refresh_token == "new-refresh"
    assert access_token == "new-access"
    assert response.raise_called is True
    assert calls == [
        {
            "url": "https://supabase.test/auth/v1/token?grant_type=refresh_token",
            "headers": {
                "Content-Type": "application/json",
                "apikey": "fake-api-key",
            },
            "json": {"refresh_token": "old-refresh"},
            "timeout": 10,
        }
    ]


class FakeAiohttpResponse:
    def __init__(self, status: int, payload: object, body: str = "") -> None:
        self.status = status
        self._payload = payload
        self._body = body
        self.raise_called = False

    async def json(self) -> object:
        return self._payload

    async def text(self) -> str:
        return self._body

    def raise_for_status(self) -> None:
        self.raise_called = True
        raise battlevive_api.aiohttp.ClientResponseError(
            request_info=SimpleNamespace(real_url="https://supabase.test/rest/v1/users"),
            history=(),
            status=self.status,
            message=self._body,
        )


class FakeGetContext:
    def __init__(self, response: FakeAiohttpResponse) -> None:
        self.response = response

    async def __aenter__(self) -> FakeAiohttpResponse:
        return self.response

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


def install_fake_session(
    monkeypatch: pytest.MonkeyPatch,
    response: FakeAiohttpResponse,
) -> list[dict[str, object]]:
    requests: list[dict[str, object]] = []

    class FakeClientSession:
        async def __aenter__(self) -> "FakeClientSession":
            return self

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def get(self, url: str, headers: dict[str, str]) -> FakeGetContext:
            requests.append({"url": url, "headers": headers})
            return FakeGetContext(response)

    monkeypatch.setattr(battlevive_api.aiohttp, "ClientSession", FakeClientSession)
    return requests


@pytest.mark.parametrize(
    ("function_name", "endpoint", "payload", "expected_summary"),
    [
        ("query_users", "users", [user_payload()], ["PlayerOne"]),
        ("query_lobbies", "lobbies", [lobby_payload()], [101]),
        ("query_season_ratings", "season_ratings", [season_rating_payload()], [1999]),
        ("query_user_trophies", "user_trophies", [{"kind": "champion"}], [{"kind": "champion"}]),
    ],
)
@pytest.mark.asyncio
async def test_query_endpoints_fetch_supabase_endpoint_and_parse_response(
    monkeypatch: pytest.MonkeyPatch,
    function_name: str,
    endpoint: str,
    payload: list[dict[str, object]],
    expected_summary: list[object],
) -> None:
    monkeypatch.setattr(battlevive_api, "SUPABASE_URL", "https://supabase.test")
    monkeypatch.setattr(battlevive_api, "SUPABASE_API_KEY", "fake-api-key")
    response = FakeAiohttpResponse(status=200, payload=payload)
    requests = install_fake_session(monkeypatch, response)

    result = await getattr(battlevive_api, function_name)("jwt-token")

    if endpoint == "users":
        summary = [item.discord_username for item in result]
    elif endpoint == "lobbies":
        summary = [item.id for item in result]
    elif endpoint == "season_ratings":
        summary = [item.mmr for item in result]
    else:
        summary = [item.json() for item in result]

    assert summary == expected_summary
    assert requests == [
        {
            "url": f"https://supabase.test/rest/v1/{endpoint}",
            "headers": {
                "Authorization": "Bearer jwt-token",
                "apikey": "fake-api-key",
                "Content-Type": "application/json",
            },
        }
    ]


@pytest.mark.asyncio
async def test_query_users_raises_for_non_success_status(monkeypatch: pytest.MonkeyPatch) -> None:
    response = FakeAiohttpResponse(status=401, payload=[], body="unauthorized")
    install_fake_session(monkeypatch, response)

    with pytest.raises(battlevive_api.aiohttp.ClientResponseError):
        await battlevive_api.query_users("bad-token")

    assert response.raise_called is True
