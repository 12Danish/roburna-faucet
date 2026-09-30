from datetime import datetime
from typing import Literal
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


class ClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    challenge_id: UUID
    chain_id: int = Field(gt=0, le=2**256 - 1)
    wallet_address: str = Field(pattern=r"^0[xX][0-9a-fA-F]{40}$")
    message: str = Field(min_length=1, max_length=4096)
    signature: str = Field(min_length=1, max_length=256)


class ClaimResponse(BaseModel):
    claim_id: UUID
    chain_id: int
    status: Literal["reserved", "submitted", "broadcast_unknown", "confirmed", "failed"]
    amount_wei: str
    transaction_hash: str | None
    transaction_url: HttpUrl | None
    reserved_at: datetime
    submitted_at: datetime | None
    confirmed_at: datetime | None
    next_eligible_at: datetime | None
    failure_code: str | None
