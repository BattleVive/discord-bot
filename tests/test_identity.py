from __future__ import annotations

from types import SimpleNamespace

import pytest

from battlevive_gateway.identity import matching_member
from battlevive_gateway.identity import save_guild_identity_links


def test_identity_matching_prefers_exact_account_name_over_display_name() -> None:
    exact = SimpleNamespace(id=1, name="Alpha", display_name="Else", global_name=None, nick=None)
    display = SimpleNamespace(id=2, name="Different", display_name="Alpha", global_name=None, nick=None)

    assert matching_member([exact, display], "alpha") is exact


@pytest.mark.asyncio
async def test_identity_saving_binds_member_number_only_for_a_unique_guild_member() -> None:
    member = SimpleNamespace(id=99, name="Alpha", display_name="Alpha", global_name=None, nick=None)
    guild = SimpleNamespace(members=[member])
    bindings: list[tuple[int, int, str]] = []

    class Identities:
        async def bind(self, member_number: int, discord_id: int, provenance: str) -> bool:
            bindings.append((member_number, discord_id, provenance))
            return True

    saved = await save_guild_identity_links(
        guild, [{"member_number": 7, "player": "Alpha"}], Identities()
    )

    assert saved == 1
    assert bindings == [(7, 99, "guild-member-name")]
