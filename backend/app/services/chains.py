import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from web3 import HTTPProvider, Web3
from web3.middleware import ExtraDataToPOAMiddleware

from app.core.chain_config import ChainDefinition


@dataclass(frozen=True)
class ChainClient:
    config: ChainDefinition
    web3: Web3
    faucet_address: str
    contract: Any


def connect_chain(
    definition: ChainDefinition,
    rpc_url: str,
    deployment_path: Path,
) -> ChainClient:
    try:
        deployment = json.loads(deployment_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Deployment metadata missing for chain {definition.chain_id}: {deployment_path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid deployment JSON for chain {definition.chain_id}") from exc

    try:
        exported_chain_id = int(deployment["chainId"])
        faucet_address = Web3.to_checksum_address(deployment["address"])
        distributor_address = Web3.to_checksum_address(definition.distributor_address)
        abi = deployment["abi"]
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"Deployment metadata for chain {definition.chain_id} is incomplete") from exc
    if exported_chain_id != definition.chain_id:
        raise RuntimeError(
            f"Deployment file chain ID {exported_chain_id} does not match configured "
            f"chain ID {definition.chain_id}"
        )
    if not isinstance(abi, list):
        raise RuntimeError(f"Deployment ABI for chain {definition.chain_id} must be a JSON array")

    web3 = Web3(HTTPProvider(rpc_url, request_kwargs={"timeout": 5}))
    if definition.poa_compatibility:
        web3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    try:
        if not web3.is_connected():
            raise RuntimeError(f"RPC is unreachable for chain {definition.chain_id}")
        actual_chain_id = web3.eth.chain_id
        if actual_chain_id != definition.chain_id:
            raise RuntimeError(
                f"RPC chain ID {actual_chain_id} does not match configured chain ID "
                f"{definition.chain_id}"
            )
        if not web3.eth.get_code(faucet_address):
            raise RuntimeError(f"No contract code at configured faucet address on {definition.chain_id}")

        contract = web3.eth.contract(address=faucet_address, abi=abi)
        role = contract.functions.DISTRIBUTOR_ROLE().call()
        if not contract.functions.hasRole(role, distributor_address).call():
            raise RuntimeError(
                f"Configured distributor is missing DISTRIBUTOR_ROLE on chain {definition.chain_id}"
            )
        max_payout = contract.functions.maxPayout().call()
        if definition.payout_amount_wei > max_payout:
            raise RuntimeError(
                f"Configured payout exceeds on-chain maxPayout on chain {definition.chain_id}"
            )
        spending_limit = contract.functions.spendingLimit().call()
        if definition.payout_amount_wei > spending_limit:
            raise RuntimeError(
                f"Configured payout exceeds on-chain spendingLimit on chain {definition.chain_id}"
            )
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(
            f"Unable to validate faucet on chain {definition.chain_id}; check its RPC, ABI, and contract configuration"
        ) from None

    return ChainClient(
        config=definition,
        web3=web3,
        faucet_address=faucet_address,
        contract=contract,
    )
