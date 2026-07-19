# bionicpro-auth

Auth Proxy (BFF) для BionicPRO. Инкапсулирует работу с Keycloak; фронтенд
никогда не получает токены — только HttpOnly-cookie `session_id`.

## Как работает

- Серверный **PKCE Authorization Code Flow**: обмен code на токены происходит на сервере.
- Токены хранятся в **Redis** под ключом `session_id`; refresh_token — зашифрован (Fernet).
- Браузеру отдаётся только cookie `session_id` (**HttpOnly**, **Secure**).
- TTL сессии (30 мин) больше TTL access_token (2 мин) — сервис сам обновляет токен по refresh.
- **Ротация `session_id`** при каждом обращении к защищённому ресурсу (защита от session fixation).
- Проксирует `/api/*` к upstream Reports API, подставляя `Authorization: Bearer`.

## Эндпоинты

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/auth/login` | Старт PKCE-флоу, редирект в Keycloak |
| GET | `/auth/callback` | Обмен code на токены, установка cookie |
| POST | `/auth/logout` | Инвалидация сессии (Redis + Keycloak) |
| GET | `/auth/userinfo` | Данные текущего пользователя |
| * | `/api/{path}` | Прокси к Reports API с Bearer-токеном |
| GET | `/health` | Health-check |

## Конфигурация

См. [`app/config.py`](app/config.py). Ключевые переменные:
`KEYCLOAK_URL`/`KEYCLOAK_PUBLIC_URL`, `KEYCLOAK_CLIENT_ID`/`KEYCLOAK_CLIENT_SECRET`,
`REDIS_URL`, `SESSION_TTL_SECONDS`, `FERNET_KEY`, `COOKIE_SECURE`.

## OTP (2FA)

Обязательный TOTP настроен декларативно в
[`keycloak/realm-export.json`](../keycloak/realm-export.json):

- **OTP Policy**: TOTP, HmacSHA1, 6 цифр, период 30 с, код одноразовый —
  совместимо с Google Authenticator / FreeOTP.
- **Required action `CONFIGURE_TOTP`** с `defaultAction: true` — каждый
  пользователь (включая LDAP-федерацию) при первом входе обязан привязать OTP
  (QR-код), далее код запрашивается при каждом входе.

Сброс OTP у пользователя: админка Keycloak → Users → Credentials → удалить OTP.

## Запуск

```bash
docker-compose up --build
# frontend: http://localhost:3000, auth-proxy: http://localhost:8000, keycloak: http://localhost:8080
```
