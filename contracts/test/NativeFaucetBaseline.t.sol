// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {NativeFaucet} from "../src/NativeFaucet.sol";

contract NativeFaucetBaselineTest is Test {
    function testDeploymentAndPausableDefault() public {
        NativeFaucet faucet = new NativeFaucet();
        assertFalse(faucet.paused());
    }
}
