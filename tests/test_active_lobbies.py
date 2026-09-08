from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from battlevive_gateway.active_lobbies import ActiveLobbyPublisher


def match(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "id": 197,
        "title": "Seasonal 3v3",
        "status": "open",
        "type": "seasonal",
        "size": 3,
        "region": "EU",
        "teamOne": "Team One",
        "teamTwo": "Team Two",
        "winner": None,
        "durationSeconds": None,
        "endedAt": None,
        "createdAt": "2026-09-06T14:25:10.923668+00:00",
        "url": "https://battlevive.com/matchmaking/2026/season-3/MATCH-24",
    }
    record.update(overrides)
    return record


@pytest.mark.asyncio
async def test_active_lobby_publisher_posts_live_match_details_and_disputed_recent_match() -> None:
    saved: list[tuple[object, ...]] = []
    active = match()
    disputed = match(id=198, title="Unresolved final", status="disputed", winner=None,
                     durationSeconds=90, endedAt="2026-09-06T15:00:10+00:00")

    class Upstream:
        async def get_result(self, path: str, *, require_fresh: bool) -> object:
            assert require_fresh is True
            if path == "/active-matches":
                return SimpleNamespace(data={"matches": [active]})
            if path == "/recent-matches":
                return SimpleNamespace(data={"matches": [disputed]})
            raise AssertionError(path)

    class Publications:
        async def list_for_feature(self, *_: object) -> list[dict[str, object]]:
            return []

        async def upsert(self, *args: object) -> None:
            saved.append(args)

    sent: list[object] = []

    async def send(*, embed: object) -> object:
        sent.append(embed)
        return SimpleNamespace(id=55 + len(sent))

    channel = SimpleNamespace(send=send)
    guild = SimpleNamespace(id=10, get_channel=lambda channel_id: channel if channel_id == 20 else None)
    bot = SimpleNamespace(get_guild=lambda guild_id: guild if guild_id == 10 else None)

    changed = await ActiveLobbyPublisher(bot, Upstream(), Publications()).reconcile_guild(
        {"guild_id": 10, "active_lobby_channel_id": 20}
    )

    assert changed is True
    assert len(sent) == 2
    live_embed, disputed_embed = (embed.to_dict() for embed in sent)
    assert live_embed["title"] == "Seasonal 3v3"
    assert live_embed["url"] == active["url"]
    assert live_embed["description"] == "Open · Seasonal · 3v3 · EU"
    assert {field["name"]: field["value"] for field in live_embed["fields"]} == {
        "Team One": "Team One", "Team Two": "Team Two",
    }
    assert disputed_embed["description"] == "⚠️ Disputed · Seasonal · 3v3 · EU"
    assert {field["name"]: field["value"] for field in disputed_embed["fields"]}["Duration"] == "1m 30s"
    assert [row[2] for row in saved] == ["match:197", "match:198"]


@pytest.mark.asyncio
async def test_active_lobby_publisher_edits_changed_posts_and_deletes_non_active_non_disputed_posts() -> None:
    current = match(status="drafting")
    previous = {
        "publication_key": "match:197", "channel_id": 20, "message_id": 55,
        "fingerprint": "old", "metadata": {},
    }
    obsolete = {
        "publication_key": "match:196", "channel_id": 20, "message_id": 54,
        "fingerprint": "old", "metadata": {},
    }
    deleted: list[tuple[object, ...]] = []
    edits: list[object] = []

    class Upstream:
        async def get_result(self, path: str, *, require_fresh: bool) -> object:
            return SimpleNamespace(data={"matches": [current] if path == "/active-matches" else []})

    class Publications:
        async def list_for_feature(self, *_: object) -> list[dict[str, object]]:
            return [previous, obsolete]

        async def upsert(self, *_: object) -> None:
            return None

        async def delete(self, *args: object) -> None:
            deleted.append(args)

    async def fetch_message(message_id: int) -> object:
        if message_id == 55:
            async def edit(*, embed: object) -> None:
                edits.append(embed)
            return SimpleNamespace(id=55, edit=edit)
        return SimpleNamespace(delete=AsyncMock())

    channel = SimpleNamespace(fetch_message=fetch_message)
    guild = SimpleNamespace(id=10, get_channel=lambda _: channel)
    bot = SimpleNamespace(get_guild=lambda _: guild)

    changed = await ActiveLobbyPublisher(bot, Upstream(), Publications()).reconcile_guild(
        {"guild_id": 10, "active_lobby_channel_id": 20}
    )

    assert changed is True
    assert len(edits) == 1
    assert deleted == [(10, "active-lobbies", "match:196")]


@pytest.mark.asyncio
async def test_active_lobby_publisher_moves_a_post_when_the_configured_channel_changes() -> None:
    previous = {
        "publication_key": "match:197", "channel_id": 20, "message_id": 55,
        "fingerprint": "old", "metadata": {},
    }
    old_message = SimpleNamespace(delete=AsyncMock())
    new_messages: list[object] = []

    class Upstream:
        async def get_result(self, path: str, *, require_fresh: bool) -> object:
            return SimpleNamespace(data={"matches": [match()] if path == "/active-matches" else []})

    class Publications:
        async def list_for_feature(self, *_: object) -> list[dict[str, object]]:
            return [previous]

        async def upsert(self, *_: object) -> None:
            return None

    old_channel = SimpleNamespace(fetch_message=AsyncMock(return_value=old_message))

    async def send(*, embed: object) -> object:
        new_messages.append(embed)
        return SimpleNamespace(id=56)

    new_channel = SimpleNamespace(send=send)
    guild = SimpleNamespace(id=10, get_channel=lambda channel_id: {20: old_channel, 21: new_channel}.get(channel_id))
    bot = SimpleNamespace(get_guild=lambda _: guild)

    await ActiveLobbyPublisher(bot, Upstream(), Publications()).reconcile_guild(
        {"guild_id": 10, "active_lobby_channel_id": 21}
    )

    old_message.delete.assert_awaited_once()
    assert len(new_messages) == 1


@pytest.mark.asyncio
async def test_active_lobby_publisher_bounds_upstream_text_to_discord_embed_limits() -> None:
    oversized = match(
        title="T" * 300, status="S" * 300, type="Y" * 300, region="R" * 300,
        teamOne="A" * 1_200, teamTwo="B" * 1_200, winner="W" * 1_200,
        createdAt="C" * 1_200, endedAt="E" * 1_200,
    )
    sent: list[object] = []

    class Upstream:
        async def get_result(self, path: str, *, require_fresh: bool) -> object:
            return SimpleNamespace(data={"matches": [oversized] if path == "/active-matches" else []})

    class Publications:
        async def list_for_feature(self, *_: object) -> list[dict[str, object]]:
            return []

        async def upsert(self, *_: object) -> None:
            return None

    async def send(*, embed: object) -> object:
        sent.append(embed)
        return SimpleNamespace(id=55)

    channel = SimpleNamespace(send=send)
    guild = SimpleNamespace(id=10, get_channel=lambda _: channel)
    bot = SimpleNamespace(get_guild=lambda _: guild)

    await ActiveLobbyPublisher(bot, Upstream(), Publications()).reconcile_guild(
        {"guild_id": 10, "active_lobby_channel_id": 20}
    )

    payload = sent[0].to_dict()
    assert len(payload["title"]) <= 256
    assert len(payload["description"]) <= 4_096
    assert all(len(field["value"]) <= 1_024 for field in payload["fields"])
    assert sum(len(str(value)) for field in payload["fields"] for value in field.values()) + len(payload["title"]) + len(payload["description"]) <= 6_000
