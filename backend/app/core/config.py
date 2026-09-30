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
    rate_limit_hash_secret: SecretStr = Field(min_length=32)
    trusted_proxy_cidrs: list[str] = Field(default_factory=list)
    distributor_keystore_path: Path | None = None
    distributor_keystore_password: SecretStr | None = None
    worker_poll_interval_seconds: int = Field(default=2, ge=1, le=60)
    transaction_replacement_after_seconds: int = Field(default=180, ge=30, le=3600)
    transaction_fee_bump_percent: int = Field(default=20, ge=10, le=100)
    transaction_max_replacements: int = Field(default=3, ge=0, le=10)

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
        try:
            import ipaddress

            for cidr in self.trusted_proxy_cidrs:
                ipaddress.ip_network(cidr, strict=False)
        except ValueError as exc:
            raise ValueError("trusted_proxy_cidrs must contain valid IP networks") from exc
        if (self.distributor_keystore_path is None) != (self.distributor_keystore_password is None):
            raise ValueError(
                "distributor_keystore_path and distributor_keystore_password must be configured together"
            )
        return self

    def resolved_chains_config(self) -> Path:
        path = self.chains_config
        if not path.is_absolute():
            path = BACKEND_ROOT / path
        return path.resolve()

    def resolved_distributor_keystore(self) -> Path | None:
        path = self.distributor_keystore_path
        if path is None:
            return None
        if not path.is_absolute():
            path = BACKEND_ROOT / path
        return path.resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()
