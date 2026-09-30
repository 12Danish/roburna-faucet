# Roburna faucet backend

The backend provides chain configuration and readiness checks, SIWE wallet challenges, claim submission and status polling, PostgreSQL claim reservation, and a separate distributor worker. The backend chooses the payout amount; the recipient signs an off-chain challenge and does not pay gas.

## Local setup

From `backend/`, create and activate your own virtual environment, then install the pinned dependencies:

```bash
python -m pip install -r requirements.txt
cp config/chains.example.json config/chains.json
```

A local `backend/.env` template is present and ignored by Git. Replace `YOUR_PASSWORD` in `BACKEND_DATABASE_URL` with the password for your local PostgreSQL role. Set `BACKEND_SIWE_DOMAIN` and `BACKEND_SIWE_URI` to the frontend origin that users should see in the wallet signing prompt; local defaults use `localhost:3000`. Generate a random value for `BACKEND_RATE_LIMIT_HASH_SECRET` with `python -c "import secrets; print(secrets.token_hex(32))"`; keep it stable across API restarts and do not commit it. Leave `BACKEND_TRUSTED_PROXY_CIDRS=[]` for direct local access. In production, list only the actual reverse proxy networks there. The challenge lifetime defaults to 300 seconds. To create that role and database in WSL, start PostgreSQL and open its admin console:

```bash
sudo service postgresql start
sudo -u postgres psql
```

At the `psql` prompt, choose a local password and create the backend role and database:

```sql
CREATE USER faucet_app WITH PASSWORD 'choose_a_local_password';
CREATE DATABASE roburna_faucet OWNER faucet_app;
\q
```

The connection URL in `.env` should then look like:

```text
postgresql://faucet_app:YOUR_PASSWORD@127.0.0.1:5432/roburna_faucet
```

Use a URL-safe password for this local setup, or URL-encode special characters in the URL. Table definitions live in `app/db/models.py`; versioned Alembic files live in `migrations/versions/`. Review migrations before applying them. For a fresh local database, run:

```bash
alembic upgrade head
alembic check
```

When a model changes later, create and review the next migration with `alembic revision --autogenerate -m "describe change"`, then run `alembic upgrade head`. Autogenerate compares the ORM model to the database; it does not replace reviewing the generated SQL operations.

Start PostgreSQL and Anvil. After deploying the faucet to Anvil, export deployment metadata from the `contracts/` directory:

```bash
python3 scripts/export_deployment.py 31337
```

The deployment export is ignored by Git. The example chain entry starts with `enabled: false` because Anvil addresses reset when the node restarts. Enable it only after deployment and confirm the deployment file points to the current deployment.

Run the API from `backend/`:

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Then open `http://127.0.0.1:8000/docs`. `/health` checks PostgreSQL and enabled RPCs; `/chains` lists enabled public chain metadata. The server refuses to enable a chain with a mismatched RPC chain ID, missing faucet code, bad deployment metadata, missing distributor role, or a payout over the on-chain limit.

Never put private keys or authenticated RPC URLs in `chains.json` or commit `.env`. The distributor address is public configuration; signer key handling is part of Phase 3.5.

## Claim reservation behavior

`app/services/claims.py` consumes a valid, already-verified challenge and reserves one claim in a short SQLAlchemy transaction. It takes a PostgreSQL transaction-level advisory lock for the chain and wallet, checks for an active claim and recent confirmation, consumes the challenge, then inserts a `reserved` claim. The partial unique index is the database backstop. RPC calls stay outside this transaction. An uncertain broadcast remains active; a known failure frees the wallet to try again; the cooldown starts at confirmation.

A concurrency integration test exercises simultaneous reservations and those claim states. Set `FAUCET_TEST_DATABASE_URL` to a dedicated disposable PostgreSQL database before running `pytest` from `backend/`. The test creates its tables from the ORM metadata and removes its rows afterward. Do not point it at a database containing application data.

## Wallet challenge (Phase 3.3)

`POST /auth/challenge` accepts a wallet address and enabled chain ID, stores a one-time nonce, and returns a short-lived SIWE message for the wallet to sign. Signing is an off-chain message and does not authorize or submit a blockchain transaction. The `verify_wallet_challenge` service verifies the signature and all stored bindings, then checks that the recipient has no deployed code on the selected chain. `POST /claims` calls this verifier before atomically consuming the challenge and reserving a claim.

The challenge verification tests use generated EOA keys and do not require PostgreSQL or an RPC node. Challenge issuance enforces PostgreSQL-backed sliding-window limits by client IP and IPv4 `/24` or IPv6 `/64` prefix. It does not spend a wallet's rate-limit allowance before the wallet has signed. Claims enforce IP and subnet limits before signature/RPC work, then enforce a wallet limit after signature verification. Identifiers are HMACed before storage, and old events are pruned opportunistically. A wallet's claim bucket follows it when the requester changes VPN exits. IP and subnet limits add friction, but cannot identify every VPN provider or stop a determined user with many wallets and exits; VPN classification requires an external IP reputation source, which is not enabled yet.

| Endpoint | IP / minute, hour | Subnet / minute, hour | Verified wallet / minute, hour |
| --- | --- | --- | --- |
| `POST /auth/challenge` | 5, 30 | 20, 100 | — |
| `POST /claims` | 3, 10 | 15, 60 | 3, 10 |

Forwarded IP headers are ignored unless the direct peer matches `BACKEND_TRUSTED_PROXY_CIDRS`; configure those CIDRs to match your proxy exactly. Excess requests return HTTP `429` and `Retry-After`.

The rate-limit table is defined by `RateLimitEvent` and its Alembic revision. Run `alembic upgrade head` before starting the API against an existing local database.

## Claim API (Phase 3.4)

`POST /claims` accepts `challenge_id`, `chain_id`, `wallet_address`, the exact SIWE `message` returned by `/auth/challenge`, and its wallet `signature`. It rejects extra fields, including a client-selected payout amount. The endpoint verifies the signature and EOA recipient, checks the current contract pause, payout cap, period allowance, and balance, then reserves the server-configured payout in PostgreSQL. Contract reads are an early check; the worker and contract check again before and during payment. An accepted request returns HTTP `202` with claim status `reserved`. Repeating the same signed request returns the same claim ID. `GET /claims/{claim_id}` returns its current status, latest transaction hash, and next eligible time after confirmation. The status route needs the claim UUID returned by submission.

A pending claim returns `409`, an active wallet cooldown returns `429` with `nextEligibleAt`, and unavailable chain state returns `503`. CAPTCHA is deferred by project choice. Keep the API on a local/private network until a public-launch abuse policy and monitoring are ready.

## Distributor worker (Phase 3.5)

Before starting the worker, generate an encrypted keystore from the dedicated distributor key. Run this from `backend/`; the script prompts for the key and keystore password without echoing either value:

```bash
python scripts/create_keystore.py
```

Set `BACKEND_DISTRIBUTOR_KEYSTORE_PATH` and `BACKEND_DISTRIBUTOR_KEYSTORE_PASSWORD` in the ignored local `.env`. Keep the keystore file out of source control. The derived account must match `distributor_address` on every enabled chain. The worker rechecks the RPC chain ID and role before sending.

The ORM transaction-attempt model records the gas fields required to rebuild a same-nonce fee replacement and enforces one active attempt per `(chain_id, sender, nonce)`. Its schema change is included in the versioned Alembic revisions. Apply pending revisions with `alembic upgrade head` after review.

Run the worker as a separate process after the API and PostgreSQL are available:

```bash
python -m app.worker
```

It polls `reserved` claims, serializes each distributor/chain with a PostgreSQL advisory lock, persists signed transaction bytes before broadcast, and reconciles uncertain sends by hash. It waits for the configured confirmation depth before marking a payout confirmed. After `BACKEND_TRANSACTION_REPLACEMENT_AFTER_SECONDS`, it can replace a stuck transaction at the same nonce with a higher fee; the replacement retains the same recipient and amount. It stops after `BACKEND_TRANSACTION_MAX_REPLACEMENTS` replacements so fee bumps cannot continue without bound. The worker remains idle until `POST /claims` reserves a claim.
