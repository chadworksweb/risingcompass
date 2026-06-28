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
        # PgBouncer (transaction mode) multiplexes, so a larger client-side
        # pool is cheap and is the intended lever here. The old 5+10 ceiling
        # (15) exhausted under normal concurrency because async request
        # handlers hold a checked-out connection across slow awaited work
        # (Opus / external HTTP), which showed up as a pool QueuePool timeout
        # while the DB itself sat idle. 20+40 (60) gives real headroom; the DB
        # has room and PgBouncer absorbs the fan-in.
        pool_size=20,
        max_overflow=40,
        pool_pre_ping=True,
        pool_recycle=300,
        # Fail a starved checkout in 10s instead of hanging 30s, so a spike
        # degrades fast and visibly rather than stacking 30s waits.
        pool_timeout=10,
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
