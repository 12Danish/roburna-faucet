from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.dependencies import get_runtime
from app.api.schemas import ClaimRequest, ClaimResponse
from app.core.config import get_settings
from app.core.runtime import Runtime
from app.services.auth import (
    ChainLookupUnavailable,
    ChallengeUnavailable as AuthenticationChallengeUnavailable,
    ContractRecipient,
    InvalidWalletSignature,
    existing_claim_for_signed_challenge,
    verify_wallet_challenge,
)
from app.services.claims import (
    ChallengeUnavailable as ReservationChallengeUnavailable,
    ClaimAlreadyPending,
    ClaimSnapshot,
    WalletCooldownActive,
    get_claim_snapshot,
    reserve_claim,
)
from app.services.eligibility import FaucetUnavailable, require_faucet_capacity
from app.services.rate_limits import (
    ClientAddressUnavailable,
    RateLimitExceeded,
    IdentityType,
    client_ip_from_request,
    enforce_request_rate_limit,
)

router = APIRouter(prefix="/claims", tags=["claims"])


def _rate_limit(
    runtime: Runtime,
    *,
    client_ip: str,
    wallet_address: str,
    secret: str,
    identity_types: tuple[IdentityType, ...],
) -> None:
    try:
        enforce_request_rate_limit(
            runtime.sessions,
            action="claim",
            client_ip=client_ip,
            wallet_address=wallet_address,
            hash_secret=secret,
            identity_types=identity_types,
        )
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "rate_limit_exceeded"},
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from None


def _response(snapshot: ClaimSnapshot, runtime: Runtime) -> ClaimResponse:
    chain = runtime.chains.get(snapshot.chain_id)
    next_eligible_at = (
        snapshot.confirmed_at + timedelta(seconds=chain.config.cooldown_seconds)
        if snapshot.confirmed_at is not None and chain is not None
        else None
    )
    transaction_url = None
    if snapshot.transaction_hash and chain is not None and chain.config.explorer_url:
        transaction_url = (
            str(chain.config.explorer_url).rstrip("/") + "/tx/" + snapshot.transaction_hash
        )
    return ClaimResponse(
        claim_id=snapshot.claim_id,
        chain_id=snapshot.chain_id,
        status=snapshot.status,
        amount_wei=str(snapshot.amount_wei),
        transaction_hash=snapshot.transaction_hash,
        transaction_url=transaction_url,
        reserved_at=snapshot.reserved_at,
        submitted_at=snapshot.submitted_at,
        confirmed_at=snapshot.confirmed_at,
        next_eligible_at=next_eligible_at,
        failure_code=snapshot.failure_code,
    )


def _existing_claim(request: ClaimRequest, runtime: Runtime) -> ClaimResponse | None:
    claim_id = existing_claim_for_signed_challenge(
        runtime.sessions,
        challenge_id=request.challenge_id,
        message_text=request.message,
        signature=request.signature,
        wallet_address=request.wallet_address,
        chain_id=request.chain_id,
    )
    if claim_id is None:
        return None
    snapshot = get_claim_snapshot(runtime.sessions, claim_id)
    if snapshot is None:
        raise HTTPException(status_code=503, detail={"code": "claim_unavailable"})
    return _response(snapshot, runtime)


@router.post("", response_model=ClaimResponse, status_code=status.HTTP_202_ACCEPTED)
def submit_claim(
    request: ClaimRequest,
    http_request: Request,
    runtime: Runtime = Depends(get_runtime),
) -> ClaimResponse:
    """Verify wallet ownership, enforce abuse limits, and durably reserve a payout."""
    settings = get_settings()
    try:
        client_ip = client_ip_from_request(http_request, settings.trusted_proxy_cidrs)
    except ClientAddressUnavailable:
        raise HTTPException(status_code=400, detail={"code": "client_address_unavailable"}) from None

    secret = settings.rate_limit_hash_secret.get_secret_value()
    _rate_limit(
        runtime,
        client_ip=client_ip,
        wallet_address=request.wallet_address,
        secret=secret,
        identity_types=("ip", "subnet"),
    )
    chain = runtime.chains.get(request.chain_id)
    if chain is None:
        raise HTTPException(status_code=404, detail={"code": "chain_not_enabled"})

    try:
        existing = _existing_claim(request, runtime)
        if existing is not None:
            return existing
        verify_wallet_challenge(
            runtime.sessions,
            challenge_id=request.challenge_id,
            message_text=request.message,
            signature=request.signature,
            wallet_address=request.wallet_address,
            chain_id=request.chain_id,
            domain=settings.siwe_domain,
            uri=settings.siwe_uri,
            chain_client=chain,
        )
    except AuthenticationChallengeUnavailable:
        # Another request may have consumed this challenge while we were verifying.
        try:
            existing = _existing_claim(request, runtime)
        except (AuthenticationChallengeUnavailable, InvalidWalletSignature):
            existing = None
        if existing is not None:
            return existing
        raise HTTPException(status_code=409, detail={"code": "challenge_unavailable"}) from None
    except InvalidWalletSignature:
        raise HTTPException(status_code=401, detail={"code": "invalid_wallet_signature"}) from None
    except ContractRecipient:
        raise HTTPException(status_code=422, detail={"code": "contract_recipient"}) from None
    except ChainLookupUnavailable:
        raise HTTPException(status_code=503, detail={"code": "chain_unavailable"}) from None

    try:
        require_faucet_capacity(chain)
    except FaucetUnavailable as exc:
        raise HTTPException(status_code=503, detail={"code": exc.code}) from None

    _rate_limit(
        runtime,
        client_ip=client_ip,
        wallet_address=request.wallet_address,
        secret=secret,
        identity_types=("wallet",),
    )
    try:
        reservation = reserve_claim(
            runtime.sessions,
            chain_id=request.chain_id,
            wallet_address=request.wallet_address,
            challenge_id=request.challenge_id,
            amount_wei=chain.config.payout_amount_wei,
            cooldown_seconds=chain.config.cooldown_seconds,
        )
    except ReservationChallengeUnavailable:
        existing = _existing_claim(request, runtime)
        if existing is not None:
            return existing
        raise HTTPException(status_code=409, detail={"code": "challenge_unavailable"}) from None
    except ClaimAlreadyPending:
        raise HTTPException(status_code=409, detail={"code": "claim_already_pending"}) from None
    except WalletCooldownActive as exc:
        retry_after = max(1, int((exc.next_eligible_at - datetime.now(timezone.utc)).total_seconds() + 0.999))
        raise HTTPException(
            status_code=429,
            detail={"code": "wallet_cooldown_active", "nextEligibleAt": exc.next_eligible_at.isoformat()},
            headers={"Retry-After": str(retry_after)},
        ) from None

    snapshot = get_claim_snapshot(runtime.sessions, reservation.claim_id)
    if snapshot is None:
        raise HTTPException(status_code=503, detail={"code": "claim_unavailable"})
    return _response(snapshot, runtime)


@router.get("/{claim_id}", response_model=ClaimResponse)
def get_claim(claim_id: UUID, runtime: Runtime = Depends(get_runtime)) -> ClaimResponse:
    snapshot = get_claim_snapshot(runtime.sessions, claim_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail={"code": "claim_not_found"})
    return _response(snapshot, runtime)
