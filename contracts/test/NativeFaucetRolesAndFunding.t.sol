// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {IAccessControl} from "@openzeppelin/contracts/access/IAccessControl.sol";
import {
    IAccessControlDefaultAdminRules
} from "@openzeppelin/contracts/access/extensions/IAccessControlDefaultAdminRules.sol";
import {NativeFaucet} from "../src/NativeFaucet.sol";

contract NativeFaucetRolesAndFundingTest is Test {
    address internal constant ADMIN = address(0xA11CE);
    address internal constant DISTRIBUTOR = address(0xD157);
    address internal constant TREASURY = address(0x7EA5);
    address internal constant RECIPIENT = address(0xB0B);
    uint256 internal constant INITIAL_MAX_PAYOUT = 5 ether;
    uint256 internal constant INITIAL_SPENDING_LIMIT = 10 ether;
    uint256 internal constant PERIOD_DURATION = 1 days;
    uint256 internal constant RECIPIENT_BALANCE_LIMIT = 500 ether;

    event Funded(address indexed sender, uint256 amount);

    function testDeploymentStateAndRoles() public {
        NativeFaucet faucet = new NativeFaucet(
            ADMIN, DISTRIBUTOR, INITIAL_MAX_PAYOUT, INITIAL_SPENDING_LIMIT, PERIOD_DURATION, RECIPIENT_BALANCE_LIMIT
        );

        assertTrue(faucet.hasRole(faucet.DEFAULT_ADMIN_ROLE(), ADMIN));
        assertEq(faucet.defaultAdmin(), ADMIN);
        assertEq(faucet.defaultAdminDelay(), 1 days);
        assertTrue(faucet.hasRole(faucet.DISTRIBUTOR_ROLE(), DISTRIBUTOR));
        assertFalse(faucet.hasRole(faucet.DISTRIBUTOR_ROLE(), ADMIN));
        assertFalse(faucet.hasRole(faucet.DEFAULT_ADMIN_ROLE(), DISTRIBUTOR));
        assertEq(faucet.maxPayout(), INITIAL_MAX_PAYOUT);
        assertEq(faucet.spendingLimit(), INITIAL_SPENDING_LIMIT);
        assertEq(faucet.periodDuration(), PERIOD_DURATION);
        assertEq(faucet.recipientBalanceLimit(), RECIPIENT_BALANCE_LIMIT);
        assertEq(faucet.periodStart(), faucet.currentPeriodStart());
        assertEq(faucet.periodSpent(), 0);
        assertFalse(faucet.paused());
    }

    function testConstructorRejectsInvalidInputs() public {
        vm.expectRevert(
            abi.encodeWithSelector(
                IAccessControlDefaultAdminRules.AccessControlInvalidDefaultAdmin.selector, address(0)
            )
        );
        new NativeFaucet(
            address(0),
            DISTRIBUTOR,
            INITIAL_MAX_PAYOUT,
            INITIAL_SPENDING_LIMIT,
            PERIOD_DURATION,
            RECIPIENT_BALANCE_LIMIT
        );

        vm.expectRevert(NativeFaucet.InvalidDistributor.selector);
        new NativeFaucet(
            ADMIN, address(0), INITIAL_MAX_PAYOUT, INITIAL_SPENDING_LIMIT, PERIOD_DURATION, RECIPIENT_BALANCE_LIMIT
        );

        vm.expectRevert(NativeFaucet.SameAdminAndDistributor.selector);
        new NativeFaucet(
            ADMIN, ADMIN, INITIAL_MAX_PAYOUT, INITIAL_SPENDING_LIMIT, PERIOD_DURATION, RECIPIENT_BALANCE_LIMIT
        );

        vm.expectRevert(NativeFaucet.InvalidMaxPayout.selector);
        new NativeFaucet(ADMIN, DISTRIBUTOR, 0, INITIAL_SPENDING_LIMIT, PERIOD_DURATION, RECIPIENT_BALANCE_LIMIT);

        vm.expectRevert(NativeFaucet.InvalidSpendingLimit.selector);
        new NativeFaucet(ADMIN, DISTRIBUTOR, INITIAL_MAX_PAYOUT, 0, PERIOD_DURATION, RECIPIENT_BALANCE_LIMIT);

        vm.expectRevert(NativeFaucet.InvalidPeriodDuration.selector);
        new NativeFaucet(ADMIN, DISTRIBUTOR, INITIAL_MAX_PAYOUT, INITIAL_SPENDING_LIMIT, 0, RECIPIENT_BALANCE_LIMIT);

        vm.expectRevert(NativeFaucet.InvalidRecipientBalanceLimit.selector);
        new NativeFaucet(ADMIN, DISTRIBUTOR, INITIAL_MAX_PAYOUT, INITIAL_SPENDING_LIMIT, PERIOD_DURATION, 0);
    }

    function testOnlyAdminCanManageDistributorRole() public {
        NativeFaucet faucet = new NativeFaucet(
            ADMIN, DISTRIBUTOR, INITIAL_MAX_PAYOUT, INITIAL_SPENDING_LIMIT, PERIOD_DURATION, RECIPIENT_BALANCE_LIMIT
        );
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

    function testDefaultAdminTransferNeedsNewAdminAcceptanceAfterDelay() public {
        NativeFaucet faucet = new NativeFaucet(
            ADMIN, DISTRIBUTOR, INITIAL_MAX_PAYOUT, INITIAL_SPENDING_LIMIT, PERIOD_DURATION, RECIPIENT_BALANCE_LIMIT
        );
        bytes32 adminRole = faucet.DEFAULT_ADMIN_ROLE();

        vm.prank(ADMIN);
        vm.expectRevert(IAccessControlDefaultAdminRules.AccessControlEnforcedDefaultAdminRules.selector);
        faucet.grantRole(adminRole, TREASURY);

        vm.prank(ADMIN);
        faucet.beginDefaultAdminTransfer(TREASURY);
        (, uint48 acceptAt) = faucet.pendingDefaultAdmin();

        vm.prank(TREASURY);
        vm.expectRevert(
            abi.encodeWithSelector(
                IAccessControlDefaultAdminRules.AccessControlEnforcedDefaultAdminDelay.selector, acceptAt
            )
        );
        faucet.acceptDefaultAdminTransfer();

        vm.warp(uint256(acceptAt) + 1);
        vm.prank(TREASURY);
        faucet.acceptDefaultAdminTransfer();
        assertEq(faucet.defaultAdmin(), TREASURY);
        assertFalse(faucet.hasRole(adminRole, ADMIN));
        assertTrue(faucet.hasRole(adminRole, TREASURY));
    }

    function testReceivesNativeFundingAndEmitsEvent() public {
        NativeFaucet faucet = new NativeFaucet(
            ADMIN, DISTRIBUTOR, INITIAL_MAX_PAYOUT, INITIAL_SPENDING_LIMIT, PERIOD_DURATION, RECIPIENT_BALANCE_LIMIT
        );
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
