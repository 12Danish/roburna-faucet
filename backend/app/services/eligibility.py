from app.services.chains import ChainClient


class FaucetUnavailable(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def require_faucet_capacity(chain: ChainClient) -> None:
    """Fail early on current chain conditions; the contract remains the payout gate."""
    try:
        if int(chain.web3.eth.chain_id) != chain.config.chain_id:
            raise FaucetUnavailable("chain_id_mismatch")
        if chain.contract.functions.paused().call():
            raise FaucetUnavailable("faucet_paused")
        amount = chain.config.payout_amount_wei
        if amount > int(chain.contract.functions.maxPayout().call()):
            raise FaucetUnavailable("payout_exceeds_maximum")
        limit = int(chain.contract.functions.spendingLimit().call())
        spent = int(chain.contract.functions.spentInCurrentPeriod().call())
        if amount > max(0, limit - spent):
            raise FaucetUnavailable("spending_limit_exhausted")
        if amount > int(chain.web3.eth.get_balance(chain.faucet_address)):
            raise FaucetUnavailable("faucet_balance_insufficient")
    except FaucetUnavailable:
        raise
    except Exception:
        raise FaucetUnavailable("chain_unavailable") from None
