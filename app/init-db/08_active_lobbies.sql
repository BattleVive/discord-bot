-- Active-lobby Discord posts, team-level draft state, and change notifications.
-- This migration is intentionally idempotent so operators can apply it to an
-- existing volume as well as through a fresh numbered-schema installation.

DO $$
BEGIN
    IF to_regclass('public.lobby_rosters') IS NOT NULL
       AND EXISTS (
           SELECT 1
           FROM information_schema.columns
           WHERE table_schema = 'public'
             AND table_name = 'lobby_rosters'
             AND column_name = 'user_id'
       )
       AND to_regclass('public.lobby_rosters_legacy_08') IS NULL
    THEN
        ALTER TABLE lobby_rosters RENAME TO lobby_rosters_legacy_08;
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS lobby_rosters (
    lobby_id             INTEGER NOT NULL REFERENCES lobbies (id) ON DELETE CASCADE,
    team                 TEXT NOT NULL CHECK (team IN ('team_one', 'team_two')),
    captain_id           UUID REFERENCES users (id) ON DELETE SET NULL,
    roster               UUID[] NOT NULL DEFAULT ARRAY[]::UUID[],
    picks                TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    bans                 TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    draft_finalized_at   TIMESTAMPTZ,
    PRIMARY KEY (lobby_id, team)
);

DO $$
BEGIN
    IF to_regclass('public.lobby_rosters_legacy_08') IS NOT NULL THEN
        INSERT INTO lobby_rosters (lobby_id, team, captain_id, roster)
        SELECT
            lobby_id,
            team,
            NULL,
            array_agg(user_id ORDER BY user_id)
        FROM lobby_rosters_legacy_08
        GROUP BY lobby_id, team
        ON CONFLICT (lobby_id, team) DO NOTHING;

        DROP TABLE lobby_rosters_legacy_08;
    END IF;
END
$$;

DROP INDEX IF EXISTS idx_lobby_rosters_user_id;
CREATE INDEX IF NOT EXISTS idx_lobby_rosters_roster
    ON lobby_rosters USING GIN (roster);

ALTER TABLE guild_config
    ADD COLUMN IF NOT EXISTS active_lobby_channel_id BIGINT,
    ADD COLUMN IF NOT EXISTS active_lobby_role_id BIGINT;

CREATE TABLE IF NOT EXISTS active_lobby_posts (
    guild_id               BIGINT NOT NULL REFERENCES guild_config (guild_id) ON DELETE CASCADE,
    lobby_id               INTEGER NOT NULL REFERENCES lobbies (id) ON DELETE CASCADE,
    channel_id             BIGINT,
    message_id             BIGINT,
    fingerprint            TEXT,
    first_seen_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    notification_handled   BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (guild_id, lobby_id),
    CHECK ((channel_id IS NULL) = (message_id IS NULL))
);

CREATE INDEX IF NOT EXISTS idx_active_lobby_posts_message
    ON active_lobby_posts (guild_id, channel_id, message_id)
    WHERE message_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS active_lobby_empty_posts (
    guild_id       BIGINT PRIMARY KEY REFERENCES guild_config (guild_id) ON DELETE CASCADE,
    channel_id     BIGINT,
    message_id     BIGINT,
    fingerprint    TEXT,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((channel_id IS NULL) = (message_id IS NULL))
);

CREATE OR REPLACE FUNCTION notify_active_lobbies_changed()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM pg_notify(
        'active_lobby_changed',
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

DROP TRIGGER IF EXISTS lobbies_active_lobby_insert ON lobbies;
CREATE TRIGGER lobbies_active_lobby_insert
AFTER INSERT ON lobbies
FOR EACH ROW EXECUTE FUNCTION notify_active_lobbies_changed();

DROP TRIGGER IF EXISTS lobbies_active_lobby_update ON lobbies;
CREATE TRIGGER lobbies_active_lobby_update
AFTER UPDATE ON lobbies
FOR EACH ROW
WHEN (OLD.* IS DISTINCT FROM NEW.*)
EXECUTE FUNCTION notify_active_lobbies_changed();

DROP TRIGGER IF EXISTS lobbies_active_lobby_delete ON lobbies;
CREATE TRIGGER lobbies_active_lobby_delete
AFTER DELETE ON lobbies
FOR EACH ROW EXECUTE FUNCTION notify_active_lobbies_changed();

DROP TRIGGER IF EXISTS users_active_lobby_insert ON users;
CREATE TRIGGER users_active_lobby_insert
AFTER INSERT ON users
FOR EACH ROW EXECUTE FUNCTION notify_active_lobbies_changed();

DROP TRIGGER IF EXISTS users_active_lobby_update ON users;
CREATE TRIGGER users_active_lobby_update
AFTER UPDATE ON users
FOR EACH ROW
WHEN (
    OLD.discord_username IS DISTINCT FROM NEW.discord_username
    OR OLD.discord_id IS DISTINCT FROM NEW.discord_id
)
EXECUTE FUNCTION notify_active_lobbies_changed();

DROP TRIGGER IF EXISTS users_active_lobby_delete ON users;
CREATE TRIGGER users_active_lobby_delete
AFTER DELETE ON users
FOR EACH ROW EXECUTE FUNCTION notify_active_lobbies_changed();

DROP TRIGGER IF EXISTS lobby_rosters_active_lobby_insert ON lobby_rosters;
CREATE TRIGGER lobby_rosters_active_lobby_insert
AFTER INSERT ON lobby_rosters
FOR EACH ROW EXECUTE FUNCTION notify_active_lobbies_changed();

DROP TRIGGER IF EXISTS lobby_rosters_active_lobby_update ON lobby_rosters;
CREATE TRIGGER lobby_rosters_active_lobby_update
AFTER UPDATE ON lobby_rosters
FOR EACH ROW
WHEN (OLD.* IS DISTINCT FROM NEW.*)
EXECUTE FUNCTION notify_active_lobbies_changed();

DROP TRIGGER IF EXISTS lobby_rosters_active_lobby_delete ON lobby_rosters;
CREATE TRIGGER lobby_rosters_active_lobby_delete
AFTER DELETE ON lobby_rosters
FOR EACH ROW EXECUTE FUNCTION notify_active_lobbies_changed();

DROP TRIGGER IF EXISTS guild_config_active_lobby_insert ON guild_config;
CREATE TRIGGER guild_config_active_lobby_insert
AFTER INSERT ON guild_config
FOR EACH ROW
WHEN (
    NEW.active_lobby_channel_id IS NOT NULL
    OR NEW.active_lobby_role_id IS NOT NULL
)
EXECUTE FUNCTION notify_active_lobbies_changed();

DROP TRIGGER IF EXISTS guild_config_active_lobby_update ON guild_config;
CREATE TRIGGER guild_config_active_lobby_update
AFTER UPDATE ON guild_config
FOR EACH ROW
WHEN (
    OLD.active_lobby_channel_id IS DISTINCT FROM NEW.active_lobby_channel_id
    OR OLD.active_lobby_role_id IS DISTINCT FROM NEW.active_lobby_role_id
)
EXECUTE FUNCTION notify_active_lobbies_changed();

DROP TRIGGER IF EXISTS guild_config_active_lobby_delete ON guild_config;
CREATE TRIGGER guild_config_active_lobby_delete
AFTER DELETE ON guild_config
FOR EACH ROW
WHEN (
    OLD.active_lobby_channel_id IS NOT NULL
    OR OLD.active_lobby_role_id IS NOT NULL
)
EXECUTE FUNCTION notify_active_lobbies_changed();
