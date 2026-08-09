-- Identity recovery, public-command controls, and generated-role ownership.
-- This migration is safe to apply repeatedly and retains users.discord_id for
-- rollback compatibility with older bot images.

CREATE TABLE IF NOT EXISTS user_discord_links (
    user_id      UUID PRIMARY KEY,
    discord_id   BIGINT NOT NULL UNIQUE,
    linked_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO user_discord_links (user_id, discord_id)
SELECT id, discord_id
FROM users
WHERE discord_id IS NOT NULL
ON CONFLICT DO NOTHING;

ALTER TABLE guild_config
    ADD COLUMN IF NOT EXISTS rank_cooldown_seconds INTEGER NOT NULL DEFAULT 20;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'guild_config'::regclass
          AND conname = 'guild_config_rank_cooldown_range'
    ) THEN
        ALTER TABLE guild_config
            ADD CONSTRAINT guild_config_rank_cooldown_range
            CHECK (rank_cooldown_seconds BETWEEN 0 AND 3600);
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS guild_command_channels (
    guild_id    BIGINT NOT NULL REFERENCES guild_config(guild_id) ON DELETE CASCADE,
    channel_id  BIGINT NOT NULL,
    rule        TEXT NOT NULL CHECK (rule IN ('allow', 'block')),
    updated_by  BIGINT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (guild_id, channel_id)
);

CREATE TABLE IF NOT EXISTS guild_created_roles (
    guild_id    BIGINT NOT NULL REFERENCES guild_config(guild_id) ON DELETE CASCADE,
    purpose     TEXT NOT NULL CHECK (purpose IN ('active_lobby', 'website_moderator')),
    role_id     BIGINT NOT NULL,
    created_by  BIGINT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (guild_id, purpose),
    UNIQUE (guild_id, role_id)
);
