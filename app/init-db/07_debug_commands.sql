-- Debug data exports are opt-in per Discord server.
ALTER TABLE guild_config
    ADD COLUMN IF NOT EXISTS debug_commands_enabled BOOLEAN NOT NULL DEFAULT FALSE;
