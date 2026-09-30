// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {NativeFaucet} from "../src/NativeFaucet.sol";

/// @notice Deploys the same NativeFaucet bytecode on any compatible EVM chain.
/// @dev scripts/deploy.py loads contracts/.env and passes FAUCET_* variables to Forge.
contract DeployNativeFaucet is Script {
    error ChainIdMismatch(uint256 expected, uint256 actual);

    function run() external returns (NativeFaucet faucet) {
        uint256 expectedChainId = vm.envUint("FAUCET_EXPECTED_CHAIN_ID");
        if (block.chainid != expectedChainId) revert ChainIdMismatch(expectedChainId, block.chainid);

        address admin = vm.envAddress("FAUCET_ADMIN");
        address distributor = vm.envAddress("FAUCET_DISTRIBUTOR");
        uint256 maxPayout = vm.envUint("FAUCET_MAX_PAYOUT_WEI");
        uint256 spendingLimit = vm.envUint("FAUCET_SPENDING_LIMIT_WEI");
        uint256 periodSeconds = vm.envUint("FAUCET_PERIOD_SECONDS");

        vm.startBroadcast();
        faucet = new NativeFaucet(admin, distributor, maxPayout, spendingLimit, periodSeconds);
        vm.stopBroadcast();

        console2.log("chainId", block.chainid);
        console2.log("faucet", address(faucet));
        console2.log("admin", admin);
        console2.log("distributor", distributor);
    }
}
