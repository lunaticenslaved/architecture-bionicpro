"""Конфигурация Reports API (значения приходят из docker-compose)."""
import os


class Settings:
    # Keycloak: валидация Bearer JWT по JWKS realm-а.
    # Внутренний URL — для похода за ключами; issuer — канонический
    # (KC_HOSTNAME_URL=http://localhost:8080, см. docker-compose).
    KEYCLOAK_URL: str = os.getenv("KEYCLOAK_URL", "http://keycloak:8080")
    KEYCLOAK_PUBLIC_URL: str = os.getenv(
        "KEYCLOAK_PUBLIC_URL", "http://localhost:8080"
    )
    KEYCLOAK_REALM: str = os.getenv("KEYCLOAK_REALM", "reports-realm")

    # Роль, дающая доступ к отчётам о работе протеза.
    REPORTS_ROLE: str = os.getenv("REPORTS_ROLE", "prothetic_user")
    # Роль, которой разрешено смотреть отчёты любых пользователей.
    ADMIN_ROLE: str = os.getenv("ADMIN_ROLE", "administrator")

    # OLAP (ClickHouse) — источник готовой витрины.
    CLICKHOUSE_HOST: str = os.getenv("CLICKHOUSE_HOST", "clickhouse")
    CLICKHOUSE_PORT: int = int(os.getenv("CLICKHOUSE_PORT", "8123"))
    CLICKHOUSE_USER: str = os.getenv("CLICKHOUSE_USER", "etl_user")
    CLICKHOUSE_PASSWORD: str = os.getenv("CLICKHOUSE_PASSWORD", "etl_password")

    @property
    def issuer(self) -> str:
        return f"{self.KEYCLOAK_PUBLIC_URL}/realms/{self.KEYCLOAK_REALM}"

    @property
    def jwks_url(self) -> str:
        return (
            f"{self.KEYCLOAK_URL}/realms/{self.KEYCLOAK_REALM}"
            "/protocol/openid-connect/certs"
        )


settings = Settings()
