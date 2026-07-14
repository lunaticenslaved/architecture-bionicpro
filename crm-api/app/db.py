"""БД CRM-сервиса (PostgreSQL).

CRM — система-источник истины по клиентам BionicPRO. Здесь хранятся:
- профиль пользователя, полученный из Яндекса (Identity Brokering);
- журнал согласий на обработку персональных данных (ФЗ-152/GDPR):
  профиль сохраняется только после явного разрешения пользователя.

Данные лежат в отдельной схеме `crm`. asyncpg-пул, схема создаётся при старте.
"""
import asyncio
import json
from typing import Optional

import asyncpg

from .config import settings

_SCHEMA = """
CREATE SCHEMA IF NOT EXISTS crm;

CREATE TABLE IF NOT EXISTS crm.user_profile (
    id            SERIAL PRIMARY KEY,
    subject       TEXT NOT NULL UNIQUE,      -- sub из access_token Keycloak
    provider      TEXT NOT NULL,             -- 'yandex'
    external_id   TEXT,                      -- id пользователя в Яндексе
    username      TEXT,
    first_name    TEXT,
    last_name     TEXT,
    display_name  TEXT,
    email         TEXT,
    raw_profile   JSONB,                     -- полный ответ login.yandex.ru/info
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS crm.user_consent (
    id            SERIAL PRIMARY KEY,
    subject       TEXT NOT NULL,
    provider      TEXT NOT NULL,
    scope         TEXT NOT NULL,             -- на что дано согласие
    granted       BOOLEAN NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_user_consent_subject
    ON crm.user_consent (subject);
"""


class Database:
    def __init__(self) -> None:
        self._pool: Optional[asyncpg.Pool] = None

    async def connect(self, *, max_attempts: int = 10, retry_delay: float = 2.0) -> None:
        last_exc: Exception = RuntimeError("connect() never attempted")
        for attempt in range(1, max_attempts + 1):
            try:
                self._pool = await asyncpg.create_pool(
                    dsn=settings.DATABASE_URL, min_size=1, max_size=5
                )
                break
            except Exception as exc:
                last_exc = exc
                if attempt < max_attempts:
                    await asyncio.sleep(retry_delay)
        else:
            raise last_exc

        async with self._pool.acquire() as conn:
            await conn.execute(_SCHEMA)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()

    async def has_consent(self, subject: str, provider: str) -> bool:
        row = await self._pool.fetchrow(
            """
            SELECT granted FROM crm.user_consent
            WHERE subject = $1 AND provider = $2
            ORDER BY created_at DESC
            LIMIT 1
            """,
            subject,
            provider,
        )
        return bool(row and row["granted"])

    async def record_consent(
        self, subject: str, provider: str, scope: str, granted: bool
    ) -> None:
        await self._pool.execute(
            """
            INSERT INTO crm.user_consent (subject, provider, scope, granted)
            VALUES ($1, $2, $3, $4)
            """,
            subject,
            provider,
            scope,
            granted,
        )

    async def upsert_profile(
        self, subject: str, provider: str, profile: dict
    ) -> None:
        """Сохраняет/обновляет профиль пользователя из Яндекса.

        `profile` — сырой ответ https://login.yandex.ru/info.
        """
        await self._pool.execute(
            """
            INSERT INTO crm.user_profile (
                subject, provider, external_id, username,
                first_name, last_name, display_name, email, raw_profile
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
            ON CONFLICT (subject) DO UPDATE SET
                provider     = EXCLUDED.provider,
                external_id  = EXCLUDED.external_id,
                username     = EXCLUDED.username,
                first_name   = EXCLUDED.first_name,
                last_name    = EXCLUDED.last_name,
                display_name = EXCLUDED.display_name,
                email        = EXCLUDED.email,
                raw_profile  = EXCLUDED.raw_profile,
                updated_at   = now()
            """,
            subject,
            provider,
            str(profile.get("id", "")),
            profile.get("login", ""),
            profile.get("first_name", ""),
            profile.get("last_name", ""),
            profile.get("display_name") or profile.get("real_name", ""),
            profile.get("default_email")
            or (profile.get("emails") or [None])[0],
            json.dumps(profile, ensure_ascii=False),
        )

    async def get_profile(self, subject: str) -> Optional[dict]:
        row = await self._pool.fetchrow(
            "SELECT * FROM crm.user_profile WHERE subject = $1", subject
        )
        return dict(row) if row else None


database = Database()
