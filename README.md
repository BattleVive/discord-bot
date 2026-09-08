# Battlevive Discord Bot

Discord bot using an isolated Discord gateway, BattleVive data service, image renderer, and PostgreSQL state store.

[![Stable Release](https://github.com/BattleVive/discord-bot/actions/workflows/release.yml/badge.svg)](https://github.com/BattleVive/discord-bot/actions/workflows/release.yml) [![CodeQL Advanced](https://github.com/BattleVive/discord-bot/actions/workflows/codeql.yml/badge.svg)](https://github.com/BattleVive/discord-bot/actions/workflows/codeql.yml)

This project is under active development.

## Requirements

- Docker or Podman with Compose support
- A Discord bot token
- A long-lived BattleVive API key

## Local setup

```bash
cp .env.example .env
# Fill the replacement values, including the same database password in
# POSTGRES_PASSWORD and DATABASE_URL; do not commit .env.
podman compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

The gateway receives only the Discord token and database/private-service URLs.
Only `upstream-data` receives the BattleVive API key. The image renderer has no
credentials or database access.

## License

This project is licensed under the AGPL-3.0. See [LICENSE](./LICENSE).

## Third-party assets

- Liberation Mono 2.1.5 is bundled under the SIL Open Font License 1.1. The original license and copyright notice are in [`app/assets/fonts/LICENSE.txt`](./app/assets/fonts/LICENSE). Upstream project and official archive: [liberationfonts/liberation-fonts](https://github.com/liberationfonts/liberation-fonts), [Liberation Fonts 2.1.5 TTF download](https://github.com/liberationfonts/liberation-fonts/files/7261482/liberation-fonts-ttf-2.1.5.tar.gz).
- Battlerite map artwork and derived champion emoji upload helpers are covered by the repository's [third-party asset notice](./app/assets/THIRD_PARTY_ASSETS.md).
