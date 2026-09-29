# NativeFaucet contract

## Phase 2.1: agreed behavior

`NativeFaucet` holds a chain's native currency and pays recipients through an authorized distributor. The recipient does not call the contract to claim. This is a non-upgradeable contract deployed separately on each EVM chain. The decisions below supersede the global spending limit and off-chain cooldown in `../docs/native_faucet_contract_spec.docx`.

### Roles and deployment

- Constructor: `NativeFaucet(address admin, address distributor, uint256 initialMaxPayout)`. The two addresses must be nonzero and different; `initialMaxPayout` must be greater than zero.
- `DEFAULT_ADMIN_ROLE` belongs to `admin`; `DISTRIBUTOR_ROLE` belongs to `distributor`. Only a distributor can call `dispense`. The admin can grant or revoke distributor permission through `AccessControl` and can perform the admin actions below.
- Treasury funds the contract by sending native currency. Treasury and validator have no contract role. Keep admin, distributor, treasury, and validator keys operationally separate.
- All amounts are integer units of the chain's smallest native-currency denomination. The deployment chooses a suitable maximum payout for that chain; the contract has no hard-coded coin amount.

### Claim and cooldown

- `dispense(address recipient, uint256 amount)` requires the distributor role and an unpaused contract. It rejects a zero recipient, zero amount, `amount > maxPayout`, `amount > address(this).balance`, and a recipient whose cooldown has not expired.
- `COOLDOWN` is a fixed `24 hours`. `nextEligibleAt[recipient]` starts at zero, so a recipient with no successful payout is eligible. A payout is allowed when `block.timestamp >= nextEligibleAt[recipient]`, including at the exact expiry timestamp.
- On a successful payout, set `nextEligibleAt[recipient] = block.timestamp + COOLDOWN`. Update it before calling the recipient and guard against reentrancy. If the native transfer fails, revert the whole transaction: funds do not move and the recipient's eligibility is unchanged.
- The cooldown is per recipient address, per deployed contract. Other addresses are unaffected. Deploying a replacement contract starts fresh on-chain cooldown state; the backend must use its claim history during migration if the old cooldowns must carry over.
- `receive()` accepts funding even while payouts are paused. Funding and payouts emit events. There is no faucet-wide spending budget or period accounting. The contract's balance and `maxPayout` bound payouts.

### Admin actions

- `setMaxPayout(uint256 amount)` is admin-only and requires `amount > 0`. It takes effect immediately and emits the old and new values.
- `pause()` and `unpause()` are admin-only. Pause blocks `dispense` but allows funding and admin withdrawal.
- `withdraw(address recipient, uint256 amount)` is admin-only. It requires a nonzero recipient, nonzero amount, and enough contract balance. It remains callable while paused and does not change any wallet's cooldown. A failed transfer reverts; a successful withdrawal emits an event.
- There is no `setSpendingLimit`, period setter, or global spending counter.

### Public interface for later integration

The implementation will expose `maxPayout()`, `COOLDOWN()`, `nextEligibleAt(address)`, and inherited role/pause getters. The backend may read `nextEligibleAt` to report eligibility, but the contract makes the final payout decision. Wallet signature verification, CAPTCHA, IP limits, and durable claim records belong in the backend. A user opening a block explorer cannot bypass the distributor role by calling `dispense` directly.

### Testable boundaries

- A second payout to the same recipient at `nextEligibleAt - 1` reverts; one at `nextEligibleAt` succeeds.
- A payout to another recipient during the first recipient's cooldown succeeds if all other checks pass.
- A reverted transfer does not start cooldown. Unauthorized calls never transfer funds.
- A paused faucet can receive funds and allow admin withdrawal, but cannot dispense.

## Phase 2.2: toolchain baseline

Foundry uses Solidity 0.8.24 with the Paris EVM target and optimizer (200 runs), as configured in `foundry.toml`. Paris is a conservative bytecode target for local development; confirm the custom chain's supported EVM revision before deploying there. The project pins OpenZeppelin Contracts v5.7.0 and `forge-std` v1.16.2 through `foundry.lock` and Git submodules. The contract imports `AccessControl`, `Pausable`, and `ReentrancyGuard`. Phase 2.3 adds roles and funding; payout behavior comes later.

From `contracts/`, run:

```sh
forge build
forge test
```

Both commands pass at the end of Phase 2.2. No environment file or signing key is needed for this local baseline.

## Phase 2.3: roles, state, and funding

The constructor checks distinct, nonzero admin and distributor addresses and a nonzero initial maximum payout. It grants `DEFAULT_ADMIN_ROLE` to the admin and `DISTRIBUTOR_ROLE` to the distributor. `COOLDOWN` is 24 hours, and `nextEligibleAt(recipient)` is zero until a payout is implemented in Phase 2.4. Anyone can fund the contract through `receive()`, which emits `Funded(sender, amount)`.

The tests in `test/NativeFaucetRolesAndFunding.t.sol` cover constructor rejection, initial state, role management permissions, and funding.
