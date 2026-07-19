# LDAP-федерация представительства

Представительство BionicPRO хранит учётные записи в собственном LDAP
(data residency). Keycloak аутентифицирует пользователей через федерацию
(`READ_ONLY`) и синхронизирует LDAP-группы в realm-роли.

## Данные LDAP

Base DN: `dc=example,dc=com`. Bootstrap: [`config.ldif`](config.ldif).

- `ou=People` — пользователи: `john.doe`, `jane.smith`, `alex.johnson` (пароль `password`).
- `ou=Groups` — роли: `cn=user` → jane.smith; `cn=prothetic_user` → john.doe, alex.johnson.

## Настройка Keycloak

User Federation описана в `realm-export.json` (секция `components`) и
импортируется автоматически. Ключевое:

| Параметр | Значение |
|----------|----------|
| `connectionUrl` | `ldap://openldap:389` |
| `usersDn` | `ou=People,dc=example,dc=com` |
| `editMode` | `READ_ONLY` (Keycloak не пишет в каталог) |
| `usernameLDAPAttribute` | `uid` |

**role-ldap-mapper**: группы из `ou=Groups` (класс `groupOfNames`, membership
по DN) маппятся в одноимённые realm-роли — единая ролевая модель для всех
представительств.

## ⚠️ Известная особенность

В [`config.ldif`](config.ldif) DN Alex — `uid=alex,...`, а группа ссылается на
`member: uid=alex.johnson,...`. Маппинг идёт по DN, поэтому Alex не получит
роль `prothetic_user`, пока его DN не исправлен на `uid=alex.johnson,...`.

## Проверка

```bash
docker compose up -d --build

# LDAP отдаёт пользователей
docker exec bionicpro-openldap \
  ldapsearch -x -H ldap://localhost -b "dc=example,dc=com" \
  -D "cn=admin,dc=example,dc=com" -w admin "(objectClass=inetOrgPerson)" dn

# phpLDAPadmin: http://localhost:8081 (cn=admin,dc=example,dc=com / admin)
# Keycloak: http://localhost:8080 (admin/admin)
#   User Federation → ldap-bionicpro → Synchronize all users
```

Вход `john.doe/password` через <http://localhost:3000> → получает роль `prothetic_user`.
