from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import aiohttp

from .supabase import SupabaseTransport


GUIDE_PAGE_BASE_URL = "https://battlevive.com/battlerite-guides"


@dataclass(frozen=True, slots=True)
class GuideMetadata:
    """The small, durable portion of a guide needed for reconciliation."""

    source_id: str
    title: str
    url: str
    last_modified: datetime


class GuideCatalogSource(Protocol):
    async def list_guides(self) -> list[GuideMetadata]: ...


class GuideContentSource(Protocol):
    async def fetch_markdown(self, source_id: str) -> str: ...


def _required_text(record: Mapping[str, object], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"guide record has no valid {field}")
    return value.strip()


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("guide record has no valid updated_at")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("guide record updated_at must include a timezone")
    return parsed


def parse_guide_catalog(payload: object) -> list[GuideMetadata]:
    """Validate guide-list rows before they can affect Discord state."""
    if not isinstance(payload, list):
        raise TypeError("guide catalog response must be an array")

    guides: list[GuideMetadata] = []
    seen_ids: set[str] = set()
    for record in payload:
        if not isinstance(record, Mapping):
            raise TypeError("guide catalog record must be an object")
        source_id = _required_text(record, "id")
        if source_id in seen_ids:
            raise ValueError("guide catalog contains duplicate guide id")
        seen_ids.add(source_id)
        title = _required_text(record, "title")
        guides.append(
            GuideMetadata(
                source_id=source_id,
                title=title,
                url=f"{GUIDE_PAGE_BASE_URL}/{source_id}",
                last_modified=_parse_timestamp(record.get("updated_at")),
            )
        )
    return guides


class SupabaseGuideCatalogSource:
    """Read public guide metadata using the same normal listing as the site."""

    def __init__(self, transport: SupabaseTransport, *, anon_key: str) -> None:
        if not anon_key:
            raise ValueError("Supabase anonymous key is required")
        self._transport = transport
        self._anon_key = anon_key

    async def list_guides(self) -> list[GuideMetadata]:
        payload = await self._transport.get(
            "guides",
            self._anon_key,
            params=(
                ("select", "*"),
                ("order", "updated_at.desc.nullslast,created_at.desc"),
            ),
        )
        return parse_guide_catalog(payload)


class HttpGuideContentSource:
    """Fetch raw guide Markdown only when a thread must be published or updated."""

    def __init__(self, base_url: str = "https://battlevive.com") -> None:
        self._base_url = base_url.rstrip("/")
        self._session: aiohttp.ClientSession | None = None

    async def fetch_markdown(self, source_id: str) -> str:
        if not source_id:
            raise ValueError("guide ID is required")
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15, connect=5))
        async with self._session.get(f"{self._base_url}/api/guides/{source_id}/markdown") as response:
            response.raise_for_status()
            markdown = await response.text()
        if not markdown:
            raise ValueError("guide Markdown response is empty")
        return markdown

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
