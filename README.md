# Battlevive Discord Bot

Discord bot that syncs player, lobby, season rating, and leaderboard data from Battlevive/Supabase and manages Battlevive roles, ranks, and match info in Discord.

[![Tests](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fvoxix-dev%2Fbattlevive-bot%2Fbadges%2Ftests.json)](https://github.com/voxix-dev/battlevive-bot/actions/workflows/tests.yml) [![Docker Image](https://github.com/voxix-dev/battlevive-bot/actions/workflows/docker-image.yml/badge.svg)](https://github.com/voxix-dev/battlevive-bot/actions/workflows/docker-image.yml) [![CodeQL Advanced](https://github.com/voxix-dev/battlevive-bot/actions/workflows/codeql.yml/badge.svg)](https://github.com/voxix-dev/battlevive-bot/actions/workflows/codeql.yml)

This project is under active development.

## Documentation

Full documentation lives in the GitHub Wiki:

- [Wiki home](https://github.com/voxix-dev/battlevive-bot/wiki)
- [User Guide](https://github.com/voxix-dev/battlevive-bot/wiki/User-Guide)
- [Server Admin Guide](https://github.com/voxix-dev/battlevive-bot/wiki/Server-Admin-Guide)
- [Deployment](https://github.com/voxix-dev/battlevive-bot/wiki/Deployment)
- [Developer Guide](https://github.com/voxix-dev/battlevive-bot/wiki/Developer-Guide)
- [Database Schema](https://github.com/voxix-dev/battlevive-bot/wiki/Database-Schema)
- [Troubleshooting](https://github.com/voxix-dev/battlevive-bot/wiki/Troubleshooting)


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

## Running in production

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

## Local development

Build the source tree and start the local image with the development Compose
file:

```bash
docker compose -f docker-compose.dev.yml up -d --build
```

or:

```bash
podman compose -f docker-compose.dev.yml up -d --build
```

For a standalone local image build:

```bash
podman build . --file Dockerfile --tag battlevive-bot:local
```

## License

This project is licensed under the AGPL-3.0. See [LICENSE](./LICENSE).

## Third-party assets

- Liberation Mono 2.1.5 is bundled under the SIL Open Font License 1.1. The original license and copyright notice are in [`app/assets/fonts/LICENSE.txt`](./app/assets/fonts/LICENSE). Upstream project and official archive: [liberationfonts/liberation-fonts](https://github.com/liberationfonts/liberation-fonts), [Liberation Fonts 2.1.5 TTF download](https://github.com/liberationfonts/liberation-fonts/files/7261482/liberation-fonts-ttf-2.1.5.tar.gz).
