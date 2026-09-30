// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {IAccessControl} from "@openzeppelin/contracts/access/IAccessControl.sol";
import {NativeFaucet} from "../src/NativeFaucet.sol";

contract ContractRecipient {
    receive() external payable {}
}

contract NativeFaucetPayoutTest is Test {
    address internal constant ADMIN = address(0xA11CE);
    address internal constant DISTRIBUTOR = address(0xD157);
    address internal constant RECIPIENT = address(0xB0B);
    address internal constant OTHER = address(0xCAFE);
    uint256 internal constant MAX_PAYOUT = 5 ether;
    uint256 internal constant SPENDING_LIMIT = 10 ether;
    uint256 internal constant INITIAL_BALANCE = 20 ether;
    uint256 internal constant PERIOD_DURATION = 1 days;
    uint256 internal constant RECIPIENT_BALANCE_LIMIT = 500 ether;

    NativeFaucet internal faucet;
    event Dispensed(address indexed distributor, address indexed recipient, uint256 amount);

    function setUp() public {
        faucet =
            new NativeFaucet(ADMIN, DISTRIBUTOR, MAX_PAYOUT, SPENDING_LIMIT, PERIOD_DURATION, RECIPIENT_BALANCE_LIMIT);
        vm.deal(address(faucet), INITIAL_BALANCE);
        vm.warp(1_000_000);
    }

    function testDispenseTransfersFundsAndCountsPeriodSpending() public {
        vm.expectEmit(true, true, false, true, address(faucet));
        emit Dispensed(DISTRIBUTOR, RECIPIENT, 2 ether);
        vm.prank(DISTRIBUTOR);
        faucet.dispense(RECIPIENT, 2 ether);
        assertEq(RECIPIENT.balance, 2 ether);
        assertEq(address(faucet).balance, INITIAL_BALANCE - 2 ether);
        assertEq(faucet.periodStart(), faucet.currentPeriodStart());
        assertEq(faucet.spentInCurrentPeriod(), 2 ether);
    }

    function testAllowsPayoutAtExactMaximumAndAvailableBalance() public {
        vm.deal(address(faucet), MAX_PAYOUT);
        vm.prank(DISTRIBUTOR);
        faucet.dispense(RECIPIENT, MAX_PAYOUT);
        assertEq(RECIPIENT.balance, MAX_PAYOUT);
        assertEq(address(faucet).balance, 0);
        assertEq(faucet.spentInCurrentPeriod(), MAX_PAYOUT);
    }

    function testOnlyDistributorCanDispense() public {
        bytes32 distributorRole = faucet.DISTRIBUTOR_ROLE();
        vm.prank(RECIPIENT);
        vm.expectRevert(
            abi.encodeWithSelector(IAccessControl.AccessControlUnauthorizedAccount.selector, RECIPIENT, distributorRole)
        );
        faucet.dispense(RECIPIENT, 1 ether);
        assertEq(RECIPIENT.balance, 0);
        assertEq(faucet.spentInCurrentPeriod(), 0);
    }

    function testRejectsInvalidRecipientAmountAndBalance() public {
        vm.startPrank(DISTRIBUTOR);
        vm.expectRevert(NativeFaucet.InvalidRecipient.selector);
        faucet.dispense(address(0), 1 ether);
        vm.expectRevert(NativeFaucet.InvalidAmount.selector);
        faucet.dispense(RECIPIENT, 0);
        vm.expectRevert(abi.encodeWithSelector(NativeFaucet.PayoutExceedsMaximum.selector, 6 ether, MAX_PAYOUT));
        faucet.dispense(RECIPIENT, 6 ether);
        vm.stopPrank();
        vm.deal(address(faucet), 0.5 ether);
        vm.prank(DISTRIBUTOR);
        vm.expectRevert(abi.encodeWithSelector(NativeFaucet.InsufficientBalance.selector, 1 ether, 0.5 ether));
        faucet.dispense(RECIPIENT, 1 ether);
        assertEq(faucet.spentInCurrentPeriod(), 0);
    }

    function testRecipientBalanceMayReachButNotExceedLimit() public {
        vm.deal(RECIPIENT, RECIPIENT_BALANCE_LIMIT - 2 ether);
        vm.prank(DISTRIBUTOR);
        faucet.dispense(RECIPIENT, 2 ether);
        assertEq(RECIPIENT.balance, RECIPIENT_BALANCE_LIMIT);

        vm.prank(DISTRIBUTOR);
        vm.expectRevert(
            abi.encodeWithSelector(
                NativeFaucet.RecipientBalanceLimitExceeded.selector, RECIPIENT_BALANCE_LIMIT, 1, RECIPIENT_BALANCE_LIMIT
            )
        );
        faucet.dispense(RECIPIENT, 1);
        assertEq(faucet.spentInCurrentPeriod(), 2 ether);
    }

    function testRejectsPayoutThatWouldCrossRecipientBalanceLimit() public {
        vm.deal(RECIPIENT, RECIPIENT_BALANCE_LIMIT - 1 ether);
        vm.prank(DISTRIBUTOR);
        vm.expectRevert(
            abi.encodeWithSelector(
                NativeFaucet.RecipientBalanceLimitExceeded.selector,
                RECIPIENT_BALANCE_LIMIT - 1 ether,
                2 ether,
                RECIPIENT_BALANCE_LIMIT
            )
        );
        faucet.dispense(RECIPIENT, 2 ether);
        assertEq(RECIPIENT.balance, RECIPIENT_BALANCE_LIMIT - 1 ether);
        assertEq(faucet.spentInCurrentPeriod(), 0);
        assertEq(address(faucet).balance, INITIAL_BALANCE);
    }

    function testContractAllowsRepeatRecipientAndCountsBothPayments() public {
        vm.startPrank(DISTRIBUTOR);
        faucet.dispense(RECIPIENT, 2 ether);
        faucet.dispense(RECIPIENT, 3 ether);
        vm.stopPrank();
        assertEq(RECIPIENT.balance, 5 ether);
        assertEq(faucet.spentInCurrentPeriod(), 5 ether);
    }

    function testSpendingLimitAppliesAcrossRecipientsAndAcceptsExactLimit() public {
        vm.startPrank(DISTRIBUTOR);
        faucet.dispense(RECIPIENT, 5 ether);
        faucet.dispense(OTHER, 5 ether);
        vm.expectRevert(abi.encodeWithSelector(NativeFaucet.SpendingLimitExceeded.selector, 1, 0));
        faucet.dispense(RECIPIENT, 1);
        vm.stopPrank();
        assertEq(faucet.spentInCurrentPeriod(), SPENDING_LIMIT);
        assertEq(address(faucet).balance, INITIAL_BALANCE - SPENDING_LIMIT);
    }

    function testSpendingLimitRejectsAmountAboveRemaining() public {
        vm.startPrank(DISTRIBUTOR);
        faucet.dispense(RECIPIENT, 5 ether);
        faucet.dispense(OTHER, 1 ether);
        vm.expectRevert(abi.encodeWithSelector(NativeFaucet.SpendingLimitExceeded.selector, 5 ether, 4 ether));
        faucet.dispense(OTHER, 5 ether);
        vm.stopPrank();
        assertEq(faucet.spentInCurrentPeriod(), 6 ether);
    }

    function testPeriodRollsOverAtExactBoundary() public {
        vm.startPrank(DISTRIBUTOR);
        faucet.dispense(RECIPIENT, 5 ether);
        faucet.dispense(OTHER, 5 ether);
        vm.stopPrank();
        uint256 nextPeriodStart = faucet.currentPeriodStart() + PERIOD_DURATION;
        vm.warp(nextPeriodStart - 1);
        vm.prank(DISTRIBUTOR);
        vm.expectRevert(abi.encodeWithSelector(NativeFaucet.SpendingLimitExceeded.selector, 1 ether, 0));
        faucet.dispense(RECIPIENT, 1 ether);
        vm.warp(nextPeriodStart);
        assertEq(faucet.spentInCurrentPeriod(), 0);
        vm.prank(DISTRIBUTOR);
        faucet.dispense(RECIPIENT, 1 ether);
        assertEq(faucet.periodStart(), nextPeriodStart);
        assertEq(faucet.spentInCurrentPeriod(), 1 ether);
        assertEq(RECIPIENT.balance, 6 ether);
    }

    function testRejectsDeployedContractRecipientWithoutChangingAccounting() public {
        ContractRecipient contractRecipient = new ContractRecipient();
        vm.prank(DISTRIBUTOR);
        vm.expectRevert(abi.encodeWithSelector(NativeFaucet.ContractRecipient.selector, address(contractRecipient)));
        faucet.dispense(address(contractRecipient), 1 ether);
        assertEq(address(contractRecipient).balance, 0);
        assertEq(address(faucet).balance, INITIAL_BALANCE);
        assertEq(faucet.spentInCurrentPeriod(), 0);
    }

    function testAcceptsWalletAddressWithNoCode() public {
        address wallet = makeAddr("wallet");
        assertEq(wallet.code.length, 0);
        vm.prank(DISTRIBUTOR);
        faucet.dispense(wallet, 1 ether);
        assertEq(wallet.balance, 1 ether);
        assertEq(faucet.spentInCurrentPeriod(), 1 ether);
    }

    function testFuzzValidPayoutConservesBalance(uint96 rawAmount) public {
        uint256 amount = bound(uint256(rawAmount), 1, MAX_PAYOUT);
        vm.prank(DISTRIBUTOR);
        faucet.dispense(RECIPIENT, amount);
        assertEq(RECIPIENT.balance, amount);
        assertEq(address(faucet).balance + RECIPIENT.balance, INITIAL_BALANCE);
        assertEq(faucet.spentInCurrentPeriod(), amount);
    }
}
