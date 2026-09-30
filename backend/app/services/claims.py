import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import ACTIVE_CLAIM_STATES, Challenge, Claim, TransactionAttempt


class ReservationRejected(Exception):
    """Base class for claim reservation decisions that the API can map to HTTP."""


class ChallengeUnavailable(ReservationRejected):
    """Challenge is missing, expired, consumed, or belongs to another wallet/chain."""


class ClaimAlreadyPending(ReservationRejected):
    """This wallet already has a claim whose outcome is not final."""


class WalletCooldownActive(ReservationRejected):
    def __init__(self, next_eligible_at: datetime) -> None:
        self.next_eligible_at = next_eligible_at
        super().__init__("wallet is still in its claim cooldown")


@dataclass(frozen=True)
class ClaimReservation:
    claim_id: UUID
    status: str
    reserved_at: datetime


@dataclass(frozen=True)
class ClaimSnapshot:
    claim_id: UUID
    chain_id: int
    status: str
    amount_wei: int
    reserved_at: datetime
    submitted_at: datetime | None
    confirmed_at: datetime | None
    failure_code: str | None
    transaction_hash: str | None


def get_claim_snapshot(sessions: sessionmaker[Session], claim_id: UUID) -> ClaimSnapshot | None:
    """Read a claim and its latest signed attempt for API polling."""
    with sessions() as session:
        claim = session.get(Claim, claim_id)
        if claim is None:
            return None
        attempt_hash = session.scalar(
            select(TransactionAttempt.transaction_hash)
            .where(TransactionAttempt.claim_id == claim_id)
            .order_by(TransactionAttempt.attempt_number.desc())
            .limit(1)
        )
        return ClaimSnapshot(
            claim_id=claim.id,
            chain_id=int(claim.chain_id),
            status=claim.status,
            amount_wei=int(claim.amount_wei),
            reserved_at=claim.reserved_at,
            submitted_at=claim.submitted_at,
            confirmed_at=claim.confirmed_at,
            failure_code=claim.failure_code,
            transaction_hash=attempt_hash,
        )


def reserve_claim(
    sessions: sessionmaker[Session],
    *,
    chain_id: int,
    wallet_address: str,
    challenge_id: UUID,
    amount_wei: int,
    cooldown_seconds: int,
) -> ClaimReservation:
    """Consume a verified challenge and reserve one payout in one DB transaction.

    The caller must verify the wallet signature before calling this function. No RPC
    calls belong here; the short transaction protects only PostgreSQL state.
    """
    wallet = _normalize_wallet(wallet_address)
    if not 0 < chain_id < 2**256:
        raise ValueError("chain_id must be a positive uint256")
    if not 0 < amount_wei < 2**256:
        raise ValueError("amount_wei must be a positive uint256")
    if cooldown_seconds <= 0:
        raise ValueError("cooldown_seconds must be positive")

    lock_key = f"{chain_id}:{wallet}"
    with sessions.begin() as session:
        # Serialize reservations for this chain/wallet, even before a claim row exists.
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": lock_key},
        )
        challenge = session.scalar(
            select(Challenge).where(Challenge.id == challenge_id).with_for_update()
        )
        if challenge is None:
            raise ChallengeUnavailable

        now = session.scalar(select(func.clock_timestamp()))
        if (
            int(challenge.chain_id) != chain_id
            or challenge.wallet_address != wallet
            or challenge.consumed_at is not None
            or challenge.expires_at <= now
        ):
            raise ChallengeUnavailable

        pending_id = session.scalar(
            select(Claim.id)
            .where(
                Claim.chain_id == Decimal(chain_id),
                Claim.wallet_address == wallet,
                Claim.status.in_(ACTIVE_CLAIM_STATES),
            )
            .limit(1)
        )
        if pending_id is not None:
            raise ClaimAlreadyPending

        next_eligible_at = session.scalar(
            select(Claim.confirmed_at + timedelta(seconds=cooldown_seconds))
            .where(
                Claim.chain_id == Decimal(chain_id),
                Claim.wallet_address == wallet,
                Claim.status == "confirmed",
            )
            .order_by(Claim.confirmed_at.desc())
            .limit(1)
        )
        if next_eligible_at is not None and next_eligible_at > now:
            raise WalletCooldownActive(next_eligible_at)

        consumed_id = session.scalar(
            update(Challenge)
            .where(
                Challenge.id == challenge_id,
                Challenge.consumed_at.is_(None),
                Challenge.expires_at > func.clock_timestamp(),
            )
            .values(consumed_at=func.clock_timestamp())
            .returning(Challenge.id)
        )
        if consumed_id is None:
            raise ChallengeUnavailable

        claim = Claim(
            chain_id=Decimal(chain_id),
            wallet_address=wallet,
            challenge_id=challenge_id,
            amount_wei=Decimal(amount_wei),
            status="reserved",
        )
        session.add(claim)
        session.flush()
        reservation = ClaimReservation(
            claim_id=claim.id,
            status=claim.status,
            reserved_at=claim.reserved_at,
        )

    return reservation


def _normalize_wallet(address: str) -> str:
    if re.fullmatch(r"0[xX][0-9a-fA-F]{40}", address) is None:
        raise ValueError("wallet_address must be a 20-byte EVM address")
    return "0x" + address[2:].lower()
