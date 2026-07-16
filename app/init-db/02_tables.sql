-- Mirrors the debloated User dataclass from battlevive.py.
-- id is the upstream Supabase UUID, kept as the primary key so it stays
-- stable across refresh cycles. discord_id is bot-owned local state and
-- must NOT be overwritten by the upstream refresh merge (see main.py).
CREATE TABLE IF NOT EXISTS users (
    id                   UUID PRIMARY KEY,
    discord_username     TEXT NOT NULL,
    discord_id           BIGINT,
    member_number         INTEGER NOT NULL,
    matches_played       INTEGER NOT NULL DEFAULT 0,
    wins                 INTEGER NOT NULL DEFAULT 0,
    losses               INTEGER NOT NULL DEFAULT 0,
    tournaments_joined   INTEGER NOT NULL DEFAULT 0,
    trophies             INTEGER NOT NULL DEFAULT 0,
    bio                  TEXT,
    favorite_champion    TEXT,
    profile_title        TEXT,
    current_mmr          INTEGER NOT NULL DEFAULT 1000,
    peak_mmr             INTEGER NOT NULL DEFAULT 1000,
    total_mmr_delta      INTEGER NOT NULL DEFAULT 0,
    username_changed_at  TIMESTAMPTZ
);

-- Only one Discord account can hold a given user row.
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_discord_id
    ON users (discord_id)
    WHERE discord_id IS NOT NULL;

-- Mirrors the debloated Lobby dataclass. id is the upstream integer id.
CREATE TABLE IF NOT EXISTS lobbies (
    id                                  INTEGER PRIMARY KEY,
    lobby_number                        INTEGER NOT NULL,
    title                               TEXT NOT NULL,
    lobby_type                          TEXT NOT NULL,
    region                               TEXT NOT NULL,
    match_size                          INTEGER NOT NULL,
    team_one_name                       TEXT NOT NULL,
    team_two_name                       TEXT NOT NULL,
    creator_id                          UUID REFERENCES users (id) ON DELETE SET NULL,
    status                               TEXT NOT NULL,
    draft_step                          INTEGER NOT NULL DEFAULT 0,
    draft_started_at                    TIMESTAMPTZ,
    winner_slot                         TEXT,
    season_year                         INTEGER NOT NULL,
    season_number                       INTEGER NOT NULL,
    season_name                         TEXT,
    created_at                          TIMESTAMPTZ NOT NULL,
    ended_at                            TIMESTAMPTZ,
    match_started_at                    TIMESTAMPTZ,
    dispute_reason                      TEXT,
    winner_confirmed_by_team_one        BOOLEAN,
    winner_confirmed_by_team_two        BOOLEAN,
    result_team_one_vote                TEXT,
    result_team_two_vote                TEXT,
    discord_match_ready_requested_at    TIMESTAMPTZ,
    discord_match_ready_sent_at         TIMESTAMPTZ,
    discord_match_ready_status          TEXT,
    discord_match_ready_error           TEXT,
    mmr_applied                         BOOLEAN NOT NULL DEFAULT FALSE,
    ban_count                           INTEGER NOT NULL DEFAULT 0,
    is_tournament                       BOOLEAN NOT NULL DEFAULT FALSE,
    tournament_match_id                 TEXT,
    tournament_name                     TEXT,
    url_year                            INTEGER,
    url_series                          TEXT,
    game_number                         INTEGER,
    has_password                        BOOLEAN NOT NULL DEFAULT FALSE,
    map_pool                            TEXT[],
    selected_map                        TEXT
);

-- team_one_roster / team_two_roster were List[str] fields on the Lobby
-- dataclass. Normalized into a join table instead of a Postgres array
-- column so individual roster entries can be indexed and queried
-- (e.g. "all lobbies a given user played in") without unnesting arrays.
CREATE TABLE IF NOT EXISTS lobby_rosters (
    lobby_id    INTEGER NOT NULL REFERENCES lobbies (id) ON DELETE CASCADE,
    user_id     UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    team        TEXT NOT NULL CHECK (team IN ('team_one', 'team_two')),
    PRIMARY KEY (lobby_id, user_id)
);
