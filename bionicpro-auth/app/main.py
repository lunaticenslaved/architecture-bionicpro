"""bionicpro-auth — бэкенд-сервис аутентификации (BFF).

Ответственность:
- полностью инкапсулирует работу с Keycloak (PKCE Authorization Code Flow);
- хранит access_token и refresh_token на сервере (Redis), привязывая их к сессии;
- фронтенду отдаёт только HttpOnly+Secure cookie session_id, без токенов;
- автоматически обновляет access_token по refresh_token при истечении;
- ротирует session_id при каждом обращении к защищённому ресурсу
  (защита от session fixation attack).
"""
import secrets
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from .config import settings
from .session_store import session_store
from . import keycloak_client as kc
from . import crm_client

# Временное хранилище state → code_verifier на время авторизационного редиректа.
# Живёт недолго (между /auth/login и /auth/callback). Для одного инстанса — dict;
# при горизонтальном масштабировании выносится в Redis.
_login_flows: dict[str, str] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await session_store.connect()
    yield
    await session_store.close()


app = FastAPI(title="bionicpro-auth", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_URL],
    allow_credentials=True,  # обязательно, чтобы браузер слал cookie
    allow_methods=["*"],
    allow_headers=["*"],
)


def _set_session_cookie(response: Response, session_id: str) -> None:
    """Устанавливает сессионную cookie с флагами безопасности."""
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=session_id,
        max_age=settings.SESSION_TTL_SECONDS,
        httponly=True,           # недоступна из JavaScript → защита от XSS
        secure=settings.COOKIE_SECURE,  # только по HTTPS в проде
        samesite="lax",          # защита от CSRF
        path="/",
    )


# --------------------------------------------------------------------------- #
#  Авторизация
# --------------------------------------------------------------------------- #
@app.get("/auth/login")
async def login(idp: str | None = None):
    """Стартует PKCE-флоу: генерирует verifier/challenge и редиректит в Keycloak.

    Параметр idp (например 'yandex') прокидывается в Keycloak как kc_idp_hint —
    пользователя сразу отправляют на выбранный внешний IdP (Identity Brokering).
    """
    state = secrets.token_urlsafe(32)
    code_verifier, code_challenge = kc.generate_pkce_pair()
    _login_flows[state] = code_verifier
    url = kc.build_authorization_url(state, code_challenge, idp_hint=idp)
    return RedirectResponse(url=url, status_code=302)


@app.get("/auth/callback")
async def callback(code: str, state: str):
    """Обрабатывает возврат из Keycloak, обменивает code на токены (server-side)."""
    code_verifier = _login_flows.pop(state, None)
    if code_verifier is None:
        raise HTTPException(status_code=400, detail="Invalid or expired state")

    tokens = await kc.exchange_code_for_tokens(code, code_verifier)

    session_id = await session_store.create_session(
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        id_token=tokens.get("id_token", ""),
    )

    # Возвращаем пользователя во фронтенд и ставим сессионную cookie.
    response = RedirectResponse(url=settings.FRONTEND_URL, status_code=302)
    _set_session_cookie(response, session_id)
    return response


@app.post("/auth/logout")
async def logout(request: Request):
    """Завершает сессию: удаляет её из Redis и инвалидирует в Keycloak."""
    session_id = request.cookies.get(settings.SESSION_COOKIE_NAME)
    response = JSONResponse({"status": "logged_out"})
    if session_id:
        session = await session_store.get_session(session_id)
        if session:
            await kc.logout(session["refresh_token"])
        await session_store.delete_session(session_id)
        response.delete_cookie(settings.SESSION_COOKIE_NAME, path="/")
    return response


def _decode(access_token: str) -> dict:
    import jwt

    return jwt.decode(access_token, options={"verify_signature": False})


def _is_yandex_login(claims: dict) -> bool:
    """Определяет, вошёл ли пользователь через Яндекс (Identity Brokering).

    Keycloak кладёт alias внешнего IdP в claim identity_provider.
    """
    return claims.get("identity_provider") == settings.YANDEX_IDP_ALIAS


@app.get("/auth/userinfo")
async def userinfo(request: Request):
    """Проверяет, авторизован ли пользователь (для фронтенда).

    Если вход был через Яндекс и пользователь ещё не дал согласие на обработку
    данных — возвращаем needs_consent=true, чтобы фронтенд показал экран запроса.
    """
    result = await _ensure_valid_session(request)
    if result is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    session, _new_session_id = result

    claims = _decode(session["access_token"])
    subject = claims.get("sub")
    via_yandex = _is_yandex_login(claims)

    needs_consent = False
    if via_yandex:
        # Согласие спрашиваем один раз: если его ещё нет в CRM — просим.
        needs_consent = not await crm_client.has_consent(subject)

    response = JSONResponse(
        {
            "username": claims.get("preferred_username"),
            "email": claims.get("email"),
            "roles": claims.get("realm_access", {}).get("roles", []),
            "identity_provider": claims.get("identity_provider"),
            "needs_consent": needs_consent,
        }
    )
    _set_session_cookie(response, _new_session_id)
    return response


@app.post("/auth/consent")
async def consent(request: Request):
    """Обрабатывает решение пользователя по согласию на обработку данных.

    Тело: {"granted": true|false}.
    - granted=true: запрашиваем профиль у Яндекса (broker token) и сохраняем
      его в CRM вместе с фактом согласия;
    - granted=false: фиксируем отказ, профиль не сохраняем.
    """
    result = await _ensure_valid_session(request)
    if result is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    session, new_session_id = result

    body = await request.json()
    granted = bool(body.get("granted"))

    claims = _decode(session["access_token"])
    subject = claims.get("sub")
    if not _is_yandex_login(claims):
        raise HTTPException(
            status_code=400, detail="Consent applies only to Yandex login"
        )

    await crm_client.record_consent(subject, granted)

    saved = False
    if granted:
        # Забираем токен Яндекса из Keycloak и тянем профиль напрямую у Яндекса.
        yandex_token = await kc.get_broker_token(
            session["access_token"], settings.YANDEX_IDP_ALIAS
        )
        if yandex_token:
            profile = await kc.fetch_yandex_profile(yandex_token)
            await crm_client.save_profile(subject, profile)
            saved = True

    response = JSONResponse({"granted": granted, "profile_saved": saved})
    _set_session_cookie(response, new_session_id)
    return response


# --------------------------------------------------------------------------- #
#  Проверка/обновление/ротация сессии
# --------------------------------------------------------------------------- #
async def _ensure_valid_session(request: Request):
    """Проверяет сессию, при необходимости обновляет access_token и ротирует id.

    Возвращает (session_data, new_session_id) либо None, если сессии нет.
    """
    session_id = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not session_id:
        return None

    session = await session_store.get_session(session_id)
    if session is None:
        return None

    access_token = session["access_token"]
    refresh_token = session["refresh_token"]
    id_token = session["id_token"]

    # Если access_token истёк — сервис сам обновляет его по refresh_token.
    if kc.is_access_token_expired(access_token):
        try:
            tokens = await kc.refresh_access_token(refresh_token)
        except httpx.HTTPStatusError:
            # refresh_token недействителен → сессия мертва.
            await session_store.delete_session(session_id)
            return None
        access_token = tokens["access_token"]
        refresh_token = tokens.get("refresh_token", refresh_token)
        id_token = tokens.get("id_token", id_token)

    # Ротация session_id при каждом запросе (anti session fixation):
    # перепривязываем токены к новому session_id, старый удаляем.
    new_session_id = await session_store.rotate_session(
        old_session_id=session_id,
        access_token=access_token,
        refresh_token=refresh_token,
        id_token=id_token,
    )

    return (
        {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "id_token": id_token,
        },
        new_session_id,
    )


# --------------------------------------------------------------------------- #
#  Прокси к защищённому ресурсу (Report API)
# --------------------------------------------------------------------------- #
@app.api_route(
    "/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"]
)
async def proxy(path: str, request: Request):
    """Проксирует запрос к upstream API, подставляя Bearer access_token из сессии.

    Фронтенд шлёт только session cookie; access_token наружу не покидает сервер.
    """
    result = await _ensure_valid_session(request)
    if result is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    session, new_session_id = result

    url = f"{settings.UPSTREAM_API_URL}/{path}"
    body = await request.body()
    # Пробрасываем безопасные заголовки, Authorization формируем сами.
    fwd_headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() in ("accept", "content-type")
    }
    fwd_headers["Authorization"] = f"Bearer {session['access_token']}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        upstream = await client.request(
            method=request.method,
            url=url,
            params=request.query_params,
            content=body,
            headers=fwd_headers,
        )

    response = Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
    )
    # Отдаём новый session_id в cookie (ротация выполнена выше).
    _set_session_cookie(response, new_session_id)
    return response


@app.get("/health")
async def health():
    return {"status": "ok"}
