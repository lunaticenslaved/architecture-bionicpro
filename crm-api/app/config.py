"""Конфигурация CRM-сервиса."""
import os


class Settings:
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql://crm_user:crm_password@crm_db:5432/crm_db",
    )
    # Issuer Keycloak (внутренний адрес) — для получения JWKS и валидации токена.
    KEYCLOAK_URL: str = os.getenv("KEYCLOAK_URL", "http://keycloak:8080")
    KEYCLOAK_REALM: str = os.getenv("KEYCLOAK_REALM", "reports-realm")
    # Audience/issuer-проверки в учебном примере упрощены; в проде обязательны.

    @property
    def issuer(self) -> str:
        return f"{self.KEYCLOAK_URL}/realms/{self.KEYCLOAK_REALM}"

    @property
    def jwks_uri(self) -> str:
        return f"{self.issuer}/protocol/openid-connect/certs"


settings = Settings()
