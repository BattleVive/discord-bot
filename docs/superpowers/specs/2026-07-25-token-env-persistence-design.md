# Token Env Persistence Design

## Goal

Persist the latest Battlevive Supabase JWT and refresh token into the project root `.env` when the bot shuts down, so container restarts can reuse valid tokens without running `utils/env_gen.py` every time.

## Architecture

The bot already keeps current tokens in the module-level `battlevive_tokens` manager and refreshes them every 30 minutes. Docker Compose currently injects `.env` values through `env_file`, which does not make the file writable inside the container. The implementation will mount the root `.env` at a stable path in the bot container and add a small persistence helper that updates only `BOOTSTRAP_JWT` and `BOOTSTRAP_REFRESH_TOKEN`.

## Components

- `docker-compose.yml`: mount root `.env` into the bot container at `/app/.env` with read/write access.
- `app/battlevive_bot/settings.py`: expose `ENV_FILE_PATH`, defaulting to `/app/.env` unless overridden.
- `app/battlevive_bot/token_persistence.py`: write current tokens to `ENV_FILE_PATH` without logging token values.
- `app/battlevive_bot/bot.py`: call the persistence helper in `BattleviveBot.close()` before closing the database pool and Discord client.
- `tests/test_token_persistence.py`: verify only token keys are updated and unrelated `.env` values are preserved.

## Data Flow

1. Startup reads bootstrap tokens from environment as it does today.
2. The existing `revalidate_tokens` loop updates the in-memory JWT and refresh token.
3. During bot shutdown, `BattleviveBot.close()` saves the current in-memory token pair to the mounted `.env` file.
4. On next container start, Compose reads the updated `.env` and injects fresh values.

## Error Handling

If either token is missing or the `.env` update fails, shutdown continues and logs an error or warning without printing secret values. The bot should not crash during shutdown solely because persistence failed.

## Testing

Unit tests will use temporary `.env` files and synthetic token values. They will not read or print real secrets. Validation will run targeted pytest tests and Python compile checks.
