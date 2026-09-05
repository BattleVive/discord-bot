# Battlevive Discord Bot

Discord bot that syncs player, lobby, season rating, and leaderboard data from Battlevive/Supabase and manages Battlevive roles, ranks, and match info in Discord.

[![Tests](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2FBattleVive%2Fdiscord-bot%2Fbadges%2Ftests.json)](https://github.com/BattleVive/discord-bot/actions/workflows/tests.yml) [![Stable Release](https://github.com/BattleVive/discord-bot/actions/workflows/release.yml/badge.svg)](https://github.com/BattleVive/discord-bot/actions/workflows/release.yml) [![CodeQL Advanced](https://github.com/BattleVive/discord-bot/actions/workflows/codeql.yml/badge.svg)](https://github.com/BattleVive/discord-bot/actions/workflows/codeql.yml)

This project is under active development.

## Documentation

Full documentation is published at [battlevive-bot.voxix.workers.dev/docs](https://battlevive-bot.voxix.workers.dev/docs):

- [Commands](https://battlevive-bot.voxix.workers.dev/docs/commands)
- [Configuration](https://battlevive-bot.voxix.workers.dev/docs/configuration)
- [Deployment](https://battlevive-bot.voxix.workers.dev/docs/deployment)
- [Development](https://battlevive-bot.voxix.workers.dev/docs/development)
- [Database](https://battlevive-bot.voxix.workers.dev/docs/database)
- [Troubleshooting](https://battlevive-bot.voxix.workers.dev/docs/troubleshooting)


## Requirements

- Docker or Podman with Compose support
- A Discord bot token
- Battlevive/Supabase API credentials

## Setup

```bash
cd utils
cp .env.example .env
# fill required values in .env
./init.sh
```

## Running the published image

From the repository root after `utils/init.sh` completes:

```bash
docker compose pull
docker compose up -d
```

or:

```bash
podman compose pull
podman compose up -d
```

Check logs with `docker compose logs -f bot` or `podman compose logs -f bot`.

The default Compose file uses the public
[`voxix/battlevive-bot`](https://hub.docker.com/r/voxix/battlevive-bot) image. The
`latest` and immutable `sha-<full-commit-sha>` tags support Linux AMD64 and
ARM64.

## Local source build

`docker-compose.dev.yml` is an override, not a standalone Compose file. It
keeps the database, volumes, networks, health checks, and migration service
from `docker-compose.yml`, then builds the bot source once as
`battlevive-bot:local` instead of pulling it from Docker Hub.

After creating `utils/.env`, run both files together from the repository root
with rootless Podman:

```bash
podman compose --env-file utils/.env \
  -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

Do not run `docker-compose.dev.yml` by itself. The `--env-file` option supplies
the database interpolation values; the override passes the same file into the
bot and migration containers. Set `BATTLEVIVE_ENV_FILE` to use a different
local env-file path. `utils/init.sh` writes the bootstrap pair to the local
env-file; the running bot owns and rotates its persistent pair at
`app/data/bot/battlevive_tokens.json`. The local override uses Podman's `:U`
mount option to give that fixed non-root runtime identity ownership of its data.

For a standalone local image build:

```bash
podman build . --file Dockerfile --tag battlevive-bot:local
```

## License

This project is licensed under the AGPL-3.0. See [LICENSE](./LICENSE).

## Third-party assets

- Liberation Mono 2.1.5 is bundled under the SIL Open Font License 1.1. The original license and copyright notice are in [`app/assets/fonts/LICENSE.txt`](./app/assets/fonts/LICENSE). Upstream project and official archive: [liberationfonts/liberation-fonts](https://github.com/liberationfonts/liberation-fonts), [Liberation Fonts 2.1.5 TTF download](https://github.com/liberationfonts/liberation-fonts/files/7261482/liberation-fonts-ttf-2.1.5.tar.gz).
- Battlerite map artwork and derived champion emoji upload helpers are covered by the repository's [third-party asset notice](./app/assets/THIRD_PARTY_ASSETS.md).
