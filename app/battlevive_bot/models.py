from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
import json
from typing import Any
from typing import ClassVar


def _parse_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _required_str(data: dict[str, Any], key: str) -> str:
    value = data[key]
    if not isinstance(value, str) or not value:
        raise TypeError(f"{key} must be a non-empty string")
    return value


def _required_int(data: dict[str, Any], key: str) -> int:
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{key} must be an integer")
    return value


def _required_id(data: dict[str, Any], key: str) -> int | str:
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, (int, str)) or value == "":
        raise TypeError(f"{key} must be an integer or non-empty string")
    return value


def _parse_collection(
    json_data: str | list[dict[str, Any]],
) -> list[dict[str, Any]]:
    data = json.loads(json_data) if isinstance(json_data, str) else json_data
    if not isinstance(data, list):
        raise TypeError("response must be an array")
    if not all(isinstance(item, dict) for item in data):
        raise TypeError("response entries must be objects")
    return data


def _to_json(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_to_json(item) for item in value]
    if isinstance(value, dict):
        return {key: _to_json(item) for key, item in value.items()}
    return value


@dataclass
class User:
    id: str
    discord_username: str
    discord_id: int | None
    member_number: int
    bio: str | None
    favorite_champion: str | None
    profile_title: str | None
    username_changed_at: datetime | None
    tournaments_joined: int | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> User:
        return cls(
            id=data["id"],
            discord_username=data["discord_username"],
            discord_id=int(data["discord_id"]) if data.get("discord_id") else None,
            member_number=data["member_number"],
            tournaments_joined=data["tournaments_joined"],
            bio=data.get("bio"),
            favorite_champion=data.get("favorite_champion"),
            profile_title=data.get("profile_title"),
            username_changed_at=_parse_dt(data.get("username_changed_at")),
        )

    def json(self) -> dict[str, Any]:
        return _to_json(asdict(self))


@dataclass
class Lobby:
    id: int
    lobby_number: int
    title: str
    lobby_type: str
    region: str
    match_size: int
    team_one_name: str
    team_two_name: str
    creator_id: str | None
    status: str
    draft_step: int
    draft_started_at: datetime | None
    winner_slot: str | None
    season_year: int
    season_number: int
    season_name: str | None
    created_at: datetime
    ended_at: datetime | None
    match_started_at: datetime | None
    dispute_reason: str | None
    winner_confirmed_by_team_one: bool | None
    winner_confirmed_by_team_two: bool | None
    result_team_one_vote: str | None
    result_team_two_vote: str | None
    discord_match_ready_requested_at: datetime | None
    discord_match_ready_sent_at: datetime | None
    discord_match_ready_status: str | None
    discord_match_ready_error: str | None
    mmr_applied: bool
    ban_count: int
    is_tournament: bool
    tournament_match_id: str | None
    tournament_name: str | None
    url_year: int | None
    url_series: str | None
    game_number: int | None
    has_password: bool
    map_pool: list[str] | None
    selected_map: str | None
    team_one_roster: list[str] = field(default_factory=list)
    team_two_roster: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Lobby:
        return cls(
            id=data["id"],
            lobby_number=data["lobby_number"],
            title=data["title"],
            lobby_type=data["lobby_type"],
            region=data["region"],
            match_size=data["match_size"],
            team_one_name=data["team_one_name"],
            team_two_name=data["team_two_name"],
            creator_id=data["creator_id"],
            status=data["status"],
            draft_step=data["draft_step"],
            draft_started_at=_parse_dt(data.get("draft_started_at")),
            winner_slot=data.get("winner_slot"),
            season_year=data["season_year"],
            season_number=data["season_number"],
            season_name=data.get("season_name"),
            created_at=_parse_dt(data["created_at"]),
            ended_at=_parse_dt(data.get("ended_at")),
            match_started_at=_parse_dt(data.get("match_started_at")),
            dispute_reason=data.get("dispute_reason"),
            winner_confirmed_by_team_one=data.get("winner_confirmed_by_team_one"),
            winner_confirmed_by_team_two=data.get("winner_confirmed_by_team_two"),
            result_team_one_vote=data.get("result_team_one_vote"),
            result_team_two_vote=data.get("result_team_two_vote"),
            discord_match_ready_requested_at=_parse_dt(
                data.get("discord_match_ready_requested_at")
            ),
            discord_match_ready_sent_at=_parse_dt(data.get("discord_match_ready_sent_at")),
            discord_match_ready_status=data.get("discord_match_ready_status"),
            discord_match_ready_error=data.get("discord_match_ready_error"),
            mmr_applied=data["mmr_applied"],
            ban_count=data["ban_count"],
            is_tournament=data["is_tournament"],
            tournament_match_id=data.get("tournament_match_id"),
            tournament_name=data.get("tournament_name"),
            url_year=data.get("url_year"),
            url_series=data.get("url_series"),
            game_number=data.get("game_number"),
            has_password=data.get("has_password", False),
            map_pool=data.get("map_pool"),
            selected_map=data.get("selected_map"),
            team_one_roster=data.get("team_one_roster") or [],
            team_two_roster=data.get("team_two_roster") or [],
        )

    def json(self) -> dict[str, Any]:
        return _to_json(asdict(self))


@dataclass(frozen=True)
class LobbyDraftAction:
    id: int | str
    lobby_id: int
    step: int
    team_slot: str
    action: str
    champion: str | None
    created_at: datetime

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LobbyDraftAction:
        champion = data.get("champion")
        if champion is not None and not isinstance(champion, str):
            raise TypeError("champion must be a string or null")
        return cls(
            id=_required_id(data, "id"),
            lobby_id=_required_int(data, "lobby_id"),
            step=_required_int(data, "step"),
            team_slot=_required_str(data, "team_slot"),
            action=_required_str(data, "action"),
            champion=champion,
            created_at=_parse_dt(_required_str(data, "created_at")),
        )

    def json(self) -> dict[str, Any]:
        return _to_json(asdict(self))


@dataclass(frozen=True)
class LobbyCaptain:
    user_id: str
    slot: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LobbyCaptain:
        return cls(
            user_id=_required_str(data, "user_id"),
            slot=_required_str(data, "slot"),
        )

    def json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MatchResultConfirmation:
    id: int | str
    lobby_id: int
    user_id: str
    selected_winner: str
    created_at: datetime
    captain_slot: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MatchResultConfirmation:
        return cls(
            id=_required_id(data, "id"),
            lobby_id=_required_int(data, "lobby_id"),
            user_id=_required_str(data, "user_id"),
            selected_winner=_required_str(data, "selected_winner"),
            created_at=_parse_dt(_required_str(data, "created_at")),
            captain_slot=_required_str(data, "captain_slot"),
        )

    def json(self) -> dict[str, Any]:
        return _to_json(asdict(self))


@dataclass
class SeasonRating:
    id: int
    user_id: str
    season_year: int
    season_number: int
    mmr: int
    wins: int
    losses: int
    matches_played: int
    updated_at: datetime

    RANKS: ClassVar[list[tuple[int, str]]] = [
        (8000, "BATTLEVIVE"),
        (5500, "Diamond"),
        (3500, "Platinum"),
        (2000, "Gold"),
        (1000, "Silver"),
        (0, "Bronze"),
    ]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SeasonRating:
        return cls(
            id=data["id"],
            user_id=data["user_id"],
            season_year=data["season_year"],
            season_number=data["season_number"],
            mmr=data["mmr"],
            wins=data["wins"],
            losses=data["losses"],
            matches_played=data["matches_played"],
            updated_at=_parse_dt(data["updated_at"]),
        )

    @classmethod
    def rank(cls, mmr: int) -> str:
        for threshold, name in cls.RANKS:
            if mmr >= threshold:
                return name
        return "Bronze"

    def json(self) -> dict[str, Any]:
        return _to_json(asdict(self))


# Keep as a thin wrapper until the upstream trophy schema is finalized.
@dataclass
class UserTrophy:
    data: dict[str, Any]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UserTrophy:
        return cls(data=data)

    def json(self) -> dict[str, Any]:
        return _to_json(self.data)


def parse_users(json_data: str | list[dict[str, Any]]) -> list[User]:
    return [User.from_dict(item) for item in _parse_collection(json_data)]


def parse_lobbies(json_data: str | list[dict[str, Any]]) -> list[Lobby]:
    return [Lobby.from_dict(item) for item in _parse_collection(json_data)]


def parse_lobby_draft_actions(
    json_data: str | list[dict[str, Any]],
) -> list[LobbyDraftAction]:
    return [
        LobbyDraftAction.from_dict(item)
        for item in _parse_collection(json_data)
    ]


def parse_lobby_captains(
    json_data: str | list[dict[str, Any]],
) -> list[LobbyCaptain]:
    return [LobbyCaptain.from_dict(item) for item in _parse_collection(json_data)]


def parse_match_result_confirmations(
    json_data: str | list[dict[str, Any]],
) -> list[MatchResultConfirmation]:
    return [
        MatchResultConfirmation.from_dict(item)
        for item in _parse_collection(json_data)
    ]


def parse_season_ratings(json_data: str | list[dict[str, Any]]) -> list[SeasonRating]:
    return [
        SeasonRating.from_dict(item)
        for item in _parse_collection(json_data)
    ]


def parse_user_trophies(json_data: str | list[dict[str, Any]]) -> list[UserTrophy]:
    return [UserTrophy.from_dict(item) for item in _parse_collection(json_data)]
