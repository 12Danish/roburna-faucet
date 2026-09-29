// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/// @notice Baseline contract for checking the pinned dependencies and toolchain.
/// @dev Faucet state and functions are added in phases 2.3 through 2.5.
contract NativeFaucet is AccessControl, Pausable, ReentrancyGuard {}
