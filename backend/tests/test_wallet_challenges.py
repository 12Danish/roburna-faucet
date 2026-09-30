from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from eth_account import Account
from eth_account.messages import encode_defunct

from app.api.dependencies import get_runtime
from app.api.routes import auth as auth_route
from app.api.routes.auth import router as auth_router
from app.services.auth import (
    ChallengeUnavailable,
    ContractRecipient,
    InvalidWalletSignature,
    create_wallet_challenge,
    verify_wallet_challenge,
)


class MemorySession:
    def __init__(self, rows: dict) -> None:
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def add(self, row) -> None:
        self.rows[row.id] = row

    def flush(self) -> None:
        pass

    def get(self, model, row_id):
        return self.rows.get(row_id)


class MemorySessions:
    def __init__(self) -> None:
        self.rows = {}

    def begin(self) -> MemorySession:
        return MemorySession(self.rows)

    def __call__(self) -> MemorySession:
        return MemorySession(self.rows)


@pytest.fixture
def issued_challenge():
    account = Account.create()
    sessions = MemorySessions()
    challenge = create_wallet_challenge(
        sessions,
        wallet_address=account.address,
        chain_id=31337,
        domain="localhost:3000",
        uri="http://localhost:3000",
        ttl_seconds=300,
    )
    signature = Account.sign_message(
        encode_defunct(text=challenge.message),
        private_key=account.key,
    ).signature.hex()
    chain = SimpleNamespace(
        config=SimpleNamespace(chain_id=31337),
        web3=SimpleNamespace(eth=SimpleNamespace(get_code=lambda _address: b"")),
    )
    row = sessions.rows[challenge.challenge_id]
    return account, sessions, challenge, signature, chain, row


def _verify(issued_challenge, **overrides):
    account, sessions, challenge, signature, chain, _row = issued_challenge
    values = {
        "sessions": sessions,
        "challenge_id": challenge.challenge_id,
        "message_text": challenge.message,
        "signature": signature,
        "wallet_address": account.address,
        "chain_id": 31337,
        "domain": "localhost:3000",
        "uri": "http://localhost:3000",
        "chain_client": chain,
    }
    values.update(overrides)
    return verify_wallet_challenge(**values)


def test_creates_persisted_siwe_message_for_wallet_and_chain(issued_challenge) -> None:
    account, sessions, challenge, _signature, _chain, row = issued_challenge

    assert row.wallet_address == account.address.lower()
    assert row.chain_id == 31337
    assert row.message == challenge.message
    assert row.nonce in challenge.message
    assert row.expires_at == challenge.expires_at


def test_verifies_valid_wallet_signature_and_empty_code(issued_challenge) -> None:
    account, _sessions, challenge, _signature, _chain, _row = issued_challenge

    verified = _verify(issued_challenge)

    assert verified.challenge_id == challenge.challenge_id
    assert verified.chain_id == 31337
    assert verified.wallet_address == account.address.lower()


def test_rejects_signature_from_another_wallet(issued_challenge) -> None:
    _account, _sessions, challenge, _signature, _chain, _row = issued_challenge
    other = Account.create()
    wrong_signature = Account.sign_message(
        encode_defunct(text=challenge.message),
        private_key=other.key,
    ).signature.hex()

    with pytest.raises(InvalidWalletSignature):
        _verify(issued_challenge, signature=wrong_signature)


def test_rejects_wrong_chain_or_domain(issued_challenge) -> None:
    with pytest.raises(ChallengeUnavailable):
        _verify(issued_challenge, chain_id=1)
    with pytest.raises(ChallengeUnavailable):
        _verify(issued_challenge, domain="attacker.example")


def test_rejects_expired_or_consumed_challenge(issued_challenge) -> None:
    _account, _sessions, _challenge, _signature, _chain, row = issued_challenge
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    with pytest.raises(ChallengeUnavailable):
        _verify(issued_challenge)

    row.expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    row.consumed_at = datetime.now(timezone.utc)
    with pytest.raises(ChallengeUnavailable):
        _verify(issued_challenge)


def test_rejects_message_tampering_and_malformed_signature(issued_challenge) -> None:
    _account, _sessions, challenge, _signature, _chain, _row = issued_challenge
    with pytest.raises(ChallengeUnavailable):
        _verify(issued_challenge, message_text=challenge.message + " altered")
    with pytest.raises(InvalidWalletSignature):
        _verify(issued_challenge, signature="0x1234")


def test_rejects_deployed_contract_recipient(issued_challenge) -> None:
    _account, _sessions, _challenge, _signature, chain, _row = issued_challenge
    chain.web3.eth.get_code = lambda _address: b"\x60\x00"

    with pytest.raises(ContractRecipient):
        _verify(issued_challenge)


def test_challenge_route_returns_stored_message(monkeypatch) -> None:
    account = Account.create()
    sessions = MemorySessions()
    chain = SimpleNamespace(config=SimpleNamespace(chain_id=31337))
    runtime = SimpleNamespace(sessions=sessions, chains={31337: chain})
    settings = SimpleNamespace(
        siwe_domain="localhost:3000",
        siwe_uri="http://localhost:3000",
        challenge_ttl_seconds=300,
        trusted_proxy_cidrs=[],
        rate_limit_hash_secret=SimpleNamespace(get_secret_value=lambda: "x" * 64),
    )
    monkeypatch.setattr(auth_route, "enforce_request_rate_limit", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_route, "get_settings", lambda: settings)
    app = FastAPI()
    app.include_router(auth_router)
    app.dependency_overrides[get_runtime] = lambda: runtime

    response = TestClient(app, client=("127.0.0.1", 1234)).post(
        "/auth/challenge",
        json={"wallet_address": account.address, "chain_id": 31337},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["message"] in [row.message for row in sessions.rows.values()]
    assert body["challenge_id"] == str(next(iter(sessions.rows)))


def test_challenge_route_rejects_disabled_chain(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(auth_router)
    app.dependency_overrides[get_runtime] = lambda: SimpleNamespace(sessions=MemorySessions(), chains={})
    settings = SimpleNamespace(
        siwe_domain="localhost:3000",
        siwe_uri="http://localhost:3000",
        challenge_ttl_seconds=300,
        trusted_proxy_cidrs=[],
        rate_limit_hash_secret=SimpleNamespace(get_secret_value=lambda: "x" * 64),
    )
    monkeypatch.setattr(auth_route, "get_settings", lambda: settings)
    monkeypatch.setattr(auth_route, "enforce_request_rate_limit", lambda *args, **kwargs: None)

    response = TestClient(app, client=("127.0.0.1", 1234)).post(
        "/auth/challenge",
        json={"wallet_address": Account.create().address, "chain_id": 31337},
    )

    assert response.status_code == 404
