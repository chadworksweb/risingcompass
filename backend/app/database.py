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
        # The DO Postgres cluster has max_connections = 25, SHARED with
        # lec_app / lecg_app / system roles, so RC realistically gets ~15. A
        # larger client pool (a brief 20+40=60 experiment on 2026-06-28)
        # OVERRUNS that cap: under cold-start concurrency the app demands more
        # server connections than the cluster can grant, transactions block
        # waiting for a slot at 0% CPU, and the whole API wedges. 5+10=15 is the
        # config that ran for months; drift (the only thing that used to exhaust
        # it by holding connections for 129s) is now cached, so 15 is safe.
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=300,
        # Fail a starved checkout in 10s instead of hanging 30s.
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
