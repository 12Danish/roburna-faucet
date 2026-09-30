# Roburna testnet deployment and Docker Compose

The public RPC returned chain ID `159` (`0x9f`) and `eth_gasPrice = 1 gwei` when checked on 2026-09-30. The current block exposed a zero `baseFeePerGas`. This guide uses legacy transactions. Recheck the live RPC before broadcasting because chain settings can change. The WebSocket endpoint is not needed by the current HTTP polling worker.

## 1. Choose the payout and prepare the three roles

The deployer is `0x7c2Eb5047858AD800414Dc8Ac60417A5d73be409`. It pays deployment gas but receives no role in the new contract. The admin is `0xbDd4628028F85bb7C4aECd9d1F426aE9525aF8F4`; only this address can pause, change payout limits, manage roles, or withdraw. The distributor is `0x546501e0c1d35822CcA63ef2A4787Ff1c2367079`; the backend worker uses its private key to call `dispense` and pays the gas. These addresses must be controlled by their respective owners. A treasury can be any funded wallet that sends RBAT to the contract address; its key is not needed by the API or worker.

[`contracts/.env.roburna.example`](../contracts/.env.roburna.example) sets a **10 RBAT maximum per payout** and a **1,000 RBAT on-chain spending cap per UTC day** (`86400` seconds). [`backend/config/chains.roburna.example.json`](../backend/config/chains.roburna.example.json) pays exactly **10 RBAT per successful claim** and allows **one confirmed claim per wallet every 24 hours**. Thus the configured backend allows up to 100 full claims per UTC day while the contract has funds. The 24-hour wallet cooldown is enforced by the backend database, not by the contract; an authorized distributor key can call the contract directly within its on-chain limits. The contract's period duration cannot be changed later. RBAT has 18 decimals: `10000000000000000000` wei = 10 RBAT; `1000000000000000000000` wei = 1,000 RBAT.

For the initial balance-cap test, set `FAUCET_RECIPIENT_BALANCE_LIMIT_WEI=10000000000000000000` in `contracts/.env.roburna`. This caps each wallet at 10 RBAT *after* a faucet payout. With a fixed 10 RBAT payout, only a wallet starting at zero RBAT qualifies. The admin can later increase the cap with `setRecipientBalanceLimit(uint256)`; 500 RBAT is `500000000000000000000` wei. The existing deployed faucet at `0xDB2c44c507Ec4C610F0a8035DEDBa00585176d55` cannot be upgraded; activating this rule requires a new deployment, updated deployment metadata and backend address, and moving funds after pausing the old contract.

Use separate keys for deployer, admin, and distributor. Fund the deployer with enough RBAT for deployment gas and the distributor with enough RBAT for payout gas. The contract itself needs RBAT for the actual payouts.

## 2. Deploy the contract

From `contracts/`:

```bash
cp .env.roburna.example .env.roburna
cast chain-id --rpc-url https://preseed-testnet-1.roburna.com
cast wallet import roburna-deployer --interactive
cast wallet address --account roburna-deployer
cast balance 0x7c2Eb5047858AD800414Dc8Ac60417A5d73be409 --rpc-url https://preseed-testnet-1.roburna.com
forge test
python3 scripts/deploy.py --env-file .env.roburna
python3 scripts/deploy.py --env-file .env.roburna --broadcast
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

Fund the contract with **1,000 RBAT from the deployer account**, as requested. The deployer must hold **more than 1,000 RBAT** before this transfer: it also pays gas for both deployment and funding. At 10 RBAT per claim, this deposit funds 100 successful claims in total until replenished. The daily spending cap resets every UTC day, but the deposit does not refill automatically:

```bash
cast send "$FAUCET_ADDRESS" --value 1000000000000000000000 --account roburna-deployer --legacy --rpc-url https://preseed-testnet-1.roburna.com
cast balance "$FAUCET_ADDRESS" --ether --rpc-url https://preseed-testnet-1.roburna.com
cast balance 0x546501e0c1d35822CcA63ef2A4787Ff1c2367079 --ether --rpc-url https://preseed-testnet-1.roburna.com
```

The distributor must have its **own** small RBAT balance for gas. Funding the faucet contract does not fund the distributor. Keep the bulk treasury elsewhere and refill the contract as needed.

## 3. Configure the API and worker

From `backend/`, first back up any existing local `config/chains.json` if you want to retain its Anvil settings. Then use the Roburna configuration:

```bash
cp config/chains.roburna.example.json config/chains.json
cp -n compose.env.example compose.env
python3 scripts/create_keystore.py
```

Run the keystore script **before** starting the worker. It prompts for the **distributor** private key and a 12+ character password and must print `0x546501e0c1d35822CcA63ef2A4787Ff1c2367079`. Compose requires the keystore source to be an existing file and will not create a directory in its place. If local Python dependencies are unavailable, install `backend/requirements.txt` in your own Python environment before running it. Do not reuse the deployer keystore. The script writes `backend/secrets/distributor.json` with mode `0600`; set `BACKEND_UID` and `BACKEND_GID` in `compose.env` to the output of `id -u` and `id -g` so the Docker worker can read it. The API container has no keystore mount or password.

Edit the ignored `compose.env` before starting. Set strong `POSTGRES_PASSWORD` and the **same** password in `BACKEND_DATABASE_URL`; the URL host remains `db`. Generate `BACKEND_RATE_LIMIT_HASH_SECRET` with `python3 -c "import secrets; print(secrets.token_hex(32))"` and keep it stable. Set `BACKEND_SIWE_DOMAIN` and `BACKEND_SIWE_URI` to the exact public frontend origin that users will sign for; `localhost:3000` is only an example. The RPC is configured as `ROBURNA_TESTNET` in `BACKEND_RPC_URLS`. Match `payout_amount_wei` in `config/chains.json` to the chosen contract maximum and keep it at or below both the on-chain maximum and period budget.

Keep the distributor keystore password outside the repository in the worker-only file referenced by `BACKEND_WORKER_ENV_FILE` (default `/etc/roburna-faucet/worker.env`). On the server, create a root-owned directory and file readable by your Compose operator's group, then edit the file **without typing the password into a shell command**:

```bash
sudo install -d -o root -g "$(id -gn)" -m 0750 /etc/roburna-faucet
sudo install -o root -g "$(id -gn)" -m 0640 /dev/null /etc/roburna-faucet/worker.env
sudoedit /etc/roburna-faucet/worker.env
```

The file contains exactly one setting: `BACKEND_DISTRIBUTOR_KEYSTORE_PASSWORD=<the password you chose in create_keystore.py>`. If you already stored the password in `backend/compose.env`, move it to this file and then remove that old line. The worker-only `env_file` uses Compose's raw format so characters such as `$` are preserved. Because Compose must read the file as your login user, the group read permission is intentional. Keep the file outside Git and backups unless the backups are encrypted. This is still a container environment variable at runtime; Docker administrators can inspect it.

The deployment JSON, `config/chains.json`, and encrypted keystore are ignored by Git. Back up the PostgreSQL volume and these files securely. If you already have claim history in a different PostgreSQL database, migrate that history before switching to the Compose database; otherwise the wallet cooldown history starts empty.

Roburna block headers require Web3.py PoA compatibility; the example chain JSON sets `poa_compatibility: true`. The configured RPC must accept `eth_sendRawTransaction` as well as reads. If a claim shows `rpc_transaction_pool_unavailable`, the public node is rejecting broadcasts with `Transaction pool not enabled`; ask the Roburna node operator to restore a synced, transaction-accepting RPC or provide another write-capable chain-159 endpoint. The existing claim ID is retained and the worker retries the same signed transaction. Do not submit another claim or reset the database.

## 4. Start and check Compose

From `backend/`:

```bash
docker compose --env-file compose.env config --quiet
docker compose --env-file compose.env up -d --build
docker compose --env-file compose.env ps
docker compose --env-file compose.env logs --tail=100 migrate api worker
API_PORT=$(awk -F= '$1 == "BACKEND_PORT" {print $2}' compose.env)
curl -fsS "http://127.0.0.1:${API_PORT:-8000}/health"
curl -fsS "http://127.0.0.1:${API_PORT:-8000}/chains"
```

For a read-only check of API functionality, `GET /health` must report both `database` and `chain:159` as `ok`, and `GET /chains` must show the deployed faucet and `payout_amount_wei` of `10000000000000000000`. To check challenge issuance without sending RBAT, run:

```bash
curl -fsS -X POST "http://127.0.0.1:${API_PORT:-8000}/auth/challenge" \
  -H 'Content-Type: application/json' \
  -d '{"wallet_address":"0x7c2Eb5047858AD800414Dc8Ac60417A5d73be409","chain_id":159}'
```

To exercise the full worker flow, use a **separate test wallet** stored as a Foundry keystore (for example, import one with `cast wallet import faucet-test --interactive`). From `backend/`, run:

```bash
python3 scripts/smoke_claim.py --account faucet-test --api-url "http://127.0.0.1:${API_PORT:-8000}"
```

The script obtains a challenge, prompts through Foundry to sign it, submits one claim, and polls until it is confirmed. It does not accept a private key argument. A successful test **sends 10 RBAT** to the test wallet and starts that wallet's 24-hour cooldown. If it times out, inspect `docker compose --env-file compose.env logs --tail=100 worker` and the claim ID printed by the script. Do not use the deployer or admin wallet for this smoke test.

Compose starts PostgreSQL, applies the existing Alembic migrations, then starts one FastAPI process and one transaction worker. API startup checks chain ID, deployed code, distributor role, and payout limits. Worker startup additionally checks that the keystore derives the configured distributor. Any mismatch fails startup. The API is published only on the loopback address at the `BACKEND_PORT` value in `compose.env` (default 8000); PostgreSQL is not published. Use an HTTPS reverse proxy for external access and set `BACKEND_TRUSTED_PROXY_CIDRS` to its actual source network so IP limits identify the real client. The current API has no browser CORS configuration, and the Next.js claim UI is still a starter; configure that integration before opening a public site. CAPTCHA, VPN reputation checks, and alerting are still outstanding.

Use `docker compose --env-file compose.env down` to stop containers while retaining the PostgreSQL volume. Do not remove the volume if you need claim history. If deployment metadata or chain config changes, restart API and worker after reviewing the change.

## Worker capacity today

The worker polls each enabled chain every **2 seconds** by default. For a chain, it processes **one active transaction at a time**: it submits one reserved claim, then reconciles that transaction and waits for the configured confirmation depth before starting the next. With one chain and immediate confirmation, the two required poll iterations imply a theoretical ceiling of roughly **one completed payout per 4 seconds (15/minute)**, before RPC calls, block time, and database work; real throughput is lower and can be much lower if a transaction stalls. It is not a batch or parallel sender. Starting multiple worker containers does not raise this single-distributor capacity because they coordinate through a PostgreSQL advisory lock and still see the same active attempt.

The configured contract cap is tighter: `1,000 RBAT / 10 RBAT = 100` full payouts per UTC day, even if the worker could process more. A 1,000 RBAT initial deposit covers only 100 claims across all days until the contract is funded again. There is no configured request queue-size limit; reservations can accumulate when submissions lag. Check `claims` and `transaction_attempts` plus worker logs for backlog and stuck transactions before increasing limits.
