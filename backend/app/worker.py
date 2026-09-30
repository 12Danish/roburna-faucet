import logging

from app.core.chain_config import load_chain_definitions
from app.core.config import get_settings
from app.db.engine import create_database_engine, create_session_factory
from app.services.chains import connect_chain
from app.services.transactions import FaucetTransactionWorker, load_distributor_private_key


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = get_settings()
    keystore_path = settings.resolved_distributor_keystore()
    if keystore_path is None or settings.distributor_keystore_password is None:
        raise RuntimeError(
            "configure BACKEND_DISTRIBUTOR_KEYSTORE_PATH and "
            "BACKEND_DISTRIBUTOR_KEYSTORE_PASSWORD before starting the worker"
        )
    private_key = load_distributor_private_key(
        str(keystore_path), settings.distributor_keystore_password.get_secret_value()
    )

    database = create_database_engine(settings.database_url.get_secret_value())
    try:
        chains = {}
        for definition, rpc_url, deployment_path in load_chain_definitions(
            settings.resolved_chains_config(), settings.rpc_urls
        ):
            chains[definition.chain_id] = connect_chain(
                definition,
                rpc_url,
                deployment_path,
            )
        if not chains:
            raise RuntimeError("enable at least one chain before starting the worker")
        worker = FaucetTransactionWorker(
            engine=database,
            sessions=create_session_factory(database),
            chains=chains,
            private_key=private_key,
            replacement_after_seconds=settings.transaction_replacement_after_seconds,
            fee_bump_percent=settings.transaction_fee_bump_percent,
            max_replacements=settings.transaction_max_replacements,
        )
        worker.run_forever(settings.worker_poll_interval_seconds)
    finally:
        database.dispose()


if __name__ == "__main__":
    main()
