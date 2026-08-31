from __future__ import annotations

from datetime import datetime
from datetime import timezone
import json

import pytest

from battlevive_bot.models import SeasonRating
from battlevive_bot.models import UserTrophy
from battlevive_bot.models import parse_lobbies
from battlevive_bot.models import parse_lobby_captains
from battlevive_bot.models import parse_lobby_draft_actions
from battlevive_bot.models import parse_lobby_roster_members
from battlevive_bot.models import parse_match_result_confirmations
from battlevive_bot.models import parse_season_ratings
from battlevive_bot.models import parse_user_trophies
from battlevive_bot.models import parse_users
from tests.factories import lobby_captain_payload
from tests.factories import lobby_draft_action_payload
from tests.factories import match_payload
from tests.factories import match_result_confirmation_payload
from tests.factories import season_rating_payload
from tests.factories import user_payload


def test_model_parsers_accept_json_and_serialize_datetimes() -> None:
    user = parse_users(json.dumps([user_payload()]))[0]
    assert user.discord_id == 111111111111111111
    assert user.username_changed_at == datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    assert user.json()["username_changed_at"] == "2026-01-01T12:00:00+00:00"

    lobby = parse_lobbies(json.dumps([match_payload()]))[0]
    assert lobby.creator_id == "00000000-0000-0000-0000-000000000001"
    assert lobby.lobby_number == 44
    assert lobby.lobby_type == "ranked"
    assert lobby.game_number == 44
    assert lobby.team_one_roster == ["00000000-0000-0000-0000-000000000001"]
    assert lobby.team_two_roster == ["00000000-0000-0000-0000-000000000002"]
    assert lobby.json()["created_at"] == "2026-01-02T18:00:00+00:00"
    assert lobby.json()["map_pool"] == ["Arena", "Ruins"]

    fallback = parse_lobbies([match_payload(match_index=None)])[0]
    assert fallback.game_number == 1

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


def test_active_lobby_models_parse_minimal_payloads() -> None:
    draft = parse_lobby_draft_actions([lobby_draft_action_payload()])[0]
    assert draft.lobby_id == 101
    assert draft.step == 3
    assert draft.champion == "Lucie"
    assert draft.json()["created_at"] == "2026-01-02T18:16:00+00:00"

    captain = parse_lobby_captains([lobby_captain_payload()])[0]
    assert captain.json() == {
        "user_id": "00000000-0000-0000-0000-000000000001",
        "slot": "team_one",
    }

    roster_member = parse_lobby_roster_members(
        [{"match_id": 101, "user_id": user_payload()["id"], "slot": "team_two"}]
    )[0]
    assert roster_member.json() == {
        "lobby_id": 101,
        "user_id": user_payload()["id"],
        "slot": "team_two",
    }

    confirmation = parse_match_result_confirmations(
        [match_result_confirmation_payload()]
    )[0]
    assert confirmation.selected_winner == "team_one"
    assert confirmation.captain_slot == "team_one"
    assert confirmation.json()["created_at"] == "2026-01-02T19:00:00+00:00"


@pytest.mark.parametrize(
    ("parser", "payload"),
    [
        (parse_lobby_draft_actions, {"id": True}),
        (
            parse_lobby_draft_actions,
            lobby_draft_action_payload(team_slot="spectator"),
        ),
        (
            parse_lobby_draft_actions,
            lobby_draft_action_payload(action="trade"),
        ),
        (parse_lobby_captains, {"user_id": None, "slot": "team_one"}),
        (
            parse_lobby_roster_members,
            {"match_id": 101, "user_id": "user", "slot": "spectator"},
        ),
        (
            parse_lobby_captains,
            lobby_captain_payload(slot="captain"),
        ),
        (
            parse_match_result_confirmations,
            match_result_confirmation_payload(selected_winner="draw"),
        ),
        (
            parse_match_result_confirmations,
            match_result_confirmation_payload(captain_slot=None),
        ),
        (
            parse_match_result_confirmations,
            match_result_confirmation_payload(captain_slot="team_three"),
        ),
    ],
)
def test_active_lobby_models_reject_malformed_payloads(
    parser: object,
    payload: dict[str, object],
) -> None:
    with pytest.raises((KeyError, TypeError, ValueError)):
        parser([payload])


def test_invalid_active_lobby_entry_rejects_the_complete_response() -> None:
    payload = [
        lobby_draft_action_payload(id=1),
        lobby_draft_action_payload(id=2, action="trade"),
    ]

    with pytest.raises(ValueError, match="action"):
        parse_lobby_draft_actions(payload)


@pytest.mark.parametrize(
    "parser",
    [
        parse_lobby_draft_actions,
        parse_lobby_captains,
        parse_lobby_roster_members,
        parse_match_result_confirmations,
    ],
)
def test_active_lobby_models_require_array_responses(parser: object) -> None:
    with pytest.raises(TypeError, match="array"):
        parser({"error": "unexpected"})


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
