// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/// @notice Holds native currency for distributor-authorized faucet payouts.
/// @dev Payout and admin functions are added in phases 2.4 and 2.5.
contract NativeFaucet is AccessControl, Pausable, ReentrancyGuard {
    bytes32 public constant DISTRIBUTOR_ROLE = keccak256("DISTRIBUTOR_ROLE");
    uint256 public constant COOLDOWN = 24 hours;

    uint256 public maxPayout;
    mapping(address recipient => uint256 timestamp) public nextEligibleAt;

    error InvalidAdmin();
    error InvalidDistributor();
    error SameAdminAndDistributor();
    error InvalidMaxPayout();

    event Funded(address indexed sender, uint256 amount);

    constructor(address admin, address distributor, uint256 initialMaxPayout) {
        if (admin == address(0)) revert InvalidAdmin();
        if (distributor == address(0)) revert InvalidDistributor();
        if (admin == distributor) revert SameAdminAndDistributor();
        if (initialMaxPayout == 0) revert InvalidMaxPayout();

        maxPayout = initialMaxPayout;
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(DISTRIBUTOR_ROLE, distributor);
    }

    receive() external payable {
        emit Funded(msg.sender, msg.value);
    }
}
