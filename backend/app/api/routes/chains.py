from fastapi import APIRouter, Depends

from app.api.dependencies import get_runtime
from app.api.schemas import ChainListResponse, ChainResponse
from app.core.runtime import Runtime

router = APIRouter(tags=["chains"])


@router.get("/chains", response_model=ChainListResponse)
def list_chains(runtime: Runtime = Depends(get_runtime)) -> ChainListResponse:
    """Return public metadata for chains that passed startup validation."""
    chains = runtime.chains
    return ChainListResponse(
        chains=[
            ChainResponse(
                chain_id=chain.config.chain_id,
                name=chain.config.name,
                currency_symbol=chain.config.currency_symbol,
                currency_decimals=chain.config.currency_decimals,
                payout_amount_wei=str(chain.config.payout_amount_wei),
                cooldown_seconds=chain.config.cooldown_seconds,
                explorer_url=chain.config.explorer_url,
                faucet_address=chain.faucet_address,
            )
            for chain in chains.values()
        ]
    )
