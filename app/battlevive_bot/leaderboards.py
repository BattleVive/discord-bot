from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

import asyncpg
import discord
from PIL import Image

from . import db
from .images import build_leaderboard_png
from .images import LeaderboardEntry
from .images import LEADERBOARD_ENTRY_H
from .images import LEADERBOARD_HEADER_H
from .images import LEADERBOARD_W
from .logs import logger
from .models import SeasonRating
from .settings import DATA_DIR
from .settings import LEADERBOARD_MAX_ENTRIES


LEADERBOARD_RENDERER_VERSION = 3
DEFAULT_DISCORD_FILESIZE_LIMIT = 10 * 1024 * 1024
NOTIFY_CHANNEL = "leaderboard_changed"


@dataclass(frozen=True, slots=True)
class DesiredSlot:
    slot: int
    season_year: int | None
    season_number: int | None
    user_id: str | None
    fingerprint: str
    entry: LeaderboardEntry | None


def _fingerprint(data: dict[str, Any]) -> str:
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _aggregate_fingerprint(slots: list[DesiredSlot] | list[dict[str, Any]]) -> str:
    """Hash the ordered slot fingerprints into a content-addressed image key."""
    return _fingerprint(
        {
            "slots": [
                slot.fingerprint
                if isinstance(slot, DesiredSlot)
                else str(slot["fingerprint"])
                for slot in slots
            ]
        }
    )


def _win_rate(wins: int, losses: int) -> float:
    matches = wins + losses
    return round(wins / matches * 100) if matches else 0


def _expected_image_size(entry_count: int) -> tuple[int, int]:
    return (
        LEADERBOARD_W,
        LEADERBOARD_HEADER_H + entry_count * LEADERBOARD_ENTRY_H,
    )


def _valid_cache_file(path: Path, expected_size: tuple[int, int]) -> bool:
    try:
        with Image.open(path) as image:
            image.load()
            return image.format == "PNG" and image.size == expected_size
    except (OSError, ValueError):
        return False


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.stem}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _render_cache_file(
    destination: Path,
    entries: list[LeaderboardEntry],
    current_season: str,
    *,
    base_path: Path | None,
    redraw_slots: set[int] | None,
) -> None:
    base_image: Image.Image | None = None
    if base_path is not None:
        with Image.open(base_path) as source:
            source.load()
            base_image = source.convert("RGBA")

    png = build_leaderboard_png(
        entries,
        current_season,
        base_image=base_image,
        redraw_slots=redraw_slots,
    )
    _write_atomic(destination, png)


async def _render_cache_file_async(
    destination: Path,
    entries: list[LeaderboardEntry],
    current_season: str,
    *,
    base_path: Path | None,
    redraw_slots: set[int] | None,
) -> None:
    """Render off-loop without relying on asyncio's shutdown-prone executor."""
    executor = ThreadPoolExecutor(
        max_workers=1,
        thread_name_prefix="leaderboard-render",
    )
    future = executor.submit(
        _render_cache_file,
        destination,
        entries,
        current_season,
        base_path=base_path,
        redraw_slots=redraw_slots,
    )
    try:
        # Polling avoids a Python 3.14 event-loop shutdown hang seen with the
        # default asyncio executor after Pillow work.
        while not future.done():
            await asyncio.sleep(0.001)
        future.result()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


class LeaderboardService:
    """Keep configured Discord leaderboards synchronized with PostgreSQL."""

    def __init__(
        self,
        bot: Any,
        dsn: str | None,
        *,
        reconcile_interval: float = 300,
        debounce_seconds: float = 0.25,
        cache_root: Path | None = None,
    ) -> None:
        self.bot = bot
        self.dsn = dsn
        self.reconcile_interval = reconcile_interval
        self.debounce_seconds = debounce_seconds
        self.cache_root = (
            Path(cache_root)
            if cache_root is not None
            else DATA_DIR / "leaderboards"
        )
        self._requested = asyncio.Event()
        self._closed = asyncio.Event()
        self._reconcile_lock = asyncio.Lock()
        self._tasks: list[asyncio.Task[None]] = []
        self._listener_connection: asyncpg.Connection | None = None

    def start(self) -> None:
        if self._tasks:
            return
        self._closed.clear()
        self._tasks = [
            asyncio.create_task(self._worker(), name="leaderboard-worker"),
            asyncio.create_task(self._listen(), name="leaderboard-listener"),
            asyncio.create_task(self._periodic(), name="leaderboard-periodic"),
        ]
        self.request_reconciliation()

    async def stop(self) -> None:
        if not self._tasks:
            return
        self._closed.set()
        self._requested.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        await self._close_listener()

    def request_reconciliation(self, *_args: object) -> None:
        self._requested.set()

    def is_running(self) -> bool:
        return (
            bool(self._tasks)
            and not self._closed.is_set()
            and all(not task.done() for task in self._tasks)
        )

    async def reconcile_all(self) -> None:
        async with self._reconcile_lock:
            configs = await db.get_configured_leaderboards()
            season, ratings = await db.get_current_leaderboard_ratings()
            for config in configs:
                try:
                    await self.reconcile_guild(config, season, ratings)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "Leaderboard reconciliation failed for guild %s.",
                        config["guild_id"],
                    )

    async def reconcile_guild(
        self,
        config: dict[str, Any],
        season: tuple[int, int] | None = None,
        ratings: list[dict[str, Any]] | None = None,
    ) -> None:
        guild_id = config["guild_id"]
        channel_id = config["leaderboard_channel_id"]
        if channel_id is None:
            return

        if ratings is None:
            season, ratings = await db.get_current_leaderboard_ratings()

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise RuntimeError("configured guild is not available to the bot")
        channel = guild.get_channel(channel_id)
        if channel is None:
            raise RuntimeError("configured leaderboard channel is not available")
        if not self._has_permissions(guild, channel):
            raise RuntimeError(
                "leaderboard channel requires View Channel, Send Messages, "
                "Attach Files, and Read Message History"
            )

        member_ids = {member.id for member in guild.members}
        eligible = [
            row
            for row in ratings
            if row["discord_id"] is not None and row["discord_id"] in member_ids
        ]
        configured_limit = config.get("leaderboard_limit")
        limit = (
            LEADERBOARD_MAX_ENTRIES
            if configured_limit is None
            else min(configured_limit, LEADERBOARD_MAX_ENTRIES)
        )
        eligible = eligible[:limit]

        desired = self._desired_slots(season, eligible)
        desired_entries = [
            slot.entry for slot in desired[1:] if slot.entry is not None
        ]
        expected_size = _expected_image_size(len(desired_entries))
        aggregate = _aggregate_fingerprint(desired)
        destination = self._cache_path(guild_id, aggregate)

        stored = await db.get_leaderboard_slots(guild_id)
        stored_by_slot = {row["slot"]: row for row in stored}
        content_changed = len(stored) != len(desired) or any(
            wanted.slot not in stored_by_slot
            or stored_by_slot[wanted.slot]["fingerprint"] != wanted.fingerprint
            for wanted in desired
        )
        changed_slots = {
            wanted.slot
            for wanted in desired
            if wanted.slot not in stored_by_slot
            or stored_by_slot[wanted.slot]["fingerprint"] != wanted.fingerprint
        }

        primary = stored_by_slot.get(0)
        same_channel = (
            primary is not None
            and primary["channel_id"] == channel_id
            and primary["message_id"] is not None
        )
        obsolete_messages = [
            row
            for row in stored
            if row["channel_id"] is not None
            and row["message_id"] is not None
            and (
                row["slot"] > 0
                or row["channel_id"] != channel_id
            )
        ]
        message = None
        if same_channel:
            try:
                message = await channel.fetch_message(primary["message_id"])
            except discord.NotFound:
                message = None

        cache_valid = _valid_cache_file(destination, expected_size)
        if (
            not content_changed
            and message is not None
            and not obsolete_messages
            and cache_valid
        ):
            return

        cache_rebuilt = False
        if not cache_valid:
            base_path = self._partial_redraw_source(
                guild_id,
                desired,
                stored,
                expected_size,
                changed_slots,
            )
            redraw_slots = changed_slots if base_path is not None else None
            await _render_cache_file_async(
                destination,
                desired_entries,
                self._season_label(desired[0]),
                base_path=base_path,
                redraw_slots=redraw_slots,
            )
            cache_rebuilt = True
            if not _valid_cache_file(destination, expected_size):
                raise RuntimeError("rendered leaderboard cache file is invalid")

        filesize_limit = (
            getattr(guild, "filesize_limit", None)
            or DEFAULT_DISCORD_FILESIZE_LIMIT
        )
        file_size = destination.stat().st_size
        if file_size > filesize_limit:
            raise RuntimeError(
                f"leaderboard image is {file_size} bytes, exceeding this guild's "
                f"{filesize_limit}-byte attachment limit"
            )

        attachment_changed = content_changed or message is None or cache_rebuilt
        if message is None:
            file = self._file(destination)
            try:
                message = await channel.send(file=file)
            finally:
                file.close()
        elif attachment_changed:
            file = self._file(destination)
            try:
                await message.edit(attachments=[file])
            finally:
                file.close()

        metadata = [
            {
                "slot": wanted.slot,
                "channel_id": channel_id if wanted.slot == 0 else None,
                "message_id": message.id if wanted.slot == 0 else None,
                "season_year": wanted.season_year,
                "season_number": wanted.season_number,
                "user_id": wanted.user_id,
                "fingerprint": wanted.fingerprint,
            }
            for wanted in desired
        ]
        await db.replace_leaderboard_slots(guild_id, metadata)

        # Old one-message-per-slot posts are removed only after the replacement
        # metadata has committed successfully.
        for row in reversed(obsolete_messages):
            old_channel = guild.get_channel(row["channel_id"])
            if old_channel is None:
                continue
            if old_channel.id == channel_id and row["message_id"] == message.id:
                continue
            try:
                old_message = await old_channel.fetch_message(row["message_id"])
                await old_message.delete()
            except discord.NotFound:
                pass
            except discord.HTTPException:
                logger.warning(
                    "Could not remove obsolete leaderboard message %s.",
                    row["message_id"],
                    exc_info=True,
                )

        self._prune_cache(guild_id, destination)

    def _desired_slots(
        self,
        season: tuple[int, int] | None,
        ratings: list[dict[str, Any]],
    ) -> list[DesiredSlot]:
        if season is None:
            label = "No active season"
            season_year = None
            season_number = None
        else:
            season_year, season_number = season
            label = f"Season {season_number}"

        result = [
            DesiredSlot(
                slot=0,
                season_year=season_year,
                season_number=season_number,
                user_id=None,
                fingerprint=_fingerprint(
                    {
                        "renderer": LEADERBOARD_RENDERER_VERSION,
                        "type": "header",
                        "season_year": season_year,
                        "season_number": season_number,
                        "label": label,
                    }
                ),
                entry=None,
            )
        ]

        for place, rating in enumerate(
            ratings[:LEADERBOARD_MAX_ENTRIES],
            start=1,
        ):
            wins = rating["wins"]
            losses = rating["losses"]
            entry = LeaderboardEntry(
                place=place,
                username=rating["discord_username"],
                rank=SeasonRating.rank(rating["mmr"]),
                mmr=rating["mmr"],
                wins=wins,
                losses=losses,
                win_rate=_win_rate(wins, losses),
            )
            user_id = str(rating["user_id"])
            result.append(
                DesiredSlot(
                    slot=place,
                    season_year=season_year,
                    season_number=season_number,
                    user_id=user_id,
                    fingerprint=_fingerprint(
                        {
                            "renderer": LEADERBOARD_RENDERER_VERSION,
                            "type": "entry",
                            "place": place,
                            "user_id": user_id,
                            "username": entry.username,
                            "rank": entry.rank,
                            "mmr": entry.mmr,
                            "wins": entry.wins,
                            "losses": entry.losses,
                            "win_rate": entry.win_rate,
                        }
                    ),
                    entry=entry,
                )
            )
        return result

    def _partial_redraw_source(
        self,
        guild_id: int,
        desired: list[DesiredSlot],
        stored: list[dict[str, Any]],
        expected_size: tuple[int, int],
        changed_slots: set[int],
    ) -> Path | None:
        if len(stored) != len(desired):
            return None
        if not changed_slots or changed_slots == set(range(len(desired))):
            return None

        stored_path = self._cache_path(
            guild_id,
            _aggregate_fingerprint(stored),
        )
        return (
            stored_path
            if _valid_cache_file(stored_path, expected_size)
            else None
        )

    def _cache_path(self, guild_id: int, fingerprint: str) -> Path:
        return self.cache_root / str(guild_id) / f"{fingerprint}.png"

    def _prune_cache(self, guild_id: int, keep: Path) -> None:
        directory = self.cache_root / str(guild_id)
        if not directory.is_dir():
            return
        for path in directory.glob("*.png"):
            if path == keep:
                continue
            try:
                path.unlink()
            except OSError:
                logger.warning(
                    "Could not prune obsolete leaderboard image %s.",
                    path,
                    exc_info=True,
                )

    @staticmethod
    def _season_label(header: DesiredSlot) -> str:
        return (
            "No active season"
            if header.season_number is None
            else f"Season {header.season_number}"
        )

    @staticmethod
    def _has_permissions(guild: Any, channel: Any) -> bool:
        if guild.me is None:
            return False
        permissions = channel.permissions_for(guild.me)
        return all(
            (
                permissions.view_channel,
                permissions.send_messages,
                permissions.attach_files,
                permissions.read_message_history,
            )
        )

    @staticmethod
    def _file(path: Path) -> discord.File:
        return discord.File(path, filename="leaderboard.png")

    async def _worker(self) -> None:
        await self.bot.wait_until_ready()
        while not self._closed.is_set():
            await self._requested.wait()
            self._requested.clear()
            await asyncio.sleep(self.debounce_seconds)
            # Notifications during the debounce window belong to this pass. Any
            # arriving during reconciliation remain set for the following pass.
            self._requested.clear()
            try:
                await self.reconcile_all()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Leaderboard reconciliation pass failed.")

    async def _periodic(self) -> None:
        while not self._closed.is_set():
            try:
                await asyncio.wait_for(
                    self._closed.wait(), timeout=self.reconcile_interval
                )
            except TimeoutError:
                self.request_reconciliation()

    async def _listen(self) -> None:
        backoff = 1.0
        while not self._closed.is_set():
            try:
                connection = await asyncpg.connect(
                    dsn=self.dsn,
                    command_timeout=10,
                )
                self._listener_connection = connection
                await connection.add_listener(NOTIFY_CHANNEL, self.request_reconciliation)
                backoff = 1.0
                self.request_reconciliation()
                await self._closed.wait()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Leaderboard database listener disconnected.")
                try:
                    await asyncio.wait_for(self._closed.wait(), timeout=backoff)
                except TimeoutError:
                    pass
                backoff = min(backoff * 2, 30.0)
            finally:
                await self._close_listener()

    async def _close_listener(self) -> None:
        connection = self._listener_connection
        if connection is None:
            return
        self._listener_connection = None
        try:
            await connection.remove_listener(
                NOTIFY_CHANNEL,
                self.request_reconciliation,
            )
        except Exception:
            logger.debug("Could not remove leaderboard listener cleanly.", exc_info=True)
        try:
            await connection.close()
        except Exception:
            logger.debug("Could not close leaderboard listener cleanly.", exc_info=True)
