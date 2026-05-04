import asyncio
import logging
import os

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy.pool import QueuePool
from app.config import settings

logger = logging.getLogger(__name__)


def _coerce_libsql_param(value):
    if isinstance(value, memoryview):
        return value.tobytes()
    return value


def _coerce_libsql_params(parameters):
    if parameters is None:
        return None
    if isinstance(parameters, dict):
        return {k: _coerce_libsql_param(v) for k, v in parameters.items()}
    if isinstance(parameters, (list, tuple)):
        return type(parameters)(_coerce_libsql_param(v) for v in parameters)
    return parameters


class _LibsqlCursorProxy:
    """Wrap libsql cursor so memoryview bind params are coerced to bytes.

    SQLAlchemy's SQLite dialect converts Python bytes to memoryview at bind
    time for BLOB columns, but libsql's bind layer rejects memoryview with
    "Unsupported parameter type <memory at ...>". Coerce back here, the last
    layer before the libsql cursor sees the params.
    """

    def __init__(self, cursor):
        object.__setattr__(self, "_cursor", cursor)

    def execute(self, statement, parameters=None):
        if parameters is None:
            return self._cursor.execute(statement)
        return self._cursor.execute(statement, _coerce_libsql_params(parameters))

    def executemany(self, statement, seq_of_parameters):
        return self._cursor.executemany(
            statement, [_coerce_libsql_params(p) for p in seq_of_parameters]
        )

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class _LibsqlConnProxy:
    """Wrap libsql.Connection to satisfy SQLAlchemy's pysqlite dialect probes
    and intercept cursor() so BLOB params are coerced before the libsql bind.

    SQLAlchemy's sqlite dialect calls .create_function() to register a Python
    REGEXP. libsql doesn't support user-defined functions, but RC never
    queries with REGEXP, so a no-op satisfies the dialect.
    """

    def __init__(self, conn):
        object.__setattr__(self, "_conn", conn)

    def create_function(self, *args, **kwargs):
        return None

    def cursor(self):
        return _LibsqlCursorProxy(self._conn.cursor())

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _build_engine():
    url = settings.database_url
    if url.startswith("libsql://") or url.startswith("https://"):
        import libsql

        token = settings.turso_auth_token
        if not token:
            raise RuntimeError("DATABASE_URL is libsql:// but TURSO_AUTH_TOKEN is unset")

        replica_path = settings.turso_replica_path
        sync_interval = settings.turso_sync_interval

        if replica_path:
            def creator():
                return _LibsqlConnProxy(libsql.connect(
                    database=replica_path,
                    sync_url=url,
                    auth_token=token,
                    sync_interval=sync_interval,
                ))
        else:
            def creator():
                return _LibsqlConnProxy(libsql.connect(database=url, auth_token=token))

        # QueuePool (with pre_ping) reuses libSQL connections across threads
        # and recycles any whose Hrana stream has been closed server-side —
        # the default SingletonThreadPool caches one connection per thread
        # forever, so idle threads hit `stream not found` on their next use.
        # pool_recycle is 300s; pre_ping catches any prematurely-closed streams
        # on checkout. A keepalive loop in main.py hits the pool every 45s to
        # prevent idle-period cold starts that re-sync the embedded replica.
        return create_engine(
            "sqlite://",
            creator=creator,
            poolclass=QueuePool,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            pool_recycle=300,
        )

    return create_engine(url, connect_args={"check_same_thread": False})


engine = _build_engine()


@event.listens_for(engine, "connect")
def _set_foreign_keys(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


# libsql raises plain ValueError("Hrana: ... stream not found ...") when the
# server-side Hrana stream has expired. The SQLite dialect's is_disconnect()
# does not recognize this, so pool_pre_ping cannot recycle the connection and
# the same stale connection gets handed to the next caller. Tag these as
# disconnects so the pool invalidates and reconnects.
_HRANA_DISCONNECT_MARKERS = (
    "stream not found",
    "Hrana:",
    "stream error",
)


@event.listens_for(engine, "handle_error", retval=False)
def _flag_hrana_disconnect(ctx):
    err = ctx.original_exception
    if isinstance(err, ValueError):
        msg = str(err)
        if any(m in msg for m in _HRANA_DISCONNECT_MARKERS):
            ctx.is_disconnect = True




SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def keepalive_loop(interval_seconds: int = 45, failure_exit_threshold: int = 4):
    """Background task that issues SELECT 1 every `interval_seconds` against
    the pool. Keeps at least one libSQL connection warm and recently-used so
    idle-period visitors never pay the embedded-replica cold-start cost
    (~2-3s per cold connection). Runs under the FastAPI lifespan alongside
    other background tasks; cancelled on shutdown.

    WAL watchdog: when the embedded replica wedges (see 2026-04-23 incident:
    `Failed to checkpoint WAL: database is locked` stuck for 2+ hours until a
    manual restart), keepalive SELECTs start failing because pool connections
    can't run PRAGMA on checkout. If we see `failure_exit_threshold` consecutive
    failures (~3 minutes at the default interval), self-exit so Docker's
    `restart: unless-stopped` policy recovers the container.
    """
    consecutive_failures = 0
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            db = SessionLocal()
            try:
                db.execute(text("SELECT 1"))
            finally:
                db.close()
            consecutive_failures = 0
        except asyncio.CancelledError:
            raise
        except Exception:
            consecutive_failures += 1
            logger.exception(
                "keepalive ping failed (%d consecutive)", consecutive_failures
            )
            if consecutive_failures >= failure_exit_threshold:
                logger.error(
                    "WAL watchdog: %d consecutive keepalive failures — "
                    "self-exiting so docker restart policy recovers the replica",
                    consecutive_failures,
                )
                os._exit(1)


def warmup():
    """Synchronously touch the engine to force replica boot during app
    startup, not during the first user request. Safe to call multiple times."""
    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
        finally:
            db.close()
        logger.info("db warmup: connection established")
    except Exception:
        logger.exception("db warmup failed")
