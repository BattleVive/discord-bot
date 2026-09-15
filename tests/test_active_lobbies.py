"""Tests for v1 active-lobby publication and reconciliation."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from battlevive_gateway.active_lobbies import ActiveLobbyPublisher
from battlevive_gateway.active_lobbies import ActiveLobbyService


def match(**overrides: object) -> dict[str, object]:
    """Build a complete v1 match with every requested rich expansion."""
    record: dict[str, object] = {
        "match_id": 197, "title": "Seasonal 3v3", "season_id": "2026-3", "type": "seasonal",
        "state": "drafting", "size": 3, "region": "EU", "url": "https://battlevive.com/matches/197",
        "created_at": "2026-09-13T12:00:00+00:00", "updated_at": "2026-09-13T12:01:00+00:00",
        "winner_team": None, "duration_seconds": None, "ended_at": None,
        "selected_map": {"map_id": "blackstone", "map_name": "Blackstone Arena", "variant": "day"},
        "team_one": {"name": "Blue", "players": [
            {"member_number": 1, "discord_id": "11", "display_name": "Alpha", "profile_url": None, "is_bot": False, "champion_id": "ashka", "champion_name": "Ashka"},
        ]},
        "team_two": {"name": "Red", "players": [
            {"member_number": 2, "discord_id": None, "display_name": "Bravo", "profile_url": None, "is_bot": False, "champion_id": "jade", "champion_name": "Jade"},
        ]},
        "draft": {"draft_phase": "in_progress", "draft_step": 3, "picks_hidden": False,
                  "picks": [{"step": 1, "team": "team_one", "action": "pick", "player_index": 0, "champion_id": "ashka", "champion_name": "Ashka"}],
                  "bans": [{"step": 2, "team": "team_two", "action": "ban", "player_index": None, "champion_id": "freya", "champion_name": "Freya"}]},
    }
    record.update(overrides)
    return record


@pytest.mark.asyncio
async def test_publisher_uses_v1_active_and_disputed_routes_and_renders_rosters_draft_and_map() -> None:
    """Dropping requested v1 expansions would erase roster, draft, or map details from Discord."""
    saved: list[tuple[object, ...]] = []
    active = match()
    disputed = match(match_id=198, title="Unresolved final", state="disputed", duration_seconds=90,
                     ended_at="2026-09-13T12:10:00+00:00", winner_team="team_two")

    class Upstream:
        async def get_result(self, path: str, *, require_fresh: bool) -> object:
            assert require_fresh is True
            if path == "/active-matches":
                return SimpleNamespace(data={"matches": [active]})
            if path == "/disputed-matches":
                return SimpleNamespace(data={"matches": [disputed]})
            raise AssertionError(path)

    class Publications:
        async def list_for_feature(self, *_: object) -> list[dict[str, object]]:
            return []

        async def upsert(self, *args: object) -> None:
            saved.append(args)

    sent: list[object] = []

    async def send(*, embed: object, content: str | None = None, **kwargs: object) -> object:
        sent.append((embed, content, kwargs["allowed_mentions"]))
        return SimpleNamespace(id=55 + len(sent))

    channel = SimpleNamespace(send=send)
    guild = SimpleNamespace(id=10, get_channel=lambda channel_id: channel if channel_id == 20 else None)
    bot = SimpleNamespace(get_guild=lambda guild_id: guild if guild_id == 10 else None)

    assert await ActiveLobbyPublisher(bot, Upstream(), Publications()).reconcile_guild({
        "guild_id": 10, "active_lobby_channel_id": 20,
        "active_lobby_role_id": 71, "website_moderator_role_id": 72,
    })

    (live_embed, live_content, live_mentions), (dispute_embed, dispute_content, dispute_mentions) = sent
    live, dispute = live_embed.to_dict(), dispute_embed.to_dict()
    fields = {field["name"]: field["value"] for field in live["fields"]}
    assert live["title"] == "Seasonal 3v3"
    assert live["description"] == "Drafting · Seasonal · 3v3 · EU"
    assert "<@11>" in fields["Blue"] and "Ashka" in fields["Blue"]
    assert "Bravo" in fields["Red"] and "Jade" in fields["Red"]
    assert fields["Picks"] == "Blue: Ashka"
    assert fields["Bans"] == "Red: Freya"
    assert fields["Map"] == "Blackstone Arena (day)"
    assert dispute["description"].startswith("⚠️ Disputed")
    assert {field["name"]: field["value"] for field in dispute["fields"]}["Duration"] == "1m 30s"
    assert [row[2] for row in saved] == ["match:197", "match:198"]
    assert live_content == "<@&71>"
    assert dispute_content == "<@&72>"
    assert live_mentions.users is False and live_mentions.everyone is False
    assert dispute_mentions.users is False and dispute_mentions.everyone is False


@pytest.mark.asyncio
async def test_publisher_restores_v1_map_thumbnail_and_champion_emoji(tmp_path: Path) -> None:
    """A v1 lobby post includes its selected-map thumbnail and configured champion emoji."""
    maps = tmp_path / "maps"
    maps.mkdir()
    (maps / "blackstone-day.png").write_bytes(b"map")
    (maps / "manifest.json").write_text(json.dumps({"maps": [{
        "name": "Blackstone Arena", "aliases": ["Blackstone"],
        "day": "blackstone-day.png", "night": None,
    }]}), encoding="utf-8")

    class Upstream:
        async def get_result(self, path: str, *, require_fresh: bool) -> object:
            return SimpleNamespace(data={"matches": [match()] if path == "/active-matches" else []})

    class Publications:
        async def list_for_feature(self, *_: object) -> list[dict[str, object]]:
            return []

        async def upsert(self, *_: object) -> None:
            return None

    channel = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=55)))
    guild = SimpleNamespace(id=10, get_channel=lambda _: channel)
    bot = SimpleNamespace(get_guild=lambda _: guild)

    await ActiveLobbyPublisher(
        bot, Upstream(), Publications(), assets_root=tmp_path,
        emoji_lookup={"ashka": "<:Ashka:1>"},
    ).reconcile_guild({"guild_id": 10, "active_lobby_channel_id": 20})

    sent = channel.send.await_args.kwargs
    fields = {field.name: field.value for field in sent["embed"].fields}
    assert "<:Ashka:1> Ashka" in fields["Blue"]
    assert sent["embed"].thumbnail.url == "attachment://map-blackstone-day.png"
    assert sent["file"].filename == "map-blackstone-day.png"


@pytest.mark.asyncio
async def test_publisher_edits_changed_posts_and_removes_untracked_matches() -> None:
    """A stale publication must be deleted when no v1 active or disputed match retains its key."""
    previous = {"publication_key": "match:197", "channel_id": 20, "message_id": 55, "fingerprint": "old", "metadata": {}}
    obsolete = {"publication_key": "match:196", "channel_id": 20, "message_id": 54, "fingerprint": "old", "metadata": {}}
    deleted: list[tuple[object, ...]] = []
    edits: list[object] = []

    class Upstream:
        async def get_result(self, path: str, *, require_fresh: bool) -> object:
            return SimpleNamespace(data={"matches": [match()] if path == "/active-matches" else []})

    class Publications:
        async def list_for_feature(self, *_: object) -> list[dict[str, object]]:
            return [previous, obsolete]

        async def upsert(self, *_: object) -> None:
            return None

        async def delete(self, *args: object) -> None:
            deleted.append(args)

    async def fetch_message(message_id: int) -> object:
        if message_id == 55:
            async def edit(*, embed: object, **_: object) -> None:
                edits.append(embed)
            return SimpleNamespace(id=55, edit=edit)
        return SimpleNamespace(delete=AsyncMock())

    channel = SimpleNamespace(fetch_message=fetch_message)
    guild = SimpleNamespace(id=10, get_channel=lambda _: channel)
    bot = SimpleNamespace(get_guild=lambda _: guild)

    assert await ActiveLobbyPublisher(bot, Upstream(), Publications()).reconcile_guild({"guild_id": 10, "active_lobby_channel_id": 20})
    assert len(edits) == 1
    assert deleted == [(10, "active-lobbies", "match:196")]


@pytest.mark.asyncio
async def test_publisher_deletes_a_stale_match_from_its_stored_channel() -> None:
    """A channel move must not orphan a stale post in the old configured channel."""
    stale = {"publication_key": "match:196", "channel_id": 19, "message_id": 54,
             "fingerprint": "old", "metadata": {}}
    deleted: list[tuple[object, ...]] = []

    class Upstream:
        async def get_result(self, path: str, *, require_fresh: bool) -> object:
            return SimpleNamespace(data={"matches": [match()] if path == "/active-matches" else []})

    class Publications:
        async def list_for_feature(self, *_: object) -> list[dict[str, object]]:
            return [stale]

        async def upsert(self, *_: object) -> None:
            return None

        async def delete(self, *args: object) -> None:
            deleted.append(args)

    old_message = SimpleNamespace(delete=AsyncMock())

    async def old_fetch(message_id: int) -> object:
        assert message_id == 54
        return old_message

    async def new_fetch(_: int) -> object:
        raise discord.NotFound(SimpleNamespace(status=404, reason="missing"), {})

    old_channel = SimpleNamespace(fetch_message=old_fetch)
    new_channel = SimpleNamespace(fetch_message=new_fetch, send=AsyncMock(return_value=SimpleNamespace(id=55)))
    guild = SimpleNamespace(id=10, get_channel=lambda channel_id: {19: old_channel, 20: new_channel}.get(channel_id))
    bot = SimpleNamespace(get_guild=lambda _: guild)

    assert await ActiveLobbyPublisher(bot, Upstream(), Publications()).reconcile_guild(
        {"guild_id": 10, "active_lobby_channel_id": 20}
    )
    old_message.delete.assert_awaited_once()
    assert deleted == [(10, "active-lobbies", "match:196")]


@pytest.mark.asyncio
async def test_publisher_clears_an_obsolete_map_attachment_when_editing() -> None:
    """A map removed upstream must remove its old Discord attachment too."""
    previous = {"publication_key": "match:197", "channel_id": 20, "message_id": 55,
                "fingerprint": "old", "metadata": {}}

    class Upstream:
        async def get_result(self, path: str, *, require_fresh: bool) -> object:
            return SimpleNamespace(data={"matches": [match(selected_map=None)] if path == "/active-matches" else []})

    class Publications:
        async def list_for_feature(self, *_: object) -> list[dict[str, object]]:
            return [previous]

        async def upsert(self, *_: object) -> None:
            return None

    edits: list[dict[str, object]] = []

    async def edit(**kwargs: object) -> None:
        edits.append(kwargs)

    async def fetch_message(message_id: int) -> object:
        assert message_id == 55
        return SimpleNamespace(id=55, edit=edit)

    channel = SimpleNamespace(fetch_message=fetch_message)
    guild = SimpleNamespace(id=10, get_channel=lambda _: channel)
    bot = SimpleNamespace(get_guild=lambda _: guild)

    assert await ActiveLobbyPublisher(bot, Upstream(), Publications()).reconcile_guild(
        {"guild_id": 10, "active_lobby_channel_id": 20}
    )
    assert edits[0]["attachments"] == []


@pytest.mark.asyncio
async def test_publisher_deletes_new_message_when_persistence_fails() -> None:
    """A message without durable publication state must be compensated immediately."""
    class Upstream:
        async def get_result(self, path: str, *, require_fresh: bool) -> object:
            return SimpleNamespace(data={"matches": [match()] if path == "/active-matches" else []})

    class Publications:
        async def list_for_feature(self, *_: object) -> list[dict[str, object]]:
            return []

        async def upsert(self, *_: object) -> None:
            raise RuntimeError("database unavailable")

    message = SimpleNamespace(id=55, delete=AsyncMock())
    channel = SimpleNamespace(send=AsyncMock(return_value=message))
    guild = SimpleNamespace(id=10, get_channel=lambda _: channel)
    bot = SimpleNamespace(get_guild=lambda _: guild)

    with pytest.raises(RuntimeError, match="database unavailable"):
        await ActiveLobbyPublisher(bot, Upstream(), Publications()).reconcile_guild({"guild_id": 10, "active_lobby_channel_id": 20})
    message.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_publisher_does_not_re_notify_when_recreating_a_deleted_tracked_post() -> None:
    """A deleted tracked post must be restored without another role notification."""
    previous = {"publication_key": "match:197", "channel_id": 20, "message_id": 55,
                "fingerprint": "old", "metadata": {}}

    class Upstream:
        async def get_result(self, path: str, *, require_fresh: bool) -> object:
            return SimpleNamespace(data={"matches": [match()] if path == "/active-matches" else []})

    class Publications:
        async def list_for_feature(self, *_: object) -> list[dict[str, object]]:
            return [previous]

        async def upsert(self, *_: object) -> None:
            return None

    async def fetch_message(_: int) -> object:
        raise discord.NotFound(SimpleNamespace(status=404, reason="missing"), {})

    channel = SimpleNamespace(fetch_message=fetch_message, send=AsyncMock(return_value=SimpleNamespace(id=56)))
    guild = SimpleNamespace(id=10, get_channel=lambda _: channel)
    bot = SimpleNamespace(get_guild=lambda _: guild)

    assert await ActiveLobbyPublisher(bot, Upstream(), Publications()).reconcile_guild({
        "guild_id": 10, "active_lobby_channel_id": 20, "active_lobby_role_id": 71,
    })
    assert channel.send.await_args.kwargs.get("content") is None


@pytest.mark.asyncio
async def test_publisher_replaces_the_empty_state_when_a_match_appears() -> None:
    """An empty channel gets one managed status post, removed before match posts."""
    empty = {"publication_key": "empty", "channel_id": 20, "message_id": 54, "fingerprint": "empty", "metadata": {}}
    deleted: list[tuple[object, ...]] = []
    sent: list[object] = []

    class Upstream:
        async def get_result(self, path: str, *, require_fresh: bool) -> object:
            return SimpleNamespace(data={"matches": [match()] if path == "/active-matches" else []})

    class Publications:
        async def list_for_feature(self, *_: object) -> list[dict[str, object]]:
            return [empty]

        async def upsert(self, *args: object) -> None:
            sent.append(args)

        async def delete(self, *args: object) -> None:
            deleted.append(args)

    old_message = SimpleNamespace(delete=AsyncMock())
    new_message = SimpleNamespace(id=55)

    async def fetch_message(message_id: int) -> object:
        assert message_id == 54
        return old_message

    channel = SimpleNamespace(fetch_message=fetch_message, send=AsyncMock(return_value=new_message))
    guild = SimpleNamespace(id=10, get_channel=lambda _: channel)
    bot = SimpleNamespace(get_guild=lambda _: guild)

    assert await ActiveLobbyPublisher(bot, Upstream(), Publications()).reconcile_guild({"guild_id": 10, "active_lobby_channel_id": 20})
    old_message.delete.assert_awaited_once()
    assert deleted == [(10, "active-lobbies", "empty")]
    assert sent[0][2] == "match:197"


@pytest.mark.asyncio
async def test_publisher_creates_one_empty_state_when_no_matches_exist() -> None:
    """The empty state retains v1's visible dark-grey Active Matches embed."""
    saved: list[tuple[object, ...]] = []

    class Upstream:
        async def get_result(self, _path: str, *, require_fresh: bool) -> object:
            assert require_fresh is True
            return SimpleNamespace(data={"matches": []})

    class Publications:
        async def list_for_feature(self, *_: object) -> list[dict[str, object]]:
            return []

        async def upsert(self, *args: object) -> None:
            saved.append(args)

    channel = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=55)))
    guild = SimpleNamespace(id=10, get_channel=lambda _: channel)
    bot = SimpleNamespace(get_guild=lambda _: guild)

    assert await ActiveLobbyPublisher(bot, Upstream(), Publications()).reconcile_guild({"guild_id": 10, "active_lobby_channel_id": 20})
    sent = channel.send.await_args.kwargs
    assert sent["content"] is None
    assert sent["embed"].title == "Active Matches"
    assert sent["embed"].description == "There are no active matches right now."
    assert int(sent["embed"].colour) == 0x607D8B
    assert sent["allowed_mentions"].users is False
    assert sent["allowed_mentions"].roles is False
    assert saved[0][2:6] == ("empty", 20, 55, None)
    assert isinstance(saved[0][6], str) and saved[0][6]


@pytest.mark.asyncio
async def test_active_lobby_service_serializes_concurrent_reconciliation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Concurrent wakeups must never publish two competing snapshots for a guild."""
    import battlevive_gateway.active_lobbies as active_lobbies

    class Connection:
        async def fetch(self, *_: object) -> list[dict[str, int]]:
            return [{"guild_id": 10, "active_lobby_channel_id": 20}]

    class Acquire:
        async def __aenter__(self) -> Connection:
            return Connection()
        async def __aexit__(self, *_: object) -> None:
            return None

    active, maximum = 0, 0
    entered, release = asyncio.Event(), asyncio.Event()

    async def reconcile_guild(_: object, __: object) -> bool:
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        entered.set()
        await release.wait()
        active -= 1
        return False

    monkeypatch.setattr(active_lobbies.ActiveLobbyPublisher, "reconcile_guild", reconcile_guild)
    service = ActiveLobbyService(SimpleNamespace(), SimpleNamespace(acquire=lambda: Acquire()), SimpleNamespace())
    first = asyncio.create_task(service.reconcile_all())
    await entered.wait()
    second = asyncio.create_task(service.reconcile_all())
    await asyncio.sleep(0)
    assert maximum == 1
    release.set()
    await asyncio.gather(first, second)
