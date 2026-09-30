import argparse
import getpass
import json
import os
from pathlib import Path

from eth_account import Account


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create an encrypted Ethereum V3 keystore for the faucet distributor."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("secrets/distributor.json"),
        help="output path relative to the backend directory (default: secrets/distributor.json)",
    )
    args = parser.parse_args()

    private_key = getpass.getpass("Distributor private key: ").strip()
    password = getpass.getpass("Keystore password (12+ characters): ")
    confirmation = getpass.getpass("Repeat keystore password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match")
    if len(password) < 12:
        raise SystemExit("Use a keystore password of at least 12 characters")

    try:
        account = Account.from_key(private_key)
        keystore = Account.encrypt(account.key, password)
    except Exception:
        raise SystemExit("Could not read that private key") from None

    output = args.output if args.output.is_absolute() else Path.cwd() / args.output
    if output.exists():
        raise SystemExit(f"Refusing to overwrite existing keystore: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(file_descriptor, "w", encoding="utf-8") as keystore_file:
        json.dump(keystore, keystore_file)
    print(f"Encrypted distributor keystore created: {output}")
    print(f"Distributor address: {account.address}")


if __name__ == "__main__":
    main()
