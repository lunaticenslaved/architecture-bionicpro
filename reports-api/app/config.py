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

    # S3-совместимое объектное хранилище (Minio) для кэширования отчётов.
    S3_ENDPOINT_URL: str = os.getenv("S3_ENDPOINT_URL", "http://minio:9000")
    S3_ACCESS_KEY: str = os.getenv("S3_ACCESS_KEY", "minioadmin")
    S3_SECRET_KEY: str = os.getenv("S3_SECRET_KEY", "minioadmin")
    S3_BUCKET_NAME: str = os.getenv("S3_BUCKET_NAME", "bionicpro-reports")

    # CDN URL prefix — Nginx reverse proxy с кэшированием,
    # раздающий файлы из S3-хранилища через публичный URL.
    CDN_URL_PREFIX: str = os.getenv(
        "CDN_URL_PREFIX", "http://localhost:8083/bionicpro-reports"
    )

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
