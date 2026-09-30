# Step 3: Minimal FastAPI faucet backend

## Goal and boundaries

Build the backend against the existing Anvil deployment first. Later, use the same application with a new chain configuration and the custom-chain deployment record. The contract keeps distributor authorization, per-payout maximum, global period spending, pause, balance checks, and deployed-contract recipient rejection. The backend verifies wallet ownership, enforces one confirmed claim per wallet per 24 hours, and submits `dispense` using its distributor signer. The browser never receives the distributor key or chooses the payout amount.

Use **FastAPI + web3.py + PostgreSQL**. Keep Redis out of the initial design. Use a small API process and one separate worker process for transaction submission and receipt reconciliation. Do not rely on in-process `BackgroundTasks` for payment completion: a process restart could lose work. PostgreSQL is the durable source of truth.

## Security scope for Roburna

Build these controls before opening a public faucet: fixed server-selected payout; one confirmed claim per wallet and chain per 24 hours; durable claim history; one-time wallet signature; independent wallet and IP/endpoint rate limits; server-validated Turnstile or comparable challenge; PostgreSQL claim reservation before broadcast; dedicated distributor signer; a small contract balance; the existing on-chain per-payout and period caps; and basic decision logs and alerts. Run without CAPTCHA only on a loopback-only Anvil development service.

The distributor wallet holds **gas only**; the payout pool lives in `NativeFaucet`. Do not put treasury, admin, validator, bridge, or deployer keys into the API process. Keep treasury funding separate and refill the contract in limited amounts. A compromised distributor can still spend from the contract up to its on-chain limit, so the contract balance and `spendingLimit` bound exposure.

Defer target-balance top-ups, subnet/ASN scoring, authenticated-account quotas, mainnet-history checks, proof-of-personhood, Proof-of-Work, and a second on-chain hourly/daily cap until traffic, abuse, or token scarcity justifies them. The first version has no user accounts and should not reject new developers for lacking mainnet history. If target-balance top-ups are later added, compute the payout server-side as a capped amount and retain the 24-hour historical cooldown. Use subnet/ASN signals for graduated risk handling rather than blanket denial of shared networks.

The current contract has **one** configurable fixed-period global cap, not separate hourly and daily caps. A backend threshold can temporarily disable claims and alert operators, while the admin can pause the contract. Do not place the admin key in the web service merely to automate `pause()`. Adding a second on-chain cap would require a new contract version. Log decisions, claim IDs, wallet/chain, amount, receipt, and minimal IP/risk data with a retention policy; alert on payout spikes, many wallets from one origin, repeated invalid signatures/challenges, transaction failures, and low faucet balance.

## Public API for the first version

| Route | Purpose | Main response |
| --- | --- | --- |
| `GET /health` | Liveness/readiness for API, database, and enabled chains. | Status; no secrets. |
| `GET /chains` | List enabled public chain metadata. | Chain ID, symbol, payout amount, cooldown, explorer URL. |
| `POST /auth/challenge` | Create a short-lived message for a wallet and chain to sign. | Challenge ID, SIWE message, expiry. |
| `POST /claims` | Verify signature and checks; reserve a claim. | `202 Accepted`, claim ID, `pending` status. |
| `GET /claims/{claim_id}` | Let the frontend poll the claim. | Status, transaction hash when known, `nextEligibleAt` after confirmation. |

The API returns `429` with `nextEligibleAt` for cooldown, `409` for an existing pending claim, and `503` for an unavailable chain. It never accepts an amount, contract address, transaction nonce, or distributor address from the client. Admin actions (`pause`, limits, role management, withdrawal) stay outside the public API.

## Phase 3.1 — App skeleton and validated configuration

- Create a small `backend/app/` package with `main.py`, `api/`, `core/config.py`, `db/`, `services/`, and `worker.py`; put tests in `backend/tests/`. Use FastAPI routers and dependency injection for database and chain clients. Use application lifespan to create and close shared resources.
- Pin the existing dependencies in `backend/requirements.txt` when implementation starts. Add only what is needed for SIWE verification, migrations, and tests. Keep a tracked `backend/.env.example` and ignored local `backend/.env`; do not put signing keys or credential-bearing RPC URLs in source control.
- Define per-chain config: chain ID, RPC URL, deployment metadata/ABI path, contract address, payout amount in smallest units, 24-hour cooldown, confirmation depth, fee mode (`legacy` or EIP-1559), explorer URL, enabled flag, and distributor signer reference. Make the local Anvil configuration the first entry.
- On startup, verify RPC chain ID, deployed code, `DISTRIBUTOR_ROLE` for the signer, and that configured payout is positive and no greater than on-chain `maxPayout`. Refuse to enable a misconfigured chain. Do not assume an Anvil address survives a node reset.

**Gate:** `GET /health` and `GET /chains` work; malformed config or a mismatched chain/contract disables payouts. No claim transaction exists yet.

## Phase 3.2 — PostgreSQL schema and transaction rules

- Add versioned migrations for `challenges`, `claims`, and transaction attempts. Store chain ID and normalized wallet address on every claim; keep timestamps in UTC. Claims have explicit states such as `reserved`, `submitted`, `confirmed`, and `failed`, with a distinct state for an uncertain broadcast that needs reconciliation.
- Add a unique active-claim rule for `(chain_id, wallet)` and a per-wallet lock or row lock during reservation. In one short PostgreSQL transaction: consume the challenge once, check the most recent confirmed claim, reject if its 24-hour cooldown is active, and reserve one claim. Keep RPC calls outside this database transaction.
- Define cooldown to begin when a payout reaches the configured confirmation depth. Persist `confirmed_at` and calculate `nextEligibleAt = confirmed_at + 24 hours`. Keep a pending/uncertain claim blocked until the chain result is known.
- Store transaction hash, sender nonce, and broadcast attempt history so worker restart or RPC timeouts cannot cause a second payout with a fresh nonce.

**Gate:** database tests prove concurrent requests for the same wallet and chain reserve at most one claim; failed/unknown claims follow explicit recovery rules.

## Phase 3.3 — Wallet challenge and ownership verification

- Build `POST /auth/challenge` using the standard Sign-In with Ethereum (SIWE, ERC-4361) message format. Bind it to wallet address, chain ID, domain/URI, random nonce, issue time, and short expiry. Persist only what is needed to verify it and reject replay.
- In `POST /claims`, parse and verify the signed message and require the recovered signer to equal the requested recipient. Check chain/domain/nonce/expiry against the stored challenge; reject altered or reused messages. The contract rejects deployed smart-contract recipients, so the first version supports EOA-style signatures only. Query recipient code on the selected chain as an early check; the contract repeats the check at payout time.
- Define a stable idempotency policy: an HTTP retry of the same accepted claim returns its existing claim ID without creating another transfer.

**Gate:** tests cover valid signature, wrong signer/chain/domain, expired challenge, replay, malformed signature, and deployed-contract recipient.

## Phase 3.4 — Claim endpoint and off-chain eligibility

- Implement `POST /claims` with server-selected payout amount and the atomic reservation from Phase 3.2. Return `202 Accepted` promptly with a claim ID; `GET /claims/{claim_id}` provides progress and a transaction link later.
- Check pending claim, 24-hour wallet cooldown, enabled chain, and recipient eligibility. Read `paused`, `maxPayout`, `spendingLimit`, `spentInCurrentPeriod`, and faucet balance for an early user-facing denial. Treat these reads as advisory because chain state can change before broadcast; the contract remains the final payout gate.
- Apply the PostgreSQL-backed sliding-window limiter to both challenge issuance and claim submission. It tracks client IP and IPv4 `/24` or IPv6 `/64` before signature verification; the claim wallet limit applies after signature verification. Forwarded client IP headers are trusted only from configured reverse proxies. Add a CAPTCHA verification interface later. Do not add Redis unless measurements justify it.

**Gate:** API tests cover success, cooldown with next eligible time, pending conflict, invalid inputs, CAPTCHA/rate denial, and chain outage without issuing a transaction.

## Phase 3.5 — Distributor transaction worker

- Implement one worker that claims reserved jobs from PostgreSQL, builds `dispense(recipient, amount)` using the deployment ABI, estimates gas, sets the configured fee mode, and signs with the distributor key held outside the repo. For Anvil, use a disposable development key imported into a local encrypted keystore; use the same signed-raw-transaction path as a custom chain. Verify the RPC chain ID and role again before sending.
- Coordinate the distributor's account nonce through the database so two jobs or worker instances cannot reuse it. Persist a deterministic signed transaction hash/raw transaction or equivalent recoverable attempt record **before** broadcasting. If RPC times out, look up or rebroadcast the same transaction; do not immediately make a new one.
- Poll receipts and check success, contract address, and `Dispensed(distributor, recipient, amount)` log. Wait for configured confirmations, then mark the claim confirmed and start the 24-hour cooldown. Handle revert, dropped/replaced transaction, reorg, and process restart through reconciliation. Release a reservation only after establishing that no payout succeeded.
- Run the worker as a separate command/process; the API should not depend on an in-memory task queue.

**Gate:** one accepted claim produces one confirmed payout; a timeout or worker restart produces no duplicate payout.

## Phase 3.6 — End-to-end Anvil verification and handoff

- Start PostgreSQL, Anvil, API, and worker. Point the backend at the current Anvil contract address and distributor signer. Make a signed claim from an EOA and verify API status, database record, receipt/event, faucet balance, and recipient balance.
- Exercise two simultaneous requests for one wallet, 24-hour cooldown, paused contract, exhausted period budget, empty faucet, wrong RPC chain ID, RPC outage, reverted transaction, and worker restart during an uncertain broadcast.
- Record startup commands, migration command, configuration keys, and recovery steps in `backend/README.md`. Later, replace the Anvil chain configuration with the custom chain's deployment JSON and repeat the same gate before enabling it.

**Gate for Step 3:** the five APIs work on Anvil; one wallet gets at most one confirmed payout in 24 hours; concurrent/retried requests cannot create duplicate transfers; the worker recovers after restart. The custom-chain rehearsal follows as a separate deployment step.

## Implementation order and FastAPI practices

Follow **configuration → schema → challenge → claim reservation → worker → integration**. Keep HTTP schemas separate from database models; return typed response models and consistent error codes. Use sync `def` route handlers if using synchronous Psycopg/web3.py, or choose async clients consistently; avoid blocking the event loop with sync RPC calls inside `async def`. Use dependency overrides with FastAPI `TestClient` for isolated route tests, real PostgreSQL for concurrency tests, and Anvil for chain integration. Keep transactions short and do not call RPC while holding a wallet database lock.

## References

- [FastAPI application structure](https://fastapi.tiangolo.com/tutorial/bigger-applications/), [lifespan](https://fastapi.tiangolo.com/advanced/events/), [settings](https://fastapi.tiangolo.com/advanced/settings/), and [testing dependencies](https://fastapi.tiangolo.com/advanced/testing-dependencies/).
- [ERC-4361: Sign-In with Ethereum](https://eips.ethereum.org/EIPS/eip-4361).
- [Psycopg transaction contexts](https://www.psycopg.org/psycopg3/docs/basic/transactions.html) and [PostgreSQL row locking](https://www.postgresql.org/docs/current/sql-select.html).
- [web3.py transaction signing and sending](https://web3py.readthedocs.io/en/stable/transactions.html).
