"""Проверка Bearer JWT от Keycloak (RS256 + JWKS) и RBAC.

Reports API — resource server (bearer-only клиент `reports-api` в Keycloak).
Токен сюда подставляет Auth Proxy (BFF) из серверной сессии; напрямую
из браузера токены не приходят.

Проверяется:
- подпись RS256 по публичному ключу из JWKS realm-а (ключи кэшируются);
- exp / iat;
- iss — канонический issuer realm-а (KC_HOSTNAME_URL);
- наличие роли `prothetic_user` (RBAC) для доступа к отчётам.

Данные фильтруются по claim `sub` — пользователь получает только свои
отчёты (требование: доступ к чужим протезам закрыт).
"""
from dataclasses import dataclass, field

import jwt
from fastapi import Depends, HTTPException, Request
from jwt import PyJWKClient

from .config import settings

# PyJWKClient сам кэширует JWKS (по умолчанию 5 минут) и умеет
# подбирать ключ по kid из заголовка токена.
_jwks_client = PyJWKClient(settings.jwks_url, cache_keys=True)


@dataclass
class Principal:
    subject: str
    username: str
    roles: list[str] = field(default_factory=list)

    @property
    def is_admin(self) -> bool:
        return settings.ADMIN_ROLE in self.roles


def _decode_token(token: str) -> dict:
    try:
        signing_key = _jwks_client.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=settings.issuer,
            # aud у Keycloak по умолчанию 'account' — проверяем roles, не aud.
            options={"verify_aud": False},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}")


async def get_current_user(request: Request) -> Principal:
    """FastAPI-dependency: аутентификация по заголовку Authorization."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")

    claims = _decode_token(auth.removeprefix("Bearer ").strip())
    return Principal(
        subject=claims.get("sub", ""),
        username=claims.get("preferred_username", ""),
        roles=claims.get("realm_access", {}).get("roles", []),
    )


async def require_reports_access(
    user: Principal = Depends(get_current_user),
) -> Principal:
    """RBAC: отчёты доступны только роли prothetic_user (или administrator)."""
    if settings.REPORTS_ROLE not in user.roles and not user.is_admin:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Role '{settings.REPORTS_ROLE}' is required "
                "to access prosthesis reports"
            ),
        )
    return user
