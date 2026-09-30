import logging

from web3 import Web3

from app.services.chains import ChainClient

logger = logging.getLogger(__name__)


class FaucetUnavailable(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def require_faucet_capacity(chain: ChainClient, recipient: str) -> None:
    """Fail early on current chain conditions; the contract remains the payout gate."""
    stage = "chain_id"
    try:
        if int(chain.web3.eth.chain_id) != chain.config.chain_id:
            raise FaucetUnavailable("chain_id_mismatch")
        stage = "paused"
        if chain.contract.functions.paused().call():
            raise FaucetUnavailable("faucet_paused")
        amount = chain.config.payout_amount_wei
        if any(item.get("type") == "function" and item.get("name") == "recipientBalanceLimit" for item in chain.contract.abi):
            stage = "recipient_limit"
            recipient_limit = int(chain.contract.functions.recipientBalanceLimit().call())
            stage = "recipient_balance"
            recipient_balance = int(chain.web3.eth.get_balance(Web3.to_checksum_address(recipient)))
            if recipient_balance >= recipient_limit or amount > recipient_limit - recipient_balance:
                raise FaucetUnavailable("recipient_balance_limit_exceeded")
        stage = "max_payout"
        if amount > int(chain.contract.functions.maxPayout().call()):
            raise FaucetUnavailable("payout_exceeds_maximum")
        stage = "spending_limit"
        limit = int(chain.contract.functions.spendingLimit().call())
        stage = "spent_in_period"
        spent = int(chain.contract.functions.spentInCurrentPeriod().call())
        if amount > max(0, limit - spent):
            raise FaucetUnavailable("spending_limit_exhausted")
        stage = "faucet_balance"
        if amount > int(chain.web3.eth.get_balance(chain.faucet_address)):
            raise FaucetUnavailable("faucet_balance_insufficient")
    except FaucetUnavailable:
        raise
    except Exception as exc:
        logger.warning(
            "Faucet capacity read failed on chain %s at %s: %s",
            chain.config.chain_id, stage, type(exc).__name__,
        )
        raise FaucetUnavailable("chain_unavailable") from None
