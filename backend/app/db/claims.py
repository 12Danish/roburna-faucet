import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from psycopg_pool import ConnectionPool


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


def reserve_claim(
    database: ConnectionPool,
    *,
    chain_id: int,
    wallet_address: str,
    challenge_id: UUID,
    amount_wei: int,
    cooldown_seconds: int,
) -> ClaimReservation:
    """Consume a verified challenge and reserve one payout in a single DB transaction.

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
    with database.connection() as connection:
        # Serialize reservations for this chain/wallet, including when no claim row exists yet.
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (lock_key,),
        )
        challenge = connection.execute(
            """
            SELECT chain_id, wallet_address, expires_at, consumed_at
            FROM challenges
            WHERE id = %s
            FOR UPDATE
            """,
            (challenge_id,),
        ).fetchone()
        if challenge is None:
            raise ChallengeUnavailable

        challenge_chain_id, challenge_wallet, expires_at, consumed_at = challenge
        now = connection.execute("SELECT clock_timestamp()").fetchone()[0]
        if (
            int(challenge_chain_id) != chain_id
            or challenge_wallet != wallet
            or consumed_at is not None
            or expires_at <= now
        ):
            raise ChallengeUnavailable

        pending = connection.execute(
            """
            SELECT 1
            FROM claims
            WHERE chain_id = %s
              AND wallet_address = %s
              AND status IN ('reserved', 'submitted', 'broadcast_unknown')
            LIMIT 1
            """,
            (chain_id, wallet),
        ).fetchone()
        if pending is not None:
            raise ClaimAlreadyPending

        cooldown = connection.execute(
            """
            SELECT confirmed_at + (%s * INTERVAL '1 second')
            FROM claims
            WHERE chain_id = %s
              AND wallet_address = %s
              AND status = 'confirmed'
            ORDER BY confirmed_at DESC
            LIMIT 1
            """,
            (cooldown_seconds, chain_id, wallet),
        ).fetchone()
        if cooldown is not None and cooldown[0] > now:
            raise WalletCooldownActive(cooldown[0])

        consumed = connection.execute(
            """
            UPDATE challenges
            SET consumed_at = clock_timestamp()
            WHERE id = %s
              AND consumed_at IS NULL
              AND expires_at > clock_timestamp()
            RETURNING id
            """,
            (challenge_id,),
        ).fetchone()
        if consumed is None:
            raise ChallengeUnavailable

        claim_id = uuid4()
        claim = connection.execute(
            """
            INSERT INTO claims (id, chain_id, wallet_address, challenge_id, amount_wei, status)
            VALUES (%s, %s, %s, %s, %s, 'reserved')
            RETURNING id, status, reserved_at
            """,
            (claim_id, chain_id, wallet, challenge_id, amount_wei),
        ).fetchone()

    return ClaimReservation(claim_id=claim[0], status=claim[1], reserved_at=claim[2])


def _normalize_wallet(address: str) -> str:
    if re.fullmatch(r"0[xX][0-9a-fA-F]{40}", address) is None:
        raise ValueError("wallet_address must be a 20-byte EVM address")
    return "0x" + address[2:].lower()
