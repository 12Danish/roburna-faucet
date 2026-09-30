"""Database-backed limiter checks. Requires a PostgreSQL test URL."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from typing import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.db.engine import create_database_engine, create_session_factory
from app.db.models import RateLimitEvent
from app.services.rate_limits import (
    RateLimitExceeded,
    enforce_request_rate_limit,
    rate_limit_identity_hashes,
)

SECRET = "test-only-hmac-secret-that-is-at-least-32-bytes"


@pytest.fixture(scope="module")
def rate_database() -> Iterator[sessionmaker[Session]]:
    database_url = os.getenv("FAUCET_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("set FAUCET_TEST_DATABASE_URL to a PostgreSQL database permitted for tests")

    engine = create_database_engine(database_url)
    # Isolate these tests from application tables, even when a developer uses a
    # local database URL. The generated name contains only hex characters.
    schema = "faucet_rate_test_" + uuid4().hex
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    isolated_engine = engine.execution_options(schema_translate_map={None: schema})
    try:
        RateLimitEvent.__table__.create(isolated_engine)
        yield create_session_factory(isolated_engine)
    finally:
        isolated_engine.dispose()
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


@pytest.mark.parametrize(
    "action,identity_type,window_seconds,maximum",
    [
        ("challenge", "ip", 60, 5),
        ("challenge", "ip", 3600, 30),
        ("challenge", "subnet", 60, 20),
        ("challenge", "subnet", 3600, 100),
        ("claim", "ip", 60, 3),
        ("claim", "ip", 3600, 10),
        ("claim", "subnet", 60, 15),
        ("claim", "subnet", 3600, 60),
        ("claim", "wallet", 60, 3),
        ("claim", "wallet", 3600, 10),
    ],
)
def test_exact_window_thresholds(
    rate_database: sessionmaker[Session],
    action: str,
    identity_type: str,
    window_seconds: int,
    maximum: int,
) -> None:
    # Different identities keep the parametrized cases independent.
    case_id = (0 if action == "challenge" else 100) + {"ip": 1, "subnet": 11, "wallet": 21}[identity_type] + (0 if window_seconds == 60 else 1)
    client_ip = f"10.{case_id}.0.10"
    wallet = "0x" + f"{case_id:040x}"
    digest = rate_limit_identity_hashes(client_ip, wallet, SECRET)[identity_type]
    # Hourly cases are outside the 60-second window, so they test the hour cap.
    age = 120 if window_seconds == 3600 else 5
    occurred_at = datetime.now(timezone.utc) - timedelta(seconds=age)
    with rate_database.begin() as session:
        session.add_all(
            RateLimitEvent(
                action=action,
                identity_type=identity_type,
                identity_hash=digest,
                occurred_at=occurred_at,
            )
            for _ in range(maximum - 1)
        )

    arguments = {
        "action": action,
        "client_ip": client_ip,
        "wallet_address": wallet,
        "hash_secret": SECRET,
        "identity_types": (identity_type,),
    }
    enforce_request_rate_limit(rate_database, **arguments)
    with pytest.raises(RateLimitExceeded) as error:
        enforce_request_rate_limit(rate_database, **arguments)
    assert error.value.retry_after_seconds > 0


def test_simultaneous_requests_cannot_exceed_ip_limit(rate_database: sessionmaker[Session]) -> None:
    client_ip = "198.18.200.10"
    wallet = "0x" + "a" * 40
    barrier = Barrier(8)

    def request() -> str:
        barrier.wait(timeout=10)
        try:
            enforce_request_rate_limit(
                rate_database,
                action="claim",
                client_ip=client_ip,
                wallet_address=wallet,
                hash_secret=SECRET,
                identity_types=("ip",),
            )
        except RateLimitExceeded:
            return "limited"
        return "allowed"

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: request(), range(8)))
    assert results.count("allowed") == 3
    assert results.count("limited") == 5


def test_wallet_limit_follows_wallet_across_ip_changes(rate_database: sessionmaker[Session]) -> None:
    wallet = "0x" + "b" * 40
    for last_octet in (1, 2, 3):
        enforce_request_rate_limit(
            rate_database,
            action="claim",
            client_ip=f"203.0.113.{last_octet}",
            wallet_address=wallet,
            hash_secret=SECRET,
            identity_types=("wallet",),
        )
    with pytest.raises(RateLimitExceeded):
        enforce_request_rate_limit(
            rate_database,
            action="claim",
            client_ip="198.51.100.7",
            wallet_address=wallet,
            hash_secret=SECRET,
            identity_types=("wallet",),
        )
