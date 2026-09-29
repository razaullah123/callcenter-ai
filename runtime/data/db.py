"""Shared asyncpg pool for the voice agent database."""

import asyncpg

from runtime.config import get_settings

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        s = get_settings()
        if s.database_url is None:
            raise RuntimeError("DATABASE_URL is not configured")
        _pool = await asyncpg.create_pool(s.database_url.get_secret_value(), min_size=1, max_size=10,
                                          ssl=s.database_ssl, timeout=10, command_timeout=10)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
