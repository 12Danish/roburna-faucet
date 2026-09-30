from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from psycopg_pool import ConnectionPool

from app.api.routes import chains, health
from app.core.chain_config import load_chain_definitions
from app.core.config import get_settings
from app.core.runtime import Runtime
from app.db.pool import create_database_pool
from app.services.chains import ChainClient, connect_chain


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    database: ConnectionPool | None = None
    try:
        database = create_database_pool(settings.database_url)
        chain_clients: dict[int, ChainClient] = {}
        for definition, rpc_url, deployment_path in load_chain_definitions(
            settings.resolved_chains_config(), settings.rpc_urls
        ):
            chain_clients[definition.chain_id] = connect_chain(
                definition,
                rpc_url,
                deployment_path,
            )
        app.state.runtime = Runtime(database=database, chains=chain_clients)
        yield
    finally:
        if database is not None:
            database.close()


app = FastAPI(
    title="Roburna Faucet API",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(health.router)
app.include_router(chains.router)
