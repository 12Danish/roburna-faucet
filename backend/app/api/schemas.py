from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


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


class ChallengeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wallet_address: str = Field(pattern=r"^0[xX][0-9a-fA-F]{40}$")
    chain_id: int = Field(gt=0, le=2**256 - 1)


class ChallengeResponse(BaseModel):
    challenge_id: UUID
    message: str
    expires_at: datetime
