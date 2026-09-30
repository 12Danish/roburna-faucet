// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {IAccessControl} from "@openzeppelin/contracts/access/IAccessControl.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {NativeFaucet} from "../src/NativeFaucet.sol";

contract RejectingWithdrawalRecipient {
    receive() external payable {
        revert("withdrawal rejected");
    }
}

contract ReenteringAdminRecipient {
    NativeFaucet public faucet;
    bool public attempted;
    bool public succeeded;
    bytes4 public errorSelector;

    function setFaucet(NativeFaucet faucet_) external {
        faucet = faucet_;
    }

    function withdraw(uint256 amount) external {
        faucet.withdraw(address(this), amount);
    }

    receive() external payable {
        attempted = true;
        (bool ok, bytes memory reason) =
            address(faucet).call(abi.encodeCall(NativeFaucet.withdraw, (address(this), 1 ether)));
        succeeded = ok;
        if (!ok && reason.length >= 4) {
            bytes4 selector;
            assembly {
                selector := mload(add(reason, 0x20))
            }
            errorSelector = selector;
        }
    }
}

contract NativeFaucetAdminTest is Test {
    address internal constant ADMIN = address(0xA11CE);
    address internal constant DISTRIBUTOR = address(0xD157);
    address internal constant TREASURY = address(0x7EA5);
    address internal constant RECIPIENT = address(0xB0B);
    uint256 internal constant INITIAL_MAX_PAYOUT = 5 ether;
    uint256 internal constant INITIAL_BALANCE = 20 ether;
    uint256 internal constant INITIAL_SPENDING_LIMIT = 10 ether;
    uint256 internal constant RECIPIENT_BALANCE_LIMIT = 500 ether;

    NativeFaucet internal faucet;

    event MaxPayoutUpdated(uint256 previousAmount, uint256 newAmount);
    event RecipientBalanceLimitUpdated(uint256 previousLimit, uint256 newLimit);
    event SpendingLimitUpdated(uint256 previousAmount, uint256 newAmount);
    event Withdrawn(address indexed recipient, uint256 amount);

    function setUp() public {
        faucet = new NativeFaucet(
            ADMIN, DISTRIBUTOR, INITIAL_MAX_PAYOUT, INITIAL_SPENDING_LIMIT, 1 days, RECIPIENT_BALANCE_LIMIT
        );
        vm.deal(address(faucet), INITIAL_BALANCE);
        vm.warp(1_000_000);
    }

    function testOnlyAdminCanChangeMaximumAndItTakesEffect() public {
        bytes32 adminRole = faucet.DEFAULT_ADMIN_ROLE();
        vm.prank(DISTRIBUTOR);
        vm.expectRevert(
            abi.encodeWithSelector(IAccessControl.AccessControlUnauthorizedAccount.selector, DISTRIBUTOR, adminRole)
        );
        faucet.setMaxPayout(1 ether);

        vm.prank(ADMIN);
        vm.expectRevert(NativeFaucet.InvalidMaxPayout.selector);
        faucet.setMaxPayout(0);

        vm.expectEmit(false, false, false, true, address(faucet));
        emit MaxPayoutUpdated(INITIAL_MAX_PAYOUT, 1 ether);
        vm.prank(ADMIN);
        faucet.setMaxPayout(1 ether);
        assertEq(faucet.maxPayout(), 1 ether);

        vm.prank(DISTRIBUTOR);
        vm.expectRevert(abi.encodeWithSelector(NativeFaucet.PayoutExceedsMaximum.selector, 2 ether, 1 ether));
        faucet.dispense(RECIPIENT, 2 ether);

        vm.prank(DISTRIBUTOR);
        faucet.dispense(RECIPIENT, 1 ether);
        assertEq(RECIPIENT.balance, 1 ether);
    }

    function testOnlyAdminCanChangeRecipientBalanceLimit() public {
        bytes32 adminRole = faucet.DEFAULT_ADMIN_ROLE();
        vm.prank(DISTRIBUTOR);
        vm.expectRevert(
            abi.encodeWithSelector(IAccessControl.AccessControlUnauthorizedAccount.selector, DISTRIBUTOR, adminRole)
        );
        faucet.setRecipientBalanceLimit(2 ether);

        vm.prank(ADMIN);
        vm.expectRevert(NativeFaucet.InvalidRecipientBalanceLimit.selector);
        faucet.setRecipientBalanceLimit(0);

        vm.expectEmit(false, false, false, true, address(faucet));
        emit RecipientBalanceLimitUpdated(RECIPIENT_BALANCE_LIMIT, 2 ether);
        vm.prank(ADMIN);
        faucet.setRecipientBalanceLimit(2 ether);
        assertEq(faucet.recipientBalanceLimit(), 2 ether);

        vm.deal(RECIPIENT, 1 ether);
        vm.prank(DISTRIBUTOR);
        vm.expectRevert(
            abi.encodeWithSelector(NativeFaucet.RecipientBalanceLimitExceeded.selector, 1 ether, 2 ether, 2 ether)
        );
        faucet.dispense(RECIPIENT, 2 ether);
    }

    function testOnlyAdminCanChangeSpendingLimit() public {
        bytes32 adminRole = faucet.DEFAULT_ADMIN_ROLE();
        vm.prank(DISTRIBUTOR);
        vm.expectRevert(
            abi.encodeWithSelector(IAccessControl.AccessControlUnauthorizedAccount.selector, DISTRIBUTOR, adminRole)
        );
        faucet.setSpendingLimit(1 ether);

        vm.prank(ADMIN);
        vm.expectRevert(NativeFaucet.InvalidSpendingLimit.selector);
        faucet.setSpendingLimit(0);

        vm.expectEmit(false, false, false, true, address(faucet));
        emit SpendingLimitUpdated(INITIAL_SPENDING_LIMIT, 2 ether);
        vm.prank(ADMIN);
        faucet.setSpendingLimit(2 ether);
        assertEq(faucet.spendingLimit(), 2 ether);
    }

    function testLoweringLimitBelowSpentBlocksUntilNextPeriod() public {
        vm.prank(DISTRIBUTOR);
        faucet.dispense(RECIPIENT, 3 ether);

        vm.prank(ADMIN);
        faucet.setSpendingLimit(2 ether);
        vm.prank(DISTRIBUTOR);
        vm.expectRevert(abi.encodeWithSelector(NativeFaucet.SpendingLimitExceeded.selector, 1 ether, 0));
        faucet.dispense(RECIPIENT, 1 ether);
        assertEq(faucet.spentInCurrentPeriod(), 3 ether);

        vm.warp(faucet.currentPeriodStart() + 1 days);
        assertEq(faucet.spentInCurrentPeriod(), 0);
        vm.prank(DISTRIBUTOR);
        faucet.dispense(RECIPIENT, 1 ether);
        assertEq(faucet.spentInCurrentPeriod(), 1 ether);
    }

    function testOnlyAdminCanPauseAndUnpause() public {
        bytes32 adminRole = faucet.DEFAULT_ADMIN_ROLE();
        vm.prank(DISTRIBUTOR);
        vm.expectRevert(
            abi.encodeWithSelector(IAccessControl.AccessControlUnauthorizedAccount.selector, DISTRIBUTOR, adminRole)
        );
        faucet.pause();

        vm.prank(ADMIN);
        faucet.pause();
        assertTrue(faucet.paused());

        vm.prank(DISTRIBUTOR);
        vm.expectRevert(Pausable.EnforcedPause.selector);
        faucet.dispense(RECIPIENT, 1 ether);

        vm.prank(DISTRIBUTOR);
        vm.expectRevert(
            abi.encodeWithSelector(IAccessControl.AccessControlUnauthorizedAccount.selector, DISTRIBUTOR, adminRole)
        );
        faucet.unpause();

        vm.prank(ADMIN);
        faucet.unpause();
        assertFalse(faucet.paused());

        vm.prank(DISTRIBUTOR);
        faucet.dispense(RECIPIENT, 1 ether);
        assertEq(RECIPIENT.balance, 1 ether);
    }

    function testCanFundAndWithdrawWhilePausedWithoutCountingAsPayouts() public {
        vm.prank(ADMIN);
        faucet.pause();

        vm.deal(TREASURY, 2 ether);
        vm.prank(TREASURY);
        (bool funded,) = address(faucet).call{value: 2 ether}("");
        assertTrue(funded);
        assertEq(address(faucet).balance, INITIAL_BALANCE + 2 ether);

        vm.expectEmit(true, false, false, true, address(faucet));
        emit Withdrawn(RECIPIENT, 3 ether);
        vm.prank(ADMIN);
        faucet.withdraw(RECIPIENT, 3 ether);

        assertEq(RECIPIENT.balance, 3 ether);
        assertEq(address(faucet).balance, INITIAL_BALANCE - 1 ether);
        assertEq(faucet.spentInCurrentPeriod(), 0);
        assertTrue(faucet.paused());
    }

    function testWithdrawRejectsUnauthorizedAndInvalidRequests() public {
        bytes32 adminRole = faucet.DEFAULT_ADMIN_ROLE();
        vm.prank(DISTRIBUTOR);
        vm.expectRevert(
            abi.encodeWithSelector(IAccessControl.AccessControlUnauthorizedAccount.selector, DISTRIBUTOR, adminRole)
        );
        faucet.withdraw(RECIPIENT, 1 ether);

        vm.startPrank(ADMIN);
        vm.expectRevert(NativeFaucet.InvalidRecipient.selector);
        faucet.withdraw(address(0), 1 ether);

        vm.expectRevert(NativeFaucet.InvalidAmount.selector);
        faucet.withdraw(RECIPIENT, 0);

        vm.expectRevert(
            abi.encodeWithSelector(NativeFaucet.InsufficientBalance.selector, INITIAL_BALANCE + 1, INITIAL_BALANCE)
        );
        faucet.withdraw(RECIPIENT, INITIAL_BALANCE + 1);
        vm.stopPrank();
    }

    function testRejectedWithdrawalKeepsFundsInFaucet() public {
        RejectingWithdrawalRecipient rejecting = new RejectingWithdrawalRecipient();
        vm.prank(ADMIN);
        vm.expectRevert(abi.encodeWithSelector(NativeFaucet.NativeTransferFailed.selector, address(rejecting), 1 ether));
        faucet.withdraw(address(rejecting), 1 ether);
        assertEq(address(faucet).balance, INITIAL_BALANCE);
    }

    function testAdminWithdrawalCannotReenter() public {
        ReenteringAdminRecipient adminContract = new ReenteringAdminRecipient();
        NativeFaucet guarded = new NativeFaucet(
            address(adminContract),
            DISTRIBUTOR,
            INITIAL_MAX_PAYOUT,
            INITIAL_SPENDING_LIMIT,
            1 days,
            RECIPIENT_BALANCE_LIMIT
        );
        adminContract.setFaucet(guarded);
        vm.deal(address(guarded), INITIAL_BALANCE);

        adminContract.withdraw(1 ether);

        assertTrue(adminContract.attempted());
        assertFalse(adminContract.succeeded());
        assertEq(adminContract.errorSelector(), ReentrancyGuard.ReentrancyGuardReentrantCall.selector);
        assertEq(address(adminContract).balance, 1 ether);
        assertEq(address(guarded).balance, INITIAL_BALANCE - 1 ether);
    }

    function testRevokedDistributorCannotPay() public {
        bytes32 distributorRole = faucet.DISTRIBUTOR_ROLE();
        vm.prank(ADMIN);
        faucet.revokeRole(distributorRole, DISTRIBUTOR);

        vm.prank(DISTRIBUTOR);
        vm.expectRevert(
            abi.encodeWithSelector(
                IAccessControl.AccessControlUnauthorizedAccount.selector, DISTRIBUTOR, distributorRole
            )
        );
        faucet.dispense(RECIPIENT, 1 ether);
        assertEq(RECIPIENT.balance, 0);
        assertEq(faucet.spentInCurrentPeriod(), 0);
    }
}
