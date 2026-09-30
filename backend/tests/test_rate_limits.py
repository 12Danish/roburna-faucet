import pytest
from fastapi import Request

from app.services.rate_limits import (
    ClientAddressUnavailable,
    client_ip_from_request,
    rate_limit_identity_hashes,
)


def _request(peer: str, forwarded: str | None = None) -> Request:
    headers = [] if forwarded is None else [(b"x-forwarded-for", forwarded.encode())]
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": headers,
            "client": (peer, 1234),
            "server": ("testserver", 80),
        }
    )


def test_ignores_forwarded_ip_from_untrusted_peer() -> None:
    request = _request("198.51.100.10", "203.0.113.9")
    assert client_ip_from_request(request, ["10.0.0.0/8"]) == "198.51.100.10"


def test_uses_forwarded_client_ip_from_trusted_proxy() -> None:
    request = _request("10.0.0.2", "203.0.113.9, 10.0.0.1")
    assert client_ip_from_request(request, ["10.0.0.0/8"]) == "203.0.113.9"


def test_rejects_malformed_forwarded_chain_from_trusted_proxy() -> None:
    request = _request("10.0.0.2", "not-an-ip")
    with pytest.raises(ClientAddressUnavailable):
        client_ip_from_request(request, ["10.0.0.0/8"])


def test_wallet_bucket_survives_vpn_exit_ip_rotation() -> None:
    first = rate_limit_identity_hashes("198.51.100.10", "0xAbC", "s" * 64)
    second = rate_limit_identity_hashes("203.0.113.27", "0xaBc", "s" * 64)

    assert first["wallet"] == second["wallet"]
    assert first["ip"] != second["ip"]
