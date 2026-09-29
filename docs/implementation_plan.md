# EVM native faucet implementation plan

## Goal and source of truth

Build a native-currency faucet in one repository, first against a custom EVM test chain and then deployable per chain without changing Solidity logic. The supplied `native_faucet_contract_spec.docx` is the original contract baseline; `faucet_flow.png` defines the user journey. The project owner has changed the contract boundary: enforce a rolling 24-hour per-recipient cooldown on-chain and omit the global spending budget and period accounting. Eligibility and claim history remain off-chain. The backend authorizes claims; the contract enforces distributor permissions, recipient cooldown, maximum payout per transaction, and available balance.

The faucet pays **native currency**, not an ERC-20 token. The distributor submits and pays gas for `dispense(recipient, amount)`; the recipient only signs a challenge and receives funds.

## Repository layout

```text
contracts/             Foundry project: Solidity, tests, deployment scripts
backend/               FastAPI service, migrations, tests
frontend/              Next.js App Router, TypeScript, Tailwind CSS
docs/                  Specs, deployment runbook, this plan
compose.yaml           Local PostgreSQL for integration testing
```

Commit the contract ABI and per-chain deployment metadata in a predictable generated location, such as `contracts/deployments/<chain-id>.json`; have backend startup load and validate these. Keep RPC URLs and signing keys in runtime environment or a secret manager, never in deployment files or the frontend. The frontend receives only public chain metadata and the API URL.

## Decisions to settle in step 1

1. Enforce the rolling 24-hour per-recipient cooldown in the contract. Omit contract-wide spending budgets and period rollover; the treasury controls total exposure through the amount it funds.
2. Define one payout amount per chain in backend config; read the 24-hour cooldown from the contract and set on-chain `maxPayout` at or above that amount. Native units are stored as integer wei; display decimals only in the UI.
3. Define confirmation depth for each chain and whether a pending claim blocks another claim. Recommend blocking until success or a reconciled failure. The contract starts the recipient cooldown when a payout succeeds on-chain; backend confirmation is a separate status.
4. Decide whether a chain can be enabled without CAPTCHA in a private development environment. Public endpoints need CAPTCHA and IP rate limits before real testnet launch.
5. Confirm RPC capabilities on the custom chain: `eth_chainId`, transaction submission, receipts, gas estimation, and either EIP-1559 fees or legacy gas price. Do not assume every EVM chain implements the same fee model or finality time.

## Step 1 — Project skeleton and configuration

Create the three subprojects, root README, documented configuration keys, and local PostgreSQL compose configuration. The project owner will create environment files. Define a chain configuration schema containing chain ID, RPC URL, contract address, explorer URL, currency symbol/decimals, payout amount, confirmation depth, enabled flag, and fee mode. In this step, validate configuration shape and required fields. Once a contract is deployed in step 2, backend startup must verify RPC `eth_chainId`, deployed contract code, and role assignments before enabling payouts. Keep admin, treasury, distributor, and validator keys separate.

**Gate:** the repository layout and local PostgreSQL service are reproducible; configuration parsing rejects missing or malformed values. Live chain and contract checks become a gate after deployment.

## Step 2 — Contract first

Follow the finer checkpoints in [`step2_contract_plan.md`](step2_contract_plan.md).

Use Foundry and a pinned OpenZeppelin Contracts release for access control, pause, and reentrancy protection. Implement non-upgradeable `NativeFaucet.sol` with admin and distributor roles; `dispense(address,uint256)`; `setMaxPayout`; `pause/unpause`; `withdraw`; and `receive`. Reject a zero recipient and zero or over-limit payout. Check the recipient cooldown and contract balance. Update recipient eligibility before the native transfer, revert if it fails, and emit funding, payout, configuration, and withdrawal events. Decide explicitly whether administrative withdrawals remain possible while paused; recommend yes for emergency migration.

Deploy with separate admin and distributor addresses. A treasury wallet funds the contract after deployment. Export ABI and deployment metadata for the backend.

**Tests:** authorized payout and exact balance change; unauthorized calls; invalid recipient/amount; per-payout cap; recipient cooldown before and at expiry; insufficient balance; paused behavior; recipient contract that reverts or attempts reentry; admin updates; withdraw; funding events. Add invariant/fuzz checks that a recipient cannot be paid twice within 24 hours and failed transfers do not start cooldown.

**Gate:** `forge test` passes and a local Anvil deployment can be funded and dispensed from with distinct keys.

## Step 3 — Minimal FastAPI claim service

Use FastAPI with `web3.py` and PostgreSQL for durable challenges, claims, transactions, audit records, and initial rate limits. Start without Redis. Keep a small API:

```text
GET  /chains                 public enabled-chain metadata
POST /auth/challenge         short-lived nonce bound to wallet, chain ID, origin, expiry
POST /claims                 signed challenge + CAPTCHA token; returns claim ID/status
GET  /claims/{id}            status and transaction hash when available
GET  /health                 service and dependency health
```

Verify the signature server-side, consume each challenge once, check requested chain, CAPTCHA, IP limit, the contract's recipient next-eligible timestamp, and any eligibility rules. Set payout amount on the server; never accept an amount from the browser. Use a PostgreSQL transaction and a unique pending-claim constraint per chain and wallet so retries or multiple API workers cannot double submit. Reserve a claim in PostgreSQL before broadcast. Submit `dispense` using the distributor key and chain-specific fee mode; persist the transaction hash and nonce. Reconcile receipts in a background worker or startup-safe polling job, handling replacement, revert, timeout, and restart without duplicating a payout. Return `pending` first; mark `confirmed` after the configured confirmation depth. Release or retry failed reservations only after checking chain state.

**Tests:** invalid/expired/replayed signatures; wrong chain; CAPTCHA failure; rate/cooldown denial; concurrent claims for one wallet; RPC timeout after broadcast; transaction revert; worker restart; receipt confirmation; chain ID mismatch. Use FastAPI `TestClient` for API tests and a local EVM for transaction tests.

**Gate:** one signed request yields one confirmed transaction; repeated and concurrent requests yield no extra transfer.

Add Redis only if measured request volume or database contention makes PostgreSQL-backed rate limits or short-lived challenge storage costly. PostgreSQL remains the durable authority for claim uniqueness and transaction state even if Redis is added.

## Step 4 — Minimal Next.js frontend

Create one responsive page with Tailwind: chain selector, connect wallet, visible payout/cooldown, CAPTCHA, request button, claim status, transaction link, and clear error messages. Use a small EVM wallet client; ask the wallet to sign only the backend challenge. Check or prompt for the selected chain before signing. Poll the claim status endpoint until confirmed or failed. Show the native currency symbol from chain metadata. Keep the distributor key and RPC credentials off the browser.

**Tests:** manual wallet flow and one browser test covering connect → sign → submit → confirmed/denied UI states.

**Gate:** a user can complete a claim on Anvil from a browser without paying gas.

## Step 5 — Custom test chain rehearsal

1. Confirm the custom chain's chain ID, fee model, native decimals, RPC methods, block timing, and explorer URL.
2. Deploy from `contracts/` with explicit chain ID and separate admin/distributor/treasury wallets. Record address, ABI version, deployment transaction, and configuration.
3. Set the maximum payout, fund the faucet from treasury, fund the distributor with gas, and keep the API disabled until startup checks pass.
4. Run contract smoke checks directly: funded balance, role assignments, pause/unpause, and one manual `dispense` to a test recipient.
5. Start PostgreSQL, backend, and frontend. Run one full signed claim; verify API status, receipt, event, recipient balance, and database record.
6. Exercise duplicate submission, two simultaneous requests, cooldown, maximum-payout rejection, empty faucet, RPC outage, reverted transaction, restart during pending state, and pause. Record expected recovery behavior for each.

**Gate:** all flows are reproducible from a runbook; API, database, and chain agree on each claim's final state.

## Step 6 — Make additional EVM chains a configuration/deployment operation

Deploy the same reviewed contract bytecode to each new chain, then add its chain config and deployment metadata. Validate RPC chain ID, contract code, roles, balance, fee mode, and a small end-to-end claim before enabling that chain in `/chains`. If a chain's EVM or RPC behavior differs, adapt its backend adapter/config and test it explicitly; do not assume bytecode portability alone guarantees operation. For contract logic changes, follow the spec's migration path: pause V1, deploy V2, move funds through an admin withdrawal, update backend address, test, then enable V2.

## Suggested build order

Complete each gate before proceeding: **skeleton/config → contract/tests → backend/transaction recovery → frontend → custom-chain rehearsal → second-chain rehearsal**. The second-chain rehearsal is the proof that deployment needs configuration and funding, not a Solidity rewrite.

## References

- Project sources: `native_faucet_contract_spec.docx` and `faucet_flow.png` in this directory.
- [Foundry documentation](https://www.getfoundry.sh/) for Forge and Anvil.
- [OpenZeppelin access control documentation](https://docs.openzeppelin.com/contracts/5.x/api/access).
- [FastAPI testing documentation](https://fastapi.tiangolo.com/tutorial/testing/).
- [web3.py transaction documentation](https://web3py.readthedocs.io/en/latest/transactions.html).
- [Next.js installation documentation](https://nextjs.org/docs/app/getting-started/installation/) for the TypeScript/App Router/Tailwind starter.
