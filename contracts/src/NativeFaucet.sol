// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {
    AccessControlDefaultAdminRules
} from "@openzeppelin/contracts/access/extensions/AccessControlDefaultAdminRules.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/// @notice Holds native currency for distributor-authorized faucet payouts.
contract NativeFaucet is AccessControlDefaultAdminRules, Pausable, ReentrancyGuard {
    uint48 public constant ADMIN_TRANSFER_DELAY = 1 days;
    bytes32 public constant DISTRIBUTOR_ROLE = keccak256("DISTRIBUTOR_ROLE");

    uint256 public immutable periodDuration;
    uint256 public maxPayout;
    uint256 public spendingLimit;
    uint256 public periodStart;
    uint256 public periodSpent;

    error InvalidDistributor();
    error SameAdminAndDistributor();
    error InvalidMaxPayout();
    error InvalidSpendingLimit();
    error InvalidPeriodDuration();
    error InvalidRecipient();
    error ContractRecipient(address recipient);
    error InvalidAmount();
    error PayoutExceedsMaximum(uint256 amount, uint256 maximum);
    error SpendingLimitExceeded(uint256 requested, uint256 remaining);
    error InsufficientBalance(uint256 requested, uint256 available);
    error NativeTransferFailed(address recipient, uint256 amount);

    event Funded(address indexed sender, uint256 amount);
    event Dispensed(address indexed distributor, address indexed recipient, uint256 amount);
    event MaxPayoutUpdated(uint256 previousAmount, uint256 newAmount);
    event SpendingLimitUpdated(uint256 previousAmount, uint256 newAmount);
    event Withdrawn(address indexed recipient, uint256 amount);

    constructor(
        address admin,
        address distributor,
        uint256 initialMaxPayout,
        uint256 initialSpendingLimit,
        uint256 periodDurationSeconds
    ) AccessControlDefaultAdminRules(ADMIN_TRANSFER_DELAY, admin) {
        if (distributor == address(0)) revert InvalidDistributor();
        if (admin == distributor) revert SameAdminAndDistributor();
        if (initialMaxPayout == 0) revert InvalidMaxPayout();
        if (initialSpendingLimit == 0) revert InvalidSpendingLimit();
        if (periodDurationSeconds == 0) revert InvalidPeriodDuration();

        maxPayout = initialMaxPayout;
        spendingLimit = initialSpendingLimit;
        periodDuration = periodDurationSeconds;
        periodStart = block.timestamp - (block.timestamp % periodDurationSeconds);
        _grantRole(DISTRIBUTOR_ROLE, distributor);
    }

    receive() external payable {
        emit Funded(msg.sender, msg.value);
    }

    /// @notice Pays native currency after the backend has checked recipient eligibility.
    function dispense(address recipient, uint256 amount)
        external
        onlyRole(DISTRIBUTOR_ROLE)
        whenNotPaused
        nonReentrant
    {
        if (recipient == address(0)) revert InvalidRecipient();
        if (recipient.code.length != 0) revert ContractRecipient(recipient);
        if (amount == 0) revert InvalidAmount();
        if (amount > maxPayout) revert PayoutExceedsMaximum(amount, maxPayout);

        uint256 currentStart = currentPeriodStart();
        uint256 spent = currentStart == periodStart ? periodSpent : 0;
        uint256 remaining = spent < spendingLimit ? spendingLimit - spent : 0;
        if (amount > remaining) revert SpendingLimitExceeded(amount, remaining);

        uint256 balance = address(this).balance;
        if (amount > balance) revert InsufficientBalance(amount, balance);

        periodStart = currentStart;
        periodSpent = spent + amount;
        (bool sent,) = recipient.call{value: amount}("");
        if (!sent) revert NativeTransferFailed(recipient, amount);

        emit Dispensed(msg.sender, recipient, amount);
    }

    /// @notice Returns the start of the UTC-aligned period containing the current block.
    function currentPeriodStart() public view returns (uint256) {
        return block.timestamp - (block.timestamp % periodDuration);
    }

    /// @notice Returns zero when the stored accounting belongs to an earlier period.
    function spentInCurrentPeriod() external view returns (uint256) {
        return periodStart == currentPeriodStart() ? periodSpent : 0;
    }

    /// @notice Changes the maximum amount for each payout.
    function setMaxPayout(uint256 amount) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (amount == 0) revert InvalidMaxPayout();
        uint256 previousAmount = maxPayout;
        maxPayout = amount;
        emit MaxPayoutUpdated(previousAmount, amount);
    }

    /// @notice Changes the total payout allowance for each fixed period.
    function setSpendingLimit(uint256 amount) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (amount == 0) revert InvalidSpendingLimit();
        uint256 previousAmount = spendingLimit;
        spendingLimit = amount;
        emit SpendingLimitUpdated(previousAmount, amount);
    }

    /// @notice Stops distributor payouts while keeping funding and withdrawal available.
    function pause() external onlyRole(DEFAULT_ADMIN_ROLE) {
        _pause();
    }

    /// @notice Resumes distributor payouts.
    function unpause() external onlyRole(DEFAULT_ADMIN_ROLE) {
        _unpause();
    }

    /// @notice Moves native currency out of the faucet for admin recovery or migration.
    function withdraw(address recipient, uint256 amount) external onlyRole(DEFAULT_ADMIN_ROLE) nonReentrant {
        if (recipient == address(0)) revert InvalidRecipient();
        if (amount == 0) revert InvalidAmount();
        uint256 balance = address(this).balance;
        if (amount > balance) revert InsufficientBalance(amount, balance);

        (bool sent,) = recipient.call{value: amount}("");
        if (!sent) revert NativeTransferFailed(recipient, amount);
        emit Withdrawn(recipient, amount);
    }
}
