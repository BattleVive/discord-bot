#!/usr/bin/env python3
from __future__ import annotations

import time
from typing import Callable
from typing import TypeVar

import aiohttp
import requests

from .logs import logger
from .models import Lobby
from .models import SeasonRating
from .models import User
from .models import UserTrophy
from .models import parse_lobbies
from .models import parse_season_ratings
from .models import parse_user_trophies
from .models import parse_users
from .settings import SUPABASE_API_KEY
from .settings import SUPABASE_URL


ParsedT = TypeVar("ParsedT")
Parser = Callable[[object], list[ParsedT]]


class BattleviveTokenManager:
    def __init__(self, JWT_token: str | None, refresh_token: str | None):
        self.JWT_token = JWT_token
        self.refresh_token = refresh_token

    @staticmethod
    def revalidate(refresh_token: str | None) -> tuple[str, str]:
        headers = {
            "Content-Type": "application/json",
            "apikey": SUPABASE_API_KEY,
        }
        payload = {
            "refresh_token": refresh_token,
        }
        logger.debug(
            "Revalidate request prepared (header keys: %s, payload keys: %s)",
            list(headers.keys()),
            list(payload.keys()),
        )

        try:
            response = requests.post(
                f"{SUPABASE_URL}/auth/v1/token?grant_type=refresh_token",
                headers=headers,
                json=payload,
                timeout=10,
            )
            logger.debug(
                "Revalidate response received (status: %s, header keys: %s)",
                response.status_code,
                list(response.headers.keys()),
            )
            response.raise_for_status()
        except requests.exceptions.Timeout as error:
            logger.error("Token refresh timed out: %s", error)
            raise
        except requests.exceptions.ConnectionError as error:
            logger.error("Token refresh connection error: %s", error)
            raise
        except requests.exceptions.HTTPError:
            logger.error(
                "Token refresh failed with status %s",
                response.status_code,
            )
            raise
        except requests.exceptions.RequestException as error:
            logger.error("Token refresh request failed: %s", error)
            raise

        try:
            data = response.json()
        except ValueError as error:
            logger.error("Token refresh response was not valid JSON: %s", error)
            raise

        try:
            new_refresh_token = data["refresh_token"]
            new_access_token = data["access_token"]
        except KeyError as error:
            logger.error("Token refresh response missing expected field %s", error)
            raise

        logger.info("Revalidated tokens")
        return new_refresh_token, new_access_token


async def _fetch_and_parse(
    session: aiohttp.ClientSession,
    JWT_token: str | None,
    endpoint: str,
    parser: Parser[ParsedT],
) -> list[ParsedT]:
    headers = {
        "Authorization": f"Bearer {JWT_token}",
        "apikey": SUPABASE_API_KEY,
        "Content-Type": "application/json",
    }
    url = f"{SUPABASE_URL}/rest/v1/{endpoint}"

    logger.debug("Fetching %s from %s", endpoint, url)
    start = time.perf_counter()

    try:
        async with session.get(url, headers=headers) as response:
            if response.status != 200:
                body = await response.text()
                logger.error(
                    "%s request failed: status=%s body=%s",
                    endpoint,
                    response.status,
                    body,
                )
                response.raise_for_status()
            raw = await response.json()
    except aiohttp.ClientError as error:
        logger.error("%s request error: %s", endpoint, error)
        raise

    try:
        result = parser(raw)
    except (KeyError, TypeError, ValueError) as error:
        logger.error("Failed to parse %s response: %s", endpoint, error)
        raise

    elapsed = time.perf_counter() - start
    logger.info("Fetched %d %s in %.2fs", len(result), endpoint, elapsed)
    return result


async def query_users(JWT_token: str | None) -> list[User]:
    async with aiohttp.ClientSession() as session:
        return await _fetch_and_parse(session, JWT_token, "users", parse_users)


async def query_lobbies(JWT_token: str | None) -> list[Lobby]:
    async with aiohttp.ClientSession() as session:
        return await _fetch_and_parse(session, JWT_token, "lobbies", parse_lobbies)


async def query_season_ratings(JWT_token: str | None) -> list[SeasonRating]:
    async with aiohttp.ClientSession() as session:
        return await _fetch_and_parse(
            session,
            JWT_token,
            "season_ratings",
            parse_season_ratings,
        )


async def query_user_trophies(JWT_token: str | None) -> list[UserTrophy]:
    async with aiohttp.ClientSession() as session:
        return await _fetch_and_parse(
            session,
            JWT_token,
            "user_trophies",
            parse_user_trophies,
        )
