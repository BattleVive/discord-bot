from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
import uuid

import pytest

pytest.importorskip("asyncpg")
pytest.importorskip("discord")
from PIL import Image

from battlevive_bot import leaderboards


def png(width: int, height: int, color: str) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), color).save(output, format="PNG")
    return output.getvalue()


class FakeMessage:
    def __init__(self, channel: "FakeChannel", message_id: int, png: bytes) -> None:
        self.channel = channel
        self.id = message_id
        self.png = png
        self.edits = 0

    async def edit(self, *, attachments: list[object]) -> None:
        attachment = attachments[0]
        attachment.fp.seek(0)
        self.png = attachment.fp.read()
        self.edits += 1

    async def delete(self) -> None:
        self.channel.messages.pop(self.id, None)


class FakeChannel:
    def __init__(self) -> None:
        self.id = 200
        self.messages: dict[int, FakeMessage] = {}
        self.next_id = 1000

    def permissions_for(self, member: object) -> SimpleNamespace:
        return SimpleNamespace(
            view_channel=True,
            send_messages=True,
            attach_files=True,
            read_message_history=True,
        )

    async def send(self, *, file: object) -> FakeMessage:
        file.fp.seek(0)
        message = FakeMessage(self, self.next_id, file.fp.read())
        self.messages[message.id] = message
        self.next_id += 1
        return message

    async def fetch_message(self, message_id: int) -> FakeMessage:
        return self.messages[message_id]


class FakeGuild:
    def __init__(self, channel: FakeChannel, member_ids: set[int]) -> None:
        self.id = 100
        self.me = object()
        self.members = [SimpleNamespace(id=member_id) for member_id in member_ids]
        self.channel = channel

    def get_channel(self, channel_id: int) -> FakeChannel | None:
        return self.channel if channel_id == self.channel.id else None


class FakeBot:
    def __init__(self, guild: FakeGuild) -> None:
        self.guild = guild

    def get_guild(self, guild_id: int) -> FakeGuild | None:
        return self.guild if guild_id == self.guild.id else None

    async def wait_until_ready(self) -> None:
        return None


def rating(discord_id: int, username: str, mmr: int) -> dict[str, object]:
    return {
        "user_id": uuid.uuid4(),
        "discord_id": discord_id,
        "discord_username": username,
        "mmr": mmr,
        "wins": 4,
        "losses": 1,
        "matches_played": 5,
        "season_year": 2026,
        "season_number": 3,
    }


@pytest.mark.asyncio
async def test_initial_publish_filters_members_and_reuses_unchanged_pngs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    channel = FakeChannel()
    guild = FakeGuild(channel, {11, 22})
    service = leaderboards.LeaderboardService(FakeBot(guild), None)
    slots: dict[int, dict[str, object]] = {}
    render_calls: list[list[str]] = []

    async def get_slots(guild_id: int) -> list[dict[str, object]]:
        return [slots[key].copy() for key in sorted(slots)]

    async def upsert_slot(
        guild_id: int,
        slot: int,
        channel_id: int | None,
        message_id: int | None,
        season_year: int | None,
        season_number: int | None,
        user_id: str | None,
        fingerprint: str,
        png: bytes,
    ) -> None:
        slots[slot] = {
            "guild_id": guild_id,
            "slot": slot,
            "channel_id": channel_id,
            "message_id": message_id,
            "season_year": season_year,
            "season_number": season_number,
            "user_id": user_id,
            "fingerprint": fingerprint,
            "png": png,
        }

    async def delete_slots(guild_id: int, first_slot: int) -> None:
        for slot in list(slots):
            if slot >= first_slot:
                del slots[slot]

    def build_header(label: str) -> bytes:
        return f"header:{label}".encode()

    async def build_entries(entries: list[object], season: str) -> list[bytes]:
        render_calls.append([entry.username for entry in entries])
        return [f"entry:{entry.username}:{entry.mmr}".encode() for entry in entries]

    async def run_inline(function: object, *args: object) -> object:
        return function(*args)

    def combine(pngs: list[bytes]) -> bytes:
        return b"|".join(pngs)

    monkeypatch.setattr(leaderboards.db, "get_leaderboard_slots", get_slots)
    monkeypatch.setattr(leaderboards.db, "upsert_leaderboard_slot", upsert_slot)
    monkeypatch.setattr(leaderboards.db, "delete_leaderboard_slots_from", delete_slots)
    monkeypatch.setattr(leaderboards, "build_leaderboard_header", build_header)
    monkeypatch.setattr(leaderboards, "build_leaderboard_images", build_entries)
    monkeypatch.setattr(leaderboards, "_combine_leaderboard_pngs", combine)
    # Python 3.14.6 in the local sandbox hangs while shutting down asyncio's
    # default executor; the production path is separately covered by images.py.
    monkeypatch.setattr(leaderboards.asyncio, "to_thread", run_inline)

    rows = [rating(11, "Alpha", 2500), rating(99, "Outside", 2400), rating(22, "Beta", 2300)]
    config = {
        "guild_id": 100,
        "leaderboard_channel_id": 200,
        "leaderboard_limit": None,
    }
    await service.reconcile_guild(config, (2026, 3), rows)

    assert [slots[index]["png"] for index in sorted(slots)] == [
        b"header:Season 3",
        b"entry:Alpha:2500",
        b"entry:Beta:2300",
    ]
    assert render_calls == [["Alpha", "Beta"]]
    assert len(channel.messages) == 1
    leaderboard_message = next(iter(channel.messages.values()))
    assert leaderboard_message.png == (
        b"header:Season 3|entry:Alpha:2500|entry:Beta:2300"
    )
    assert slots[0]["message_id"] == leaderboard_message.id
    assert slots[1]["message_id"] is None
    assert slots[2]["message_id"] is None

    render_calls.clear()
    await service.reconcile_guild(config, (2026, 3), rows)
    assert render_calls == []
    assert all(message.edits == 0 for message in channel.messages.values())

    rows[0]["mmr"] = 2600
    await service.reconcile_guild(config, (2026, 3), rows)
    assert render_calls == [["Alpha"]]
    assert len(channel.messages) == 1
    assert leaderboard_message.edits == 1
    assert leaderboard_message.png == (
        b"header:Season 3|entry:Alpha:2600|entry:Beta:2300"
    )


def test_empty_season_and_zero_match_state_are_deterministic() -> None:
    channel = FakeChannel()
    service = leaderboards.LeaderboardService(
        FakeBot(FakeGuild(channel, {11})),
        None,
    )

    empty = service._desired_slots(None, [])
    zero = rating(11, "Alpha", 1000)
    zero["wins"] = 0
    zero["losses"] = 0
    populated = service._desired_slots((2026, 3), [zero])

    assert len(empty) == 1
    assert empty[0].season_number is None
    assert populated[1].entry.win_rate == 0


def test_combined_leaderboard_is_one_vertically_stacked_png() -> None:
    combined = leaderboards._combine_leaderboard_pngs(
        [png(640, 184, "red"), png(640, 124, "blue")]
    )

    with Image.open(BytesIO(combined)) as image:
        assert image.size == (640, 308)
        assert image.getpixel((10, 10)) == (255, 0, 0)
        assert image.getpixel((10, 200)) == (0, 0, 255)
