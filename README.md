# Battlevive Discord Bot

Discord bot that syncs player/lobby data from the Battlevive platform (via Supabase) and manages roles, ranks, and match info in Discord.

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
podman-compose up -d --build
```

(or `podman compose up -d --build` if your Podman version has the built-in compose provider)

Check logs with `docker compose logs -f` / `podman-compose logs -f`.

## Database schema

See [SCHEMA.md](./SCHEMA.md).

---

This project is under active development – setup steps and structure may change.
