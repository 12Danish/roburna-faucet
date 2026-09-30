import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, PositiveInt, SecretStr, model_validator


class ChainDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chain_id: int = Field(gt=0, le=2**256 - 1)
    name: str = Field(min_length=1, max_length=80)
    rpc_url_key: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    deployment_file: Path
    currency_symbol: str = Field(min_length=1, max_length=12)
    currency_decimals: int = Field(ge=0, le=36)
    payout_amount_wei: int = Field(gt=0, le=2**256 - 1)
    cooldown_seconds: PositiveInt = 86400
    confirmation_depth: PositiveInt = 1
    fee_mode: Literal["legacy", "eip1559"]
    explorer_url: HttpUrl | None = None
    enabled: bool = False
    distributor_address: str


class ChainsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chains: list[ChainDefinition]

    @model_validator(mode="after")
    def chain_ids_are_unique(self) -> "ChainsFile":
        ids = [chain.chain_id for chain in self.chains]
        if len(ids) != len(set(ids)):
            raise ValueError("chain_id values must be unique")
        return self


def load_chain_definitions(
    config_path: Path,
    rpc_urls: dict[str, SecretStr],
) -> list[tuple[ChainDefinition, str, Path]]:
    try:
        raw_data = json.loads(config_path.read_text(encoding="utf-8"))
        config = ChainsFile.model_validate(raw_data)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Chain configuration file not found: {config_path}") from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(f"Invalid chain configuration in {config_path}: {exc}") from exc

    result: list[tuple[ChainDefinition, str, Path]] = []
    for definition in config.chains:
        if not definition.enabled:
            continue
        rpc_url = rpc_urls.get(definition.rpc_url_key)
        if rpc_url is None or not rpc_url.get_secret_value():
            raise RuntimeError(
                f"Enabled chain {definition.chain_id} requires RPC URL key "
                f"{definition.rpc_url_key} in BACKEND_RPC_URLS"
            )
        deployment_path = definition.deployment_file
        if not deployment_path.is_absolute():
            deployment_path = config_path.parent / deployment_path
        result.append((definition, rpc_url.get_secret_value(), deployment_path.resolve()))
    return result
