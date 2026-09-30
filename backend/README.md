# Roburna faucet backend

Phase 3.1 provides a small FastAPI app with typed chain settings, startup validation, and read-only `/health` and `/chains` endpoints. Phase 3.2 defines PostgreSQL tables as SQLAlchemy ORM models and reserves claims in an atomic database transaction. Phase 3.3 adds SIWE wallet challenges and signature verification. There is no claim submission endpoint or transaction worker yet.

## Local setup

From `backend/`, create and activate your own virtual environment, then install the pinned dependencies:

```bash
python -m pip install -r requirements.txt
cp config/chains.example.json config/chains.json
```

A local `backend/.env` template is present and ignored by Git. Replace `YOUR_PASSWORD` in `BACKEND_DATABASE_URL` with the password for your local PostgreSQL role. Set `BACKEND_SIWE_DOMAIN` and `BACKEND_SIWE_URI` to the frontend origin that users should see in the wallet signing prompt; local defaults use `localhost:3000`. The challenge lifetime defaults to 300 seconds. To create that role and database in WSL, start PostgreSQL and open its admin console:

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

Use a URL-safe password for this local setup, or URL-encode special characters in the URL. The table definitions live in `app/db/models.py`. From `backend/`, ask Alembic to generate a migration by comparing those models with the database:

```bash
alembic revision --autogenerate -m "create faucet tables"
```

Review the generated file in `migrations/versions/`, then apply it:

```bash
alembic upgrade head
```

Autogenerate uses the SQLAlchemy metadata as its source, but the migration file is still the versioned change that gets applied to the database. If this is an empty database, the generated revision should create all three tables and their constraints/indexes. If tables were already created with the old `create_schema.py` command, autogenerate may see no changes; recreate this disposable local database before generating the initial revision.

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

`POST /auth/challenge` accepts a wallet address and enabled chain ID, stores a one-time nonce, and returns a short-lived SIWE message for the wallet to sign. Signing is an off-chain message and does not authorize or submit a blockchain transaction. The `verify_wallet_challenge` service verifies the signature and all stored bindings, then checks that the recipient has no deployed code on the selected chain. Phase 3.4 will call this verifier before atomically consuming the challenge and reserving a claim.

The challenge verification tests use generated EOA keys and do not require PostgreSQL or an RPC node.
