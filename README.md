# Battlevive Discord Bot

Discord bot that syncs player/lobby data from the Battlevive platform (via Supabase) and manages roles, ranks, and match info in Discord.

[![Test Suite](https://github.com/voxix-dev/battlevive-bot/actions/workflows/tests.yml/badge.svg)](https://github.com/voxix-dev/battlevive-bot/actions/workflows/tests.yml) [![Docker Image CI](https://github.com/voxix-dev/battlevive-bot/actions/workflows/docker-image.yml/badge.svg)](https://github.com/voxix-dev/battlevive-bot/actions/workflows/docker-image.yml) [![CodeQL Advanced](https://github.com/voxix-dev/battlevive-bot/actions/workflows/codeql.yml/badge.svg)](https://github.com/voxix-dev/battlevive-bot/actions/workflows/codeql.yml)

## Requirements

- Docker or Podman (with compose support)
- A Discord bot token
- Battlevive/Supabase API credentials

## Setup

```
cd utils
cp .env.example .env
```

Fill in the values in `.env`, then run:

```
./init.sh
```

This installs the helper script's dependencies and runs it to generate/finalize your `.env`. Follow its prompts before continuing.

## Running

From the repo root, after `utils/init.sh` has completed:

**Docker**

```
docker compose up -d --build
```

**Podman**

```
podman compose up -d --build --no-deps bot
```

This builds and recreates only the bot service; it assumes the database service
is already running. Runtime files under `app/data/` (including the PostgreSQL
data directory) are excluded from the build context, and the database container
and data are not recreated or removed. For a build without restarting the bot,
use `podman compose build bot`. `podman compose` may print that it is using an
external Compose provider; that is normal for Podman.

If `podman-compose` is installed and preferred, the equivalent commands are
`podman-compose build bot` and `podman-compose up -d --no-deps bot`.

For a standalone build, use `podman build -t battlevive-bot:local .`. Compose
uses the same image tag, so the next `podman compose up -d` will use that build.

Check logs with `docker compose logs -f bot` / `podman compose logs -f bot`.

## Database schema

See [SCHEMA.md](./SCHEMA.md).

When upgrading an existing PostgreSQL volume, the container's initialization
scripts are not rerun automatically. Apply the new guild configuration schema
once as a database administrator:

```
psql "$DATABASE_URL" -f app/init-db/04_guild_config.sql
```

Fresh databases receive this table automatically during initialization.

---
This project is licensed under the AGPL-3.0 (see LICENSE).

## Third-party assets

- Liberation Mono 2.1.5 is bundled under the SIL Open Font License 1.1. The
  original license and copyright notice are in
  [`app/assets/fonts/LiberationMono-LICENSE.txt`](./app/assets/fonts/LICENSE).
  Upstream project and official archive:
  [liberationfonts/liberation-fonts](https://github.com/liberationfonts/liberation-fonts),
  [Liberation Fonts 2.1.5 TTF download](https://github.com/liberationfonts/liberation-fonts/files/7261482/liberation-fonts-ttf-2.1.5.tar.gz).


This project is under active development.
