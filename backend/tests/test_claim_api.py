from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from eth_account import Account
from eth_account.messages import encode_defunct

from app.api.dependencies import get_runtime
from app.api.routes import claims as claim_routes
from app.services.auth import InvalidWalletSignature, existing_claim_for_signed_challenge
from app.services.claims import ClaimReservation, ClaimSnapshot, WalletCooldownActive
from app.services.eligibility import FaucetUnavailable
from app.services.rate_limits import RateLimitExceeded


def _api(monkeypatch, *, existing_id=None):
    account = Account.create()
    challenge_id = uuid4()
    claim_id = existing_id or uuid4()
    message = "test signed challenge"
    signature = Account.sign_message(encode_defunct(text=message), account.key).signature.hex()
    chain = SimpleNamespace(
        config=SimpleNamespace(
            chain_id=31337,
            payout_amount_wei=10**18,
            cooldown_seconds=86400,
            explorer_url="http://explorer.example",
        ),
        faucet_address="0x" + "1" * 40,
    )
    runtime = SimpleNamespace(sessions=object(), chains={31337: chain})
    settings = SimpleNamespace(
        trusted_proxy_cidrs=[],
        rate_limit_hash_secret=SimpleNamespace(get_secret_value=lambda: "s" * 64),
        siwe_domain="localhost:3000",
        siwe_uri="http://localhost:3000",
    )
    monkeypatch.setattr(claim_routes, "get_settings", lambda: settings)
    monkeypatch.setattr(claim_routes, "existing_claim_for_signed_challenge", lambda *args, **kwargs: existing_id)
    monkeypatch.setattr(claim_routes, "require_faucet_capacity", lambda chain, recipient: None)
    monkeypatch.setattr(claim_routes, "verify_wallet_challenge", lambda *args, **kwargs: None)
    monkeypatch.setattr(claim_routes, "reserve_claim", lambda *args, **kwargs: ClaimReservation(claim_id, "reserved", datetime.now(timezone.utc)))
    monkeypatch.setattr(
        claim_routes,
        "get_claim_snapshot",
        lambda *args, **kwargs: ClaimSnapshot(
            claim_id=claim_id,
            chain_id=31337,
            status="reserved",
            amount_wei=10**18,
            reserved_at=datetime.now(timezone.utc),
            submitted_at=None,
            confirmed_at=None,
            failure_code=None,
            transaction_hash=None,
        ),
    )
    app = FastAPI()
    app.include_router(claim_routes.router)
    app.dependency_overrides[get_runtime] = lambda: runtime
    client = TestClient(app, client=("127.0.0.1", 1234))
    body = {
        "challenge_id": str(challenge_id),
        "chain_id": 31337,
        "wallet_address": account.address,
        "message": message,
        "signature": signature,
    }
    return client, body, claim_id


def test_claim_reserves_server_selected_amount_after_two_stage_limits(monkeypatch) -> None:
    client, body, claim_id = _api(monkeypatch)
    scopes = []
    monkeypatch.setattr(
        claim_routes,
        "enforce_request_rate_limit",
        lambda *args, **kwargs: scopes.append(kwargs["identity_types"]),
    )
    amounts = []
    monkeypatch.setattr(
        claim_routes,
        "reserve_claim",
        lambda *args, **kwargs: (amounts.append(kwargs["amount_wei"]), ClaimReservation(claim_id, "reserved", datetime.now(timezone.utc)))[1],
    )

    response = client.post("/claims", json=body)

    assert response.status_code == 202
    assert response.json()["claim_id"] == str(claim_id)
    assert response.json()["status"] == "reserved"
    assert scopes == [("ip", "subnet"), ("wallet",)]
    assert amounts == [10**18]


def test_invalid_signature_never_reserves_claim(monkeypatch) -> None:
    client, body, _ = _api(monkeypatch)
    monkeypatch.setattr(claim_routes, "enforce_request_rate_limit", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        claim_routes,
        "verify_wallet_challenge",
        lambda *args, **kwargs: (_ for _ in ()).throw(InvalidWalletSignature()),
    )
    monkeypatch.setattr(
        claim_routes,
        "reserve_claim",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not reserve")),
    )

    response = client.post("/claims", json=body)

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "invalid_wallet_signature"


def test_repeated_signed_request_returns_existing_claim(monkeypatch) -> None:
    claim_id = uuid4()
    client, body, _ = _api(monkeypatch, existing_id=claim_id)
    monkeypatch.setattr(claim_routes, "enforce_request_rate_limit", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        claim_routes,
        "reserve_claim",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not reserve")),
    )

    response = client.post("/claims", json=body)

    assert response.status_code == 202
    assert response.json()["claim_id"] == str(claim_id)


def test_claim_rate_limit_stops_signature_and_rpc_work(monkeypatch) -> None:
    client, body, _ = _api(monkeypatch)
    monkeypatch.setattr(
        claim_routes,
        "enforce_request_rate_limit",
        lambda *args, **kwargs: (_ for _ in ()).throw(RateLimitExceeded(30)),
    )
    monkeypatch.setattr(
        claim_routes,
        "verify_wallet_challenge",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not verify")),
    )

    response = client.post("/claims", json=body)

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "30"


def test_paused_faucet_does_not_reserve_claim(monkeypatch) -> None:
    client, body, _ = _api(monkeypatch)
    monkeypatch.setattr(claim_routes, "enforce_request_rate_limit", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        claim_routes,
        "require_faucet_capacity",
        lambda chain, recipient: (_ for _ in ()).throw(FaucetUnavailable("faucet_paused")),
    )
    monkeypatch.setattr(
        claim_routes,
        "reserve_claim",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not reserve")),
    )

    response = client.post("/claims", json=body)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "faucet_paused"


def test_recipient_balance_cap_rejection_does_not_reserve_claim(monkeypatch) -> None:
    client, body, _ = _api(monkeypatch)
    monkeypatch.setattr(claim_routes, "enforce_request_rate_limit", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        claim_routes,
        "require_faucet_capacity",
        lambda chain, recipient: (_ for _ in ()).throw(FaucetUnavailable("recipient_balance_limit_exceeded")),
    )
    monkeypatch.setattr(
        claim_routes,
        "reserve_claim",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not reserve")),
    )

    response = client.post("/claims", json=body)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "recipient_balance_limit_exceeded"


def test_cooldown_returns_next_eligible_time(monkeypatch) -> None:
    client, body, _ = _api(monkeypatch)
    monkeypatch.setattr(claim_routes, "enforce_request_rate_limit", lambda *args, **kwargs: None)
    next_eligible_at = datetime(2099, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(
        claim_routes,
        "reserve_claim",
        lambda *args, **kwargs: (_ for _ in ()).throw(WalletCooldownActive(next_eligible_at)),
    )

    response = client.post("/claims", json=body)

    assert response.status_code == 429
    assert response.json()["detail"]["nextEligibleAt"] == next_eligible_at.isoformat()


def test_claim_status_includes_latest_transaction_hash(monkeypatch) -> None:
    client, body, claim_id = _api(monkeypatch)
    tx_hash = "0x" + "a" * 64
    monkeypatch.setattr(
        claim_routes,
        "get_claim_snapshot",
        lambda *args, **kwargs: ClaimSnapshot(
            claim_id=claim_id,
            chain_id=31337,
            status="submitted",
            amount_wei=10**18,
            reserved_at=datetime.now(timezone.utc),
            submitted_at=datetime.now(timezone.utc),
            confirmed_at=None,
            failure_code=None,
            transaction_hash=tx_hash,
        ),
    )

    response = client.get(f"/claims/{claim_id}")

    assert response.status_code == 200
    assert response.json()["transaction_hash"] == tx_hash
    assert response.json()["transaction_url"] == "http://explorer.example/tx/" + tx_hash


class _RetrySession:
    def __init__(self, claim, challenge):
        self.claim = claim
        self.challenge = challenge

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def scalar(self, statement):
        return self.claim

    def get(self, model, row_id):
        return self.challenge


def test_accepted_claim_retry_requires_original_wallet_signature() -> None:
    wallet = Account.create()
    other = Account.create()
    challenge_id = uuid4()
    claim_id = uuid4()
    message = "exact message accepted originally"
    row = SimpleNamespace(
        id=claim_id,
        challenge_id=challenge_id,
        chain_id=31337,
        wallet_address=wallet.address.lower(),
    )
    challenge = SimpleNamespace(
        chain_id=31337,
        wallet_address=wallet.address.lower(),
        message=message,
        consumed_at=datetime.now(timezone.utc),
    )
    sessions = lambda: _RetrySession(row, challenge)
    signature = Account.sign_message(encode_defunct(text=message), wallet.key).signature.hex()

    assert existing_claim_for_signed_challenge(
        sessions,
        challenge_id=challenge_id,
        message_text=message,
        signature=signature,
        wallet_address=wallet.address,
        chain_id=31337,
    ) == claim_id

    wrong_signature = Account.sign_message(encode_defunct(text=message), other.key).signature.hex()
    with pytest.raises(InvalidWalletSignature):
        existing_claim_for_signed_challenge(
            sessions,
            challenge_id=challenge_id,
            message_text=message,
            signature=wrong_signature,
            wallet_address=wallet.address,
            chain_id=31337,
        )
