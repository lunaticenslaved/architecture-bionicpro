"""Интеграция с Keycloak по OpenID Connect.

Реализует серверную часть PKCE Authorization Code Flow:
- генерация code_verifier / code_challenge;
- обмен authorization code на токены;
- обновление access_token по refresh_token;
- проверка истечения access_token;
- logout в Keycloak.
"""
import base64
import hashlib
import secrets
import time
from typing import Optional
from urllib.parse import urlencode

import httpx
import jwt

from .config import settings


def generate_pkce_pair() -> tuple[str, str]:
    """Возвращает (code_verifier, code_challenge) по методу S256."""
    code_verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def build_authorization_url(
    state: str, code_challenge: str, idp_hint: Optional[str] = None
) -> str:
    """Формирует URL для редиректа пользователя на страницу входа Keycloak.

    Если задан idp_hint (alias внешнего IdP, например 'yandex'), добавляем
    kc_idp_hint — Keycloak сразу перенаправит пользователя на этот IdP.
    """
    params = {
        "client_id": settings.CLIENT_ID,
        "response_type": "code",
        "scope": "openid profile email",
        "redirect_uri": settings.REDIRECT_URI,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if idp_hint:
        params["kc_idp_hint"] = idp_hint
    return f"{settings.authorization_endpoint}?{urlencode(params)}"


async def exchange_code_for_tokens(code: str, code_verifier: str) -> dict:
    """Обменивает authorization code на access/refresh токены (server-side)."""
    data = {
        "grant_type": "authorization_code",
        "client_id": settings.CLIENT_ID,
        "client_secret": settings.CLIENT_SECRET,
        "code": code,
        "redirect_uri": settings.REDIRECT_URI,
        "code_verifier": code_verifier,
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(settings.token_endpoint, data=data)
        resp.raise_for_status()
        return resp.json()


async def refresh_access_token(refresh_token: str) -> dict:
    """Получает новый access_token (и, при ротации, новый refresh_token)."""
    data = {
        "grant_type": "refresh_token",
        "client_id": settings.CLIENT_ID,
        "client_secret": settings.CLIENT_SECRET,
        "refresh_token": refresh_token,
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(settings.token_endpoint, data=data)
        resp.raise_for_status()
        return resp.json()


async def logout(refresh_token: str) -> None:
    """Инвалидирует сессию на стороне Keycloak."""
    data = {
        "client_id": settings.CLIENT_ID,
        "client_secret": settings.CLIENT_SECRET,
        "refresh_token": refresh_token,
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(
            f"{settings.issuer}/protocol/openid-connect/logout", data=data
        )


async def get_broker_token(access_token: str, alias: str) -> Optional[str]:
    """Достаёт из Keycloak access_token внешнего IdP (Яндекса).

    Работает при storeToken=true у IdP: Keycloak хранит токен Яндекса и отдаёт
    его по /broker/<alias>/token, авторизуя запрос access_token'ом Keycloak.
    Возвращает access_token Яндекса либо None.
    """
    url = settings.broker_token_endpoint(alias)
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            url, headers={"Authorization": f"Bearer {access_token}"}
        )
        if resp.status_code != 200:
            return None
        # Ответ может быть form-encoded или JSON в зависимости от версии.
        ctype = resp.headers.get("content-type", "")
        if "application/json" in ctype:
            return resp.json().get("access_token")
        # form-encoded: access_token=...&token_type=...
        from urllib.parse import parse_qs

        parsed = parse_qs(resp.text)
        vals = parsed.get("access_token")
        return vals[0] if vals else None


async def fetch_yandex_profile(yandex_access_token: str) -> dict:
    """Запрашивает профиль пользователя у Яндекса по его access_token.

    OAuth-токен Яндекса передаётся в заголовке по схеме 'OAuth <token>'.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            settings.YANDEX_USERINFO_URL,
            headers={"Authorization": f"OAuth {yandex_access_token}"},
        )
        resp.raise_for_status()
        return resp.json()


def is_access_token_expired(access_token: str, leeway_seconds: int = 10) -> bool:
    """Проверяет, истёк ли access_token (с небольшим запасом leeway).

    Подпись здесь не проверяется — токен выпущен доверенным Keycloak и хранится
    только на сервере. Нужен лишь claim exp, чтобы решить, пора ли обновлять.
    """
    try:
        claims = jwt.decode(access_token, options={"verify_signature": False})
    except jwt.PyJWTError:
        return True
    exp = claims.get("exp")
    if exp is None:
        return True
    return time.time() >= (exp - leeway_seconds)
