-- Forum-thread guide publication state. Guide bodies are intentionally never persisted.

ALTER TABLE guild_config
    ADD COLUMN IF NOT EXISTS guide_forum_channel_id BIGINT,
    ADD COLUMN IF NOT EXISTS guide_notification_role_id BIGINT,
    ADD COLUMN IF NOT EXISTS guide_auto_delete_on_removal BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE guild_created_roles
    DROP CONSTRAINT IF EXISTS guild_created_roles_purpose_check;
ALTER TABLE guild_created_roles
    ADD CONSTRAINT guild_created_roles_purpose_check
    CHECK (purpose IN ('active_lobby', 'website_moderator', 'guide_updates'));

CREATE TABLE IF NOT EXISTS guide_threads (
    guild_id          BIGINT NOT NULL REFERENCES guild_config(guild_id) ON DELETE CASCADE,
    source_guide_id   TEXT NOT NULL,
    thread_id         BIGINT NOT NULL,
    source_updated_at TIMESTAMPTZ NOT NULL,
    managed_message_ids BIGINT[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (guild_id, source_guide_id),
    UNIQUE (guild_id, thread_id)
);
