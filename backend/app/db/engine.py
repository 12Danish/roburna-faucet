from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def create_database_engine(database_url: str) -> Engine:
    url = make_url(database_url).set(drivername="postgresql+psycopg")
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
