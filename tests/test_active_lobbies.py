from __future__ import annotations

from datetime import UTC
from datetime import datetime
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

discord = pytest.importorskip("discord")
pytest.importorskip("asyncpg")

from battlevive_bot.active_lobbies import ActiveLobbyService
from battlevive_bot.active_lobbies import application_emoji_lookup
from battlevive_bot.active_lobbies import build_active_lobby_embed
from battlevive_bot.active_lobbies import build_match_url
from battlevive_bot.active_lobbies import LobbyPollState
from battlevive_bot.active_lobbies import MapResolver
from battlevive_bot.active_lobbies import newest_confirmations
from battlevive_bot.active_lobbies import result_confirmation_text
from battlevive_bot.active_lobbies import result_is_resolved
from battlevive_bot.active_lobbies import validate_battlevive_url


def candidate(lobby_id: int = 165, **overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "id": lobby_id,
        "lobby_number": lobby_id,
        "game_number": 19,
        "url_year": 2026,
        "url_series": "season-one",
        "team_one_name": "Blue",
        "team_two_name": "Red",
        "status": "drafting",
        "draft_step": 3,
        "draft_started_at": datetime(2026, 8, 1, tzinfo=UTC),
        "ended_at": None,
        "winner_slot": None,
        "dispute_reason": None,
        "selected_map": None,
        "team_one_roster": ["user-one", "unlinked"],
        "team_two_roster": ["user-two"],
        "team_one_captain_id": "user-one",
        "team_two_captain_id": "user-two",
        "team_one_picks": [],
        "team_one_bans": [],
        "team_two_picks": [],
        "team_two_bans": [],
        "draft_finalized_at": None,
        "users_by_id": {
            "user-one": {
                "id": "user-one",
                "discord_id": 111,
                "discord_username": "One",
            },
            "unlinked": {
                "id": "unlinked",
                "discord_id": None,
                "discord_username": "Fallback Name",
            },
            "user-two": {
                "id": "user-two",
                "discord_id": 222,
                "discord_username": "Two",
            },
        },
    }
    data.update(overrides)
    return data


def action(
    step: int,
    slot: str,
    kind: str,
    champion: str | None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=step,
        lobby_id=165,
        step=step,
        team_slot=slot,
        action=kind,
        champion=champion,
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
    )


def confirmation(
    row_id: int,
    slot: str,
    winner: str,
    minute: int,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=row_id,
        lobby_id=165,
        user_id=f"user-{row_id}",
        selected_winner=winner,
        created_at=datetime(2026, 8, 1, 12, minute, tzinfo=UTC),
        captain_slot=slot,
    )


class FakeEmoji:
    def __init__(self, name: str, value: str) -> None:
        self.name = name
        self.value = value

    def __str__(self) -> str:
        return self.value


def test_url_validation_and_optional_match_link() -> None:
    assert validate_battlevive_url("https://battlevive.test/") == "https://battlevive.test"
    assert build_match_url(candidate(), "https://battlevive.test") == (
        "https://battlevive.test/matchmaking/2026/season-one/GAME-19"
    )
    assert build_match_url(candidate(url_series=None), "https://battlevive.test") is None
    with pytest.raises(ValueError, match="HTTPS"):
        validate_battlevive_url("http://battlevive.test")
    with pytest.raises(ValueError, match="credentials"):
        validate_battlevive_url("https://name:secret@battlevive.test")


def test_embed_uses_case_insensitive_emojis_mentions_and_map_alias(
    tmp_path: Path,
) -> None:
    map_dir = tmp_path / "maps"
    map_dir.mkdir()
    (map_dir / "blackstone-day.png").write_bytes(b"not-opened-by-builder")
    (map_dir / "manifest.json").write_text(
        json.dumps(
            {
                "maps": [
                    {
                        "name": "Blackstone Arena",
                        "aliases": ["Blackstone"],
                        "day": "blackstone-day.png",
                        "night": "blackstone-night.png",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    state = LobbyPollState(
        actions=[
            action(3, "team_one", "pick", "Lucie"),
            action(1, "team_one", "map_ban", "Ignored"),
            action(2, "team_two", "ban", "Raigon"),
        ]
    )
    emoji_lookup = application_emoji_lookup(
        [FakeEmoji("LUCIE", "<:LUCIE:1>"), FakeEmoji("raIGON", "<:raIGON:2>")]
    )

    rendered = build_active_lobby_embed(
        candidate(selected_map="Blackstone"),
        state,
        emoji_lookup=emoji_lookup,
        map_resolver=MapResolver(tmp_path),
        battlevive_url="https://battlevive.test",
    )

    assert rendered.embed.title == "Game 19 · Lobby #165"
    assert rendered.embed.url.endswith("/matchmaking/2026/season-one/GAME-19")
    team_values = [field.value for field in rendered.embed.fields[:2]]
    assert "👑 <@111>" in team_values[0]
    assert "Fallback Name" in team_values[0]
    assert "<:LUCIE:1> Lucie" in team_values[0]
    assert "<:raIGON:2> Raigon" in team_values[1]
    assert "Ignored" not in "\n".join(team_values)
    assert rendered.embed.thumbnail.url.startswith("attachment://map-")
    assert rendered.map_path == map_dir / "blackstone-day.png"


def test_unknown_map_and_missing_url_components_have_safe_fallback(tmp_path: Path) -> None:
    rendered = build_active_lobby_embed(
        candidate(selected_map="Future Arena", game_number=None),
        LobbyPollState(),
        emoji_lookup={},
        map_resolver=MapResolver(tmp_path),
        battlevive_url="https://battlevive.test",
    )

    assert rendered.embed.url is None
    assert rendered.map_path is None
    assert rendered.embed.fields[2].value == "Future Arena"


def test_newest_confirmation_per_slot_drives_consensus_and_dispute() -> None:
    rows = [
        confirmation(1, "team_one", "team_two", 1),
        confirmation(2, "team_one", "team_one", 2),
        confirmation(3, "team_two", "team_one", 3),
    ]
    latest = newest_confirmations(rows)
    state = LobbyPollState(confirmations=rows, draft_finalized=True)

    assert latest["team_one"].id == 2
    assert result_is_resolved(candidate(ended_at="done"), state) is True
    assert "Disputed" not in result_confirmation_text(candidate(), rows)

    disputed = [
        confirmation(4, "team_one", "team_one", 4),
        confirmation(5, "team_two", "team_two", 5),
    ]
    assert "⚠️ **Disputed**" in result_confirmation_text(candidate(), disputed)
    assert result_is_resolved(
        candidate(status="disputed", ended_at="done", winner_slot="team_two"),
        LobbyPollState(confirmations=disputed, draft_finalized=True),
    ) is True


class FakeAPI:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []
        self.fail_draft = 0

    async def get_lobby_draft_actions(self, lobby_id: int) -> list[object]:
        self.calls.append(("draft", lobby_id))
        if self.fail_draft:
            self.fail_draft -= 1
            raise RuntimeError("temporary")
        return [action(1, "team_one", "pick", "Lucie")]

    async def get_lobby_captains(self, lobby_id: int) -> list[object]:
        self.calls.append(("captains", lobby_id))
        return [
            SimpleNamespace(slot="team_one", user_id="user-one"),
            SimpleNamespace(slot="team_two", user_id="user-two"),
        ]

    async def get_match_result_confirmations(self, lobby_id: int) -> list[object]:
        self.calls.append(("confirmations", lobby_id))
        return []


class FakeDatabase:
    def __init__(self) -> None:
        self.configs: list[dict[str, object]] = []
        self.candidates: list[dict[str, object]] = []
        self.posts: dict[int, dict[int, dict[str, object]]] = {}
        self.empty: dict[int, dict[str, object]] = {}
        self.finalize_failures = 0
        self.finalized: list[tuple[int, list[object]]] = []
        self.captains: list[tuple[int, list[object]]] = []

    async def get_active_lobby_candidates(self) -> list[dict[str, object]]:
        return self.candidates

    async def update_lobby_captains(self, lobby_id: int, captains: list[object]) -> None:
        self.captains.append((lobby_id, captains))

    async def finalize_lobby_draft(
        self,
        lobby_id: int,
        actions: list[object],
        finalized_at: object = None,
    ) -> None:
        if self.finalize_failures:
            self.finalize_failures -= 1
            raise RuntimeError("database unavailable")
        self.finalized.append((lobby_id, actions))

    async def get_configured_active_lobbies(self) -> list[dict[str, object]]:
        return self.configs

    async def get_active_lobby_post_states(self, guild_id: int) -> list[dict[str, object]]:
        return [dict(row) for row in self.posts.get(guild_id, {}).values()]

    async def ensure_active_lobby_post_states(
        self,
        guild_id: int,
        lobby_ids: list[int],
        *,
        notification_handled: bool,
    ) -> None:
        rows = self.posts.setdefault(guild_id, {})
        for lobby_id in lobby_ids:
            rows.setdefault(
                lobby_id,
                {
                    "guild_id": guild_id,
                    "lobby_id": lobby_id,
                    "channel_id": None,
                    "message_id": None,
                    "fingerprint": None,
                    "notification_handled": notification_handled,
                },
            )

    async def record_active_lobby_post(
        self,
        guild_id: int,
        lobby_id: int,
        channel_id: int,
        message_id: int,
        fingerprint: str,
        *,
        notification_handled: bool,
    ) -> None:
        previous = self.posts.setdefault(guild_id, {}).get(lobby_id, {})
        self.posts[guild_id][lobby_id] = {
            **previous,
            "guild_id": guild_id,
            "lobby_id": lobby_id,
            "channel_id": channel_id,
            "message_id": message_id,
            "fingerprint": fingerprint,
            "notification_handled": bool(
                previous.get("notification_handled") or notification_handled
            ),
        }

    async def clear_active_lobby_post_message(
        self,
        guild_id: int,
        lobby_id: int,
        channel_id: int,
        message_id: int,
    ) -> bool:
        row = self.posts[guild_id][lobby_id]
        if row["channel_id"] != channel_id or row["message_id"] != message_id:
            return False
        row.update(channel_id=None, message_id=None, fingerprint=None)
        return True

    async def get_active_lobby_empty_post(self, guild_id: int) -> dict[str, object] | None:
        row = self.empty.get(guild_id)
        return dict(row) if row is not None else None

    async def record_active_lobby_empty_post(
        self,
        guild_id: int,
        channel_id: int,
        message_id: int,
        fingerprint: str,
    ) -> None:
        self.empty[guild_id] = {
            "guild_id": guild_id,
            "channel_id": channel_id,
            "message_id": message_id,
            "fingerprint": fingerprint,
        }

    async def clear_active_lobby_empty_post(
        self,
        guild_id: int,
        channel_id: int,
        message_id: int,
    ) -> bool:
        row = self.empty[guild_id]
        if row["channel_id"] != channel_id or row["message_id"] != message_id:
            return False
        row.update(channel_id=None, message_id=None, fingerprint=None)
        return True


class FakeBot:
    def __init__(self) -> None:
        self.guilds: dict[int, FakeGuild] = {}
        self.emoji_fetches = 0

    def get_guild(self, guild_id: int) -> "FakeGuild | None":
        return self.guilds.get(guild_id)

    async def fetch_application_emojis(self) -> list[FakeEmoji]:
        self.emoji_fetches += 1
        return [FakeEmoji("Lucie", "<:Lucie:1>")]

    async def wait_until_ready(self) -> None:
        return None


@pytest.mark.asyncio
async def test_no_idle_api_requests_and_multi_guild_polling_is_global(
    tmp_path: Path,
) -> None:
    api = FakeAPI()
    database = FakeDatabase()
    bot = FakeBot()
    service = ActiveLobbyService(
        bot, api, database, None, "https://battlevive.test", tmp_path
    )
    service._reconcile_guild = AsyncMock()
    database.candidates = [
        candidate(team_one_captain_id=None, team_two_captain_id=None)
    ]

    await service.reconcile_once()
    assert api.calls == []
    assert service.polling_active is False

    database.configs = [
        {"guild_id": 1, "active_lobby_channel_id": 10, "active_lobby_role_id": None},
        {"guild_id": 2, "active_lobby_channel_id": 20, "active_lobby_role_id": None},
    ]
    await service.reconcile_once()

    assert api.calls.count(("draft", 165)) == 1
    assert api.calls.count(("captains", 165)) == 1
    assert service._reconcile_guild.await_count == 2
    assert bot.emoji_fetches == 1


@pytest.mark.asyncio
async def test_transient_failure_retains_state_and_uses_bounded_retry(
    tmp_path: Path,
) -> None:
    now = [100.0]
    api = FakeAPI()
    api.fail_draft = 1
    database = FakeDatabase()
    service = ActiveLobbyService(
        FakeBot(),
        api,
        database,
        None,
        "https://battlevive.test",
        tmp_path,
        clock=lambda: now[0],
    )
    row = candidate(team_one_captain_id=None, team_two_captain_id=None)

    await service._poll_lobby(row)
    assert service._states[165].actions == []
    first_calls = list(api.calls)
    await service._poll_lobby(row)
    assert api.calls == first_calls

    now[0] += 5
    await service._poll_lobby(row)
    assert [item[0] for item in api.calls].count("draft") == 2
    assert service._states[165].actions
    assert service._states[165].failures == 0


@pytest.mark.asyncio
async def test_final_draft_persistence_retries_before_resolution_can_delete(
    tmp_path: Path,
) -> None:
    now = [100.0]
    api = FakeAPI()
    database = FakeDatabase()
    database.finalize_failures = 1
    service = ActiveLobbyService(
        FakeBot(),
        api,
        database,
        None,
        "https://battlevive.test",
        tmp_path,
        clock=lambda: now[0],
    )
    row = candidate(ended_at=datetime(2026, 8, 1, tzinfo=UTC))

    await service._poll_lobby(row)
    assert service._states[165].draft_finalized is False
    assert database.finalized == []

    now[0] += 5
    await service._poll_lobby(row)
    assert service._states[165].draft_finalized is True
    assert len(database.finalized) == 1


@pytest.mark.asyncio
async def test_service_start_is_idempotent_and_stop_cancels_workers(
    tmp_path: Path,
) -> None:
    service = ActiveLobbyService(
        FakeBot(),
        FakeAPI(),
        FakeDatabase(),
        None,
        "https://battlevive.test",
        tmp_path,
        idle_interval=60,
    )

    service.start()
    tasks = list(service._tasks)
    service.start()
    assert service._tasks == tasks

    await service.stop()
    assert service._tasks == []
    assert service.polling_active is False
    assert all(task.done() for task in tasks)


class FakeMessage:
    def __init__(self, message_id: int, channel: "FakeChannel", **kwargs: object) -> None:
        self.id = message_id
        self.channel = channel
        self.kwargs = kwargs
        self.deleted = False
        self.edits: list[dict[str, object]] = []

    async def edit(self, **kwargs: object) -> None:
        self.edits.append(kwargs)

    async def delete(self) -> None:
        self.deleted = True
        self.channel.messages.pop(self.id, None)


class FakeResponse:
    status = 404
    reason = "Not Found"
    headers: dict[str, str] = {}


class FakeChannel:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id
        self.messages: dict[int, FakeMessage] = {}
        self.sent: list[FakeMessage] = []
        self._next_id = channel_id * 100

    def permissions_for(self, member: object) -> SimpleNamespace:
        return SimpleNamespace(
            view_channel=True,
            send_messages=True,
            embed_links=True,
            attach_files=True,
            read_message_history=True,
            mention_everyone=True,
        )

    async def send(self, **kwargs: object) -> FakeMessage:
        self._next_id += 1
        message = FakeMessage(self._next_id, self, **kwargs)
        self.messages[message.id] = message
        self.sent.append(message)
        return message

    async def fetch_message(self, message_id: int) -> FakeMessage:
        if message_id not in self.messages:
            raise discord.NotFound(FakeResponse(), "missing")
        return self.messages[message_id]


class FakeRole:
    id = 77
    managed = False

    def is_default(self) -> bool:
        return False


class FakeGuild:
    def __init__(self, guild_id: int, *channels: FakeChannel) -> None:
        self.id = guild_id
        self.me = object()
        self.channels = {channel.id: channel for channel in channels}
        self.role = FakeRole()

    def get_channel(self, channel_id: int) -> FakeChannel | None:
        return self.channels.get(channel_id)

    def get_role(self, role_id: int) -> FakeRole | None:
        return self.role if role_id == self.role.id else None


@pytest.mark.asyncio
async def test_silent_baseline_one_time_ping_move_delete_recovery_and_reset(
    tmp_path: Path,
) -> None:
    database = FakeDatabase()
    api = FakeAPI()
    bot = FakeBot()
    old_channel = FakeChannel(10)
    new_channel = FakeChannel(20)
    guild = FakeGuild(1, old_channel, new_channel)
    bot.guilds[1] = guild
    service = ActiveLobbyService(
        bot, api, database, None, "https://battlevive.test", tmp_path
    )
    config = {
        "guild_id": 1,
        "active_lobby_channel_id": 10,
        "active_lobby_role_id": 77,
    }
    first = candidate()

    await service._reconcile_guild(config, [first])
    first_message = old_channel.sent[-1]
    assert first_message.kwargs["content"] is None
    assert database.posts[1][165]["notification_handled"] is True

    second = candidate(166)
    await service._reconcile_guild(config, [first, second])
    second_message = old_channel.sent[-1]
    assert second_message.kwargs["content"] == "<@&77>"
    allowed = second_message.kwargs["allowed_mentions"]
    assert allowed.users is False
    assert allowed.everyone is False
    assert allowed.roles == [guild.role]

    second_message_id = second_message.id
    old_channel.messages.pop(second_message_id)
    await service._reconcile_guild(config, [first, second])
    recovered = old_channel.sent[-1]
    assert recovered.kwargs["content"] is None

    moved = {**config, "active_lobby_channel_id": 20}
    await service._reconcile_guild(moved, [first, second])
    assert len(new_channel.sent) == 2
    assert recovered.deleted is True
    assert database.posts[1][166]["channel_id"] == 20

    reset = {**moved, "active_lobby_channel_id": None}
    await service._reconcile_guild(reset, [first, second])
    assert database.posts[1][165]["message_id"] is None
    assert database.posts[1][165]["notification_handled"] is True
    assert database.posts[1][166]["message_id"] is None


@pytest.mark.asyncio
async def test_empty_state_is_singleton_and_moves_replacement_first(tmp_path: Path) -> None:
    database = FakeDatabase()
    bot = FakeBot()
    old_channel = FakeChannel(10)
    new_channel = FakeChannel(20)
    guild = FakeGuild(1, old_channel, new_channel)
    bot.guilds[1] = guild
    service = ActiveLobbyService(
        bot, FakeAPI(), database, None, "https://battlevive.test", tmp_path
    )

    await service._reconcile_guild(
        {"guild_id": 1, "active_lobby_channel_id": 10, "active_lobby_role_id": None},
        [],
    )
    original = old_channel.sent[0]
    await service._reconcile_guild(
        {"guild_id": 1, "active_lobby_channel_id": 10, "active_lobby_role_id": None},
        [],
    )
    assert len(old_channel.sent) == 1

    await service._reconcile_guild(
        {"guild_id": 1, "active_lobby_channel_id": 20, "active_lobby_role_id": None},
        [],
    )
    assert len(new_channel.sent) == 1
    assert original.deleted is True
    assert database.empty[1]["channel_id"] == 20
