# Roburna faucet backend

Phase 3.1 adds the FastAPI application skeleton, validated per-chain settings, startup checks, and read-only `/health` and `/chains` endpoints. It does not submit payouts.

## Local setup

From `backend/`, create and activate your own virtual environment, then install the pinned dependencies:

```bash
python -m pip install -r requirements.txt
cp config/chains.example.json config/chains.json
```

A local `backend/.env` template is present and ignored by Git. Replace `YOUR_PASSWORD` in `BACKEND_DATABASE_URL` with the password for your local PostgreSQL role. To create that role and the database in WSL, start PostgreSQL and open its admin console:

```bash
sudo service postgresql start
sudo -u postgres psql
```

Then run these SQL commands in `psql`, choosing a password you can put in `backend/.env`:

```sql
CREATE ROLE faucet_app WITH LOGIN PASSWORD 'choose_a_local_password';
CREATE DATABASE roburna_faucet OWNER faucet_app;
\q
```

Use a URL-safe password (letters and numbers) for this local setup, or URL-encode special characters in the connection URL. The configured URL should look like:

```text
postgresql://faucet_app:YOUR_PASSWORD@127.0.0.1:5432/roburna_faucet
```

Run the migration after setting the URL:

```bash
alembic upgrade head
```

Start PostgreSQL and Anvil. After deploying the faucet to Anvil, export deployment metadata from the `contracts/` directory:

```bash
python3 scripts/export_deployment.py 31337
```

The deployment export is ignored by Git. The example chain entry starts with `enabled: false` because Anvil addresses reset when the node is restarted. Enable it only after deployment and confirm the deployment file points to the current Anvil deployment. Set `BACKEND_DATABASE_URL` and `BACKEND_RPC_URLS` in the local `.env` file.

Run the development server from `backend/`:

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Then open `http://127.0.0.1:8000/docs`. `/health` reports readiness for PostgreSQL and enabled RPCs; `/chains` lists enabled public chain metadata. The server refuses to start if an enabled chain has a wrong RPC chain ID, missing faucet code, a bad deployment record, a missing distributor role, or a configured payout over the on-chain limit.

Never put private keys or authenticated RPC URLs in `chains.json` or commit `.env`. The distributor address is public configuration; signer key handling is part of Phase 3.5.

## Phase 3.2 database setup

After PostgreSQL is available and `.env` contains `BACKEND_DATABASE_URL`, run the `alembic upgrade head` command shown above from `backend/` to apply the versioned schema migration. The migration creates `challenges`, `claims`, and `transaction_attempts`. The backend’s claim reservation function consumes a valid, already-verified challenge and inserts a `reserved` claim in one short PostgreSQL transaction. A transaction-scoped advisory lock serializes reservations for the same chain and wallet; a partial unique index is the database-level backstop. Cooldown is measured from a confirmed claim. No blockchain transaction is sent by this phase.

A concurrency integration test uses a **dedicated disposable PostgreSQL database**. Set `FAUCET_TEST_DATABASE_URL` to that database and run `pytest` from `backend/`; without it, the integration test is skipped. Do not point it at a database containing application data because it applies the schema migration.
