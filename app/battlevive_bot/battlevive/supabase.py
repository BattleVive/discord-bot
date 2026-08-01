from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Sequence
from urllib.parse import urlsplit

import aiohttp

from .tokens import TokenPair
from ..logs import logger


class SupabaseTransport:
    def __init__(
        self,
        supabase_url: str,
        api_key: str,
        *,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        parsed_url = urlsplit(supabase_url)
        if (
            parsed_url.scheme != "https"
            or not parsed_url.hostname
            or parsed_url.username is not None
            or parsed_url.password is not None
        ):
            raise ValueError("Supabase URL must be a safe HTTPS URL")
        if not api_key:
            raise ValueError("Supabase API key is required")
        self._supabase_url = supabase_url.rstrip("/")
        self._api_key = api_key
        self._session = session
        self._owns_session = session is None
        self._timeout = aiohttp.ClientTimeout(total=15, connect=5)

    async def get(
        self,
        endpoint: str,
        access_token: str,
        *,
        params: Mapping[str, str] | Sequence[tuple[str, str]] | None = None,
    ) -> object:
        session = self._get_session()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "apikey": self._api_key,
            "Content-Type": "application/json",
        }
        url = f"{self._supabase_url}/rest/v1/{endpoint}"

        try:
            async with session.get(
                url,
                headers=headers,
                params=params,
                timeout=self._timeout,
            ) as response:
                if response.status != 200:
                    await response.text()
                    logger.error(
                        "%s request failed with status %s",
                        endpoint,
                        response.status,
                    )
                    response.raise_for_status()
                return await response.json()
        except aiohttp.ClientError as error:
            logger.error("%s request error: %s", endpoint, error)
            raise

    async def refresh(self, refresh_token: str) -> TokenPair:
        session = self._get_session()
        headers = {
            "Content-Type": "application/json",
            "apikey": self._api_key,
        }
        payload = {"refresh_token": refresh_token}
        url = f"{self._supabase_url}/auth/v1/token?grant_type=refresh_token"

        try:
            async with session.post(
                url,
                headers=headers,
                json=payload,
                timeout=self._timeout,
            ) as response:
                if response.status != 200:
                    await response.text()
                    logger.error(
                        "Token refresh failed with status %s",
                        response.status,
                    )
                    response.raise_for_status()
                data = await response.json()
        except aiohttp.ClientError as error:
            logger.error("Token refresh request error: %s", error)
            raise

        if not isinstance(data, dict):
            raise TypeError("Token refresh response must be an object")

        tokens = TokenPair.from_values(
            data.get("access_token"),
            data.get("refresh_token"),
        )
        if tokens is None:
            raise KeyError("Token refresh response is missing a valid token pair")
        return tokens

    async def close(self) -> None:
        if (
            self._owns_session
            and self._session is not None
            and not self._session.closed
        ):
            await self._session.close()

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
            self._owns_session = True
        return self._session
