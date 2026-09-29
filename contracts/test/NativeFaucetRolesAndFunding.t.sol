// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {IAccessControl} from "@openzeppelin/contracts/access/IAccessControl.sol";
import {NativeFaucet} from "../src/NativeFaucet.sol";

contract NativeFaucetRolesAndFundingTest is Test {
    address internal constant ADMIN = address(0xA11CE);
    address internal constant DISTRIBUTOR = address(0xD157);
    address internal constant TREASURY = address(0x7EA5);
    address internal constant RECIPIENT = address(0xB0B);
    uint256 internal constant INITIAL_MAX_PAYOUT = 5 ether;

    event Funded(address indexed sender, uint256 amount);

    function testDeploymentStateAndRoles() public {
        NativeFaucet faucet = new NativeFaucet(ADMIN, DISTRIBUTOR, INITIAL_MAX_PAYOUT);

        assertTrue(faucet.hasRole(faucet.DEFAULT_ADMIN_ROLE(), ADMIN));
        assertTrue(faucet.hasRole(faucet.DISTRIBUTOR_ROLE(), DISTRIBUTOR));
        assertFalse(faucet.hasRole(faucet.DISTRIBUTOR_ROLE(), ADMIN));
        assertFalse(faucet.hasRole(faucet.DEFAULT_ADMIN_ROLE(), DISTRIBUTOR));
        assertEq(faucet.maxPayout(), INITIAL_MAX_PAYOUT);
        assertEq(faucet.COOLDOWN(), 24 hours);
        assertEq(faucet.nextEligibleAt(RECIPIENT), 0);
        assertFalse(faucet.paused());
    }

    function testConstructorRejectsInvalidInputs() public {
        vm.expectRevert(NativeFaucet.InvalidAdmin.selector);
        new NativeFaucet(address(0), DISTRIBUTOR, INITIAL_MAX_PAYOUT);

        vm.expectRevert(NativeFaucet.InvalidDistributor.selector);
        new NativeFaucet(ADMIN, address(0), INITIAL_MAX_PAYOUT);

        vm.expectRevert(NativeFaucet.SameAdminAndDistributor.selector);
        new NativeFaucet(ADMIN, ADMIN, INITIAL_MAX_PAYOUT);

        vm.expectRevert(NativeFaucet.InvalidMaxPayout.selector);
        new NativeFaucet(ADMIN, DISTRIBUTOR, 0);
    }

    function testOnlyAdminCanManageDistributorRole() public {
        NativeFaucet faucet = new NativeFaucet(ADMIN, DISTRIBUTOR, INITIAL_MAX_PAYOUT);
        bytes32 distributorRole = faucet.DISTRIBUTOR_ROLE();
        bytes32 adminRole = faucet.DEFAULT_ADMIN_ROLE();

        vm.prank(TREASURY);
        vm.expectRevert(
            abi.encodeWithSelector(IAccessControl.AccessControlUnauthorizedAccount.selector, TREASURY, adminRole)
        );
        faucet.grantRole(distributorRole, RECIPIENT);

        vm.prank(TREASURY);
        vm.expectRevert(
            abi.encodeWithSelector(IAccessControl.AccessControlUnauthorizedAccount.selector, TREASURY, adminRole)
        );
        faucet.revokeRole(distributorRole, DISTRIBUTOR);

        vm.startPrank(ADMIN);
        faucet.grantRole(distributorRole, RECIPIENT);
        assertTrue(faucet.hasRole(distributorRole, RECIPIENT));
        faucet.revokeRole(distributorRole, DISTRIBUTOR);
        assertFalse(faucet.hasRole(distributorRole, DISTRIBUTOR));
        vm.stopPrank();
    }

    function testReceivesNativeFundingAndEmitsEvent() public {
        NativeFaucet faucet = new NativeFaucet(ADMIN, DISTRIBUTOR, INITIAL_MAX_PAYOUT);
        uint256 fundingAmount = 20 ether;
        vm.deal(TREASURY, fundingAmount);

        vm.expectEmit(true, false, false, true, address(faucet));
        emit Funded(TREASURY, fundingAmount);
        vm.prank(TREASURY);
        (bool ok,) = address(faucet).call{value: fundingAmount}("");

        assertTrue(ok);
        assertEq(address(faucet).balance, fundingAmount);
        assertEq(TREASURY.balance, 0);
    }
}
