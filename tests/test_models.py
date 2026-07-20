from __future__ import annotations

from datetime import datetime
from datetime import timezone
import json

import pytest

from battlevive_bot.models import SeasonRating
from battlevive_bot.models import UserTrophy
from battlevive_bot.models import parse_lobbies
from battlevive_bot.models import parse_season_ratings
from battlevive_bot.models import parse_user_trophies
from battlevive_bot.models import parse_users
from tests.factories import lobby_payload
from tests.factories import season_rating_payload
from tests.factories import user_payload


def test_model_parsers_accept_json_and_serialize_datetimes() -> None:
    user = parse_users(json.dumps([user_payload()]))[0]
    assert user.discord_id == 111111111111111111
    assert user.username_changed_at == datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    assert user.json()["username_changed_at"] == "2026-01-01T12:00:00+00:00"

    lobby = parse_lobbies(json.dumps([lobby_payload()]))[0]
    assert lobby.creator_id == "00000000-0000-0000-0000-000000000001"
    assert lobby.team_one_roster == ["00000000-0000-0000-0000-000000000001"]
    assert lobby.team_two_roster == ["00000000-0000-0000-0000-000000000002"]
    assert lobby.json()["created_at"] == "2026-01-02T18:00:00+00:00"
    assert lobby.json()["map_pool"] == ["Arena", "Ruins"]

    rating = parse_season_ratings(json.dumps([season_rating_payload()]))[0]
    assert rating.updated_at == datetime(2026, 1, 3, 9, 30, tzinfo=timezone.utc)
    assert rating.json()["updated_at"] == "2026-01-03T09:30:00+00:00"

    trophy = parse_user_trophies(json.dumps([{"kind": "champion", "points": 10}]))[0]
    assert trophy.json() == {"kind": "champion", "points": 10}


def test_user_trophy_json_serializes_nested_datetimes() -> None:
    earned_at = datetime(2026, 1, 4, 10, 0, tzinfo=timezone.utc)
    trophy = UserTrophy.from_dict({"earned_at": earned_at, "history": [earned_at]})

    assert trophy.json() == {
        "earned_at": "2026-01-04T10:00:00+00:00",
        "history": ["2026-01-04T10:00:00+00:00"],
    }


@pytest.mark.parametrize(
    ("mmr", "rank"),
    [
        (-1, "Bronze"),
        (0, "Bronze"),
        (999, "Bronze"),
        (1000, "Silver"),
        (1999, "Silver"),
        (2000, "Gold"),
        (3499, "Gold"),
        (3500, "Platinum"),
        (5499, "Platinum"),
        (5500, "Diamond"),
        (7999, "Diamond"),
        (8000, "BATTLEVIVE"),
    ],
)
def test_rank_threshold_boundaries(mmr: int, rank: str) -> None:
    assert SeasonRating.rank(mmr) == rank
