# NativeFaucet contract

[Project overview](../README.md) · [Original contract spec sheet](../docs/native_faucet_contract_spec.docx) · [Deployment runbook](../docs/deployment_runbook.md)

[`NativeFaucet.sol`](src/NativeFaucet.sol) is a non-upgradeable contract that holds an EVM chain's **native currency** and transfers it when an authorized distributor calls `dispense`. It does not mint tokens or implement ERC-20. The recipient signs an off-chain request through the [backend](../backend/README.md); the recipient does not call `dispense` or pay gas.

## Contract project files

| Path | Purpose |
| --- | --- |
| [`src/NativeFaucet.sol`](src/NativeFaucet.sol) | Contract implementation and on-chain rules. |
| [`test/`](test/) | Foundry tests for payouts, roles, funding, admin actions, and failure paths. |
| [`script/DeployNativeFaucet.s.sol`](script/DeployNativeFaucet.s.sol) | Foundry deployment script. |
| [`scripts/deploy.py`](scripts/deploy.py), [`scripts/export_deployment.py`](scripts/export_deployment.py) | Environment-aware deployment launcher and public ABI/deployment exporter. |
| [`foundry.toml`](foundry.toml), [`.env.example`](.env.example) | Compiler/EVM settings and local deployment configuration template. |

Generated `out/`, `cache/`, `broadcast/`, and local `deployments/` data belong to the build/deployment workflow; the live `.env` is ignored by Git.

## Roles and funds

| Actor | Responsibility |
| --- | --- |
| Admin | Owns `DEFAULT_ADMIN_ROLE`; manages the distributor role, limits, pause state, and withdrawals. Admin transfer is a two-step OpenZeppelin handoff with a one-day delay. |
| Distributor | Owns `DISTRIBUTOR_ROLE`; calls `dispense(recipient, amount)` and pays gas. The backend worker holds this signer. |
| Treasury | External wallet that refills the faucet contract. It has no special contract role. |
| Recipient | Receives native currency. The current contract rejects addresses with deployed code. |

Anyone may send native currency to `receive()`. This lets a treasury or another wallet refill the contract without a privileged funding transaction. Only the distributor can make faucet payouts. Keep the admin, distributor, and treasury keys separate.

## Constructor and state

Deploy with `NativeFaucet(admin, distributor, initialMaxPayout, initialSpendingLimit, periodDurationSeconds)`. The admin and distributor must be different nonzero addresses; the three numeric values must be positive. Amounts are integers in the chain's smallest native unit, commonly wei.

| State | Meaning |
| --- | --- |
| `periodDuration` | Immutable number of seconds in one spending period. |
| `maxPayout` | Maximum native currency in one `dispense` call; admin can change it. |
| `spendingLimit` | Maximum total `dispense` value in a fixed period; admin can change it. |
| `periodStart`, `periodSpent` | Accounting for the current stored period. `spentInCurrentPeriod()` reports zero after the period rolls over until another payout occurs. |
| pause state and roles | OpenZeppelin `Pausable` and `AccessControlDefaultAdminRules` control payouts and permissions. |

Periods are aligned to Unix time zero: `currentPeriodStart() = block.timestamp - block.timestamp % periodDuration`. With `86400` seconds, periods begin at midnight UTC. Spending does not roll over into the next period. A payout near a boundary can be followed by another payout in the next period.

## Functions and events

| Function | Caller | Contract rule |
| --- | --- | --- |
| `dispense(recipient, amount)` | Distributor | Requires unpaused state, nonzero recipient with no deployed code, `0 < amount <= maxPayout`, enough remaining period allowance, and enough contract balance. Updates accounting before the native transfer; a failed transfer reverts everything. |
| `setMaxPayout(amount)` | Admin | Sets a positive per-payout maximum; emits `MaxPayoutUpdated`. |
| `setSpendingLimit(amount)` | Admin | Sets a positive period budget; emits `SpendingLimitUpdated`. Lowering it below already-recorded spending blocks further payouts until the next period or a higher limit. |
| `pause()` / `unpause()` | Admin | Stops or resumes `dispense`. Funding and admin withdrawals still work while paused. |
| `withdraw(recipient, amount)` | Admin | Moves funds for recovery or migration; requires a nonzero recipient, positive amount, and sufficient balance. It does not consume the period payout budget. |
| `receive()` | Anyone | Accepts native currency; emits `Funded`. |
| `currentPeriodStart()` / `spentInCurrentPeriod()` | Anyone, read-only | Reports period accounting. |

Successful payouts emit `Dispensed`; withdrawals emit `Withdrawn`. The contract uses a reentrancy guard on `dispense` and `withdraw`. Public getters expose the limits, period state, and `DISTRIBUTOR_ROLE` constant.

## Contract and backend boundary

The contract enforces authorization and financial limits even if someone calls it directly through a block explorer. Its `recipient.code.length` check excludes deployed contracts but does not prove a wallet is controlled by the requester. The backend verifies a signed SIWE challenge and checks recipient code before reserving a payout. The contract checks code again when paying.

Wallet cooldown and claim history are **off-chain** in PostgreSQL. The contract has no per-recipient cooldown mapping: an authorized distributor can call `dispense` twice for the same recipient if on-chain limits permit it. The backend prevents ordinary users from doing that through its active-claim rule and cooldown. The contract's period spending cap and limited balance bound the funds available to a compromised distributor.

## Build, test, deploy

Foundry compiles Solidity `0.8.24` for the Paris EVM target with 200 optimizer runs. OpenZeppelin Contracts supplies access control, pause, and reentrancy protection. Confirm the target chain supports this EVM revision and its configured gas-fee mode.

From `contracts/`:

```bash
forge build
forge test
```

The tests in [`test/`](test/) cover roles, payout and period caps, exact rollover, recipient-code checks, pause, withdrawal, reentrancy, and payout amount fuzzing. Deployment uses [`script/DeployNativeFaucet.s.sol`](script/DeployNativeFaucet.s.sol), launched through [`scripts/deploy.py`](scripts/deploy.py) with ignored local `.env` settings. [`scripts/export_deployment.py`](scripts/export_deployment.py) exports the ABI and public deployment metadata for the backend. Follow the [deployment runbook](../docs/deployment_runbook.md) for Anvil and other EVM chains.

Logic changes require a new contract: pause the old faucet, deploy the new version, move remaining funds with an admin withdrawal, update backend deployment metadata and chain config, then test before enabling payouts.
