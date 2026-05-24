import logging

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from app.config import settings

logger = logging.getLogger(__name__)


# Single plain engine against DigitalOcean Managed Postgres. Reached through
# DO's PgBouncer connection pool (transaction mode) in both environments, so
# SQLAlchemy uses a small client-side pool and lets PgBouncer multiplex.
# No embedded replica, no Hrana streams, no primary/replica split: Postgres
# MVCC means writes never wedge reads, and a transaction can span a long Opus
# call without a stream dying. See RISING-COMPASS-POSTGRES-MIGRATION.md.
def _build_engine():
    return create_engine(
        settings.database_url,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=300,
    )


engine = _build_engine()

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
