# NativeFaucet contract

`NativeFaucet` holds a chain's native currency and pays recipients through an authorized distributor. It is non-upgradeable and deployed separately on each EVM chain. Recipients do not call the contract to claim.

## Contract rules

- Deploy with `NativeFaucet(admin, distributor, initialMaxPayout, initialSpendingLimit, periodDurationSeconds)`. Addresses must be distinct and nonzero; the three numeric values must be positive. Amounts use the native currency's smallest unit.
- The admin has `DEFAULT_ADMIN_ROLE`; the backend signer has `DISTRIBUTOR_ROLE`. Only a distributor can call `dispense(recipient, amount)`. The admin can grant and revoke distributor access. OpenZeppelin `AccessControlDefaultAdminRules` restricts the admin role to one account and requires a two-step transfer with a one-day delay.
- Each payout requires an unpaused contract, a nonzero recipient with no deployed code, a positive amount no greater than `maxPayout`, sufficient contract balance, and enough remaining room under `spendingLimit` for the current period.
- Periods are fixed windows aligned to Unix time zero: `currentPeriodStart() = block.timestamp - block.timestamp % periodDuration`. For a one-day duration, boundaries are midnight UTC. `periodDuration` cannot change after deployment. `periodStart` and `periodSpent` are updated on a successful payout; `spentInCurrentPeriod()` returns zero if the stored accounting is from an older period.
- Accounting is updated before the native transfer. A failed transfer reverts both the payment and accounting. `dispense` and `withdraw` have a reentrancy guard.
- `setMaxPayout` and `setSpendingLimit` are admin-only, require a positive value, and emit update events. Lowering `spendingLimit` below spending already recorded blocks further payouts until the next period or a higher limit is set; it does not erase past spending.
- `pause` and `unpause` are admin-only. Anyone can fund through `receive()` while paused. The admin can `withdraw(recipient, amount)` while paused. Funding, payouts, and withdrawals emit events. Withdrawals do not count as faucet spending because the period limit applies to `dispense` payouts.

## Backend boundary

The contract rejects deployed contract recipients at payout time using `recipient.code.length != 0`. This excludes smart contract wallets. An address with no code is not necessarily an EOA: it could be under construction or awaiting deployment. The backend must recover the signer of a fresh wallet challenge and require it to match the recipient address, then check for deployed code on the selected chain.

The 24-hour per-wallet cooldown belongs in FastAPI/PostgreSQL. The backend must serialize claims per chain and wallet, block pending or too-soon claims, and reconcile uncertain broadcasts before releasing a reservation. The contract has no recipient cooldown mapping; an authorized distributor can pay a wallet again within the period if the global limit permits it. Protect the distributor key. The backend is scheduled for Step 3 and has not been implemented yet.

## Toolchain and verification

Foundry uses Solidity 0.8.24, the Paris EVM target, and optimizer with 200 runs. OpenZeppelin Contracts v5.7.0 supplies delayed admin handoff, access control, pause, and reentrancy protection. Confirm the custom chain supports the selected EVM revision before deployment.

From `contracts/`, run `forge build` and `forge test`. The tests cover roles, funding, the per-payout and global caps, exact period rollover, a repeated recipient, deployed contract rejection, admin changes, pausing, withdrawal, and payout amount fuzzing. Phase 2.7 will add a local Anvil deployment script.

## Security and deployment assumptions

- Hold the admin role in a well-controlled multisignature account. The admin can withdraw the entire balance or raise the payout limits immediately; the delayed transfer protects admin handoff, not a compromised current admin. Keep the distributor signing key separate and fund the faucet with only the amount intended for exposure.
- The global limit applies to `dispense` within fixed periods. A payout immediately before a period boundary and another immediately after it can use two periods' allowances close together. The custom chain's block timestamps determine the boundary.
- The recipient code check is a filter for deployed contracts, not proof of EOA ownership. It also excludes code-bearing EOA delegation schemes on chains that support them. Backend signature verification and chain-specific code checks remain necessary before enabling public claims.
- The 24-hour wallet cooldown and duplicate-claim handling are not yet implemented. This contract alone is not a complete public faucet.
