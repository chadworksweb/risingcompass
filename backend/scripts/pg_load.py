"""One-time (re-runnable) data copy: Turso/libSQL -> DigitalOcean Postgres.

Reads and writes through SQLAlchemy using the same model Table objects on both
ends, so the SQLite dialect's result processors convert stored values back to
native Python (JSON text -> dict, ISO string -> datetime, 0/1 -> bool, BLOB ->
bytes) and the Postgres dialect re-serializes them correctly. No hand-rolled
per-type coercion.

Re-runnable: truncates every model table (RESTART IDENTITY CASCADE) before
loading, then resets serial sequences to MAX(pk) afterward. Run it once now and
again right before cutover for a fresh copy. See
RISING-COMPASS-POSTGRES-MIGRATION.md (Cutover 3).

Run (with the SSH tunnel up):
    set TURSO_SRC_URL=libsql://...
    set TURSO_SRC_TOKEN=eyJ...
    .venv\\Scripts\\python.exe scripts\\pg_load.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import libsql
from sqlalchemy import Integer, create_engine, select, text

import app.models  # noqa: F401 - registers every model on Base.metadata
from app.database import Base, engine as pg_engine

SRC_URL = os.environ["TURSO_SRC_URL"]
SRC_TOKEN = os.environ["TURSO_SRC_TOKEN"]


class _ConnProxy:
    """Satisfy SQLAlchemy's sqlite dialect probe (create_function) on a libsql
    connection. Read-only path, so no BLOB-bind coercion needed."""

    def __init__(self, conn):
        object.__setattr__(self, "_conn", conn)

    def create_function(self, *args, **kwargs):
        return None

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _src_engine():
    def creator():
        return _ConnProxy(libsql.connect(database=SRC_URL, auth_token=SRC_TOKEN))

    return create_engine("sqlite://", creator=creator)


def main():
    src_engine = _src_engine()
    tables = Base.metadata.sorted_tables  # parents first

    print(f"Source: {SRC_URL}")
    print(f"Target: {pg_engine.url.render_as_string(hide_password=True)}")
    print(f"Tables: {len(tables)}")

    # Source (Turso/SQLite) never enforced FKs, so it carries dangling
    # references. Postgres rejects them. Null out orphaned values (the link was
    # already broken) where the column allows it; error on a non-nullable orphan
    # so data is never silently dropped.
    valid_keys: dict[tuple[str, str], set] = {}

    def _valid(src, reftbl, refcol):
        key = (reftbl, refcol)
        if key not in valid_keys:
            res = src.execute(text(f'SELECT DISTINCT "{refcol}" FROM "{reftbl}"'))
            valid_keys[key] = {r[0] for r in res if r[0] is not None}
        return valid_keys[key]

    counts = {}
    nulled = {}
    with src_engine.connect() as src, pg_engine.begin() as dst:
        # DO Managed Postgres doesn't grant session_replication_role, so FK
        # triggers stay live during load. sorted_tables inserts parents before
        # children, which satisfies them; TRUNCATE CASCADE handles teardown.
        names = ", ".join(f'"{t.name}"' for t in tables)
        dst.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))

        for t in tables:
            rows = [dict(m) for m in src.execute(select(t)).mappings()]

            for fk in t.foreign_key_constraints:
                cols = list(fk.columns)
                if len(cols) != 1:
                    continue  # composite FKs: skip auto-null
                col = cols[0]
                refcol = list(fk.elements)[0].column
                vk = _valid(src, refcol.table.name, refcol.name)
                for row in rows:
                    v = row.get(col.name)
                    if v is not None and v not in vk:
                        if not col.nullable:
                            raise RuntimeError(
                                f"orphan in NON-NULLABLE {t.name}.{col.name} "
                                f"-> {refcol.table.name}.{refcol.name}: value {v!r}"
                            )
                        row[col.name] = None
                        nulled[f"{t.name}.{col.name}"] = (
                            nulled.get(f"{t.name}.{col.name}", 0) + 1
                        )

            if rows:
                dst.execute(t.insert(), rows)
            counts[t.name] = len(rows)

        # Reset serial sequences so the next real insert doesn't collide.
        reset = 0
        for t in tables:
            for col in t.primary_key.columns:
                if not isinstance(col.type, Integer):
                    continue
                seq = dst.execute(
                    text("SELECT pg_get_serial_sequence(:t, :c)"),
                    {"t": t.name, "c": col.name},
                ).scalar()
                if not seq:
                    continue
                mx = dst.execute(
                    text(f'SELECT MAX("{col.name}") FROM "{t.name}"')
                ).scalar()
                if mx is None:
                    dst.execute(text("SELECT setval(:s, 1, false)"), {"s": seq})
                else:
                    dst.execute(
                        text("SELECT setval(:s, :v, true)"), {"s": seq, "v": mx}
                    )
                reset += 1

    total = sum(counts.values())
    nonempty = {k: v for k, v in counts.items() if v}
    print(f"\nLoaded {total} rows across {len(nonempty)} non-empty tables; "
          f"{reset} sequences reset.")
    if nulled:
        print("Orphaned FK references nulled (parent missing in source):")
        for k, v in sorted(nulled.items()):
            print(f"  {v:>7}  {k}")
    for k in sorted(nonempty, key=lambda k: -nonempty[k]):
        print(f"  {nonempty[k]:>7}  {k}")


if __name__ == "__main__":
    main()
