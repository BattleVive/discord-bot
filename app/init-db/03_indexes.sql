-- Only one Discord account can hold a given user row.
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_discord_id
    ON users (discord_id)
    WHERE discord_id IS NOT NULL;

-- Fallback lookup path when discord_id is not yet resolved.
CREATE INDEX IF NOT EXISTS idx_users_discord_username
    ON users (discord_username);

-- Current season leaderboard.
CREATE INDEX IF NOT EXISTS idx_season_ratings_leaderboard
    ON season_ratings (season_year, season_number, mmr DESC);

-- All seasons for a user.
CREATE INDEX IF NOT EXISTS idx_season_ratings_user
    ON season_ratings (user_id);

-- User's rating for a specific season.
CREATE UNIQUE INDEX IF NOT EXISTS idx_season_ratings_user_season
    ON season_ratings (user_id, season_year, season_number);

-- Active lobbies channel: filter by status, most recent first.
CREATE INDEX IF NOT EXISTS idx_lobbies_status_created_at
    ON lobbies (status, created_at DESC);

-- Season-scoped queries.
CREATE INDEX IF NOT EXISTS idx_lobbies_season
    ON lobbies (season_year, season_number);

-- All lobbies a user played in.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'lobby_rosters'
          AND column_name = 'user_id'
    ) THEN
        CREATE INDEX IF NOT EXISTS idx_lobby_rosters_user_id
            ON lobby_rosters (user_id);
    END IF;
END
$$;
