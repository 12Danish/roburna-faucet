from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
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
    siwe_domain: str = "localhost:3000"
    siwe_uri: str = "http://localhost:3000"
    challenge_ttl_seconds: int = Field(default=300, ge=60, le=900)

    @field_validator("database_url", mode="before")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        if not isinstance(value, str) or not value.startswith(("postgresql://", "postgres://")):
            raise ValueError("database_url must use postgresql:// or postgres://")
        return value

    @model_validator(mode="after")
    def validate_siwe_origin(self) -> "Settings":
        parsed_uri = urlsplit(self.siwe_uri)
        if (
            parsed_uri.scheme not in {"http", "https"}
            or not parsed_uri.netloc
            or parsed_uri.username is not None
            or parsed_uri.password is not None
            or parsed_uri.fragment
            or parsed_uri.netloc != self.siwe_domain
        ):
            raise ValueError("siwe_domain must match the host and port in siwe_uri")
        return self

    def resolved_chains_config(self) -> Path:
        path = self.chains_config
        if not path.is_absolute():
            path = BACKEND_ROOT / path
        return path.resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()
