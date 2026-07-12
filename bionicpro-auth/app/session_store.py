"""Хранилище сессий в Redis.

Задачи модуля:
- привязка access_token и refresh_token к session_id;
- шифрование refresh_token в хранилище (Fernet);
- ротация session_id (защита от session fixation);
- TTL сессии больше времени жизни access_token.
"""
import json
import secrets
from typing import Optional

import redis.asyncio as redis
from cryptography.fernet import Fernet

from .config import settings


def _build_fernet() -> Fernet:
    """Возвращает Fernet для шифрования refresh_token.

    Если ключ не задан в окружении — генерируем эфемерный (данные переживут
    только текущий процесс). В проде FERNET_KEY обязателен.
    """
    key = settings.FERNET_KEY
    if not key:
        key = Fernet.generate_key().decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


class SessionStore:
    def __init__(self) -> None:
        self._redis: Optional[redis.Redis] = None
        self._fernet = _build_fernet()

    async def connect(self) -> None:
        self._redis = redis.from_url(settings.REDIS_URL, decode_responses=True)

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()

    @staticmethod
    def _key(session_id: str) -> str:
        return f"session:{session_id}"

    @staticmethod
    def _new_session_id() -> str:
        # 256 бит энтропии — криптостойкий идентификатор сессии.
        return secrets.token_urlsafe(32)

    async def create_session(
        self, access_token: str, refresh_token: str, id_token: str = ""
    ) -> str:
        """Создаёт новую сессию и возвращает session_id."""
        session_id = self._new_session_id()
        await self._write(session_id, access_token, refresh_token, id_token)
        return session_id

    async def _write(
        self, session_id: str, access_token: str, refresh_token: str, id_token: str
    ) -> None:
        # refresh_token хранится в зашифрованном виде.
        encrypted_refresh = self._fernet.encrypt(refresh_token.encode()).decode()
        payload = json.dumps(
            {
                "access_token": access_token,
                "refresh_token_enc": encrypted_refresh,
                "id_token": id_token,
            }
        )
        # TTL продлевается при каждой записи — «скользящая» сессия.
        await self._redis.set(
            self._key(session_id), payload, ex=settings.SESSION_TTL_SECONDS
        )

    async def get_session(self, session_id: str) -> Optional[dict]:
        """Возвращает данные сессии с расшифрованным refresh_token, либо None."""
        raw = await self._redis.get(self._key(session_id))
        if raw is None:
            return None
        data = json.loads(raw)
        refresh_token = self._fernet.decrypt(
            data["refresh_token_enc"].encode()
        ).decode()
        return {
            "access_token": data["access_token"],
            "refresh_token": refresh_token,
            "id_token": data.get("id_token", ""),
        }

    async def rotate_session(
        self,
        old_session_id: str,
        access_token: str,
        refresh_token: str,
        id_token: str = "",
    ) -> str:
        """Перепривязывает токены к новому session_id.

        Старый session_id немедленно удаляется — защита от session fixation
        и от повторного использования перехваченной cookie.
        """
        new_session_id = self._new_session_id()
        await self._write(new_session_id, access_token, refresh_token, id_token)
        await self.delete_session(old_session_id)
        return new_session_id

    async def delete_session(self, session_id: str) -> None:
        await self._redis.delete(self._key(session_id))


session_store = SessionStore()
