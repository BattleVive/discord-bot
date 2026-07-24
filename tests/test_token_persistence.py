from pathlib import Path

from battlevive_bot.token_persistence import save_tokens_to_env


def test_save_tokens_to_env_updates_bootstrap_tokens_and_preserves_other_values(
    tmp_path: Path,
) -> None:
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


def test_save_tokens_to_env_returns_false_when_tokens_are_missing(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("BOOTSTRAP_JWT=old-jwt\n", encoding="utf-8")

    saved = save_tokens_to_env(env_file, None, "new-refresh")

    assert saved is False
    assert env_file.read_text(encoding="utf-8") == "BOOTSTRAP_JWT=old-jwt\n"
