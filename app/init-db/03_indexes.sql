-- Leaderboard sort (current MMR, descending).
CREATE INDEX IF NOT EXISTS idx_users_current_mmr ON users (current_mmr DESC);

-- Fallback lookup path when discord_id is not yet resolved.
CREATE INDEX IF NOT EXISTS idx_users_discord_username ON users (discord_username);

-- Active lobbies channel: filter by status, most recent first.
CREATE INDEX IF NOT EXISTS idx_lobbies_status_created_at ON lobbies (status, created_at DESC);

-- Season-scoped queries (e.g. current season leaderboard/lobby list).
CREATE INDEX IF NOT EXISTS idx_lobbies_season ON lobbies (season_year, season_number);

-- "All lobbies a user played in" lookup.
CREATE INDEX IF NOT EXISTS idx_lobby_rosters_user_id ON lobby_rosters (user_id);
