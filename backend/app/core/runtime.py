from psycopg_pool import ConnectionPool

from app.services.chains import ChainClient


class Runtime:
    def __init__(self, database: ConnectionPool, chains: dict[int, ChainClient]) -> None:
        self.database = database
        self.chains = chains
