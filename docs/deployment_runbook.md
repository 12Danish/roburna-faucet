# NativeFaucet deployment runbook

The deployment settings live in `contracts/.env`. The tracked `contracts/.env.example` contains Anvil sample values; the real `.env` is ignored by Git. `contracts/scripts/deploy.py` reads the file, checks the connected chain ID, and runs the Foundry Solidity deployment script. A dry run is the default; `--broadcast` sends the deployment transaction. No private key belongs in `.env`.

## 1. Deploy on local Anvil

In terminal 1:

```sh
cd contracts
anvil --chain-id 31337
```

In terminal 2:

```sh
cd contracts
cp .env.example .env  # only if .env does not already exist
forge build
forge test
python3 scripts/deploy.py
python3 scripts/deploy.py --broadcast
```

The example assigns Anvil account 0 as deployer, account 1 as admin, and account 2 as distributor. Anvil's accounts are unlocked and funded with local test currency. The launcher only permits `FAUCET_SIGNER_MODE=unlocked` against a loopback RPC. Copy the deployed `faucet` address from the broadcast output.

Inspect the deployment, then fund and pay one recipient:

```sh
export FAUCET_ADDRESS=<deployed-address>
cast code "$FAUCET_ADDRESS" --rpc-url http://127.0.0.1:8545
cast send "$FAUCET_ADDRESS" --value 5000000000000000000 \
  --from 0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266 --unlocked \
  --rpc-url http://127.0.0.1:8545
cast send "$FAUCET_ADDRESS" 'dispense(address,uint256)' \
  0x90f79bf6eb2c4f870365e785982e1f101e93b906 1000000000000000000 \
  --from 0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc --unlocked \
  --rpc-url http://127.0.0.1:8545
cast call "$FAUCET_ADDRESS" 'spentInCurrentPeriod()(uint256)' \
  --rpc-url http://127.0.0.1:8545
cast balance "$FAUCET_ADDRESS" --rpc-url http://127.0.0.1:8545
```

With these values, period spending is `1000000000000000000` and the faucet balance is `4000000000000000000`. The local deployment disappears when Anvil is reset. Never use Anvil's well-known accounts on a public network.

## 2. Deploy on another EVM test chain

Edit `contracts/.env` with that chain's public deployment settings:

| Key | Meaning |
| --- | --- |
| `FAUCET_RPC_URL` | HTTP(S) RPC endpoint; keep credential-bearing URLs private. |
| `FAUCET_EXPECTED_CHAIN_ID` | Expected chain ID; the launcher and Solidity script both check it. |
| `FAUCET_ADMIN` | Admin account, preferably a multisignature wallet. |
| `FAUCET_DISTRIBUTOR` | Backend signer that will submit payouts. |
| `FAUCET_MAX_PAYOUT_WEI` | Maximum amount per payout in the chain's smallest native unit. |
| `FAUCET_RECIPIENT_BALANCE_LIMIT_WEI` | Maximum native balance a recipient may have after a payout; `500000000000000000000` is 500 units on an 18-decimal chain. |
| `FAUCET_SPENDING_LIMIT_WEI` | Total `dispense` allowance per period in the same unit. |
| `FAUCET_PERIOD_SECONDS` | Immutable fixed-period duration; `86400` is one UTC day. |
| `FAUCET_SIGNER_MODE` | `keystore` for a remote test chain. |
| `FAUCET_DEPLOYER_ACCOUNT` | Name of a Foundry keystore outside this repo. |
| `FAUCET_LEGACY_GAS` | `true` only if the chain needs legacy gas transactions. |

For a remote keystore signer, `FAUCET_DEPLOYER` is optional; if set, the launcher verifies that the keystore derives this address before running Forge. Set the signer mode/account for your test chain. The deployer needs native currency for gas. The distributor also needs gas for later payouts. Fund the faucet separately from your treasury after deployment.

```sh
cd contracts
python3 scripts/deploy.py
python3 scripts/deploy.py --broadcast
```

The first command simulates deployment and must pass before the second command broadcasts. The launcher rejects a chain ID mismatch. For a custom chain, confirm the RPC supports transaction submission, gas estimation, and receipts; check its EVM revision and fee mode before deployment. Use the correct gas mode in `.env`. Do not use unlocked signing on a public RPC.

After broadcasting, copy the faucet address, check that code exists there, read both roles and configuration getters, including `recipientBalanceLimit()`, fund the contract with the treasury signer, and submit one small `dispense` from the distributor signer. Verify the transaction receipts, `Dispensed` event, recipient balance, faucet balance, and `spentInCurrentPeriod`. Do this before enabling the chain in the backend.

## 3. Export public metadata

Foundry saves ignored broadcast details under `contracts/broadcast/DeployNativeFaucet.s.sol/<chain-id>/`. The exporter verifies a successful creation receipt and writes the address, transaction hash, block, constructor settings, and ABI:

```sh
python3 scripts/export_deployment.py <chain-id>
```

The default output is `contracts/deployments/<chain-id>.json`. Review it before committing it for backend use. For ephemeral Anvil deployments, keep the output outside the repo:

```sh
python3 scripts/export_deployment.py 31337 --output /tmp/native-faucet-anvil.json
```

Repeat with a separate `.env` configuration for each chain. You can pass a different file with `python3 scripts/deploy.py --env-file <path>`; do not commit files containing private RPC credentials. All chains use the same reviewed source and pinned compiler/dependencies. Runtime bytecode can differ because `periodDuration` is immutable.
