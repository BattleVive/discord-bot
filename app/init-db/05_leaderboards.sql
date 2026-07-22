-- Automatic Discord leaderboard configuration, cache, and change notifications.
-- This file is intentionally idempotent so it can also be applied manually to
-- an existing PostgreSQL volume.
ALTER TABLE guild_config
    ADD COLUMN IF NOT EXISTS leaderboard_limit INTEGER;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'guild_config_leaderboard_limit_positive'
          AND conrelid = 'guild_config'::regclass
    ) THEN
        ALTER TABLE guild_config
            ADD CONSTRAINT guild_config_leaderboard_limit_positive
            CHECK (leaderboard_limit IS NULL OR leaderboard_limit > 0);
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS leaderboard_slots (
    guild_id       BIGINT NOT NULL REFERENCES guild_config (guild_id) ON DELETE CASCADE,
    slot            INTEGER NOT NULL CHECK (slot >= 0),
    channel_id      BIGINT,
    message_id      BIGINT,
    season_year     INTEGER,
    season_number   INTEGER,
    user_id         UUID,
    fingerprint     TEXT NOT NULL,
    png             BYTEA NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (guild_id, slot),
    CHECK ((channel_id IS NULL) = (message_id IS NULL)),
    CHECK (slot <> 0 OR user_id IS NULL)
);

CREATE OR REPLACE FUNCTION notify_leaderboard_changed()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM pg_notify(
        'leaderboard_changed',
        json_build_object(
            'table', TG_TABLE_NAME,
            'operation', TG_OP
        )::text
    );
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS season_ratings_leaderboard_insert ON season_ratings;
CREATE TRIGGER season_ratings_leaderboard_insert
AFTER INSERT ON season_ratings
FOR EACH ROW EXECUTE FUNCTION notify_leaderboard_changed();

DROP TRIGGER IF EXISTS season_ratings_leaderboard_update ON season_ratings;
CREATE TRIGGER season_ratings_leaderboard_update
AFTER UPDATE ON season_ratings
FOR EACH ROW
WHEN (OLD.* IS DISTINCT FROM NEW.*)
EXECUTE FUNCTION notify_leaderboard_changed();

DROP TRIGGER IF EXISTS season_ratings_leaderboard_delete ON season_ratings;
CREATE TRIGGER season_ratings_leaderboard_delete
AFTER DELETE ON season_ratings
FOR EACH ROW EXECUTE FUNCTION notify_leaderboard_changed();

DROP TRIGGER IF EXISTS users_leaderboard_link_update ON users;
CREATE TRIGGER users_leaderboard_link_update
AFTER UPDATE ON users
FOR EACH ROW
WHEN (
    OLD.discord_username IS DISTINCT FROM NEW.discord_username
    OR OLD.discord_id IS DISTINCT FROM NEW.discord_id
)
EXECUTE FUNCTION notify_leaderboard_changed();
