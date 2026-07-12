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


# Обязательная OTP-аутентификация (2FA) в Keycloak

## Цель

После утечки данных одного пароля недостаточно. Включаем второй фактор —
одноразовый пароль (TOTP), который пользователь вводит из приложения-
аутентификатора (**Google Authenticator** или **FreeOTP**). Вход в систему
возможен только после ввода корректного OTP-кода.

## Что сделано

Настройка декларативна в [`keycloak/realm-export.json`](../keycloak/realm-export.json)
и применяется автоматически при старте (`--import-realm`).

### 1. OTP-политика realm (TOTP)

```json
"otpPolicyType": "totp",
"otpPolicyAlgorithm": "HmacSHA1",
"otpPolicyDigits": 6,
"otpPolicyPeriod": 30,
"otpPolicyLookAheadWindow": 1,
"otpPolicyInitialCounter": 0,
"otpPolicyCodeReusable": false,
"otpSupportedApplications": ["totpAppGoogleName", "totpAppFreeOTPName"]
```

| Параметр | Значение | Комментарий |
|----------|----------|-------------|
| `otpPolicyType` | `totp` | Time-based OTP (не HOTP) — совместим с Google Authenticator / FreeOTP |
| `otpPolicyAlgorithm` | `HmacSHA1` | стандарт TOTP, поддерживается всеми аутентификаторами |
| `otpPolicyDigits` | `6` | длина кода |
| `otpPolicyPeriod` | `30` | окно жизни кода, сек |
| `otpPolicyCodeReusable` | `false` | один код нельзя использовать повторно |

### 2. Обязательный ввод OTP для всех пользователей

```json
"requiredActions": [
  {
    "alias": "CONFIGURE_TOTP",
    "providerId": "CONFIGURE_TOTP",
    "enabled": true,
    "defaultAction": true,
    "priority": 10
  }
]
```

`"defaultAction": true` означает, что required action `CONFIGURE_TOTP`
назначается **каждому** пользователю по умолчанию. При первом входе
(логин+пароль) Keycloak принудительно показывает экран настройки OTP:
пользователь сканирует QR-код в Google Authenticator/FreeOTP и подтверждает
первый код. Далее при каждом входе после пароля запрашивается OTP-код.

Это распространяется в том числе на пользователей из LDAP-федерации
(Задача 4) — при первом входе они тоже обязаны привязать OTP.

## Как это работает при входе

1. Пользователь вводит логин и пароль (браузерный flow Keycloak).
2. Так как `CONFIGURE_TOTP` — default action, при первом входе Keycloak
   требует настроить OTP: показывает QR-код и секрет.
3. Пользователь добавляет аккаунт в Google Authenticator / FreeOTP и вводит
   сгенерированный 6-значный код.
4. На всех последующих входах после пароля Keycloak запрашивает актуальный
   TOTP-код. Без корректного кода вход невозможен.

## Проверка

```bash
docker compose up -d --build

# Keycloak admin: http://localhost:8080  (admin / admin)
#   Realm reports-realm → Authentication → Policies → OTP Policy = TOTP
#   Realm reports-realm → Authentication → Required Actions →
#     Configure OTP = Enabled + Default Action (ON)
```

Сценарий: вход через `http://localhost:8000/auth/login` под любым
пользователем (например `user1` / `password123`). После пароля Keycloak
покажет экран привязки OTP, затем — запрос OTP-кода. Вход завершится только
после ввода кода из аутентификатора.

## Замечание

Секрет OTP хранится в Keycloak per-user. Чтобы сбросить привязку у
конкретного пользователя (например, при потере устройства), администратор в
консоли: `Users → <user> → Credentials → удалить OTP`, либо повторно
назначить required action `Configure OTP`.
