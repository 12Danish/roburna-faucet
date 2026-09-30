from eth_account import Account
from types import SimpleNamespace

from app.services.transactions import (
    choose_next_nonce,
    FaucetTransactionWorker,
    load_distributor_private_key,
)


def test_nonce_allocation_uses_the_higher_pending_or_database_value() -> None:
    assert choose_next_nonce(chain_pending_nonce=8, highest_recorded_nonce=6) == 8
    assert choose_next_nonce(chain_pending_nonce=4, highest_recorded_nonce=8) == 9
    assert choose_next_nonce(chain_pending_nonce=0, highest_recorded_nonce=None) == 0


def test_keystore_round_trip(tmp_path) -> None:
    account = Account.create()
    password = "test-only-keystore-password"
    path = tmp_path / "distributor.json"
    path.write_text(__import__("json").dumps(Account.encrypt(account.key, password)))

    decrypted = load_distributor_private_key(str(path), password)

    assert Account.from_key(decrypted).address == account.address


def test_replacement_fee_bump_supports_legacy_and_eip1559() -> None:
    worker = object.__new__(FaucetTransactionWorker)
    worker.fee_bump_percent = 20
    legacy_chain = SimpleNamespace(
        config=SimpleNamespace(fee_mode="legacy"),
        web3=SimpleNamespace(eth=SimpleNamespace(gas_price=110)),
    )
    eip1559_chain = SimpleNamespace(
        config=SimpleNamespace(fee_mode="eip1559"),
        web3=SimpleNamespace(
            eth=SimpleNamespace(
                get_block=lambda _tag: {"baseFeePerGas": 100},
                max_priority_fee=3,
            )
        ),
    )

    assert worker._bumped_fee_fields(legacy_chain, 100, None, None) == {"gasPrice": 120}
    assert worker._bumped_fee_fields(eip1559_chain, None, 200, 10) == {
        "maxPriorityFeePerGas": 12,
        "maxFeePerGas": 240,
    }
