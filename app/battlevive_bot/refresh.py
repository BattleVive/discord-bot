from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import time

from . import db
from .db import MissingUsersError
from .logs import logger
from .models import Lobby, SeasonRating, User


@dataclass(slots=True)
class RefreshResult:
    users: list[User] | None = None
    lobbies: list[Lobby] | None = None
    ratings: list[SeasonRating] | None = None
    deleted_user_ids: list[object] | None = None
    coalesced: bool = False


class RefreshCoordinator:
    def __init__(
        self,
        client: object,
        on_success: Callable[[RefreshResult], None] | None = None,
    ) -> None:
        self.client = client
        self.on_success = on_success
        self.lock = asyncio.Lock()
        self.last_users_ratings_refresh = float("-inf")

    def _publish(self, result: RefreshResult) -> RefreshResult:
        if result.deleted_user_ids:
            logger.info(
                "Removed %d users absent from the authoritative snapshot: %s",
                len(result.deleted_user_ids),
                ", ".join(str(user_id) for user_id in result.deleted_user_ids),
            )
        if self.on_success is not None:
            self.on_success(result)
        return result

    async def hourly_users_refresh(self) -> RefreshResult:
        async with self.lock:
            users = await self.client.get_users()
            deleted = await db.sync_users_to_db(users)
            return self._publish(RefreshResult(users=users, deleted_user_ids=deleted))

    async def frequent_lobbies_ratings_refresh(self) -> RefreshResult:
        async with self.lock:
            lobbies, ratings = await asyncio.gather(
                self.client.get_lobbies(), self.client.get_season_ratings()
            )
            try:
                await db.sync_battlevive_data_to_db(
                    lobbies=lobbies, season_ratings=ratings
                )
            except MissingUsersError:
                users = await self.client.get_users()
                authoritative_ids = db._validated_user_ids(users)
                deleted = await db.sync_users_to_db(users)
                clean_lobbies = db.sanitize_lobbies(lobbies, authoritative_ids)
                clean_ratings = db.sanitize_season_ratings(ratings, authoritative_ids)
                try:
                    await db.sync_battlevive_data_to_db(
                        lobbies=clean_lobbies, season_ratings=clean_ratings
                    )
                except Exception:
                    logger.exception(
                        "Dependent refresh retry failed; retaining prior valid "
                        "state where possible."
                    )
                    raise
                self.last_users_ratings_refresh = time.monotonic()
                return self._publish(
                    RefreshResult(users, clean_lobbies, clean_ratings, deleted)
                )
            return self._publish(RefreshResult(lobbies=lobbies, ratings=ratings))

    async def users_and_ratings_refresh(
        self,
        local_check: Callable[[], Awaitable[bool]] | None = None,
    ) -> RefreshResult:
        async with self.lock:
            if local_check is not None and await local_check():
                return RefreshResult(coalesced=True)
            now = time.monotonic()
            if now - self.last_users_ratings_refresh < 30:
                return RefreshResult(coalesced=True)
            users, ratings = await asyncio.gather(
                self.client.get_users(), self.client.get_season_ratings()
            )
            authoritative_ids = db._validated_user_ids(users)
            deleted = await db.sync_users_to_db(users)
            ratings = db.sanitize_season_ratings(ratings, authoritative_ids)
            await db.sync_season_ratings_to_db(ratings)
            self.last_users_ratings_refresh = time.monotonic()
            return self._publish(
                RefreshResult(
                    users=users,
                    ratings=ratings,
                    deleted_user_ids=deleted,
                )
            )

    async def full_manual_refresh(self) -> RefreshResult:
        async with self.lock:
            users, lobbies, ratings = await asyncio.gather(
                self.client.get_users(),
                self.client.get_lobbies(),
                self.client.get_season_ratings(),
            )
            authoritative_ids = db._validated_user_ids(users)
            clean_lobbies = db.sanitize_lobbies(lobbies, authoritative_ids)
            clean_ratings = db.sanitize_season_ratings(ratings, authoritative_ids)
            deleted = await db.sync_battlevive_data_to_db(
                users,
                clean_lobbies,
                clean_ratings,
            )
            self.last_users_ratings_refresh = time.monotonic()
            return self._publish(
                RefreshResult(users, clean_lobbies, clean_ratings, deleted)
            )
