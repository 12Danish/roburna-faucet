# Roburna faucet backend

Phase 3.1 adds the FastAPI application skeleton, validated per-chain settings, startup checks, and read-only `/health` and `/chains` endpoints. It does not submit payouts.

## Local setup

From `backend/`, create and activate your own virtual environment, then install the pinned dependencies:

```bash
python -m pip install -r requirements.txt
cp .env.example .env
cp config/chains.example.json config/chains.json
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
