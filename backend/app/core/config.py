from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BACKEND_",
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: SecretStr
    chains_config: Path = Path("config/chains.json")
    rpc_urls: dict[str, SecretStr] = Field(default_factory=dict)

    @field_validator("database_url", mode="before")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        if not isinstance(value, str) or not value.startswith(("postgresql://", "postgres://")):
            raise ValueError("database_url must use postgresql:// or postgres://")
        return value

    def resolved_chains_config(self) -> Path:
        path = self.chains_config
        if not path.is_absolute():
            path = BACKEND_ROOT / path
        return path.resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()
