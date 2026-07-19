"""CRM API — система-источник истины по клиентам BionicPRO.

Отвечает за хранение профиля пользователя (полученного из Яндекса через
Identity Brokering) и журнала согласий на обработку персональных данных.

Границы ответственности:
- bionicpro-auth (Auth Proxy) НЕ ходит в БД напрямую, а вызывает этот API;
- CRM владеет клиентскими данными и их схемой.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .db import database


class ProfileIn(BaseModel):
    subject: str
    provider: str = "yandex"
    profile: dict  # сырой ответ login.yandex.ru/info


class ConsentIn(BaseModel):
    subject: str
    provider: str = "yandex"
    scope: str = "profile"
    granted: bool


@asynccontextmanager
async def lifespan(app: FastAPI):
    await database.connect()
    yield
    await database.close()


app = FastAPI(title="crm-api", lifespan=lifespan)


@app.post("/users/profile")
async def save_profile(body: ProfileIn):
    """Сохраняет/обновляет профиль клиента, полученный из Яндекса."""
    await database.upsert_profile(body.subject, body.provider, body.profile)
    return {"status": "saved", "subject": body.subject}


@app.get("/users/profile/{subject}")
async def get_profile(subject: str):
    profile = await database.get_profile(subject)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    # raw_profile отдаём как есть; убираем внутренние поля дат из ответа не нужно.
    return profile


@app.post("/users/consent")
async def save_consent(body: ConsentIn):
    """Фиксирует решение пользователя по согласию на обработку данных."""
    await database.record_consent(
        body.subject, body.provider, body.scope, body.granted
    )
    return {"status": "recorded", "granted": body.granted}


@app.get("/users/consent/{subject}")
async def get_consent(subject: str, provider: str = "yandex"):
    granted = await database.has_consent(subject, provider)
    return {"subject": subject, "provider": provider, "granted": granted}


@app.get("/health")
async def health():
    return {"status": "ok"}
