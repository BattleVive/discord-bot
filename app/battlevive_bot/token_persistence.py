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
