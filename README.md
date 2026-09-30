# Roburna native faucet

A monorepo for dispensing a small amount of an EVM chain's **native currency**. The wallet signs an off-chain challenge; the backend approves and records the claim; a separate distributor worker pays gas and calls the faucet contract. The recipient does not send a transaction. This is a native-currency faucet, not an ERC-20 token faucet.

The Solidity contract and FastAPI claim flow are implemented. The Next.js frontend is currently a starter page. Local Anvil is the first test chain; each later EVM chain needs its own contract deployment, RPC settings, and backend chain entry. No Roburna testnet deployment is recorded in this repo. A [Roburna testnet deployment and Compose guide](docs/roburna_testnet_deployment.md) is available.

## Project layout

| Path | Purpose | Details |
| --- | --- | --- |
| [`contracts/`](contracts/README.md) | Foundry project: `NativeFaucet.sol`, Solidity tests, deployment script, and ABI/deployment exporter. | [Contract specification](contracts/README.md) · [original spec sheet](docs/native_faucet_contract_spec.docx) |
| [`backend/`](backend/README.md) | FastAPI routes, SQLAlchemy models, Alembic migrations, PostgreSQL rate limits, and distributor worker. | [Backend setup and security](backend/README.md) |
| [`frontend/`](frontend/README.md) | Next.js App Router, TypeScript, and Tailwind starter. | The wallet claim UI is still to be built. |
| [`docs/`](docs/implementation_plan.md) | Implementation plans, original spec, user flow, and deployment instructions. | [Deployment runbook](docs/deployment_runbook.md) · [faucet flow](docs/faucet_flow.png) |

Important source files: [`contracts/src/NativeFaucet.sol`](contracts/src/NativeFaucet.sol) holds and dispenses funds; [`backend/app/main.py`](backend/app/main.py) assembles the API; [`backend/app/api/routes/claims.py`](backend/app/api/routes/claims.py) accepts claims; [`backend/app/services/rate_limits.py`](backend/app/services/rate_limits.py) enforces request limits; [`backend/app/worker.py`](backend/app/worker.py) starts the distributor process. Configuration examples are [`contracts/.env.example`](contracts/.env.example), [`backend/.env.example`](backend/.env.example), and [`backend/config/chains.example.json`](backend/config/chains.example.json).

## Claim flow

1. The client reads `GET /chains`, then requests a one-time SIWE message from `POST /auth/challenge` for its wallet and selected chain.
2. The wallet signs that message off-chain. `POST /claims` verifies the signature and eligibility, checks request limits and current faucet capacity, and reserves the server-configured payout in PostgreSQL.
3. The worker signs and submits `dispense(recipient, amount)` as the authorized distributor. The contract enforces its role, pause, payout, period-budget, recipient-code, and balance rules.
4. `GET /claims/{claim_id}` reports the claim state and transaction hash. A confirmed payout starts the backend's per-wallet cooldown.

The contract holds the native-currency payout pool. The distributor wallet needs native currency for **gas**. An admin controls roles, limits, pause, and withdrawals; a separate treasury wallet can fund the contract. Keep these keys separate.

## Run locally

1. Install Foundry, Python/PostgreSQL, and Node.js. Start Anvil, then deploy and fund the contract using the [deployment runbook](docs/deployment_runbook.md). Export its deployment metadata after broadcasting.
2. Configure PostgreSQL, `backend/.env`, and `backend/config/chains.json`; review and apply Alembic migrations. Follow the [backend setup](backend/README.md). An enabled chain must point to the live contract and the correct distributor address.
3. Start the API with `uvicorn app.main:app --reload --host 127.0.0.1 --port 8000` from `backend/`. Start the separate worker with `python -m app.worker` after configuring its encrypted distributor keystore.
4. Run `forge test` from `contracts/` and `python -m pytest` from `backend/`. PostgreSQL integration tests additionally need `FAUCET_TEST_DATABASE_URL` set to a database permitted for tests.

The backend has been exercised end to end against an isolated Anvil node and temporary PostgreSQL schema: challenge, signed claim, worker payout, confirmed status, idempotent retry, and cooldown. See each subproject README for what its automated tests cover.

## Deployment and security boundary

The contract is non-upgradeable; deploy the reviewed source separately on each compatible chain. The backend validates RPC chain ID, deployed code, distributor role, and configured payout before enabling a chain. Contract changes require a new deployment and an explicit backend configuration update. See the [contract specification](contracts/README.md) and [deployment runbook](docs/deployment_runbook.md).

The backend currently uses SIWE ownership checks, PostgreSQL claim reservation and cooldown, IP/network/wallet request limits, and durable transaction reconciliation. CAPTCHA, VPN reputation checks, and alerting remain to be added before opening a public faucet. Local Anvil accounts and keys must never be used on a public chain.
