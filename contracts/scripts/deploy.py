#!/usr/bin/env python3
"""Load contracts/.env, validate the target chain, and run the Foundry deployment script."""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}\Z")
KEYS = {
    "FAUCET_RPC_URL",
    "FAUCET_EXPECTED_CHAIN_ID",
    "FAUCET_ADMIN",
    "FAUCET_DISTRIBUTOR",
    "FAUCET_MAX_PAYOUT_WEI",
    "FAUCET_SPENDING_LIMIT_WEI",
    "FAUCET_PERIOD_SECONDS",
    "FAUCET_SIGNER_MODE",
    "FAUCET_DEPLOYER",
    "FAUCET_DEPLOYER_ACCOUNT",
    "FAUCET_LEGACY_GAS",
}
REQUIRED = KEYS - {"FAUCET_DEPLOYER", "FAUCET_DEPLOYER_ACCOUNT", "FAUCET_LEGACY_GAS"}


def read_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise ValueError(f"missing configuration file: {path}")
    config: dict[str, str] = {}
    for number, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not separator or key not in KEYS or key in config:
            raise ValueError(f"invalid or duplicate key on line {number}: {key}")
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f"unclosed quoted value on line {number}")
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if not value:
            raise ValueError(f"empty value for {key} on line {number}")
        config[key] = value
    missing = REQUIRED - config.keys()
    if missing:
        raise ValueError(f"missing required keys: {', '.join(sorted(missing))}")
    return config


def positive_int(config: dict[str, str], key: str) -> int:
    value = config[key]
    if not value.isdecimal() or int(value) <= 0:
        raise ValueError(f"{key} must be a positive decimal integer")
    return int(value)


def validate(config: dict[str, str]) -> None:
    rpc = urlparse(config["FAUCET_RPC_URL"])
    if rpc.scheme not in {"http", "https"} or not rpc.hostname:
        raise ValueError("FAUCET_RPC_URL must be an HTTP(S) URL")
    for key in ("FAUCET_EXPECTED_CHAIN_ID", "FAUCET_MAX_PAYOUT_WEI", "FAUCET_SPENDING_LIMIT_WEI", "FAUCET_PERIOD_SECONDS"):
        positive_int(config, key)
    for key in ("FAUCET_ADMIN", "FAUCET_DISTRIBUTOR"):
        if not ADDRESS.fullmatch(config[key]) or int(config[key], 16) == 0:
            raise ValueError(f"{key} must be a nonzero EVM address")
    if config["FAUCET_ADMIN"].lower() == config["FAUCET_DISTRIBUTOR"].lower():
        raise ValueError("admin and distributor must be different addresses")
    if config.get("FAUCET_LEGACY_GAS", "false").lower() not in {"true", "false"}:
        raise ValueError("FAUCET_LEGACY_GAS must be true or false")

    mode = config["FAUCET_SIGNER_MODE"]
    if mode == "unlocked":
        if rpc.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("unlocked signing is allowed only for a local RPC")
        if not ADDRESS.fullmatch(config.get("FAUCET_DEPLOYER", "")):
            raise ValueError("FAUCET_DEPLOYER must be an EVM address in unlocked mode")
    elif mode == "keystore":
        if not config.get("FAUCET_DEPLOYER_ACCOUNT"):
            raise ValueError("FAUCET_DEPLOYER_ACCOUNT is required in keystore mode")
    else:
        raise ValueError("FAUCET_SIGNER_MODE must be unlocked or keystore")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--broadcast", action="store_true", help="send the deployment transaction")
    args = parser.parse_args()

    try:
        config = read_env(args.env_file)
        validate(config)
        rpc_url = config["FAUCET_RPC_URL"]
        chain = subprocess.run(
            ["cast", "chain-id", "--rpc-url", rpc_url],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        if chain != str(positive_int(config, "FAUCET_EXPECTED_CHAIN_ID")):
            raise ValueError(f"RPC chain ID {chain} differs from FAUCET_EXPECTED_CHAIN_ID")

        if config["FAUCET_SIGNER_MODE"] == "keystore" and config.get("FAUCET_DEPLOYER"):
            signer = subprocess.run(
                ["cast", "wallet", "address", "--account", config["FAUCET_DEPLOYER_ACCOUNT"]],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            if signer.lower() != config["FAUCET_DEPLOYER"].lower():
                raise ValueError(f"keystore signer {signer} differs from FAUCET_DEPLOYER")

        command = [
            "forge", "script", "script/DeployNativeFaucet.s.sol:DeployNativeFaucet",
            "--rpc-url", rpc_url,
        ]
        if config["FAUCET_SIGNER_MODE"] == "unlocked":
            command += ["--sender", config["FAUCET_DEPLOYER"], "--unlocked"]
        else:
            command += ["--account", config["FAUCET_DEPLOYER_ACCOUNT"]]
        if config.get("FAUCET_LEGACY_GAS", "false").lower() == "true":
            command.append("--legacy")
        if args.broadcast:
            command.append("--broadcast")

        child_env = os.environ.copy()
        child_env.update(config)
        print(f"Validated chain ID {chain}; {'broadcasting' if args.broadcast else 'dry run'}", flush=True)
        return subprocess.run(command, cwd=ROOT, env=child_env, check=False).returncode
    except subprocess.CalledProcessError:
        print("Deployment configuration error: RPC or keystore check failed", file=sys.stderr)
        return 1
    except (ValueError, OSError) as exc:
        print(f"Deployment configuration error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
