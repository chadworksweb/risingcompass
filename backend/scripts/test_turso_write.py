"""Verify ORM writes work end-to-end against Turso."""
import os
import sys

# Load .env
from dotenv import load_dotenv  # noqa: F401  (optional)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from sqlalchemy import text

db = SessionLocal()
try:
    db.execute(text("CREATE TABLE IF NOT EXISTS _turso_smoketest (id INTEGER PRIMARY KEY, val TEXT)"))
    db.execute(text("INSERT INTO _turso_smoketest (val) VALUES (:v)"), {"v": "hello-from-rc"})
    db.commit()
    rows = db.execute(text("SELECT id, val FROM _turso_smoketest ORDER BY id DESC LIMIT 3")).fetchall()
    print("Recent rows:", [tuple(r) for r in rows])
    db.execute(text("DROP TABLE _turso_smoketest"))
    db.commit()
    print("Write + drop OK")
finally:
    db.close()
