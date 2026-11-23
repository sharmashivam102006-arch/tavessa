"""Configuration — single source of truth for settings.

All secrets and environment-specific values come from environment
variables (``.env`` locally, never committed). No module reads
``os.environ`` directly; everything goes through ``settings`` here.
See ``.env.example`` at the repo root for every key.
"""
import logging
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

_DEV_SECRET = "dev-only-change-me-in-production"


class Settings(BaseSettings):
    """Application settings, loaded from the environment."""

    model_config = SettingsConfigDict(
        # .env lives at the repo root; also accept one next to the backend
        # so `uvicorn` works whether it is started from tavessa/ or backend/.
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = Field(default="development")
    secret_key: str = Field(default=_DEV_SECRET, alias="SECRET_KEY")
    database_url: str = Field(
        default="postgresql+psycopg://tavessa:tavessa@localhost:5432/tavessa",
        alias="DATABASE_URL",
    )
    frontend_url: str = Field(default="http://localhost:5173", alias="FRONTEND_URL")
    backend_url: str = Field(default="http://localhost:8000", alias="BACKEND_URL")
    cors_origins: str = Field(
        default="http://localhost:5173,http://localhost:4173",
        alias="CORS_ORIGINS",
    )
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    access_token_expire_minutes: int = Field(
        default=15, alias="ACCESS_TOKEN_EXPIRE_MINUTES"
    )
    # Kitchen Display System — DEV-ONLY access key (Stage 4). The KDS
    # has no staff auth yet (that lands with JWT login in a later
    # stage), so in non-production environments the kitchen endpoints
    # and WebSocket accept this shared key instead. In production the
    # kitchen routes refuse to serve at all (503) until staff auth
    # exists. Never commit a real key; the default is dev-only.
    kitchen_dev_key: str = Field(default="dev-kitchen-only", alias="KITCHEN_DEV_KEY")

    @field_validator("environment")
    @classmethod
    def _check_environment(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"development", "staging", "production"}:
            raise ValueError("ENVIRONMENT must be development, staging or production")
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def validate_for_startup(self) -> None:
        """Fail fast on unsafe production configuration."""
        if self.is_production and self.secret_key == _DEV_SECRET:
            raise RuntimeError(
                "SECRET_KEY is still the shipped dev default and "
                "ENVIRONMENT=production. Set a long random SECRET_KEY first."
            )
        if self.is_production and "localhost" in self.database_url:
            logger.warning(
                "DATABASE_URL points at localhost while ENVIRONMENT=production."
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
