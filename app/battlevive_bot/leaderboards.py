from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
from io import BytesIO
import json
from typing import Any

import asyncpg
import discord
from PIL import Image

from . import db
from .images import LeaderboardEntry
from .images import build_leaderboard_header
from .images import build_leaderboard_images
from .logs import logger
from .models import SeasonRating


LEADERBOARD_RENDERER_VERSION = 1
NOTIFY_CHANNEL = "leaderboard_changed"


@dataclass(slots=True)
class DesiredSlot:
    slot: int
    season_year: int | None
    season_number: int | None
    user_id: str | None
    fingerprint: str
    entry: LeaderboardEntry | None
    png: bytes | None = None


def _fingerprint(data: dict[str, Any]) -> str:
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _win_rate(wins: int, losses: int) -> float:
    matches = wins + losses
    return round(wins / matches * 100) if matches else 0


def _combine_leaderboard_pngs(pngs: list[bytes]) -> bytes:
    """Stack the cached header and entry cards into one Discord attachment."""
    images: list[Image.Image] = []
    try:
        for png in pngs:
            with Image.open(BytesIO(png)) as source:
                images.append(source.convert("RGB"))
        width = max(image.width for image in images)
        height = sum(image.height for image in images)
        combined = Image.new("RGB", (width, height))
        y = 0
        for image in images:
            combined.paste(image, (0, y))
            y += image.height
        output = BytesIO()
        combined.save(output, format="PNG", optimize=True)
        return output.getvalue()
    finally:
        for image in images:
            image.close()


class LeaderboardService:
    """Keep configured Discord leaderboards synchronized with PostgreSQL."""

    def __init__(
        self,
        bot: Any,
        dsn: str | None,
        *,
        reconcile_interval: float = 300,
        debounce_seconds: float = 0.25,
    ) -> None:
        self.bot = bot
        self.dsn = dsn
        self.reconcile_interval = reconcile_interval
        self.debounce_seconds = debounce_seconds
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
        limit = config.get("leaderboard_limit")
        if limit is not None:
            eligible = eligible[:limit]

        desired = self._desired_slots(season, eligible)
        stored = await db.get_leaderboard_slots(guild_id)
        stored_by_slot = {row["slot"]: row for row in stored}
        await self._render_changed(desired, stored_by_slot)

        content_changed = len(stored) != len(desired) or any(
            wanted.slot not in stored_by_slot
            or stored_by_slot[wanted.slot]["fingerprint"] != wanted.fingerprint
            for wanted in desired
        )
        primary = stored_by_slot.get(0)
        same_channel = (
            primary is not None
            and primary["channel_id"] == channel_id
            and primary["message_id"] is not None
        )
        legacy_messages = [
            row
            for row in stored
            if row["slot"] > 0
            and row["channel_id"] == channel_id
            and row["message_id"] is not None
        ]
        message = None
        if same_channel:
            try:
                message = await channel.fetch_message(primary["message_id"])
            except discord.NotFound:
                message = None

        if not content_changed and message is not None and not legacy_messages:
            return

        combined_png = await asyncio.to_thread(
            _combine_leaderboard_pngs,
            [self._required_png(wanted) for wanted in desired],
        )
        if message is None:
            message = await channel.send(file=self._file(combined_png))
        else:
            await message.edit(attachments=[self._file(combined_png)])

        # Collapse leaderboards created by the previous one-message-per-slot
        # implementation only after the combined message succeeds.
        for row in reversed(legacy_messages):
            try:
                old_message = await channel.fetch_message(row["message_id"])
                await old_message.delete()
            except discord.NotFound:
                pass

        for wanted in desired:
            await db.upsert_leaderboard_slot(
                guild_id,
                wanted.slot,
                channel_id if wanted.slot == 0 else None,
                message.id if wanted.slot == 0 else None,
                wanted.season_year,
                wanted.season_number,
                wanted.user_id,
                wanted.fingerprint,
                self._required_png(wanted),
            )

        desired_count = len(desired)
        if any(row["slot"] >= desired_count for row in stored):
            await db.delete_leaderboard_slots_from(guild_id, desired_count)

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

        for place, rating in enumerate(ratings, start=1):
            wins = rating["wins"]
            losses = rating["losses"]
            win_rate = _win_rate(wins, losses)
            rank = SeasonRating.rank(rating["mmr"])
            entry = LeaderboardEntry(
                place=place,
                username=rating["discord_username"],
                rank=rank,
                mmr=rating["mmr"],
                wins=wins,
                losses=losses,
                win_rate=win_rate,
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
                            "season_year": season_year,
                            "season_number": season_number,
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

    async def _render_changed(
        self,
        desired: list[DesiredSlot],
        stored: dict[int, dict[str, Any]],
    ) -> None:
        changed = [
            wanted
            for wanted in desired
            if wanted.slot not in stored
            or stored[wanted.slot]["fingerprint"] != wanted.fingerprint
        ]
        for wanted in desired:
            old = stored.get(wanted.slot)
            if old is not None and old["fingerprint"] == wanted.fingerprint:
                wanted.png = bytes(old["png"])

        header = next((wanted for wanted in changed if wanted.slot == 0), None)
        entries = [wanted for wanted in changed if wanted.entry is not None]
        tasks: list[Any] = []
        if header is not None:
            label = (
                "No active season"
                if header.season_number is None
                else f"Season {header.season_number}"
            )
            tasks.append(asyncio.to_thread(build_leaderboard_header, label))
        if entries:
            current_season = (
                "No active season"
                if desired[0].season_number is None
                else f"Season {desired[0].season_number}"
            )
            tasks.append(
                build_leaderboard_images(
                    [wanted.entry for wanted in entries if wanted.entry is not None],
                    current_season,
                )
            )
        if not tasks:
            return

        rendered = await asyncio.gather(*tasks)
        result_index = 0
        if header is not None:
            header.png = rendered[result_index]
            result_index += 1
        if entries:
            for wanted, png in zip(entries, rendered[result_index], strict=True):
                wanted.png = png

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
    def _file(png: bytes) -> discord.File:
        return discord.File(BytesIO(png), filename="leaderboard.png")

    @staticmethod
    def _required_png(slot: DesiredSlot) -> bytes:
        if slot.png is None:
            raise RuntimeError(f"leaderboard slot {slot.slot} has no rendered image")
        return slot.png

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
