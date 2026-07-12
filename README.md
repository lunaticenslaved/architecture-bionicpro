# BionicPRO

## Состав окружения (docker-compose)

| Сервис | Порт | Назначение |
|--------|------|------------|
| `keycloak` | 8080 | Identity Broker, User Federation, OTP |
| `keycloak_db` | 5433 | БД Keycloak (postgres) |
| `openldap` | 389/636 | Каталог пользователей представительства |
| `phpldapadmin` | 8081 | Веб-UI для LDAP |
| `redis` | 6379 | Серверные сессии Auth Proxy |
| `bionicpro-auth` | 8000 | Auth Proxy (BFF) |
| `crm-api` | 8090 | CRM API — владелец клиентских данных |
| `crm_db` | 5434 | БД CRM (профиль + согласия) |
| `frontend` | 3000 | React-приложение |

---

# Поэтапная проверка

## 0. Предварительная подготовка

```bash
cp .env.example .env
#   затем указать в .env реальные YANDEX_CLIENT_ID / YANDEX_CLIENT_SECRET
#   (со страницы приложения на https://oauth.yandex.ru).
#   Redirect URI приложения Яндекса:
#   http://localhost:8080/realms/reports-realm/broker/yandex/endpoint

# Поднять всё окружение
docker compose up -d --build

# Убедиться, что все контейнеры запустились
docker compose ps
```

Дождаться, пока Keycloak импортирует realm:

```bash
docker compose logs -f keycloak | grep -i import
```

---

## Задача 2 — PKCE S256

**Цель:** авторизация идёт по Authorization Code Flow с PKCE, без Implicit/Code Grant без PKCE.

1. Открыть <http://localhost:3000>, нажать **Login**.
2. В адресной строке при редиректе на Keycloak проверить наличие параметров
   `code_challenge=...` и `code_challenge_method=S256`.
3. В форме регистрации ввести `user1/password123`.

✅ Ожидание: вход проходит, в запросе есть `code_challenge` с методом S256.

---

## Задача 3 — Auth Proxy (BFF): токены на сервере

**Цель:** токены не попадают в браузер; сессия — только HttpOnly-cookie; авто-refresh и ротация.

1. Войти на <http://localhost:3000>.
2. DevTools → Application → Cookies для `localhost:8000`: есть cookie
   `session_id` с флагами **HttpOnly** и **SameSite=Lax**. Токенов (JWT) в
   cookie/localStorage быть **не должно**.
3. Проверить, что токены хранятся в Redis (в зашифрованном виде refresh_token):
   ```bash
   docker exec -it $(docker compose ps -q redis) redis-cli KEYS "session:*"
   ```
4. **Ротация session_id:** повторный запрос к защищённому ресурсу выдаёт новую
   cookie `session_id` (значение меняется), старый ключ в Redis удаляется.
5. **Авто-refresh:** access_token живёт 120 сек (`accessTokenLifespan: 120`),
   TTL сессии — 1800 сек. Через >2 мин запрос всё ещё работает — Auth Proxy сам
   обновил токен по refresh_token.

✅ Ожидание: браузер видит только `session_id`; токены — в Redis; сессия
переживает истечение access_token.

---

## Задача 4 — LDAP-федерация + роли представительства

**Цель:** Keycloak аутентифицирует пользователей из внешнего LDAP; роли синхронизируются.

1. Проверить, что LDAP отдаёт пользователей:
   ```bash
   docker exec bionicpro-openldap \
     ldapsearch -x -H ldap://localhost -b "dc=example,dc=com" \
     -D "cn=admin,dc=example,dc=com" -w admin "(objectClass=inetOrgPerson)" dn
   ```
2. В админке Keycloak → User Federation → `ldap-bionicpro` → **Test connection**
   и **Test authentication** = успех. Нажать **Synchronize all users**.
3. Users → найти `john.doe`, `jane.smith` — импортированы из LDAP.
4. Войти под `john.doe` / `password` (через <http://localhost:3000>).
5. Проверить роли: у `john.doe` и `alex.johnson` должна быть роль
   `prothetic_user`, у `jane.smith` — `user` (маппинг из LDAP-групп `ou=Groups`).

> ⚠️ Известная особенность: в [`ldap/config.ldif`](ldap/config.ldif) DN Alex —
> `uid=alex`, а группа ссылается на `uid=alex.johnson`. Из-за несоответствия DN
> Alex может не получить роль. Подробности — в [`ldap/README.md`](ldap/README.md).

✅ Ожидание: LDAP-пользователи входят через Keycloak и получают realm-роли из групп.

---

## Задача 5 — Обязательный OTP (2FA)

**Цель:** после пароля обязателен одноразовый код из Google Authenticator/FreeOTP.

1. В админке Keycloak → realm `reports-realm` → Authentication → **Policies →
   OTP Policy**: тип **TOTP**, HmacSHA1, 6 цифр, период 30 сек.
2. Authentication → **Required Actions** → **Configure OTP** = Enabled +
   **Default Action = ON**.
3. Войти новым пользователем (например `user1` / `password123`) через
   <http://localhost:3000>:
   - после пароля Keycloak покажет **QR-код** для привязки OTP;
   - отсканировать в Google Authenticator / FreeOTP, ввести 6-значный код.
4. Выйти и войти снова — Keycloak запросит **актуальный OTP-код**; без него
   вход невозможен.

✅ Ожидание: вход завершается только после ввода корректного OTP.

---

## Задача 6 — Identity Brokering через Яндекс ID + согласие + профиль в CRM

**Цель:** вход через Яндекс; сервис спрашивает разрешение и сохраняет профиль в БД.

1. На <http://localhost:3000> нажать **«Войти через Яндекс ID»**.
   - Auth Proxy редиректит с `kc_idp_hint=yandex`, Keycloak — на Яндекс OAuth.
2. Авторизоваться в Яндексе, вернуться в приложение.
3. Приложение показывает экран **«Разрешение на использование данных»**
   (сработал `needs_consent=true` из `/auth/userinfo`).
4. Нажать **«Разрешаю»**. Auth Proxy:
   - фиксирует согласие в CRM;
   - забирает токен Яндекса из Keycloak (`/broker/yandex/token`, `storeToken=true`);
   - запрашивает профиль у Яндекса (`login.yandex.ru/info`);
   - сохраняет профиль в CRM.
5. Проверить сохранённые данные:
   ```bash
   # через CRM API
   curl "http://localhost:8090/users/profile/<sub>"

   # напрямую в БД CRM
   docker exec -it bionicpro-crm-db \
     psql -U crm_user -d crm_db \
     -c "SELECT subject, email, display_name FROM crm.user_profile;"
   docker exec -it bionicpro-crm-db \
     psql -U crm_user -d crm_db \
     -c "SELECT subject, granted, created_at FROM crm.user_consent;"
   ```
6. **Проверка отказа:** войдя новым Яндекс-аккаунтом и нажав «Не разрешаю»,
   убедиться, что в `crm.user_consent` записан `granted=false`, а в
   `crm.user_profile` профиля нет.

✅ Ожидание: профиль из Яндекса попадает в CRM **только** после явного согласия;
решение (в т.ч. отказ) фиксируется в журнале согласий.

---

## Полезные адреса

- Frontend: <http://localhost:3000>
- Keycloak admin: <http://localhost:8080> (`admin` / `admin`)
- phpLDAPadmin: <http://localhost:8081> (`cn=admin,dc=example,dc=com` / `admin`)
- CRM API (Swagger): <http://localhost:8090/docs>
- Auth Proxy health: <http://localhost:8000/health>

## Остановка

```bash
docker compose down          # остановить
docker compose down -v       # остановить и удалить тома (сброс данных)
```
