# bionicpro-auth

Бэкенд-сервис аутентификации (BFF / Auth Proxy) для BionicPRO. Инкапсулирует
всю работу с Keycloak и управление сессиями. Фронтенд никогда не получает
access/refresh токены — только HttpOnly сессионную cookie.

## Что делает сервис

- Реализует серверную часть **PKCE Authorization Code Flow** с Keycloak.
- Обменивает authorization code на `access_token` + `refresh_token` **на сервере**.
- Хранит токены в **Redis**, привязывая их к `session_id`:
  - `access_token` — в оперативной памяти распределённого кеша (Redis);
  - `refresh_token` — в **зашифрованном** виде (Fernet).
- Отдаёт фронтенду только cookie `session_id` с флагами **HttpOnly** и **Secure**.
- TTL сессии (30 мин) **больше** времени жизни `access_token` (2 мин), поэтому
  при истечении access_token сервис сам обновляет его по `refresh_token`.
- **Ротирует `session_id` при каждом** обращении к защищённому ресурсу
  (перепривязка токенов к новому `session_id`, старый удаляется) — защита от
  session fixation attack.
- Проксирует запросы к upstream Report API, подставляя `Authorization: Bearer`.

## Эндпоинты

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/auth/login` | Старт PKCE-флоу, редирект в Keycloak |
| GET | `/auth/callback` | Обмен code на токены, создание сессии, установка cookie |
| POST | `/auth/logout` | Инвалидация сессии в Redis и в Keycloak |
| GET | `/auth/userinfo` | Данные текущего пользователя (проверка сессии) |
| * | `/api/{path}` | Прокси к Report API с подстановкой access_token |
| GET | `/health` | Health-check |

## Поток аутентификации

```
Браузер → GET /auth/login
  bionicpro-auth: генерирует code_verifier + code_challenge (S256)
  → redirect в Keycloak (браузер логинится)
Keycloak → GET /auth/callback?code=...&state=...
  bionicpro-auth: обмен code + code_verifier → access + refresh (server-side)
  сохраняет токены в Redis под session_id
  → Set-Cookie: session_id (HttpOnly, Secure); redirect на фронтенд

Браузер → GET /api/reports (cookie: session_id)
  bionicpro-auth: находит сессию, при истечении access_token обновляет по refresh
  ротирует session_id (старый удаляет, новый пишет)
  → проксирует в Report API с Bearer access_token
  → Set-Cookie: новый session_id; отдаёт данные
```

## Переменные окружения

См. [`app/config.py`](app/config.py). Ключевые:

- `KEYCLOAK_URL` / `KEYCLOAK_PUBLIC_URL` — внутренний и браузерный адрес Keycloak.
- `KEYCLOAK_CLIENT_ID` / `KEYCLOAK_CLIENT_SECRET` — confidential-клиент `bionicpro-auth`.
- `REDIS_URL` — адрес распределённого кеша сессий.
- `SESSION_TTL_SECONDS` — TTL сессии (по умолчанию 1800, > 120 сек access_token).
- `FERNET_KEY` — ключ шифрования refresh_token в Redis.
- `COOKIE_SECURE` — Secure-флаг cookie (в проде `true`, локально по http — `false`).

## Запуск

Сервис поднимается вместе со всем стеком:

```bash
docker-compose up --build
```

- Фронтенд: `http://localhost:3000`
- bionicpro-auth: `http://localhost:8000`
- Keycloak: `http://localhost:8080`
