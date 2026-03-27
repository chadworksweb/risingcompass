from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.config import settings

if settings.is_turso:
    engine = create_engine(settings.effective_database_url, echo=False)
else:
    engine = create_engine(
        settings.effective_database_url,
        connect_args={"check_same_thread": False},
    )


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
