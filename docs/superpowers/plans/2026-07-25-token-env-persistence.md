# Token Env Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Save the latest Battlevive JWT and refresh token into the root `.env` during bot shutdown so container restarts reuse valid tokens.

**Architecture:** Add a focused token persistence module that updates only `BOOTSTRAP_JWT` and `BOOTSTRAP_REFRESH_TOKEN` in a configured env file. Mount the root `.env` into the bot container and call the helper from `BattleviveBot.close()` without logging secret values.

**Tech Stack:** Python 3.14-compatible code, discord.py tasks, python-dotenv, pytest, Docker Compose/Podman Compose.

## Global Constraints

- Do not open or print real `.env` secret values.
- Preserve existing project formatting and Python 3.14-compatible syntax.
- Use tests with temporary files and synthetic token values only.
- Root `.env` remains ignored by git and must not be committed.

---

## File Structure

- Modify `docker-compose.yml`: add a read/write bind mount for root `.env` at `/app/.env`.
- Modify `app/battlevive_bot/settings.py`: add `ENV_FILE_PATH` as a `Path` configurable by `ENV_FILE_PATH`, default `/app/.env`.
- Create `app/battlevive_bot/token_persistence.py`: expose `save_tokens_to_env(env_file_path: Path, jwt_token: str | None, refresh_token: str | None) -> bool`.
- Modify `app/battlevive_bot/bot.py`: import and call the helper during `BattleviveBot.close()`.
- Create `tests/test_token_persistence.py`: prove token keys update and non-token values are preserved.

### Task 1: Token Persistence Helper

**Files:**
- Create: `app/battlevive_bot/token_persistence.py`
- Test: `tests/test_token_persistence.py`

**Interfaces:**
- Consumes: `Path` and current token strings from callers.
- Produces: `save_tokens_to_env(env_file_path: Path, jwt_token: str | None, refresh_token: str | None) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path

from battlevive_bot.token_persistence import save_tokens_to_env


def test_save_tokens_to_env_updates_bootstrap_tokens_and_preserves_other_values(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DISCORD_TOKEN=discord-token\n"
        "BOOTSTRAP_JWT=old-jwt\n"
        "BOOTSTRAP_REFRESH_TOKEN=old-refresh\n"
        "POSTGRES_DB=battlevive\n",
        encoding="utf-8",
    )

    saved = save_tokens_to_env(env_file, "new-jwt", "new-refresh")

    assert saved is True
    assert env_file.read_text(encoding="utf-8") == (
        "DISCORD_TOKEN=discord-token\n"
        "BOOTSTRAP_JWT='new-jwt'\n"
        "BOOTSTRAP_REFRESH_TOKEN='new-refresh'\n"
        "POSTGRES_DB=battlevive\n"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_token_persistence.py::test_save_tokens_to_env_updates_bootstrap_tokens_and_preserves_other_values -v`
Expected: FAIL because `battlevive_bot.token_persistence` does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
from pathlib import Path

from dotenv import set_key

from .logs import logger


def save_tokens_to_env(
    env_file_path: Path,
    jwt_token: str | None,
    refresh_token: str | None,
) -> bool:
    if not jwt_token or not refresh_token:
        logger.warning("Skipping Battlevive token persistence because a token is missing.")
        return False

    try:
        set_key(env_file_path, "BOOTSTRAP_JWT", jwt_token)
        set_key(env_file_path, "BOOTSTRAP_REFRESH_TOKEN", refresh_token)
    except OSError:
        logger.exception("Failed to persist Battlevive tokens to env file.")
        return False

    logger.info("Persisted Battlevive tokens to env file.")
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_token_persistence.py::test_save_tokens_to_env_updates_bootstrap_tokens_and_preserves_other_values -v`
Expected: PASS.

### Task 2: Shutdown Integration and Compose Mount

**Files:**
- Modify: `app/battlevive_bot/settings.py`
- Modify: `app/battlevive_bot/bot.py`
- Modify: `docker-compose.yml`
- Test: `tests/test_token_persistence.py`

**Interfaces:**
- Consumes: `save_tokens_to_env(env_file_path, jwt_token, refresh_token)` from Task 1.
- Produces: shutdown behavior where `BattleviveBot.close()` attempts to persist current tokens.

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path

from battlevive_bot.token_persistence import save_tokens_to_env


def test_save_tokens_to_env_returns_false_when_tokens_are_missing(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("BOOTSTRAP_JWT=old-jwt\n", encoding="utf-8")

    saved = save_tokens_to_env(env_file, None, "new-refresh")

    assert saved is False
    assert env_file.read_text(encoding="utf-8") == "BOOTSTRAP_JWT=old-jwt\n"
```

- [ ] **Step 2: Run test to verify it fails if missing-token guard is absent**

Run: `pytest tests/test_token_persistence.py -v`
Expected: PASS after Task 1 if the guard is already implemented; if it fails, implement the guard exactly as specified in Task 1.

- [ ] **Step 3: Add settings path**

In `app/battlevive_bot/settings.py`, add:

```python
ENV_FILE_PATH = Path(os.getenv("ENV_FILE_PATH", "/app/.env"))
```

- [ ] **Step 4: Wire shutdown persistence**

In `app/battlevive_bot/bot.py`, import `save_tokens_to_env` and `ENV_FILE_PATH`, then call:

```python
save_tokens_to_env(
    ENV_FILE_PATH,
    battlevive_tokens.JWT_token,
    battlevive_tokens.refresh_token,
)
```

at the start of `BattleviveBot.close()` before closing the DB pool.

- [ ] **Step 5: Add compose mount**

In `docker-compose.yml`, add under bot `volumes`:

```yaml
      - ./.env:/app/.env:Z
```

- [ ] **Step 6: Run validation**

Run: `pytest tests/test_token_persistence.py tests/test_bot_refresh.py -v`
Expected: PASS.

Run: `python -m compileall app utils`
Expected: PASS.
