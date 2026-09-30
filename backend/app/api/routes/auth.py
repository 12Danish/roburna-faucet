from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from web3 import Web3

from app.api.dependencies import get_runtime
from app.api.schemas import ChallengeRequest, ChallengeResponse
from app.core.config import get_settings
from app.core.runtime import Runtime
from app.services.auth import create_wallet_challenge

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.post("/challenge", response_model=ChallengeResponse, status_code=status.HTTP_201_CREATED)
def create_challenge(
    request: ChallengeRequest,
    runtime: Runtime = Depends(get_runtime),
) -> ChallengeResponse:
    """Issue a short-lived SIWE message for a wallet to sign."""
    if request.chain_id not in runtime.chains:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="chain is not enabled")

    settings = get_settings()
    try:
        challenge = create_wallet_challenge(
            runtime.sessions,
            wallet_address=Web3.to_checksum_address(request.wallet_address),
            chain_id=request.chain_id,
            domain=settings.siwe_domain,
            uri=settings.siwe_uri,
            ttl_seconds=settings.challenge_ttl_seconds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    return ChallengeResponse(
        challenge_id=challenge.challenge_id,
        message=challenge.message,
        expires_at=challenge.expires_at,
    )
