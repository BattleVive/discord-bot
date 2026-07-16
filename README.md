# WIP discord bot for battlevive website 

# preperation
requires browser and manual login, do this on client and manualy copy .env for prod
```bash
cd utils
```
fill up .env.example
```bash
mv .env.example .env
chmod +x init.sh
./init.sh
cd ..
```
# run
```bash
docker compose build
docker compose up -d
```
or 
```bash
podman compose build
podman compose up -d
```
# Schema

```mermaid
erDiagram
    users ||--o{ lobbies : creates
    users ||--o{ lobby_rosters : plays_in
    lobbies ||--o{ lobby_rosters : has_roster

    users {
        UUID id PK
        TEXT discord_username
        BIGINT discord_id UK "nullable, resolved locally by the bot"
        INTEGER member_number
        INTEGER matches_played
        INTEGER wins
        INTEGER losses
        INTEGER tournaments_joined
        INTEGER trophies
        TEXT bio
        TEXT favorite_champion
        TEXT profile_title
        INTEGER current_mmr
        INTEGER peak_mmr
        INTEGER total_mmr_delta
        TIMESTAMPTZ username_changed_at
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

- `users.id` and `lobbies.id` are upstream Supabase primary keys, kept as-is so joins and refresh-merges don't need a translation layer.
- `users.discord_id` is **bot-owned local state**, not part of the upstream payload. The refresh loop (`main.py`) must `UPDATE ... SET discord_username = ..., current_mmr = ...` etc. per-column, and never blanket-overwrite `discord_id` – see the earlier discussion on `refresh_loop` replacing `battlevive_users` wholesale.
- `team_one_roster` / `team_two_roster` (originally `List[str]` on the `Lobby` dataclass) are normalized into `lobby_rosters` rather than kept as Postgres array columns, so a query like "every lobby a given user played in" is a plain indexed join instead of an array-unnest scan.
- Not included yet, out of scope for this pass: guild config table (admin config command), leaderboard/active-lobbies message-tracking tables (channel ID → message ID). Add these once those commands are actually being built, per the earlier storage-design discussion.



