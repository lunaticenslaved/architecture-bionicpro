# LDAP-федерация для представительства BionicPRO в другой стране

## Цель

Представительство BionicPRO в другой стране хранит учётные записи своих
пользователей в собственном каталоге LDAP (data residency — данные остаются
в стране по требованиям локального законодательства). Keycloak должен ходить
в этот LDAP за аутентификацией и автоматически синхронизировать роли
пользователей разных представительств в единую ролевую модель realm.

Что сделано:

1. Развёрнут LDAP-сервер **OpenLDAP** с bootstrap-данными из
   [`ldap/config.ldif`](../ldap/config.ldif).
2. Настроена **User Federation** в Keycloak — realm `reports-realm` ходит
   в LDAP за аутентификацией (`READ_ONLY`).
3. Добавлен **role-ldap-mapper** — LDAP-группы (`ou=Groups`) синхронизируются
   в realm-роли Keycloak, что даёт единую ролевую модель для всех
   представительств.

## Компоненты

| Сервис | Образ | Порт | Назначение |
|--------|-------|------|------------|
| `openldap` | `osixia/openldap:1.5.0` | 389 / 636 | Каталог пользователей представительства |
| `phpldapadmin` | `osixia/phpldapadmin:0.9.0` | 8081 | Веб-UI для инспекции LDAP |
| `keycloak` | `quay.io/keycloak/keycloak:21.1` | 8080 | Identity Broker + User Federation |

## Данные LDAP

Base DN: `dc=example,dc=com` (совпадает с `LDAP_DOMAIN=example.com`).

- `ou=People` — пользователи (`inetOrgPerson`): `john.doe`, `jane.smith`,
  `alex.johnson`. Пароль у всех — `password`.
- `ou=Groups` — роли (`groupOfNames`):
  - `cn=user` → `jane.smith`
  - `cn=prothetic_user` → `john.doe`, `alex.johnson`

## Настройка Keycloak (realm-export.json)

User Federation описана декларативно в секции `components` →
`org.keycloak.storage.UserStorageProvider` и импортируется автоматически
при старте (`--import-realm`).

Ключевые параметры провайдера `ldap`:

| Параметр | Значение | Комментарий |
|----------|----------|-------------|
| `connectionUrl` | `ldap://openldap:389` | адрес сервиса в docker-сети |
| `usersDn` | `ou=People,dc=example,dc=com` | где искать пользователей |
| `bindDn` | `cn=admin,dc=example,dc=com` | сервисная учётка |
| `editMode` | `READ_ONLY` | Keycloak не пишет в чужой каталог |
| `usernameLDAPAttribute` | `uid` | логин = uid |
| `userObjectClasses` | `inetOrgPerson` | класс объектов |
| `importEnabled` | `true` | пользователи импортируются в realm при первом входе |

### Маппинг ролей (role-ldap-mapper)

```
mode                          = READ_ONLY
roles.dn                      = ou=Groups,dc=example,dc=com
role.name.ldap.attribute      = cn
role.object.classes           = groupOfNames
membership.ldap.attribute     = member
membership.attribute.type     = DN
use.realm.roles.mapping       = true
user.roles.retrieve.strategy  = LOAD_ROLES_BY_MEMBER_ATTRIBUTE
```

LDAP-группа `prothetic_user` маппится на одноимённую realm-роль
`prothetic_user`, `user` — на `user`. Так пользователи из LDAP-каталога
представительства получают те же роли, что и локальные пользователи realm,
и попадают под единый access-control (`prothetic_user` — доступ к отчётам).

## Важно: несогласованность в исходном ldif

В [`ldap/config.ldif`](../ldap/config.ldif) у пользователя Alex DN записан как
`uid=alex,...`, а атрибут `uid: alex.johnson`. При этом группа
`prothetic_user` ссылается на него по DN `member: uid=alex.johnson,...`.
Так как маппинг членства идёт по **DN** (`membership.attribute.type=DN`),
запись `uid=alex.johnson` в группе не находит реальный объект `uid=alex`,
и Alex не получит роль `prothetic_user`.

Чтобы синхронизация ролей работала для всех пользователей, DN записи Alex
должен совпадать с тем, как на него ссылается группа — то есть
`dn: uid=alex.johnson,ou=People,dc=example,dc=com`.

## Запуск и проверка

```bash
# Поднять всё окружение
docker compose up -d --build

# Проверить, что LDAP отдаёт пользователей
docker exec bionicpro-openldap \
  ldapsearch -x -H ldap://localhost -b "dc=example,dc=com" \
  -D "cn=admin,dc=example,dc=com" -w admin "(objectClass=inetOrgPerson)" dn

# Веб-UI LDAP:      http://localhost:8081
#   Login DN:       cn=admin,dc=example,dc=com
#   Password:       admin

# Keycloak admin:   http://localhost:8080  (admin / admin)
#   User Federation → ldap-bionicpro → Synchronize all users
```

Проверка входа: пользователь `john.doe` / `password` должен успешно
авторизоваться через Keycloak и получить realm-роль `prothetic_user`.
