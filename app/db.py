# db.py
# pyrefly: ignore [missing-import]
import asyncpg
from logs import logger

_pool: asyncpg.Pool | None = None


async def init_pool(dsn: str) -> asyncpg.Pool:
    global _pool
    _pool = await asyncpg.create_pool(
        dsn=dsn,
        min_size=1,
        max_size=5,  # 2-5 guilds, single bot process – no need for a large pool
        command_timeout=10,
    )
    logger.info("PostgreSQL connection pool created.")
    return _pool


async def close_pool() -> None:
    if _pool is not None:
        await _pool.close()
        logger.info("PostgreSQL connection pool closed.")


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Connection pool not initialized. Call init_pool() first.")
    return _pool


