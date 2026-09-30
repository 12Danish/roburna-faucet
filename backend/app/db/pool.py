from psycopg_pool import ConnectionPool


def create_database_pool(database_url: str) -> ConnectionPool:
    pool = ConnectionPool(
        conninfo=database_url,
        min_size=1,
        max_size=5,
        timeout=5,
        max_waiting=20,
        open=False,
        name="faucet-api",
    )
    pool.open(wait=True, timeout=5)
    return pool
