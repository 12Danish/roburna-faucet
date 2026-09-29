# Step 2: NativeFaucet contract implementation

This plan starts from the existing Foundry scaffold in `contracts/`. It covers the non-upgradeable native-currency contract and a local Anvil smoke test. The backend, frontend, and custom-chain rehearsal are later steps. The written contract specification in `native_faucet_contract_spec.docx` is the baseline. The project owner has changed two rules: the contract enforces one successful payout per recipient address per rolling 24 hours, and it has no faucet-wide spending budget or period accounting. Eligibility, CAPTCHA, and claim history remain off-chain.

## Phase 2.1 — Freeze contract behavior

**Decide and document:**

- Enforce a rolling 24-hour cooldown for each recipient address on-chain. Store its next eligible timestamp. The first payout is allowed; another is rejected before that timestamp and allowed at the exact timestamp. A reverted payout must not start a cooldown.
- Keep only a maximum payout per transaction and the available contract balance as payout bounds. There is no global spending budget, period duration, or period rollover.
- Constructor inputs: distinct nonzero admin and initial distributor addresses, and a nonzero maximum payout.
- Units: all contract amounts use the chain's smallest native-currency unit; display conversion belongs outside Solidity.
- Admin policy: admin can grant and revoke the distributor role and change `maxPayout`.
- Pause policy: pause stops `dispense`, while admin withdrawal remains available for emergency migration.

**Deliverable:** a short contract interface and behavior note in `contracts/README.md`. **Gate:** the cooldown, role, and maximum-payout rules are unambiguous before coding.

## Phase 2.2 — Prepare dependencies and baseline

Pin a reviewed OpenZeppelin Contracts 5.x release in Foundry. Use its `AccessControl`, `Pausable`, and `ReentrancyGuard` modules. Keep `forge-std` for tests. Pin the Solidity compiler and use a conservative EVM target in `foundry.toml`; verify target compatibility with the custom chain before Step 5 deployment, and record those choices. Do not create environment files or put keys in source control.

**Deliverable:** reproducible dependency references and a compiling empty `NativeFaucet.sol`. **Gate:** `forge build` and a baseline `forge test` pass.

## Phase 2.3 — Roles, state, and funding

Implement constructor validation, admin and distributor roles, a fixed 24-hour recipient cooldown, recipient next-eligible timestamps, configurable maximum payout, getters, errors, and events. Add `receive()` for native funding and a funding event. Keep the validator, treasury, admin, and distributor as separate accounts; the validator has no contract role.

**Tests:** constructor rejects bad configuration; only the intended accounts have roles; an unauthorized caller cannot grant or revoke the distributor role; funding increases contract balance and emits the expected event. **Gate:** role and funding tests pass.

## Phase 2.4 — Distributor payout

Implement `dispense(recipient, amount)` for the distributor only. Reject a zero recipient, zero amount, amount above `maxPayout`, a recipient whose 24-hour cooldown has not expired, insufficient contract balance, or paused state. Update the recipient next-eligible timestamp before making the native transfer; use a reentrancy guard and revert on failed transfer. Emit a payout event containing recipient, amount, and distributor.

**Tests:** successful payout changes balances and recipient eligibility exactly; a repeat payout one second before the 24-hour expiry reverts and one at the exact expiry succeeds; another recipient remains eligible; unauthorized, invalid, paused, over-limit, and empty-balance calls revert; a failed recipient transfer leaves cooldown unchanged. **Gate:** all payout behavior tests pass.

## Phase 2.5 — Admin controls

Add `setMaxPayout`, `pause`, `unpause`, and `withdraw(recipient, amount)`. Emit events for limit changes and withdrawals. Validate withdrawal recipient and amount. Role grant/revoke comes from `AccessControl` and must be tested with the chosen admin policy.

**Tests:** only admin can change controls or withdraw; a changed maximum payout takes effect immediately; paused payouts fail while permitted admin withdrawal works; a revoked distributor immediately loses payout permission. **Gate:** admin tests pass.

## Phase 2.6 — Adversarial and property tests

Add focused tests for a recipient contract that rejects payment and one that attempts reentry. Fuzz payout amounts and timestamps around the 24-hour cooldown boundary. Check that a recipient cannot be paid twice within 24 hours and a reverted transaction does not start cooldown. Review event coverage and revert reasons. Run `forge test` and inspect the gas report for `dispense`.

**Deliverable:** the contract test suite and a brief review note of assumptions or remaining risks. **Gate:** all tests pass, including adversarial cases, and the contract is ready for local deployment.

## Phase 2.7 — Local deployment and handoff

Write a Foundry deployment script that takes the admin and distributor addresses and maximum payout as explicit inputs. Deploy to Anvil with distinct accounts, fund the contract from treasury, fund the distributor for gas, execute one `dispense`, and verify the receipt, event, recipient next-eligible timestamp, contract balance, and recipient balance. Export the ABI and local deployment metadata for later backend integration. Never commit private keys or Anvil mnemonic files.

**Gate for Step 2:** the same script and instructions reproduce a funded local deployment and one authorized payout; `forge test` passes. The custom test chain is exercised in Step 5 after the backend and frontend exist.

## Build sequence

Complete each phase before the next: **behavior → dependencies → roles/funding → payout → admin controls → adversarial tests → Anvil deployment**. Every phase should leave the contract compiling and tests passing.

## References

- Project specification: `native_faucet_contract_spec.docx` in this directory.
- [Foundry documentation](https://getfoundry.sh/) for build, test, scripts, and Anvil.
- [OpenZeppelin role-based access control](https://docs.openzeppelin.com/contracts/5.x/access-control).
