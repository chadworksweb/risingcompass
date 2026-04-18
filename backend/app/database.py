from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy.pool import QueuePool
from app.config import settings


class _LibsqlConnProxy:
    """Wrap libsql.Connection to satisfy SQLAlchemy's pysqlite dialect probes.

    SQLAlchemy's sqlite dialect calls .create_function() to register a Python REGEXP.
    libsql doesn't support user-defined functions, but RC never queries with REGEXP,
    so a no-op satisfies the dialect.
    """

    def __init__(self, conn):
        object.__setattr__(self, "_conn", conn)

    def create_function(self, *args, **kwargs):
        return None

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _build_engine():
    url = settings.database_url
    if url.startswith("libsql://") or url.startswith("https://"):
        import libsql

        token = settings.turso_auth_token
        if not token:
            raise RuntimeError("DATABASE_URL is libsql:// but TURSO_AUTH_TOKEN is unset")

        def creator():
            return _LibsqlConnProxy(libsql.connect(database=url, auth_token=token))

        # QueuePool (with pre_ping) reuses libSQL connections across threads
        # and recycles any whose Hrana stream has been closed server-side —
        # the default SingletonThreadPool caches one connection per thread
        # forever, so idle threads hit `stream not found` on their next use.
        return create_engine(
            "sqlite://",
            creator=creator,
            poolclass=QueuePool,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            pool_recycle=60,
        )

    return create_engine(url, connect_args={"check_same_thread": False})


engine = _build_engine()


@event.listens_for(engine, "connect")
def _set_foreign_keys(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
