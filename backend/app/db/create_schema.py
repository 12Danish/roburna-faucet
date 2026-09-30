from app.core.config import get_settings
from app.db.base import Base
from app.db.models import Challenge, Claim, TransactionAttempt  # Register all mapped tables.
from app.db.engine import create_database_engine


def main() -> None:
    engine = create_database_engine(get_settings().database_url.get_secret_value())
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
