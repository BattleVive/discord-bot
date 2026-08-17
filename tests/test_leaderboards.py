from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

pytest.importorskip("asyncpg")
pytest.importorskip("discord")
from PIL import Image

from battlevive_bot import leaderboards
from battlevive_bot.images import LeaderboardEntry


class FakeMessage:
    def __init__(self, channel: "FakeChannel", message_id: int, image: bytes) -> None:
        self.channel = channel
        self.id = message_id
        self.image = image
        self.edits = 0

    async def edit(self, *, attachments: list[object]) -> None:
        if self.channel.fail_edit:
            raise RuntimeError("edit failed")
        attachment = attachments[0]
        attachment.fp.seek(0)
        self.image = attachment.fp.read()
        self.edits += 1

    async def delete(self) -> None:
        self.channel.messages.pop(self.id, None)


class FakeChannel:
    def __init__(self) -> None:
        self.id = 200
        self.messages: dict[int, FakeMessage] = {}
        self.next_id = 1000
        self.fail_send = False
        self.fail_edit = False

    def permissions_for(self, member: object) -> SimpleNamespace:
        return SimpleNamespace(
            view_channel=True,
            send_messages=True,
            attach_files=True,
            read_message_history=True,
        )

    async def send(self, *, file: object) -> FakeMessage:
        if self.fail_send:
            raise RuntimeError("upload failed")
        file.fp.seek(0)
        message = FakeMessage(self, self.next_id, file.fp.read())
        self.messages[message.id] = message
        self.next_id += 1
        return message

    async def fetch_message(self, message_id: int) -> FakeMessage:
        try:
            return self.messages[message_id]
        except KeyError as error:
            response = SimpleNamespace(status=404, reason="Not Found", headers={})
            raise leaderboards.discord.NotFound(response, "missing") from error


class FakeGuild:
    def __init__(self, channel: FakeChannel, member_ids: set[int]) -> None:
        self.id = 100
        self.me = object()
        self.members = [SimpleNamespace(id=member_id) for member_id in member_ids]
        self.channel = channel
        self.filesize_limit = 10 * 1024 * 1024

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


def config(*, limit: int | None = None) -> dict[str, int | None]:
    return {
        "guild_id": 100,
        "leaderboard_channel_id": 200,
        "leaderboard_limit": limit,
    }


def install_slot_store(monkeypatch: pytest.MonkeyPatch) -> dict[int, dict[str, object]]:
    """Install the atomic metadata API against a small in-memory fake database."""
    slots: dict[int, dict[str, object]] = {}

    async def get_slots(guild_id: int) -> list[dict[str, object]]:
        assert guild_id == 100
        return [slots[key].copy() for key in sorted(slots)]

    async def replace_slots(
        guild_id: int, replacement: list[dict[str, object]]
    ) -> None:
        assert guild_id == 100
        slots.clear()
        slots.update({int(row["slot"]): row.copy() for row in replacement})

    monkeypatch.setattr(leaderboards.db, "get_leaderboard_slots", get_slots)
    monkeypatch.setattr(leaderboards.db, "replace_leaderboard_slots", replace_slots)
    return slots


def cache_images(cache_root: Path) -> list[Path]:
    return sorted(cache_root.glob("100/*.png"))


def test_row_fingerprints_exclude_season_metadata_but_header_does_not() -> None:
    channel = FakeChannel()
    service = leaderboards.LeaderboardService(
        FakeBot(FakeGuild(channel, {11})),
        None,
    )
    row = rating(11, "Alpha", 2500)

    season_three = service._desired_slots((2026, 3), [row])
    season_four = service._desired_slots((2026, 4), [row])

    assert season_three[0].fingerprint != season_four[0].fingerprint
    assert season_three[1].fingerprint == season_four[1].fingerprint
    assert leaderboards._aggregate_fingerprint(
        season_three
    ) != leaderboards._aggregate_fingerprint(season_four)


@pytest.mark.asyncio
async def test_first_publish_is_cached_then_an_unchanged_pass_is_a_noop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    channel = FakeChannel()
    service = leaderboards.LeaderboardService(
        FakeBot(FakeGuild(channel, {11, 22})), None, cache_root=tmp_path
    )
    slots = install_slot_store(monkeypatch)
    rows = [rating(11, "Alpha", 2500), rating(99, "Outside", 2400), rating(22, "Beta", 2300)]

    await service.reconcile_guild(config(), (2026, 3), rows)

    assert len(channel.messages) == 1
    assert len(cache_images(tmp_path)) == 1
    assert set(slots) == {0, 1, 2}
    assert "png" not in slots[0]
    message = next(iter(channel.messages.values()))
    assert message.image.startswith(b"\x89PNG\r\n\x1a\n")
    assert slots[0]["message_id"] == message.id
    assert slots[1]["message_id"] is None

    await service.reconcile_guild(config(), (2026, 3), rows)

    assert len(cache_images(tmp_path)) == 1
    assert message.edits == 0


@pytest.mark.asyncio
async def test_one_changed_row_reuses_the_cached_image_and_redraws_that_slot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    channel = FakeChannel()
    service = leaderboards.LeaderboardService(
        FakeBot(FakeGuild(channel, {11})), None, cache_root=tmp_path
    )
    install_slot_store(monkeypatch)
    rows = [rating(11, "Alpha", 2500)]
    await service.reconcile_guild(config(), (2026, 3), rows)

    calls: list[tuple[object, object]] = []
    original_render = leaderboards.build_leaderboard_png

    def observe_render(
        entries: list[LeaderboardEntry],
        current_season: str,
        *,
        base_image: bytes | None = None,
        redraw_slots: set[int] | None = None,
    ) -> bytes:
        calls.append((base_image, redraw_slots))
        return original_render(
            entries,
            current_season,
            base_image=base_image,
            redraw_slots=redraw_slots,
        )

    monkeypatch.setattr(leaderboards, "build_leaderboard_png", observe_render)
    rows[0]["mmr"] = 2600
    await service.reconcile_guild(config(), (2026, 3), rows)

    assert len(calls) == 1
    assert calls[0][0] is not None
    assert 1 in calls[0][1]


@pytest.mark.asyncio
async def test_header_only_change_reuses_the_cached_image(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    channel = FakeChannel()
    service = leaderboards.LeaderboardService(
        FakeBot(FakeGuild(channel, {11})), None, cache_root=tmp_path
    )
    install_slot_store(monkeypatch)
    rows = [rating(11, "Alpha", 2500)]
    await service.reconcile_guild(config(), (2026, 3), rows)

    calls: list[tuple[object, object]] = []
    original_render = leaderboards.build_leaderboard_png

    def observe_render(
        entries: list[LeaderboardEntry],
        current_season: str,
        *,
        base_image: bytes | None = None,
        redraw_slots: set[int] | None = None,
    ) -> bytes:
        calls.append((base_image, redraw_slots))
        return original_render(
            entries,
            current_season,
            base_image=base_image,
            redraw_slots=redraw_slots,
        )

    monkeypatch.setattr(leaderboards, "build_leaderboard_png", observe_render)
    await service.reconcile_guild(config(), (2026, 4), rows)

    assert len(calls) == 1
    assert calls[0][0] is not None
    assert 0 in calls[0][1]


@pytest.mark.asyncio
async def test_entry_count_change_full_redraws_to_the_new_height(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    channel = FakeChannel()
    service = leaderboards.LeaderboardService(
        FakeBot(FakeGuild(channel, {11, 22})), None, cache_root=tmp_path
    )
    install_slot_store(monkeypatch)
    rows = [rating(11, "Alpha", 2500)]
    await service.reconcile_guild(config(), (2026, 3), rows)

    calls: list[object] = []
    original_render = leaderboards.build_leaderboard_png

    def observe_render(
        entries: list[LeaderboardEntry],
        current_season: str,
        *,
        base_image: bytes | None = None,
        redraw_slots: set[int] | None = None,
    ) -> bytes:
        calls.append(base_image)
        return original_render(entries, current_season, base_image=base_image, redraw_slots=redraw_slots)

    monkeypatch.setattr(leaderboards, "build_leaderboard_png", observe_render)
    await service.reconcile_guild(config(), (2026, 3), rows + [rating(22, "Beta", 2400)])

    assert calls == [None]


@pytest.mark.asyncio
async def test_missing_or_corrupt_cache_recovers_with_a_full_redraw(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    channel = FakeChannel()
    service = leaderboards.LeaderboardService(
        FakeBot(FakeGuild(channel, {11})), None, cache_root=tmp_path
    )
    install_slot_store(monkeypatch)
    rows = [rating(11, "Alpha", 2500)]
    await service.reconcile_guild(config(), (2026, 3), rows)
    image_path = cache_images(tmp_path)[0]
    image_path.write_bytes(b"not a png")

    calls: list[object] = []
    original_render = leaderboards.build_leaderboard_png

    def observe_render(
        entries: list[LeaderboardEntry], current_season: str, **kwargs: object
    ) -> bytes:
        calls.append(kwargs.get("base_image"))
        return original_render(entries, current_season, **kwargs)

    monkeypatch.setattr(leaderboards, "build_leaderboard_png", observe_render)
    await service.reconcile_guild(config(), (2026, 3), rows)

    assert calls == [None]
    with Image.open(image_path) as image:
        image.verify()


@pytest.mark.asyncio
async def test_deleted_message_reuploads_the_cached_png_without_rerendering(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    channel = FakeChannel()
    service = leaderboards.LeaderboardService(
        FakeBot(FakeGuild(channel, {11})), None, cache_root=tmp_path
    )
    slots = install_slot_store(monkeypatch)
    rows = [rating(11, "Alpha", 2500)]
    await service.reconcile_guild(config(), (2026, 3), rows)
    channel.messages.clear()

    def unexpected_render(*args: object, **kwargs: object) -> bytes:
        raise AssertionError("cached image should have been reused")

    monkeypatch.setattr(leaderboards, "build_leaderboard_png", unexpected_render)
    await service.reconcile_guild(config(), (2026, 3), rows)

    message = next(iter(channel.messages.values()))
    assert slots[0]["message_id"] == message.id


@pytest.mark.asyncio
async def test_upload_failure_keeps_existing_metadata_and_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    channel = FakeChannel()
    service = leaderboards.LeaderboardService(
        FakeBot(FakeGuild(channel, {11})), None, cache_root=tmp_path
    )
    slots = install_slot_store(monkeypatch)
    rows = [rating(11, "Alpha", 2500)]
    await service.reconcile_guild(config(), (2026, 3), rows)
    original_slots = {slot: row.copy() for slot, row in slots.items()}
    original_images = cache_images(tmp_path)
    channel.fail_send = True
    channel.messages.clear()

    with pytest.raises(RuntimeError, match="upload failed"):
        await service.reconcile_guild(config(), (2026, 3), rows)

    assert slots == original_slots
    assert cache_images(tmp_path) == original_images


@pytest.mark.asyncio
async def test_failed_message_edit_keeps_previous_message_metadata_and_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    channel = FakeChannel()
    service = leaderboards.LeaderboardService(
        FakeBot(FakeGuild(channel, {11})),
        None,
        cache_root=tmp_path,
    )
    slots = install_slot_store(monkeypatch)
    rows = [rating(11, "Alpha", 2500)]
    await service.reconcile_guild(config(), (2026, 3), rows)
    message = next(iter(channel.messages.values()))
    original_message_image = message.image
    original_slots = {slot: row.copy() for slot, row in slots.items()}
    original_cache = cache_images(tmp_path)[0]

    rows[0]["mmr"] = 2600
    channel.fail_edit = True
    with pytest.raises(RuntimeError, match="edit failed"):
        await service.reconcile_guild(config(), (2026, 3), rows)

    assert message.image == original_message_image
    assert slots == original_slots
    assert original_cache.is_file()


@pytest.mark.asyncio
async def test_atomic_cache_write_failure_keeps_existing_message_and_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    channel = FakeChannel()
    service = leaderboards.LeaderboardService(
        FakeBot(FakeGuild(channel, {11})), None, cache_root=tmp_path
    )
    slots = install_slot_store(monkeypatch)

    def fail_atomic_write(path: Path, data: bytes) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(leaderboards, "_write_atomic", fail_atomic_write)
    with pytest.raises(OSError, match="disk full"):
        await service.reconcile_guild(config(), (2026, 3), [rating(11, "Alpha", 2500)])

    assert slots == {}
    assert channel.messages == {}
    assert cache_images(tmp_path) == []


@pytest.mark.asyncio
async def test_successful_reconciliation_cleans_up_legacy_slot_messages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    channel = FakeChannel()
    service = leaderboards.LeaderboardService(
        FakeBot(FakeGuild(channel, {11, 22})), None, cache_root=tmp_path
    )
    slots = install_slot_store(monkeypatch)
    rows = [rating(11, "Alpha", 2500), rating(22, "Beta", 2400)]
    await service.reconcile_guild(config(), (2026, 3), rows)
    legacy = FakeMessage(channel, 2000, b"legacy")
    channel.messages[legacy.id] = legacy
    slots[1]["channel_id"] = channel.id
    slots[1]["message_id"] = legacy.id

    await service.reconcile_guild(config(), (2026, 3), rows)

    assert legacy.id not in channel.messages
    assert slots[1]["channel_id"] is None
    assert slots[1]["message_id"] is None


@pytest.mark.asyncio
async def test_limit_defaults_to_and_never_exceeds_fifty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    channel = FakeChannel()
    member_ids = set(range(1, 60))
    service = leaderboards.LeaderboardService(
        FakeBot(FakeGuild(channel, member_ids)), None, cache_root=tmp_path
    )
    slots = install_slot_store(monkeypatch)
    rows = [rating(discord_id, f"Player {discord_id}", 10_000 - discord_id) for discord_id in member_ids]

    await service.reconcile_guild(config(), (2026, 3), rows)

    assert set(slots) == set(range(51))


@pytest.mark.asyncio
async def test_attachment_size_limit_prevents_upload_and_metadata_replacement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    channel = FakeChannel()
    guild = FakeGuild(channel, {11})
    guild.filesize_limit = 1
    service = leaderboards.LeaderboardService(FakeBot(guild), None, cache_root=tmp_path)
    slots = install_slot_store(monkeypatch)

    with pytest.raises(RuntimeError, match="attachment limit"):
        await service.reconcile_guild(config(), (2026, 3), [rating(11, "Alpha", 2500)])

    assert slots == {}
    assert channel.messages == {}


@pytest.mark.asyncio
async def test_service_liveness_fails_when_an_internal_task_terminates(
    tmp_path: Path,
) -> None:
    service = leaderboards.LeaderboardService(
        FakeBot(FakeGuild(FakeChannel(), set())),
        None,
        cache_root=tmp_path,
    )
    pending = asyncio.create_task(asyncio.sleep(60))

    async def terminate() -> None:
        return None

    terminated = asyncio.create_task(terminate())
    service._tasks = [pending, terminated]
    assert service.is_running() is True
    await terminated
    try:
        assert service.is_running() is False
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
