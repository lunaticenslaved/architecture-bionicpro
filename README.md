# BionicPRO

Учебный проект: безопасная аутентификация (Keycloak + BFF), ETL/CDC-аналитика и отчёты.

Подробности по задачам: [Task1](Task1/README.md) · [Task2](Task2/README.md) · [Task3](Task3/README.md) · [Task4](Task4/README.md)

## Состав окружения

| Сервис | Порт | Назначение |
|--------|------|------------|
| `keycloak` | 8080 | Identity Broker, User Federation, OTP |
| `openldap` / `phpldapadmin` | 389 / 8081 | Каталог пользователей + веб-UI |
| `redis` | 6379 | Сессии Auth Proxy |
| `bionicpro-auth` | 8000 | Auth Proxy (BFF) |
| `crm-api` / `crm_db` | 8090 / 5434 | CRM API и БД |
| `frontend` | 3000 | React-приложение |
| `clickhouse` | 8123 | OLAP (telemetry + витрины) |
| `airflow-webserver` | 8082 | Airflow UI (admin/admin) |
| `reports-api` | 8091 | Сервис отчётов |
| `minio` / `nginx-cdn` | 9002 / 8083 | S3-кэш отчётов + CDN |
| `kafka-connect` / `kafka-ui` | 8084 / 8085 | Debezium CDC + веб-UI Kafka |
| `telemetry-api` | 8092 | Приём телеметрии с протезов |

## Запуск

```bash
cp .env.example .env   # указать YANDEX_CLIENT_ID / YANDEX_CLIENT_SECRET
docker compose up -d --build
docker compose ps
```

> **Кастомный образ Keycloak**: Яндекс ID — OAuth 2.0 без OIDC, поэтому используется
> расширение [`playa-ru/keycloak-russian-providers`](https://github.com/playa-ru/keycloak-russian-providers)
> (нативный провайдер `yandex`). Сборка — [`keycloak/Dockerfile`](keycloak/Dockerfile).

> ⚠️ Правки `realm-export.json` не применяются к уже существующему realm.
> Полный переимпорт: `docker compose down -v && sudo rm -rf ./postgres-keycloak-data && docker compose up -d --build`

## Проверка

### PKCE S256
Открыть <http://localhost:3000> → **Login**. В редиректе на Keycloak должны быть
`code_challenge=...&code_challenge_method=S256`. Вход: `user1/password123`.

### Auth Proxy (BFF)
После входа в DevTools → Cookies: только `session_id` (**HttpOnly**, SameSite=Lax),
токенов в браузере нет. Токены — в Redis (`docker exec -it $(docker compose ps -q redis) redis-cli KEYS "session:*"`).
`session_id` ротируется при каждом запросе; access_token (TTL 120 c) обновляется автоматически.

### LDAP-федерация
Админка Keycloak → User Federation → `ldap-bionicpro` → Test connection/authentication →
Synchronize all users. Вход `john.doe/password` → роль `prothetic_user`
(маппинг из LDAP-групп). Детали и известная особенность с DN Alex — [`ldap/README.md`](ldap/README.md).

### OTP (2FA)
При первом входе Keycloak требует привязать TOTP (QR-код для Google Authenticator/FreeOTP),
далее при каждом входе запрашивается код. Настройка — в realm-export
(OTP Policy = TOTP, Configure OTP = Default Action).

### Вход через Яндекс ID + согласие
<http://localhost:3000> → «Войти через Яндекс ID» → после входа приложение
спрашивает разрешение на использование данных. При согласии профиль Яндекса
сохраняется в CRM, решение фиксируется в `crm.user_consent`:

```bash
docker exec -it bionicpro-crm-db psql -U crm_user -d crm_db \
  -c "SELECT subject, email, display_name FROM crm.user_profile;"
```

## Полезные адреса

- Frontend: <http://localhost:3000>
- Keycloak admin: <http://localhost:8080> (`admin`/`admin`)
- phpLDAPadmin: <http://localhost:8081> (`cn=admin,dc=example,dc=com`/`admin`)
- CRM API (Swagger): <http://localhost:8090/docs>
- Airflow: <http://localhost:8082> (`admin`/`admin`)
- Kafka UI: <http://localhost:8085>

## Остановка

```bash
docker compose down       # остановить
docker compose down -v    # остановить и сбросить данные
```
