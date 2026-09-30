# Roburna testnet deployment and Docker Compose

The public RPC returned chain ID `159` (`0x9f`) and `eth_gasPrice = 1 gwei` when checked on 2026-09-30. The current block exposed a zero `baseFeePerGas`. This guide uses legacy transactions. Recheck the live RPC before broadcasting because chain settings can change. The WebSocket endpoint is not needed by the current HTTP polling worker.

## 1. Choose the payout and prepare the three roles

The deployer is `0x7c2Eb5047858AD800414Dc8Ac60417A5d73be409`. It pays deployment gas but receives no role in the new contract. The admin is `0xbDd4628028F85bb7C4aECd9d1F426aE9525aF8F4`; only this address can pause, change payout limits, manage roles, or withdraw. The distributor is `0x546501e0c1d35822CcA63ef2A4787Ff1c2367079`; the backend worker uses its private key to call `dispense` and pays the gas. These addresses must be controlled by their respective owners. A treasury can be any funded wallet that sends RBAT to the contract address; its key is not needed by the API or worker.

[`contracts/.env.roburna.example`](../contracts/.env.roburna.example) illustrates **0.1 RBAT per claim**, a **5 RBAT per UTC day** on-chain spending cap, and an **86400-second period**. That permits at most 50 full claims per day while the contract is funded. The backend template uses a separate 86400-second **per-wallet** cooldown. Choose and review these numbers before deployment; the contract's period duration cannot be changed later. RBAT has 18 decimals: `100000000000000000` wei = 0.1 RBAT, `5000000000000000000` wei = 5 RBAT.

Use separate keys for deployer, admin, and distributor. Fund the deployer with enough RBAT for deployment gas and the distributor with enough RBAT for payout gas. The contract itself needs RBAT for the actual payouts.

## 2. Deploy the contract

From `contracts/`:

```bash
cp -n .env.roburna.example .env
cast chain-id --rpc-url https://preseed-testnet-1.roburna.com
cast wallet import roburna-deployer --interactive
cast wallet address --account roburna-deployer
cast balance 0x7c2Eb5047858AD800414Dc8Ac60417A5d73be409 --rpc-url https://preseed-testnet-1.roburna.com
forge test
python3 scripts/deploy.py
python3 scripts/deploy.py --broadcast
python3 scripts/export_deployment.py 159
```

`cast wallet import --interactive` prompts for the **deployer** private key and a keystore password; do not place the key on the command line or in `.env`. Confirm the derived address matches the deployer above. The deployment helper also checks this address when `FAUCET_DEPLOYER` is set. The first `deploy.py` call simulates without publishing; `--broadcast` sends the deployment transaction. The deployer keystore password may be prompted for more than once. No admin or distributor key is needed to deploy.

The export creates the ignored `contracts/deployments/159.json`. Record and review its `address` and transaction hash. Verify bytecode and constructor values with `cast code` / `cast call` against the RPC. Set the address in a shell variable for the following checks:

```bash
export FAUCET_ADDRESS=<address from contracts/deployments/159.json>
cast code "$FAUCET_ADDRESS" --rpc-url https://preseed-testnet-1.roburna.com
cast call "$FAUCET_ADDRESS" 'defaultAdmin()(address)' --rpc-url https://preseed-testnet-1.roburna.com
cast call "$FAUCET_ADDRESS" 'maxPayout()(uint256)' --rpc-url https://preseed-testnet-1.roburna.com
cast call "$FAUCET_ADDRESS" 'spendingLimit()(uint256)' --rpc-url https://preseed-testnet-1.roburna.com
cast call "$FAUCET_ADDRESS" 'hasRole(bytes32,address)(bool)' "$(cast keccak 'DISTRIBUTOR_ROLE')" 0x546501e0c1d35822CcA63ef2A4787Ff1c2367079 --rpc-url https://preseed-testnet-1.roburna.com
```

Fund the contract from any **funded** wallet you control. For the 5 RBAT example, a 5 RBAT deposit funds at most 50 claims until replenished. The sender also needs gas. This command uses the deployer as an example funding wallet; use a separate treasury account if preferred:

```bash
cast send "$FAUCET_ADDRESS" --value 5ether --account roburna-deployer --legacy --rpc-url https://preseed-testnet-1.roburna.com
cast balance "$FAUCET_ADDRESS" --ether --rpc-url https://preseed-testnet-1.roburna.com
cast balance 0x546501e0c1d35822CcA63ef2A4787Ff1c2367079 --ether --rpc-url https://preseed-testnet-1.roburna.com
```

The distributor must have its **own** small RBAT balance for gas. Funding the faucet contract does not fund the distributor. Keep the bulk treasury elsewhere and refill the contract as needed.

## 3. Configure the API and worker

From `backend/`:

```bash
cp -n config/chains.roburna.example.json config/chains.json
cp -n compose.env.example compose.env
python3 scripts/create_keystore.py
```

The keystore script prompts for the **distributor** private key and a 12+ character password. It must print `0x546501e0c1d35822CcA63ef2A4787Ff1c2367079`. If local Python dependencies are unavailable, install `backend/requirements.txt` in your own Python environment before running it. Do not reuse the deployer keystore. The script writes `backend/secrets/distributor.json` with mode `0600`; set `BACKEND_UID` and `BACKEND_GID` in `compose.env` to the output of `id -u` and `id -g` so the Docker worker can read it. The API container has no keystore mount or password.

Edit the ignored `compose.env` before starting. Set strong `POSTGRES_PASSWORD` and the **same** password in `BACKEND_DATABASE_URL`; the URL host remains `db`. Generate `BACKEND_RATE_LIMIT_HASH_SECRET` with `python3 -c "import secrets; print(secrets.token_hex(32))"` and keep it stable. Set `BACKEND_DISTRIBUTOR_KEYSTORE_PASSWORD` to the keystore password. Set `BACKEND_SIWE_DOMAIN` and `BACKEND_SIWE_URI` to the exact public frontend origin that users will sign for; `localhost:3000` is only an example. The RPC is configured as `ROBURNA_TESTNET` in `BACKEND_RPC_URLS`. Match `payout_amount_wei` in `config/chains.json` to the chosen contract maximum and keep it at or below both the on-chain maximum and period budget.

The deployment JSON, `config/chains.json`, and encrypted keystore are ignored by Git. Back up the PostgreSQL volume and these files securely. If you already have claim history in a different PostgreSQL database, migrate that history before switching to the Compose database; otherwise the wallet cooldown history starts empty.

## 4. Start and check Compose

From `backend/`:

```bash
docker compose --env-file compose.env config --quiet
docker compose --env-file compose.env up -d --build
docker compose --env-file compose.env ps
docker compose --env-file compose.env logs --tail=100 migrate api worker
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/chains
```

Compose starts PostgreSQL, applies the existing Alembic migrations, then starts one FastAPI process and one transaction worker. API startup checks chain ID, deployed code, distributor role, and payout limits. Worker startup additionally checks that the keystore derives the configured distributor. Any mismatch fails startup. The API is published only on `127.0.0.1:8000`; PostgreSQL is not published. Use an HTTPS reverse proxy for external access and set `BACKEND_TRUSTED_PROXY_CIDRS` to its actual source network so IP limits identify the real client. The current API has no browser CORS configuration, and the Next.js claim UI is still a starter; configure that integration before opening a public site. CAPTCHA, VPN reputation checks, and alerting are still outstanding.

Use `docker compose --env-file compose.env down` to stop containers while retaining the PostgreSQL volume. Do not remove the volume if you need claim history. If deployment metadata or chain config changes, restart API and worker after reviewing the change.

## Worker capacity today

The worker polls each enabled chain every **2 seconds** by default. For a chain, it processes **one active transaction at a time**: it submits one reserved claim, then reconciles that transaction and waits for the configured confirmation depth before starting the next. With one chain and immediate confirmation, the two required poll iterations imply a theoretical ceiling of roughly **one completed payout per 4 seconds (15/minute)**, before RPC calls, block time, and database work; real throughput is lower and can be much lower if a transaction stalls. It is not a batch or parallel sender. Starting multiple worker containers does not raise this single-distributor capacity because they coordinate through a PostgreSQL advisory lock and still see the same active attempt.

The sample contract cap is tighter: `5 RBAT / 0.1 RBAT = 50` full payouts per UTC day, even if the worker could process more. There is no configured request queue-size limit; reservations can accumulate when submissions lag. Check `claims` and `transaction_attempts` plus worker logs for backlog and stuck transactions before increasing limits.
