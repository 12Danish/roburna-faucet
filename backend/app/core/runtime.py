from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.services.chains import ChainClient


class Runtime:
    def __init__(
        self,
        database: Engine,
        sessions: sessionmaker[Session],
        chains: dict[int, ChainClient],
    ) -> None:
        self.database = database
        self.sessions = sessions
        self.chains = chains
