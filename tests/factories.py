from __future__ import annotations


USER_A_ID = "00000000-0000-0000-0000-000000000001"
USER_B_ID = "00000000-0000-0000-0000-000000000002"


def user_payload(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "id": USER_A_ID,
        "discord_username": "PlayerOne",
        "discord_id": "111111111111111111",
        "member_number": 7,
        "bio": "Frontline player",
        "favorite_champion": "Vive",
        "profile_title": "Duelist",
        "username_changed_at": "2026-01-01T12:00:00+00:00",
        "tournaments_joined": 3,
    }
    data.update(overrides)
    return data


def lobby_payload(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "id": 101,
        "lobby_number": 44,
        "title": "Evening Scrim",
        "lobby_type": "ranked",
        "region": "NA",
        "match_size": 5,
        "team_one_name": "Blue",
        "team_two_name": "Red",
        "creator_id": USER_A_ID,
        "status": "waiting",
        "draft_step": 2,
        "draft_started_at": "2026-01-02T18:15:00+00:00",
        "winner_slot": None,
        "season_year": 2026,
        "season_number": 1,
        "season_name": "Spring",
        "created_at": "2026-01-02T18:00:00+00:00",
        "ended_at": None,
        "match_started_at": "2026-01-02T18:30:00+00:00",
        "dispute_reason": None,
        "winner_confirmed_by_team_one": None,
        "winner_confirmed_by_team_two": None,
        "result_team_one_vote": None,
        "result_team_two_vote": None,
        "discord_match_ready_requested_at": None,
        "discord_match_ready_sent_at": None,
        "discord_match_ready_status": None,
        "discord_match_ready_error": None,
        "mmr_applied": False,
        "ban_count": 1,
        "is_tournament": False,
        "tournament_match_id": None,
        "tournament_name": None,
        "url_year": 2026,
        "url_series": "spring",
        "game_number": 1,
        "has_password": True,
        "map_pool": ["Arena", "Ruins"],
        "selected_map": "Arena",
        "team_one_roster": [USER_A_ID],
        "team_two_roster": [USER_B_ID],
    }
    data.update(overrides)
    return data


def match_payload(**overrides: object) -> dict[str, object]:
    """Return an upstream MATCHES row using its current field names."""
    data = lobby_payload()
    data["match_index"] = data.pop("lobby_number")
    data["match_type"] = data.pop("lobby_type")
    data["match_sequence"] = data.pop("game_number")
    data.update(overrides)
    return data


def season_rating_payload(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "id": 501,
        "user_id": USER_A_ID,
        "season_year": 2026,
        "season_number": 1,
        "mmr": 1999,
        "wins": 8,
        "losses": 4,
        "matches_played": 12,
        "updated_at": "2026-01-03T09:30:00+00:00",
    }
    data.update(overrides)
    return data


def lobby_draft_action_payload(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "id": 901,
        "match_id": 101,
        "step": 3,
        "team_slot": "team_one",
        "action": "pick",
        "champion": "Lucie",
        "created_at": "2026-01-02T18:16:00+00:00",
    }
    data.update(overrides)
    return data


def lobby_captain_payload(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "user_id": USER_A_ID,
        "slot": "team_one",
    }
    data.update(overrides)
    return data


def match_result_confirmation_payload(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "id": 801,
        "match_id": 101,
        "user_id": USER_A_ID,
        "selected_winner": "team_one",
        "created_at": "2026-01-02T19:00:00+00:00",
        "captain_slot": "team_one",
    }
    data.update(overrides)
    return data
