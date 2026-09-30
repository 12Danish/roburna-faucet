# Roburna faucet backend

[Project overview](../README.md) · [Contract specification](../contracts/README.md) · [Backend implementation plan](../docs/step3_backend_plan.md)

This FastAPI service coordinates native-currency claims on configured EVM chains. A user signs a short-lived SIWE message; the API verifies ownership and reserves a fixed payout in PostgreSQL; a separate worker signs `NativeFaucet.dispense(recipient, amount)` with the distributor wallet. The wallet receives native currency without paying gas. The API never accepts a client-selected payout amount or a private key.

## Project files

| Path | Responsibility |
| --- | --- |
| [`app/main.py`](app/main.py), [`app/api/routes/`](app/api/routes/) | FastAPI lifespan, health and chain metadata, challenge issuance, claim submission, and status polling. |
| [`app/api/schemas.py`](app/api/schemas.py) | Validated request and response models; extra claim fields are rejected. |
| [`app/core/config.py`](app/core/config.py), [`app/core/chain_config.py`](app/core/chain_config.py) | Typed environment settings and per-chain configuration. |
| [`app/services/auth.py`](app/services/auth.py) | SIWE challenge creation, signature and EOA checks, authenticated claim retries. |
| [`app/services/claims.py`](app/services/claims.py), [`app/services/eligibility.py`](app/services/eligibility.py) | Atomic claim reservation, cooldown, claim status, and early contract-capacity checks. |
| [`app/services/rate_limits.py`](app/services/rate_limits.py) | PostgreSQL request limits and trusted-proxy client IP resolution. |
| [`app/services/transactions.py`](app/services/transactions.py), [`app/worker.py`](app/worker.py) | Distributor nonce coordination, signing, broadcast, receipt reconciliation, and worker process. |
| [`app/db/models.py`](app/db/models.py), [`migrations/versions/`](migrations/versions/) | SQLAlchemy table definitions and versioned Alembic migrations. |
| [`config/chains.example.json`](config/chains.example.json), [`.env.example`](.env.example) | Templates for local or per-chain settings. The live `config/chains.json` and `.env` are ignored by Git. |
| [`scripts/create_keystore.py`](scripts/create_keystore.py), [`tests/`](tests/) | Encrypted distributor keystore creation and backend tests. |

PostgreSQL stores `challenges`, `claims`, `transaction_attempts`, and short-lived `rate_limit_events`. It remains the authority for claim state; there is no Redis dependency.

## API and claim lifecycle

| Route | Result |
| --- | --- |
| `GET /health` | Readiness for PostgreSQL and enabled RPCs. |
| `GET /chains` | Enabled chain metadata, payout amount, cooldown, and faucet address. |
| `POST /auth/challenge` | A one-time SIWE message bound to wallet, chain ID, origin, nonce, and expiry. |
| `POST /claims` | Verifies the signed challenge, checks eligibility and limits, then returns `202` with the reserved claim ID and status. |
| `GET /claims/{claim_id}` | Current state, amount, latest transaction hash, and next eligible time after confirmation. |

A claim request contains `challenge_id`, `chain_id`, `wallet_address`, the exact SIWE `message`, and its `signature`. The API checks current contract pause, payout cap, remaining period allowance, and balance before reservation. These reads can become stale; the worker checks again, and the contract is the final payout gate. The database transaction consumes the challenge and inserts one `reserved` claim under a per-chain/per-wallet advisory lock. It blocks an active claim and starts the configured wallet cooldown only after a confirmed payout. Repeating an accepted signed request returns the same claim ID.

The worker processes reserved claims separately from FastAPI. It persists each signed transaction and nonce **before** broadcast, reconciles uncertain sends by transaction hash, and waits for the configured confirmation depth. A stuck transaction may be replaced with a higher fee at the **same nonce**, preserving recipient and amount. A pending or uncertain claim stays blocked until its chain outcome is known.

## Security controls

| Control | Implementation |
| --- | --- |
| Wallet ownership and replay | SIWE signature must match the recipient; message binds wallet, chain, domain, URI, nonce, and expiry. Each challenge is consumed once with claim reservation. Authenticated retries return the existing claim. |
| Recipient type | Backend checks that no code is deployed at the recipient address; the contract repeats this at payout. This excludes smart contract wallets but is not, by itself, proof of EOA ownership. |
| Payout and cooldown | Amount comes from chain config. PostgreSQL permits one active claim per wallet and chain, records confirmed history, and enforces the configured cooldown (86400 seconds in the Anvil example). The contract enforces its own per-payout and period limits. |
| Request limits | Challenges have IP and network-prefix limits. Claims check IP/prefix before expensive verification, then a wallet limit **after** signature verification so another person cannot consume that wallet's claim allowance. |
| Client IP | `X-Forwarded-For` is used only when the direct peer is in `BACKEND_TRUSTED_PROXY_CIDRS`; otherwise the direct peer IP is used. Store HMAC identifiers in `rate_limit_events`, not raw IPs or wallet addresses. |
| Signing and recovery | Use a dedicated distributor key in an encrypted local keystore; never use an admin, treasury, validator, bridge, or deployer key. The worker checks RPC chain ID and distributor role. Database locks coordinate nonces across workers; signed bytes, attempts, and receipts survive restart. |
| Startup validation | An enabled chain must have the expected RPC chain ID, deployed code, ABI/deployment metadata, distributor role, and a configured payout within on-chain limits. |

Current rolling request limits are fixed in [`app/services/rate_limits.py`](app/services/rate_limits.py):

| Endpoint | IP: minute / hour | IPv4 `/24` or IPv6 `/64`: minute / hour | Verified wallet: minute / hour |
| --- | ---: | ---: | ---: |
| `POST /auth/challenge` | 5 / 30 | 20 / 100 | — |
| `POST /claims` | 3 / 10 | 15 / 60 | 3 / 10 |

A limit returns HTTP `429` with `Retry-After`; an active claim returns `409`; cooldown returns `429` with `nextEligibleAt`. A wallet's claim bucket remains the same when its IP changes. IP/network limits and wallet cooldown make abuse harder but do not identify every VPN or stop a person with many wallets. CAPTCHA is deferred; VPN reputation checks and alerting are not implemented. Keep the API private/local until those public-faucet controls are decided and deployed.

## Local setup

From `backend/`, activate your Python environment and install dependencies:

```bash
python -m pip install -r requirements.txt
cp -n config/chains.example.json config/chains.json
cp -n .env.example .env
```

Create a PostgreSQL role and database if you do not already have them:

```bash
sudo service postgresql start
sudo -u postgres psql
```

```sql
CREATE USER faucet_app WITH PASSWORD 'choose_a_local_password';
CREATE DATABASE roburna_faucet OWNER faucet_app;
\q
```

In the ignored `.env`, set `BACKEND_DATABASE_URL`, `BACKEND_RPC_URLS`, `BACKEND_SIWE_DOMAIN`, and `BACKEND_SIWE_URI`. Generate `BACKEND_RATE_LIMIT_HASH_SECRET` with `python -c "import secrets; print(secrets.token_hex(32))"`; keep it stable across restarts. Leave `BACKEND_TRUSTED_PROXY_CIDRS=[]` for direct local access. When a reverse proxy is introduced, list only its actual network ranges there. The full setting list is in [`.env.example`](.env.example). Keep credential-bearing RPC URLs and keystore passwords out of Git.

Start Anvil and deploy/fund the contract according to the [deployment runbook](../docs/deployment_runbook.md). From `contracts/`, export the deployment with `python3 scripts/export_deployment.py 31337`. Then set `enabled: true` in the ignored `backend/config/chains.json` only when it points to the current deployment and the configured distributor has the contract role. A fresh Anvil node loses the previous deployment.

Review [the migrations](migrations/versions/), then from `backend/` run:

```bash
alembic upgrade head
alembic check
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

The API docs are at `http://127.0.0.1:8000/docs`. When a model changes later, use `alembic revision --autogenerate -m "describe change"`, review the generated operations, then apply the migration.

To run payouts, create an encrypted keystore for the **distributor** address:

```bash
python scripts/create_keystore.py
```

Set `BACKEND_DISTRIBUTOR_KEYSTORE_PATH` and `BACKEND_DISTRIBUTOR_KEYSTORE_PASSWORD` in the worker's environment or ignored `.env`. The keystore must derive the distributor configured for every enabled chain. Start the worker in a separate terminal from `backend/`:

```bash
python -m app.worker
```

## Tests

From `backend/`, run `python -m pytest`. Unit and API tests cover SIWE binding, invalid signatures, EOA checks, rate-limit response order, cooldown/error mapping, nonce choice, and fee replacement calculations. For PostgreSQL integration tests, set `FAUCET_TEST_DATABASE_URL` to a **dedicated test database** and run `python -m pytest` again. The claim-reservation test exercises concurrent claims, pending states, failure release, and cooldown. The rate-limit tests verify every configured minute/hour threshold, concurrent IP requests, and wallet limits across IP changes; they create and drop a temporary schema. PostgreSQL tests skip when the test URL is absent.

A separate isolated Anvil/PostgreSQL smoke run has verified challenge → signed claim → worker → confirmed payout, idempotent retry, and cooldown. That smoke run is not yet a checked-in automated test. The [Solidity tests](../contracts/README.md) cover the contract's own security rules.
