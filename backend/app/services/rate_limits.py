from __future__ import annotations

import hashlib
import hmac
import ipaddress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import Request
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import RateLimitEvent

IdentityType = Literal["ip", "subnet", "wallet"]
_ACTION_POLICIES = {
    # Challenges are unauthenticated, so only IP and subnet limits apply here.
    "challenge": {
        "ip": ((60, 5), (3600, 30)),
        "subnet": ((60, 20), (3600, 100)),
        "wallet": ((60, 3), (3600, 10)),
    },
    # The claim route checks IP/subnet before signature verification and wallet after.
    "claim": {
        "ip": ((60, 3), (3600, 10)),
        "subnet": ((60, 15), (3600, 60)),
        "wallet": ((60, 3), (3600, 10)),
    },
}
_CLEANUP_LOCK_KEY = -894332772812907312


class RateLimitExceeded(Exception):
    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__("request rate limit exceeded")


class ClientAddressUnavailable(Exception):
    """The immediate peer or its trusted proxy forwarding chain was invalid."""


@dataclass(frozen=True)
class _Identity:
    kind: IdentityType
    value: str
    digest: str


def client_ip_from_request(request: Request, trusted_proxy_cidrs: list[str]) -> str:
    """Resolve the caller IP, using forwarding headers only from a trusted proxy."""
    if request.client is None:
        raise ClientAddressUnavailable
    try:
        peer = ipaddress.ip_address(request.client.host)
        trusted = [ipaddress.ip_network(value, strict=False) for value in trusted_proxy_cidrs]
    except ValueError:
        raise ClientAddressUnavailable from None

    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded or not any(peer in network for network in trusted):
        return str(peer)

    try:
        chain = [ipaddress.ip_address(part.strip()) for part in forwarded.split(",")]
    except ValueError:
        raise ClientAddressUnavailable from None
    if not chain or len(chain) > 20:
        raise ClientAddressUnavailable

    # Walk from the direct peer toward the client. Stop at the first untrusted hop,
    # so a client cannot spoof an address to the left of the trusted proxy chain.
    current = peer
    for candidate in reversed(chain):
        if not any(current in network for network in trusted):
            break
        current = candidate
    return str(current)


def enforce_request_rate_limit(
    sessions: sessionmaker[Session],
    *,
    action: Literal["challenge", "claim"],
    client_ip: str,
    wallet_address: str,
    hash_secret: str,
    identity_types: tuple[IdentityType, ...] = ("ip", "subnet", "wallet"),
) -> None:
    """Atomically apply the selected rolling-window limits."""
    if not identity_types or len(set(identity_types)) != len(identity_types):
        raise ValueError("identity_types must be a nonempty set of distinct identities")
    identities = [
        identity
        for identity in _rate_limit_identities(client_ip, wallet_address, hash_secret)
        if identity.kind in identity_types
    ]
    policies = _ACTION_POLICIES[action]
    if len(identities) != len(identity_types):
        raise ValueError("unsupported rate-limit identity type")
    now = datetime.now(timezone.utc)

    with sessions.begin() as session:
        # All workers acquire the same advisory locks in sorted order. That makes the
        # read-count-insert sequence atomic even when requests hit different API workers.
        for identity in sorted(identities, key=lambda item: item.digest):
            lock_key = int.from_bytes(bytes.fromhex(identity.digest[:16]), "big", signed=True)
            session.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key})

        # Opportunistic bounded retention. This nonblocking transaction lock allows
        # at most one concurrent request to prune expired, pseudonymous event rows.
        acquired = session.execute(
            text("SELECT pg_try_advisory_xact_lock(:lock_key)"),
            {"lock_key": _CLEANUP_LOCK_KEY},
        ).scalar_one()
        if acquired:
            session.execute(
                delete(RateLimitEvent).where(
                    RateLimitEvent.occurred_at < now - timedelta(hours=1)
                )
            )

        violations: list[int] = []
        for identity in identities:
            events = list(
                session.scalars(
                    select(RateLimitEvent.occurred_at)
                    .where(
                        RateLimitEvent.action == action,
                        RateLimitEvent.identity_type == identity.kind,
                        RateLimitEvent.identity_hash == identity.digest,
                        RateLimitEvent.occurred_at > now - timedelta(hours=1),
                    )
                    .order_by(RateLimitEvent.occurred_at.asc())
                ).all()
            )
            for window_seconds, maximum in policies[identity.kind]:
                within_window = [
                    occurred_at
                    for occurred_at in events
                    if occurred_at > now - timedelta(seconds=window_seconds)
                ]
                if len(within_window) >= maximum:
                    expiry_index = len(within_window) - maximum
                    expires_at = within_window[expiry_index] + timedelta(seconds=window_seconds)
                    violations.append(max(1, int((expires_at - now).total_seconds() + 0.999)))

        if violations:
            raise RateLimitExceeded(max(violations))

        session.add_all(
            RateLimitEvent(
                action=action,
                identity_type=identity.kind,
                identity_hash=identity.digest,
                occurred_at=now,
            )
            for identity in identities
        )


def rate_limit_identity_hashes(client_ip: str, wallet_address: str, hash_secret: str) -> dict[str, str]:
    """Return the pseudonymous bucket keys, useful for testing policy behavior."""
    return {
        identity.kind: identity.digest
        for identity in _rate_limit_identities(client_ip, wallet_address, hash_secret)
    }


def _rate_limit_identities(client_ip: str, wallet_address: str, hash_secret: str) -> list[_Identity]:
    ip = ipaddress.ip_address(client_ip)
    prefix = 24 if ip.version == 4 else 64
    subnet = str(ipaddress.ip_network(f"{ip}/{prefix}", strict=False))
    return [
        _make_identity("ip", str(ip), hash_secret),
        _make_identity("subnet", subnet, hash_secret),
        _make_identity("wallet", wallet_address.lower(), hash_secret),
    ]


def _make_identity(kind: IdentityType, value: str, secret: str) -> _Identity:
    payload = f"{kind}:{value}".encode()
    digest = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return _Identity(kind, value, digest)
