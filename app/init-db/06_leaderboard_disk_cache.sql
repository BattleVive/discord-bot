-- Move complete leaderboard PNGs to the persistent app/data volume and cap the
-- configured number of displayed players.
ALTER TABLE leaderboard_slots
    DROP COLUMN IF EXISTS png;

ALTER TABLE guild_config
    DROP CONSTRAINT IF EXISTS guild_config_leaderboard_limit_positive;

UPDATE guild_config
SET leaderboard_limit = 50
WHERE leaderboard_limit > 50;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'guild_config_leaderboard_limit_range'
          AND conrelid = 'guild_config'::regclass
    ) THEN
        ALTER TABLE guild_config
            ADD CONSTRAINT guild_config_leaderboard_limit_range
            CHECK (
                leaderboard_limit IS NULL
                OR leaderboard_limit BETWEEN 1 AND 50
            );
    END IF;
END
$$;
