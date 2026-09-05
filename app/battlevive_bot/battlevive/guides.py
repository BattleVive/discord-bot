from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import aiohttp

from .supabase import SupabaseTransport
from ..logs import logger


GUIDE_PAGE_BASE_URL = "https://battlevive.com/battlerite-guides"


@dataclass(frozen=True, slots=True)
class GuideMetadata:
    """The small, durable portion of a guide needed for reconciliation."""

    source_id: str
    title: str
    url: str
    last_modified: datetime
    champion: str | None = None


class GuideCatalogSource(Protocol):
    async def list_guides(self) -> list[GuideMetadata]:
        """Retrieve the available guide metadata from the catalog source."""
        ...

    async def close(self) -> None:
        """Release resources owned by the catalog source."""
        ...


class GuideContentSource(Protocol):
    async def fetch_markdown(self, source_id: str) -> str:
        """Fetch the Markdown content for a guide."""
        ...

    async def close(self) -> None:
        """Release resources owned by the content source."""
        ...


def _required_text(record: Mapping[str, object], field: str) -> str:
    """
    Extract and trim a required non-empty text field from a guide record.
    
    Parameters:
    	record (Mapping[str, object]): Guide record containing the field.
    	field (str): Name of the required field.
    
    Returns:
    	str: The trimmed field value.
    
    Raises:
    	ValueError: If the field is missing, not a string, or empty after trimming.
    """
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"guide record has no valid {field}")
    return value.strip()


def _required_source_id(record: Mapping[str, object]) -> str:
    """
    Extract the guide identifier used by public URLs and Markdown endpoints.
    
    Parameters:
    	record (Mapping[str, object]): Guide record containing a ``guide_number`` or fallback ``id`` value.
    
    Returns:
    	str: The trimmed guide identifier.
    
    Raises:
    	ValueError: If neither identifier is a non-boolean integer or non-empty string.
    """
    value = record.get("guide_number", record.get("id"))
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ValueError("guide record has no valid guide_number or id")


def _optional_text(record: Mapping[str, object], field: str) -> str | None:
    """
    Return a trimmed optional text field from a guide record.
    
    Parameters:
    	record (Mapping[str, object]): The record containing the field.
    	field (str): The field name to retrieve.
    
    Returns:
    	str | None: The trimmed field value, or `None` when the value is missing, empty, or not a string.
    """
    value = record.get(field)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _parse_timestamp(value: object) -> datetime:
    """
    Parse a timezone-aware timestamp from a guide record.
    
    Parameters:
        value (object): Timestamp value in ISO 8601 string format, including timezone information.
    
    Returns:
        datetime: The parsed timezone-aware timestamp.
    
    Raises:
        ValueError: If the value is not a string, cannot be parsed, or lacks timezone information.
    """
    if not isinstance(value, str):
        raise ValueError("guide record has no valid updated_at")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("guide record updated_at must include a timezone")
    return parsed


def parse_guide_catalog(payload: object) -> list[GuideMetadata]:
    """
    Parse guide catalog data into validated guide metadata.
    
    Parameters:
    	payload (object): Catalog response containing guide records.
    
    Returns:
    	list[GuideMetadata]: Valid guide metadata records.
    
    Raises:
    	TypeError: If the payload or a catalog record is not the expected object or array.
    	ValueError: If duplicate guide identifiers are present.
    """
    if not isinstance(payload, list):
        raise TypeError("guide catalog response must be an array")

    guides: list[GuideMetadata] = []
    seen_ids: set[str] = set()
    for index, record in enumerate(payload):
        if not isinstance(record, Mapping):
            raise TypeError("guide catalog record must be an object")
        try:
            source_id = _required_source_id(record)
            title = _required_text(record, "title")
            last_modified = _parse_timestamp(record.get("updated_at"))
        except (TypeError, ValueError):
            logger.warning("Skipping malformed guide catalog record at index %s.", index)
            continue
        if source_id in seen_ids:
            raise ValueError("guide catalog contains duplicate guide id")
        seen_ids.add(source_id)
        guides.append(
            GuideMetadata(
                source_id=source_id,
                title=title,
                url=f"{GUIDE_PAGE_BASE_URL}/{source_id}",
                last_modified=last_modified,
                champion=_optional_text(record, "champion"),
            )
        )
    return guides


class SupabaseGuideCatalogSource:
    """Read public guide metadata using the same normal listing as the site."""

    def __init__(self, transport: SupabaseTransport, *, anon_key: str) -> None:
        """Initialize a guide catalog source with a Supabase transport and anonymous key.
        
        Parameters:
        	transport (SupabaseTransport): Transport used to access Supabase.
        	anon_key (str): Supabase anonymous access key.
        
        Raises:
        	ValueError: If the anonymous key is empty.
        """
        if not anon_key:
            raise ValueError("Supabase anonymous key is required")
        self._transport = transport
        self._anon_key = anon_key

    async def list_guides(self) -> list[GuideMetadata]:
        """Retrieve and validate the available guide metadata.
        
        Returns:
        	list[GuideMetadata]: The parsed guide metadata records.
        """
        payload = await self._transport.get(
            "guides",
            self._anon_key,
            params=(
                ("select", "*"),
                ("order", "updated_at.desc.nullslast,created_at.desc"),
            ),
        )
        return parse_guide_catalog(payload)

    async def close(self) -> None:
        """Close the transport owned by this catalog source."""
        await self._transport.close()


class HttpGuideContentSource:
    """Fetch raw guide Markdown only when a thread must be published or updated."""

    def __init__(self, base_url: str = "https://battlevive.com") -> None:
        """Initialize an HTTP guide content source with the specified base URL.
        
        Parameters:
        	base_url (str): Base URL used for guide content requests.
        """
        self._base_url = base_url.rstrip("/")
        self._session: aiohttp.ClientSession | None = None

    async def fetch_markdown(self, source_id: str) -> str:
        """
        Fetch the Markdown content for a guide.
        
        Parameters:
            source_id (str): Identifier of the guide whose content should be fetched.
        
        Returns:
            str: The guide's Markdown content.
        """
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
        """Close the active HTTP session, if one exists."""
        if self._session is not None and not self._session.closed:
            await self._session.close()
