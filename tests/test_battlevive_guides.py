from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Sequence
from datetime import UTC
from datetime import datetime
from types import SimpleNamespace

import aiohttp
import pytest

from battlevive_bot.battlevive.guides import GuideMetadata
from battlevive_bot.battlevive.guides import SupabaseGuideCatalogSource
from battlevive_bot.battlevive.guides import parse_guide_catalog


def guide_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "guide-123",
        "title": "How to draft",
        "created_at": "2026-08-01T09:00:00+00:00",
        "updated_at": "2026-08-02T10:30:00+00:00",
    }
    payload.update(overrides)
    return payload


def response_error(status: int) -> aiohttp.ClientResponseError:
    return aiohttp.ClientResponseError(
        request_info=SimpleNamespace(
            real_url="https://supabase.test/rest/v1/guides"
        ),
        history=(),
        status=status,
        message="request failed",
    )


class FakeGuideTransport:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.calls: list[
            tuple[
                str,
                str,
                Mapping[str, str] | Sequence[tuple[str, str]] | None,
            ]
        ] = []

    async def get(
        self,
        endpoint: str,
        access_token: str,
        *,
        params: Mapping[str, str] | Sequence[tuple[str, str]] | None = None,
    ) -> object:
        self.calls.append((endpoint, access_token, params))
        if isinstance(self.payload, BaseException):
            raise self.payload
        return self.payload


def test_parse_guide_catalog_preserves_identity_timestamp_and_canonical_url() -> None:
    """Would fail if guide IDs, timestamps, or canonical URLs were derived wrongly."""
    guides = parse_guide_catalog(
        [
            guide_payload(),
            guide_payload(
                id="another-guide",
                title="Counterplay",
                updated_at="2026-08-03T13:45:00Z",
            ),
        ]
    )

    assert guides == [
        GuideMetadata(
            source_id="guide-123",
            title="How to draft",
            url="https://battlevive.com/battlerite-guides/guide-123",
            last_modified=datetime(2026, 8, 2, 10, 30, tzinfo=UTC),
        ),
        GuideMetadata(
            source_id="another-guide",
            title="Counterplay",
            url="https://battlevive.com/battlerite-guides/another-guide",
            last_modified=datetime(2026, 8, 3, 13, 45, tzinfo=UTC),
        ),
    ]


def test_parse_guide_catalog_normalizes_numeric_upstream_ids() -> None:
    """Would fail if real integer primary keys were discarded as malformed."""
    guides = parse_guide_catalog([guide_payload(id=42)])

    assert guides[0].source_id == "42"
    assert guides[0].url == "https://battlevive.com/battlerite-guides/42"


def test_parse_guide_catalog_uses_public_guide_number_for_markdown_identity() -> None:
    """Would fail if internal database IDs were sent to the public Markdown API."""
    guides = parse_guide_catalog([guide_payload(id=1004, guide_number=4)])

    assert guides[0].source_id == "4"
    assert guides[0].url == "https://battlevive.com/battlerite-guides/4"


def test_parse_guide_catalog_preserves_champion_for_forum_icon() -> None:
    """Would fail if a guide's champion icon could not be rendered in Discord."""
    guides = parse_guide_catalog([guide_payload(champion="Ezmo")])

    assert guides[0].champion == "Ezmo"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        guide_payload(id=""),
        guide_payload(id=None),
        guide_payload(title=""),
        guide_payload(title=None),
        guide_payload(updated_at=None),
        guide_payload(updated_at="not-a-timestamp"),
        guide_payload(updated_at="2026-08-02T10:30:00"),
        [guide_payload(), "not-a-guide"],
        {"id": "not-an-array"},
    ],
)
def test_parse_guide_catalog_rejects_malformed_normal_endpoint_records(
    payload: object,
) -> None:
    """Would fail if malformed listing data were silently published as a guide."""
    with pytest.raises((KeyError, TypeError, ValueError)):
        parse_guide_catalog(payload)


def test_parse_guide_catalog_rejects_duplicate_stable_ids() -> None:
    """Would fail if two upstream records could overwrite one forum-thread mapping."""
    with pytest.raises(ValueError, match="duplicate"):
        parse_guide_catalog([guide_payload(), guide_payload(title="A duplicate")])


def test_parse_guide_catalog_skips_malformed_rows_without_discarding_valid_guides() -> None:
    """Would fail if one incomplete upstream row prevented guide reconciliation."""
    guides = parse_guide_catalog(
        [guide_payload(), guide_payload(id=None), {"title": "Untitled"}]
    )

    assert guides == [
        GuideMetadata(
            source_id="guide-123",
            title="How to draft",
            url="https://battlevive.com/battlerite-guides/guide-123",
            last_modified=datetime(2026, 8, 2, 10, 30, tzinfo=UTC),
        )
    ]


@pytest.mark.asyncio
async def test_catalog_source_queries_normal_guides_endpoint_with_public_anon_auth() -> None:
    """Would fail if the listing used Markdown, user credentials, or unstable ordering."""
    transport = FakeGuideTransport([guide_payload()])
    source = SupabaseGuideCatalogSource(transport, anon_key="public-anon-key")

    guides = await source.list_guides()

    assert [guide.source_id for guide in guides] == ["guide-123"]
    assert transport.calls == [
        (
            "guides",
            "public-anon-key",
            (
                ("select", "*"),
                ("order", "updated_at.desc.nullslast,created_at.desc"),
            ),
        )
    ]


@pytest.mark.asyncio
async def test_catalog_source_propagates_normal_endpoint_failures() -> None:
    """Would fail if an unavailable listing were treated as an empty website."""
    source = SupabaseGuideCatalogSource(
        FakeGuideTransport(response_error(503)),
        anon_key="public-anon-key",
    )

    with pytest.raises(aiohttp.ClientResponseError, match="request failed") as raised:
        await source.list_guides()

    assert raised.value.status == 503
