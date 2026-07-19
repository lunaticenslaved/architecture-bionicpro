"""HTTP-клиент к CRM API.

Auth Proxy не работает с БД клиентов напрямую — он вызывает CRM API,
который владеет данными. Здесь: сохранение профиля из Яндекса, запись и
проверка согласия на обработку персональных данных.
"""
import httpx

from .config import settings


async def has_consent(subject: str, provider: str = "yandex") -> bool:
    """Проверяет в CRM, дал ли пользователь согласие на обработку данных."""
    url = f"{settings.CRM_API_URL}/users/consent/{subject}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url, params={"provider": provider})
        if resp.status_code != 200:
            return False
        return bool(resp.json().get("granted"))


async def record_consent(
    subject: str, granted: bool, provider: str = "yandex", scope: str = "profile"
) -> None:
    """Фиксирует в CRM решение пользователя по согласию."""
    url = f"{settings.CRM_API_URL}/users/consent"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            url,
            json={
                "subject": subject,
                "provider": provider,
                "scope": scope,
                "granted": granted,
            },
        )
        resp.raise_for_status()


async def save_profile(
    subject: str, profile: dict, provider: str = "yandex"
) -> None:
    """Отправляет профиль пользователя из Яндекса в CRM для сохранения."""
    url = f"{settings.CRM_API_URL}/users/profile"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            url,
            json={"subject": subject, "provider": provider, "profile": profile},
        )
        resp.raise_for_status()
