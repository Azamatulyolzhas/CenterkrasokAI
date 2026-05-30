"""
Асинхронная работа с PostgreSQL (asyncpg).
Если БД недоступна — функции логируют ошибку и не прерывают работу бота.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None

CREATE_USERS_TABLE = """
CREATE TABLE IF NOT EXISTS users (
    chat_id BIGINT PRIMARY KEY,
    username TEXT,
    full_name TEXT,
    first_seen TIMESTAMP DEFAULT NOW(),
    last_active TIMESTAMP DEFAULT NOW(),
    message_count INTEGER DEFAULT 0
);
"""

CREATE_MESSAGES_TABLE = """
CREATE TABLE IF NOT EXISTS messages (
    id SERIAL PRIMARY KEY,
    chat_id BIGINT REFERENCES users(chat_id),
    user_text TEXT,
    bot_response TEXT,
    timestamp TIMESTAMP DEFAULT NOW()
);
"""


def _normalize_database_url(url: str | None) -> str | None:
    if not url:
        return None
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


async def _create_pool(database_url: str) -> asyncpg.Pool:
    """Создаёт пул; для Railway пробует SSL, затем без SSL (локальная разработка)."""
    for ssl_mode in ("require", False):
        try:
            kwargs: dict[str, Any] = {"min_size": 1, "max_size": 5}
            if ssl_mode:
                kwargs["ssl"] = ssl_mode
            return await asyncpg.create_pool(database_url, **kwargs)
        except Exception as exc:
            if ssl_mode is False:
                raise exc
            logger.debug("Подключение с SSL не удалось, пробуем без SSL: %s", exc)
    raise RuntimeError("Не удалось создать пул соединений")


async def init_db() -> bool:
    """Создаёт пул и таблицы. Возвращает True при успехе."""
    global _pool

    database_url = _normalize_database_url(os.getenv("DATABASE_URL"))
    if not database_url:
        logger.warning("DATABASE_URL не задан — статистика и логи диалогов отключены")
        return False

    try:
        _pool = await _create_pool(database_url)
        async with _pool.acquire() as conn:
            await conn.execute(CREATE_USERS_TABLE)
            await conn.execute(CREATE_MESSAGES_TABLE)
        logger.info("PostgreSQL подключена, таблицы готовы")
        return True
    except Exception as exc:
        logger.error("Не удалось инициализировать БД: %s", exc)
        _pool = None
        return False


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("Пул PostgreSQL закрыт")


def is_db_ready() -> bool:
    return _pool is not None


async def add_or_update_user(
    chat_id: int,
    username: str | None,
    full_name: str | None,
) -> None:
    if _pool is None:
        return
    try:
        async with _pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO users (chat_id, username, full_name, last_active)
                VALUES ($1, $2, $3, NOW())
                ON CONFLICT (chat_id) DO UPDATE SET
                    username = EXCLUDED.username,
                    full_name = EXCLUDED.full_name,
                    last_active = NOW()
                """,
                chat_id,
                username,
                full_name,
            )
    except Exception as exc:
        logger.error("add_or_update_user(%s): %s", chat_id, exc)


async def increment_message_count(chat_id: int) -> None:
    if _pool is None:
        return
    try:
        async with _pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE users
                SET message_count = message_count + 1,
                    last_active = NOW()
                WHERE chat_id = $1
                """,
                chat_id,
            )
    except Exception as exc:
        logger.error("increment_message_count(%s): %s", chat_id, exc)


async def save_message(chat_id: int, user_text: str, bot_response: str) -> None:
    if _pool is None:
        return
    try:
        async with _pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO messages (chat_id, user_text, bot_response)
                VALUES ($1, $2, $3)
                """,
                chat_id,
                user_text,
                bot_response,
            )
    except Exception as exc:
        logger.error("save_message(%s): %s", chat_id, exc)


async def get_stats() -> dict[str, Any] | None:
    if _pool is None:
        return None
    try:
        async with _pool.acquire() as conn:
            total_users = await conn.fetchval("SELECT COUNT(*) FROM users")
            active_today = await conn.fetchval(
                """
                SELECT COUNT(*) FROM users
                WHERE last_active >= CURRENT_DATE
                """
            )
            total_messages = await conn.fetchval("SELECT COUNT(*) FROM messages")
            rows = await conn.fetch(
                """
                SELECT username, full_name, message_count
                FROM users
                ORDER BY message_count DESC
                LIMIT 5
                """
            )

        top_users = [
            {
                "username": row["username"],
                "full_name": row["full_name"],
                "message_count": row["message_count"],
            }
            for row in rows
        ]

        return {
            "total_users": total_users or 0,
            "active_today": active_today or 0,
            "total_messages": total_messages or 0,
            "top_users": top_users,
        }
    except Exception as exc:
        logger.error("get_stats: %s", exc)
        return None
