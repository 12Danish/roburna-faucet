#!/usr/bin/env python3
"""Export public faucet deployment metadata from a successful Foundry broadcast."""

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("chain_id", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.chain_id <= 0:
        parser.error("chain_id must be positive")

    broadcast_path = ROOT / "broadcast" / "DeployNativeFaucet.s.sol" / str(args.chain_id) / "run-latest.json"
    artifact_path = ROOT / "out" / "NativeFaucet.sol" / "NativeFaucet.json"
    broadcast = json.loads(broadcast_path.read_text())
    artifact = json.loads(artifact_path.read_text())

    if int(broadcast["chain"]) != args.chain_id:
        raise ValueError("broadcast chain ID does not match requested chain ID")
    creations = [tx for tx in broadcast["transactions"] if tx.get("contractName") == "NativeFaucet" and tx.get("transactionType") == "CREATE"]
    if len(creations) != 1:
        raise ValueError("expected exactly one NativeFaucet creation transaction")
    tx = creations[0]
    receipts = [receipt for receipt in broadcast["receipts"] if receipt.get("transactionHash", "").lower() == tx["hash"].lower()]
    if len(receipts) != 1 or int(receipts[0]["status"], 16) != 1:
        raise ValueError("NativeFaucet deployment receipt is missing or failed")
    receipt = receipts[0]
    if receipt["contractAddress"].lower() != tx["contractAddress"].lower():
        raise ValueError("transaction and receipt contract addresses differ")
    if len(tx["arguments"]) != 6:
        raise ValueError("unexpected NativeFaucet constructor arguments")

    admin, distributor, max_payout, spending_limit, period_seconds, recipient_balance_limit = tx["arguments"]
    deployment = {
        "chainId": args.chain_id,
        "address": tx["contractAddress"],
        "transactionHash": tx["hash"],
        "deploymentBlock": int(receipt["blockNumber"], 16),
        "constructor": {
            "admin": admin,
            "distributor": distributor,
            "maxPayoutWei": str(max_payout),
            "spendingLimitWei": str(spending_limit),
            "periodDurationSeconds": str(period_seconds),
            "recipientBalanceLimitWei": str(recipient_balance_limit),
        },
        "abi": artifact["abi"],
    }
    output = args.output or ROOT / "deployments" / f"{args.chain_id}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")
    output.write_text(json.dumps(deployment, indent=2) + "\n")
    print(output)


if __name__ == "__main__":
    main()
