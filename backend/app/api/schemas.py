from pydantic import BaseModel, ConfigDict, HttpUrl


class ChainResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chain_id: int
    name: str
    currency_symbol: str
    currency_decimals: int
    payout_amount_wei: str
    cooldown_seconds: int
    explorer_url: HttpUrl | None
    faucet_address: str


class ChainListResponse(BaseModel):
    chains: list[ChainResponse]


class HealthResponse(BaseModel):
    status: str
    checks: dict[str, str]
