"""Convert every `timestamp without time zone` column to `timestamptz`.

WHY THIS EXISTS. The whole codebase stored naive UTC: 139 `datetime.utcnow()`
call sites writing into 178 bare `Column(DateTime)` columns, which Postgres
creates as `timestamp without time zone`. That was internally CONSISTENT and
therefore correct at rest -- naive Python matched naive columns exactly, and the
2026-08-17 audit recommended against touching it for precisely that reason.

It is being migrated anyway, at Chad's direction, because `datetime.utcnow()` is
deprecated on Python 3.12 (the Dockerfile base) and the deprecation only ever
gets more expensive to answer.

THE THING THAT MAKES THIS DANGEROUS, and why this migration is not optional.
Swapping the 139 call sites to `datetime.now(timezone.utc)` WITHOUT this
migration writes aware datetimes into naive columns and compares aware values
against naive ones -- a TypeError at runtime, on whichever path happens to mix
them first. A half-migration is strictly worse than either end state. Code,
column types, and stored values move together or not at all.

THE CONVERSION IS SAFE BECAUSE THE STORED VALUES ARE ALREADY UTC. Every writer
used `utcnow()`. So reinterpreting each stored value AT TIME ZONE 'UTC' is a
relabelling, not a shift: the instant on the clock does not move, it just stops
being ambiguous.

NO TABLE REWRITE. Since PG 12, `timestamp -> timestamptz` is a metadata-only
change when the session TimeZone is UTC; otherwise it rewrites the table. Prod
runs PG 18.4 with `TimeZone = GMT`, which is the same offset but not literally
the string the planner checks, so this sets `TimeZone` to UTC explicitly for the
transaction. The largest affected table (`api_call_log`, ~222k rows) would take
seconds even on the rewrite path, so this is about avoiding a long ACCESS
EXCLUSIVE lock, not about feasibility.

DRIVEN OFF information_schema, NOT A HAND-TYPED LIST. 178 columns across ~90
tables is exactly the kind of list that goes stale the first time someone adds a
table, and a missed column is a naive/aware mix waiting to happen. This asks the
database what it actually has.

DEFAULTS ARE CONVERTED TOO. Many columns carry `DEFAULT (NOW() AT TIME ZONE
'utc')`, which yields a naive timestamp. Left alone against a timestamptz column
Postgres would coerce it using the SESSION TimeZone at INSERT time, so a row
written under a non-UTC session would land shifted. Those defaults are rewritten
to plain `now()`, which is already an absolute instant.
"""
import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)


def up(conn):
    # Metadata-only conversion path (see the module docstring). SET LOCAL scopes
    # this to the migration's transaction.
    conn.execute(text("SET LOCAL TimeZone = 'UTC'"))

    cols = conn.execute(text("""
        SELECT c.table_name, c.column_name, c.column_default
        FROM information_schema.columns c
        JOIN information_schema.tables t
          ON t.table_schema = c.table_schema
         AND t.table_name = c.table_name
         AND t.table_type = 'BASE TABLE'
        WHERE c.table_schema = 'public'
          AND c.data_type = 'timestamp without time zone'
        ORDER BY c.table_name, c.column_name
    """)).fetchall()

    if not cols:
        logger.info("156: no naive timestamp columns left; nothing to convert")
        return

    converted = 0
    defaults_fixed = 0
    for table, column, default in cols:
        # Identifiers come from information_schema, never from user input, but
        # they still get quoted so a table named like a keyword cannot break the
        # statement.
        tbl, col = f'"{table}"', f'"{column}"'

        # Drop a naive default BEFORE the type change: `NOW() AT TIME ZONE 'utc'`
        # is timestamp-typed, and Postgres will not re-type a column out from
        # under a default that no longer matches.
        if default is not None:
            conn.execute(text(f"ALTER TABLE {tbl} ALTER COLUMN {col} DROP DEFAULT"))

        conn.execute(text(
            f"ALTER TABLE {tbl} ALTER COLUMN {col} TYPE timestamptz "
            f"USING {col} AT TIME ZONE 'UTC'"
        ))
        converted += 1

        if default is not None:
            # Any flavour of "now" becomes plain now(), which is already an
            # absolute instant and needs no timezone gymnastics. Anything else
            # (a literal, a constant) is restored verbatim.
            low = (default or "").lower()
            new_default = "now()" if "now()" in low or "current_timestamp" in low else default
            conn.execute(text(
                f"ALTER TABLE {tbl} ALTER COLUMN {col} SET DEFAULT {new_default}"
            ))
            defaults_fixed += 1

    logger.info("156: converted %s columns to timestamptz (%s defaults rewritten)",
                converted, defaults_fixed)
