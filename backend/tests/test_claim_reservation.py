import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Iterator
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from psycopg_pool import ConnectionPool

from app.core.config import get_settings
from app.db.claims import (
    ClaimAlreadyPending,
    ClaimReservation,
    WalletCooldownActive,
    reserve_claim,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def database() -> Iterator[ConnectionPool]:
    database_url = os.getenv("FAUCET_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("set FAUCET_TEST_DATABASE_URL to a dedicated PostgreSQL test database")

    previous_url = os.environ.get("BACKEND_DATABASE_URL")
    os.environ["BACKEND_DATABASE_URL"] = database_url
    get_settings.cache_clear()
    try:
        alembic_config = Config(str(BACKEND_ROOT / "alembic.ini"))
        alembic_config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
        command.upgrade(alembic_config, "head")
    finally:
        if previous_url is None:
            os.environ.pop("BACKEND_DATABASE_URL", None)
        else:
            os.environ["BACKEND_DATABASE_URL"] = previous_url
        get_settings.cache_clear()

    pool = ConnectionPool(
        conninfo=database_url,
        min_size=2,
        max_size=4,
        open=True,
        name="faucet-test",
    )
    pool.wait(timeout=5)
    yield pool
    pool.close()


def test_concurrent_reservations_for_one_wallet_create_one_claim(database: ConnectionPool) -> None:
    chain_id = 31337
    wallet = "0x" + secrets.token_hex(20)
    challenge_ids = [uuid4(), uuid4(), uuid4()]

    with database.connection() as connection:
        for challenge_id in challenge_ids:
            connection.execute(
                """
                INSERT INTO challenges (id, chain_id, wallet_address, nonce, message, expires_at)
                VALUES (%s, %s, %s, %s, %s, clock_timestamp() + INTERVAL '5 minutes')
                """,
                (challenge_id, chain_id, wallet, uuid4().hex, "test challenge"),
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

        with database.connection() as connection:
            claim_count = connection.execute(
                "SELECT count(*) FROM claims WHERE chain_id = %s AND wallet_address = %s",
                (chain_id, wallet),
            ).fetchone()[0]
        assert claim_count == 1

        first_claim = next(result for result in results if isinstance(result, ClaimReservation))
        with database.connection() as connection:
            connection.execute(
                "UPDATE claims SET status = 'broadcast_unknown' WHERE id = %s",
                (first_claim.claim_id,),
            )
        with pytest.raises(ClaimAlreadyPending):
            reserve_claim(
                database,
                chain_id=chain_id,
                wallet_address=wallet,
                challenge_id=challenge_ids[1],
                amount_wei=10**18,
                cooldown_seconds=24 * 60 * 60,
            )

        with database.connection() as connection:
            connection.execute(
                """
                UPDATE claims
                SET status = 'failed', failed_at = clock_timestamp(), failure_code = 'test_revert'
                WHERE id = %s
                """,
                (first_claim.claim_id,),
            )
        second_claim = reserve_claim(
            database,
            chain_id=chain_id,
            wallet_address=wallet,
            challenge_id=challenge_ids[1],
            amount_wei=10**18,
            cooldown_seconds=24 * 60 * 60,
        )

        with database.connection() as connection:
            connection.execute(
                """
                UPDATE claims
                SET status = 'confirmed', failed_at = NULL, confirmed_at = clock_timestamp()
                WHERE id = %s
                """,
                (second_claim.claim_id,),
            )
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
        with database.connection() as connection:
            connection.execute(
                "DELETE FROM claims WHERE chain_id = %s AND wallet_address = %s",
                (chain_id, wallet),
            )
            connection.execute("DELETE FROM challenges WHERE id = ANY(%s)", (challenge_ids,))
