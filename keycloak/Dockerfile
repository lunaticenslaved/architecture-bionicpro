# Кастомный образ Keycloak с нативным провайдером «Яндекс» (OAuth 2.0, без openid).
#
# Зачем: встроенный generic-провайдер типа "oidc" принудительно добавляет scope
# "openid" (требование спецификации OIDC), а Яндекс — это OAuth 2.0, не OIDC,
# и отвечает invalid_scope. Расширение playa-ru/keycloak-russian-providers даёт
# нативный тип провайдера "yandex" на чистом OAuth2 без openid.
#
# Источник: https://github.com/playa-ru/keycloak-russian-providers
FROM quay.io/keycloak/keycloak:21.1.1

# JAR провайдеров российских соцсетей (Яндекс/VK/OK/Mail.ru) под Keycloak 21.1.1.
ADD --chown=keycloak:keycloak \
    https://repo1.maven.org/maven2/ru/playa/keycloak/keycloak-russian-providers/21.1.1.rsp/keycloak-russian-providers-21.1.1.rsp.jar \
    /opt/keycloak/providers/keycloak-russian-providers.jar

# Транзитивные зависимости, требуемые расширением (см. инструкцию по установке).
ADD --chown=keycloak:keycloak \
    https://repo1.maven.org/maven2/com/jayway/jsonpath/json-path/2.7.0/json-path-2.7.0.jar \
    /opt/keycloak/providers/json-path-2.7.0.jar
ADD --chown=keycloak:keycloak \
    https://repo1.maven.org/maven2/net/minidev/json-smart/2.4.7/json-smart-2.4.7.jar \
    /opt/keycloak/providers/json-smart-2.4.7.jar

# Пересобрать конфигурацию Keycloak с новыми провайдерами (валидация загрузки JAR).
RUN /opt/keycloak/bin/kc.sh build
