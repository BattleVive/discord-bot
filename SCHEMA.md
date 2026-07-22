```mermaid
erDiagram
    users ||--o{ season_ratings : has
    users ||--o{ lobbies : creates
    users ||--o{ lobby_rosters : plays_in
    lobbies ||--o{ lobby_rosters : has_roster
    guild_config {
        BIGINT guild_id PK
        BIGINT leaderboard_channel_id "nullable"
        TIMESTAMPTZ updated_at
        BIGINT updated_by "nullable Discord user ID"
    }

    users {
        UUID id PK
        TEXT discord_username
        BIGINT discord_id UK "nullable, resolved locally by the bot"
        INTEGER member_number
        INTEGER tournaments_joined
        TEXT bio
        TEXT favorite_champion
        TEXT profile_title
        TIMESTAMPTZ username_changed_at
    }

    season_ratings {
        BIGINT id PK
        UUID user_id FK
        INTEGER season_year
        INTEGER season_number
        INTEGER mmr
        INTEGER wins
        INTEGER losses
        INTEGER matches_played
        TIMESTAMPTZ updated_at
    }

    lobbies {
        INTEGER id PK
        INTEGER lobby_number
        TEXT title
        TEXT lobby_type
        TEXT region
        INTEGER match_size
        TEXT team_one_name
        TEXT team_two_name
        UUID creator_id FK
        TEXT status
        INTEGER draft_step
        TIMESTAMPTZ draft_started_at
        TEXT winner_slot
        INTEGER season_year
        INTEGER season_number
        TEXT season_name
        TIMESTAMPTZ created_at
        TIMESTAMPTZ ended_at
        TIMESTAMPTZ match_started_at
        TEXT dispute_reason
        BOOLEAN winner_confirmed_by_team_one
        BOOLEAN winner_confirmed_by_team_two
        TEXT result_team_one_vote
        TEXT result_team_two_vote
        TIMESTAMPTZ discord_match_ready_requested_at
        TIMESTAMPTZ discord_match_ready_sent_at
        TEXT discord_match_ready_status
        TEXT discord_match_ready_error
        BOOLEAN mmr_applied
        INTEGER ban_count
        BOOLEAN is_tournament
        TEXT tournament_match_id
        TEXT tournament_name
        INTEGER url_year
        TEXT url_series
        INTEGER game_number
        BOOLEAN has_password
        TEXT_ARRAY map_pool
        TEXT selected_map
    }

    lobby_rosters {
        INTEGER lobby_id PK,FK
        UUID user_id PK,FK
        TEXT team "team_one or team_two"
    }
```

## Notes

- `users.id`, `lobbies.id`, and `season_ratings.id` are upstream Supabase primary keys, kept as-is.
- `users.discord_id` is **bot-owned local state**, not part of the upstream payload. Sync logic must never overwrite it.
- `season_ratings` is a separate upstream endpoint (`/season_ratings`), one row per user per season – `UNIQUE (user_id, season_year, season_number)`.
- `team_one_roster` / `team_two_roster` from the upstream `Lobby` payload are normalized into `lobby_rosters` rather than kept as array columns.
- Write order matters: `users` must be synced before `lobbies` and `season_ratings`, since both foreign-key into it.
- `guild_config` stores one row per Discord server. A nullable `leaderboard_channel_id` means the leaderboard destination is unset; the table does not yet track published messages.
- `updated_by` stores the Discord user ID that last changed the configuration and may be null for older/manual rows.
- Leaderboard publishing and active-lobbies message-tracking tables are not implemented yet.
