#!/usr/bin/env python3
"""Submit one Roburna testnet claim with a local Foundry keystore account."""

import argparse
import json
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def request_json(url: str, payload: dict[str, object] | None = None) -> dict[str, object]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method="POST" if data is not None else "GET",
    )
    try:
        with urlopen(request, timeout=10) as response:
            return json.load(response)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach {url}: {exc.reason}") from exc


def cast(*args: str) -> str:
    try:
        result = subprocess.run(
            ["cast", "wallet", *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Foundry cast is not installed or not on PATH") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"cast wallet {args[0]} failed; check the account and keystore password") from exc
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", required=True, help="name of a Foundry test-wallet keystore")
    parser.add_argument("--api-url", default="http://127.0.0.1:8001")
    parser.add_argument("--wait-seconds", type=int, default=120)
    args = parser.parse_args()
    if args.wait_seconds < 1:
        parser.error("--wait-seconds must be positive")
    api = args.api_url.rstrip("/")
    address = cast("address", "--account", args.account)
    print(f"Testing claim for {address}", flush=True)

    challenge = request_json(
        f"{api}/auth/challenge", {"wallet_address": address, "chain_id": 159}
    )
    signature = cast("sign", "--account", args.account, str(challenge["message"]))
    claim = request_json(
        f"{api}/claims",
        {
            "challenge_id": challenge["challenge_id"],
            "chain_id": 159,
            "wallet_address": address,
            "message": challenge["message"],
            "signature": signature,
        },
    )
    claim_id = str(claim["claim_id"])
    last_status = None
    deadline = time.monotonic() + args.wait_seconds
    while True:
        claim = request_json(f"{api}/claims/{claim_id}")
        status = str(claim["status"])
        if status != last_status:
            print(f"claim {claim_id}: {status}", flush=True)
            last_status = status
        if status == "confirmed":
            print(f"transaction: {claim['transaction_hash']}")
            return 0
        if status == "failed":
            raise RuntimeError(f"claim failed: {claim.get('failure_code')}")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"claim is still {status}; inspect worker logs and query /claims/{claim_id}")
        time.sleep(2)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, KeyError, ValueError) as exc:
        print(f"Smoke claim failed: {exc}", file=sys.stderr)
        sys.exit(1)
