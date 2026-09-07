from __future__ import annotations

import inspect
import pytest

from battlevive_gateway.guides import Guide
from battlevive_gateway.guides import GuideReconciler
from battlevive_gateway.guides import GuidePublication
from battlevive_gateway.guides import champion_icon_url
from battlevive_gateway.guides import normalize_discord_markdown


class Publications:
    def __init__(self) -> None:
        self.saved: list[tuple[object, ...]] = []
        self.deleted: list[tuple[int, str, str]] = []

    async def list_for_feature(self, _: int, __: str) -> list[dict[str, object]]:
        return [{"publication_key": "guide:2", "thread_id": 22}]

    async def upsert(self, *args: object) -> None:
        self.saved.append(args)

    async def delete(self, guild_id: int, feature: str, key: str) -> None:
        self.deleted.append((guild_id, feature, key))


class Discord:
    def __init__(self) -> None:
        self.created: list[Guide] = []
        self.archived: list[int] = []

    async def create_or_update(self, guide: Guide, existing: object) -> GuidePublication:
        self.created.append(guide)
        return GuidePublication(existing.get("thread_id", 11) if isinstance(existing, dict) else 11, (11, 12))

    async def archive(self, thread_id: int) -> None:
        self.archived.append(thread_id)


@pytest.mark.asyncio
async def test_guide_reconciliation_creates_and_archives_removed_guides() -> None:
    publications, discord = Publications(), Discord()
    await GuideReconciler(publications, discord).reconcile(7, [Guide(1, "One", "# One")])
    assert discord.created == [Guide(1, "One", "# One")]
    assert discord.archived == [22]
    assert publications.deleted == [(7, "guide", "guide:2")]
    assert publications.saved[0][-1]["message_ids"] == [11, 12]


def test_guide_markdown_keeps_code_and_replaces_battlerite_images_with_emojis() -> None:
    markdown = (
        "![](https://battlevive.com/images/champions/battlerites/Varesh/Varesh_Mouse1_Control.png)**Silence**\n"
        "```\n![](https://battlevive.com/images/champions/battlerites/Varesh/Varesh_E_Offense.png)\n```\n---\n"
    )

    assert normalize_discord_markdown(
        markdown, {"varesh_mouse1_control": "<:varesh_mouse1_control:42>"}
    ) == (
        "<:varesh_mouse1_control:42> **Silence**\n"
        "```\n![](https://battlevive.com/images/champions/battlerites/Varesh/Varesh_E_Offense.png)\n```"
    )


def test_guide_champion_thumbnail_preserves_special_champion_spelling() -> None:
    assert champion_icon_url("Shen Rao") == "https://battlevive.com/images/champions/icons/Shen-Rao.png"


def test_fresh_guide_installation_requires_tracked_publications() -> None:
    source = inspect.getsource(__import__("battlevive_gateway.guides", fromlist=["DiscordGuidePublisher"]).DiscordGuidePublisher._replace)
    assert "history" not in source
    assert "metadata is missing" in source
