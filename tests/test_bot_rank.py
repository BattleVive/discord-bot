from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

import pytest


discord = pytest.importorskip("discord")
pytest.importorskip("asyncpg")

from PIL import Image

from battlevive_bot import bot as bot_module


class FakeAvatar:
    def __init__(self) -> None:
        output = BytesIO()
        Image.new("RGB", (128, 128), (20, 80, 140)).save(output, format="PNG")
        self.data = output.getvalue()

    def with_size(self, size: int) -> FakeAvatar:
        assert size == 128
        return self

    async def read(self) -> bytes:
        return self.data


class FakeResponse:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def send_message(self, **kwargs: object) -> None:
        self.messages.append(kwargs)

    def is_done(self) -> bool:
        return bool(self.messages)


class FakePool:
    async def fetchrow(self, query: str, discord_id: int) -> dict[str, object]:
        assert "INNER JOIN season_ratings" in query
        assert discord_id == 12345
        return {
            "discord_id": discord_id,
            "discord_username": "PlayerOne",
            "member_number": 42,
            "mmr": 1500,
            "wins": 12,
            "losses": 8,
        }


@pytest.mark.asyncio
async def test_rank_builds_a_fresh_in_memory_card_on_every_invocation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    build_calls: list[dict[str, object]] = []

    def fake_build_card(**kwargs: object) -> bytes:
        build_calls.append(kwargs)
        return b"fresh-png-" + str(len(build_calls)).encode()

    monkeypatch.setattr(bot_module, "get_pool", lambda: FakePool())
    monkeypatch.setattr(bot_module, "build_card", fake_build_card)
    monkeypatch.setattr(bot_module, "DATA_DIR", tmp_path)

    response = FakeResponse()
    user = SimpleNamespace(
        id=12345,
        name="player_account",
        display_name="Player One",
        display_avatar=FakeAvatar(),
    )
    interaction = SimpleNamespace(user=user, response=response)

    await bot_module.rank_command.callback(interaction)
    await bot_module.rank_command.callback(interaction)

    assert len(build_calls) == 2
    assert len(response.messages) == 2
    assert not list(tmp_path.iterdir())
    for message in response.messages:
        assert set(message) == {"file"}
        attachment = message["file"]
        assert attachment.filename == "profile.png"
        assert not isinstance(attachment.fp, (str, bytes))
