"""Конфигурация сервиса bionicpro-auth.

Все значения читаются из переменных окружения, чтобы сервис оставался
stateless и легко разворачивался в разных окружениях.
"""
import os


class Settings:
    # --- Keycloak / OIDC ---
    KEYCLOAK_URL: str = os.getenv("KEYCLOAK_URL", "http://keycloak:8080")
    KEYCLOAK_REALM: str = os.getenv("KEYCLOAK_REALM", "reports-realm")
    CLIENT_ID: str = os.getenv("KEYCLOAK_CLIENT_ID", "bionicpro-auth")
    CLIENT_SECRET: str = os.getenv(
        "KEYCLOAK_CLIENT_SECRET", "bionicpro-auth-secret-CHANGE-ME"
    )

    # Публичный (browser-facing) URL Keycloak — нужен для редиректа пользователя.
    # Внутри docker-сети браузер не видит имя сервиса "keycloak", поэтому
    # для авторизационного редиректа используется внешний адрес.
    KEYCLOAK_PUBLIC_URL: str = os.getenv(
        "KEYCLOAK_PUBLIC_URL", "http://localhost:8080"
    )

    # --- Адреса приложения ---
    # Куда Keycloak возвращает пользователя после логина (redirect_uri).
    REDIRECT_URI: str = os.getenv(
        "REDIRECT_URI", "http://localhost:8000/auth/callback"
    )
    # Куда вернуть пользователя во фронтенд после успешного логина.
    FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:3000")

    # --- Upstream (защищаемый ресурс) ---
    # Report API, к которому bionicpro-auth проксирует запросы, подставляя токен.
    UPSTREAM_API_URL: str = os.getenv("UPSTREAM_API_URL", "http://reports-api:8080")

    # --- CRM API (владелец клиентских данных) ---
    # Куда Auth Proxy отправляет профиль из Яндекса и согласия пользователя.
    CRM_API_URL: str = os.getenv("CRM_API_URL", "http://crm-api:8080")

    # --- Identity Brokering (Яндекс ID) ---
    # Alias внешнего IdP в Keycloak. По claim identity_provider в токене
    # сервис понимает, что пользователь вошёл через Яндекс.
    YANDEX_IDP_ALIAS: str = os.getenv("YANDEX_IDP_ALIAS", "yandex")
    # Endpoint Яндекса для получения профиля по broker access_token.
    YANDEX_USERINFO_URL: str = os.getenv(
        "YANDEX_USERINFO_URL", "https://login.yandex.ru/info?format=json"
    )

    # --- Redis (распределённый кеш сессий) ---
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://redis:6379/0")

    # --- Сессии ---
    SESSION_COOKIE_NAME: str = os.getenv("SESSION_COOKIE_NAME", "session_id")
    # TTL сессии должен быть больше времени жизни access_token (120 сек),
    # чтобы при истечении access_token сервис успел обновить его по refresh_token.
    SESSION_TTL_SECONDS: int = int(os.getenv("SESSION_TTL_SECONDS", "1800"))  # 30 мин
    # Secure-флаг у cookie. В локальной разработке по http выключаем.
    COOKIE_SECURE: bool = os.getenv("COOKIE_SECURE", "false").lower() == "true"

    # --- Шифрование refresh_token в хранилище ---
    # Ключ Fernet (base64, 32 байта). В проде задаётся через секрет.
    FERNET_KEY: str = os.getenv("FERNET_KEY", "")

    @property
    def issuer(self) -> str:
        return f"{self.KEYCLOAK_URL}/realms/{self.KEYCLOAK_REALM}"

    @property
    def public_issuer(self) -> str:
        return f"{self.KEYCLOAK_PUBLIC_URL}/realms/{self.KEYCLOAK_REALM}"

    @property
    def token_endpoint(self) -> str:
        return f"{self.issuer}/protocol/openid-connect/token"

    @property
    def authorization_endpoint(self) -> str:
        # Авторизация происходит в браузере пользователя → публичный URL.
        return f"{self.public_issuer}/protocol/openid-connect/auth"

    @property
    def logout_endpoint(self) -> str:
        return f"{self.public_issuer}/protocol/openid-connect/logout"

    def broker_token_endpoint(self, alias: str) -> str:
        """Endpoint Keycloak для получения сохранённого токена внешнего IdP.

        Требует storeToken=true у IdP и роль broker read-token у клиента.
        """
        return f"{self.issuer}/broker/{alias}/token"


settings = Settings()
