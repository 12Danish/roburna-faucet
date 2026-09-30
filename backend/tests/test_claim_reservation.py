import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from typing import Iterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.services.claims import (
    ClaimAlreadyPending,
    ClaimReservation,
    WalletCooldownActive,
    reserve_claim,
)
from app.db.models import Challenge, Claim, TransactionAttempt
from app.db.engine import create_database_engine, create_session_factory

BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def database() -> Iterator[sessionmaker[Session]]:
    database_url = os.getenv("FAUCET_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("set FAUCET_TEST_DATABASE_URL to a dedicated PostgreSQL test database")

    engine = create_database_engine(database_url)
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    yield sessions
    engine.dispose()


def test_concurrent_reservations_for_one_wallet_create_one_claim(database: sessionmaker[Session]) -> None:
    chain_id = 31337
    wallet = "0x" + secrets.token_hex(20)
    challenge_ids = [uuid4(), uuid4(), uuid4()]

    with database.begin() as session:
        session.add_all(
            Challenge(
                id=challenge_id,
                chain_id=chain_id,
                wallet_address=wallet,
                nonce=uuid4().hex,
                message="test challenge",
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            )
            for challenge_id in challenge_ids
        )

    barrier = Barrier(2)

    def reserve(challenge_id: UUID) -> ClaimReservation | str:
        barrier.wait(timeout=5)
        try:
            return reserve_claim(
                database,
                chain_id=chain_id,
                wallet_address=wallet,
                challenge_id=challenge_id,
                amount_wei=10**18,
                cooldown_seconds=24 * 60 * 60,
            )
        except ClaimAlreadyPending:
            return "pending"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(reserve, challenge_ids[:2]))

        assert sum(isinstance(result, ClaimReservation) for result in results) == 1
        assert results.count("pending") == 1

        with database() as session:
            claim_count = len(
                session.scalars(
                    select(Claim.id).where(Claim.chain_id == chain_id, Claim.wallet_address == wallet)
                ).all()
            )
        assert claim_count == 1

        first_claim = next(result for result in results if isinstance(result, ClaimReservation))
        with database.begin() as session:
            session.get(Claim, first_claim.claim_id).status = "broadcast_unknown"
        with pytest.raises(ClaimAlreadyPending):
            reserve_claim(
                database,
                chain_id=chain_id,
                wallet_address=wallet,
                challenge_id=challenge_ids[1],
                amount_wei=10**18,
                cooldown_seconds=24 * 60 * 60,
            )

        with database.begin() as session:
            failed_claim = session.get(Claim, first_claim.claim_id)
            failed_claim.status = "failed"
            failed_claim.failed_at = datetime.now(timezone.utc)
            failed_claim.failure_code = "test_revert"
        second_claim = reserve_claim(
            database,
            chain_id=chain_id,
            wallet_address=wallet,
            challenge_id=challenge_ids[1],
            amount_wei=10**18,
            cooldown_seconds=24 * 60 * 60,
        )

        with database.begin() as session:
            confirmed_claim = session.get(Claim, second_claim.claim_id)
            confirmed_claim.status = "confirmed"
            confirmed_claim.failed_at = None
            confirmed_claim.confirmed_at = datetime.now(timezone.utc)
        with pytest.raises(WalletCooldownActive):
            reserve_claim(
                database,
                chain_id=chain_id,
                wallet_address=wallet,
                challenge_id=challenge_ids[2],
                amount_wei=10**18,
                cooldown_seconds=24 * 60 * 60,
            )
    finally:
        with database.begin() as session:
            session.execute(
                delete(Claim).where(Claim.chain_id == chain_id, Claim.wallet_address == wallet)
            )
            session.execute(delete(Challenge).where(Challenge.id.in_(challenge_ids)))
