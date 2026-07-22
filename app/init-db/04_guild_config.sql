CREATE TABLE IF NOT EXISTS guild_config (
    guild_id                BIGINT PRIMARY KEY,
    leaderboard_channel_id  BIGINT,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by              BIGINT
);
