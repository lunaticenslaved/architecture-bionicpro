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
