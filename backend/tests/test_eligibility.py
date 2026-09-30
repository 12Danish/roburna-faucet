from types import SimpleNamespace

import pytest

from app.services.eligibility import FaucetUnavailable, require_faucet_capacity


class _Call:
    def __init__(self, value):
        self.value = value

    def call(self):
        return self.value


def _chain(recipient_balance: int, *, cap: int | None = 500):
    balances = {"recipient": recipient_balance, "faucet": 1000}
    functions = SimpleNamespace(
        paused=lambda: _Call(False),
        maxPayout=lambda: _Call(10),
        spendingLimit=lambda: _Call(1000),
        spentInCurrentPeriod=lambda: _Call(0),
        recipientBalanceLimit=lambda: _Call(cap),
    )
    abi = [{"type": "function", "name": "recipientBalanceLimit"}] if cap is not None else []
    return SimpleNamespace(
        config=SimpleNamespace(chain_id=159, payout_amount_wei=10),
        web3=SimpleNamespace(eth=SimpleNamespace(chain_id=159, get_balance=balances.__getitem__)),
        contract=SimpleNamespace(abi=abi, functions=functions),
        faucet_address="faucet",
    )


def test_balance_cap_allows_exact_target_and_rejects_excess() -> None:
    require_faucet_capacity(_chain(490), "recipient")
    for balance in (491, 500, 501):
        with pytest.raises(FaucetUnavailable) as error:
            require_faucet_capacity(_chain(balance), "recipient")
        assert error.value.code == "recipient_balance_limit_exceeded"


def test_old_deployment_without_balance_cap_still_works() -> None:
    require_faucet_capacity(_chain(999, cap=None), "recipient")
