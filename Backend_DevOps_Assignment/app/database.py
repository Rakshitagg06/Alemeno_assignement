import logging
import time
from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()


def wait_for_database(max_attempts: int = 20, delay_seconds: int = 2) -> None:
    for attempt in range(1, max_attempts + 1):
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            logger.info("Database connection ready")
            return
        except Exception as exc:
            logger.warning(
                "Database connection attempt %s/%s failed: %s",
                attempt,
                max_attempts,
                exc,
            )
            if attempt == max_attempts:
                raise
            time.sleep(delay_seconds)


def create_tables() -> None:
    wait_for_database()
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables ensured")


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

