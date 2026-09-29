# Step 2: NativeFaucet contract implementation

This plan covers the non-upgradeable native-currency contract and a local Anvil smoke test. The written contract specification in `native_faucet_contract_spec.docx` is the baseline. Per-wallet eligibility and the 24-hour cooldown belong to the backend in Step 3; the contract enforces distributor authorization, a maximum per payout, and a global spending limit per fixed period.

## Phase 2.1 — Freeze contract behavior

- `dispense(recipient, amount)` is callable only by an authorized distributor. Recipients cannot claim directly through a block explorer.
- A payout must be positive, no greater than `maxPayout`, within available balance, and within `spendingLimit - periodSpent` for the current period.
- `periodDuration` is a positive deployment input. Periods align to Unix time zero. Spending resets on the first successful payout in a new period; the view getter reports zero before that payout. For a one-day period, boundaries are midnight UTC.
- Constructor inputs are distinct nonzero admin and distributor addresses, positive maximum payout, positive spending limit, and positive period duration. Contract amounts use the chain's smallest native-currency unit.
- The admin manages distributor roles, `maxPayout`, `spendingLimit`, pause/unpause, and withdrawal. Admin handoff uses a two-step transfer with a one-day delay. Pausing stops payouts while funding and admin withdrawal remain possible.
- The 24-hour wallet cooldown, signed wallet challenge, CAPTCHA, IP limits, and claim history are backend responsibilities. A no-code check in Solidity rejects deployed contracts but does not prove EOA ownership by itself. The contract intentionally has no recipient eligibility mapping.

**Deliverable:** the interface and behavior note in `contracts/README.md`.

## Phase 2.2 — Prepare dependencies and baseline

Pin Solidity, OpenZeppelin Contracts, and `forge-std` in Foundry. Use `AccessControlDefaultAdminRules`, `Pausable`, and `ReentrancyGuard`; confirm the chosen EVM target on the custom chain. Do not create environment files or commit keys.

**Gate:** `forge build` and baseline `forge test` pass.

## Phase 2.3 — Roles, state, and funding

Implement constructor validation, roles, `maxPayout`, `spendingLimit`, immutable `periodDuration`, period accounting state, public getters, events, and `receive()`. Keep admin, distributor, treasury, and validator as separate accounts; treasury and validator have no contract role.

**Tests:** invalid constructor inputs, role management, initial accounting state, and funding event.

## Phase 2.4 — Distributor payout

Implement distributor-only `dispense`. Check amount, recipient code, period allowance, available balance, and paused state. Update period accounting before transfer; revert on failed transfer and guard reentrancy. Emit a payout event.

**Tests:** balances and accounting on success; same recipient can receive again on-chain; deployed contract recipients are rejected; cap shared across recipients; exact limit and period boundary; invalid, unauthorized, paused, over-limit, and empty-balance calls revert; failed payment leaves accounting unchanged.

## Phase 2.5 — Admin controls

Add `setMaxPayout`, `setSpendingLimit`, `pause`, `unpause`, and `withdraw`. Emit configuration and withdrawal events. A lower spending limit does not reset the current period's spending.

**Tests:** admin-only controls, immediate limit changes, paused withdrawal, and distributor revocation.

## Phase 2.6 — Adversarial and property tests

Test deployed contract rejection, admin withdrawal transfer failure, and attempted reentrancy during admin withdrawal. Fuzz payout amounts. Verify period accounting and failed-transfer rollback. Run `forge test` and inspect payout gas usage.

**Gate:** contract test suite passes.

## Phase 2.7 — Local deployment and handoff

Write a Foundry script with explicit admin, distributor, maximum payout, spending limit, and period duration inputs. Deploy to Anvil, fund from treasury, fund the distributor for gas, execute a payout, and verify the event, accounting, and balances. Export ABI and deployment metadata for backend integration. Never commit private keys or Anvil mnemonic files.

**Gate:** reproducible local deployment and payout; `forge test` passes. Custom-chain rehearsal follows after backend and frontend integration.
