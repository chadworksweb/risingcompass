"""Fix schema drift missed by the initial pg_baseline.

models.py had drifted from the migration-applied Turso schema: several columns
were added by numbered migrations but never added to the model classes, so the
create_all() baseline (pg_baseline.py) didn't create them and pg_load.py (which
selects model columns) didn't copy their data.

This adds the missing columns to the live Postgres tables (ADD COLUMN IF NOT
EXISTS -- non-destructive, safe on a serving DB) and backfills their values from
Turso by id. Idempotent. One-shot remediation; kept for the record.

Run (with the SSH tunnel up + TURSO_SRC_URL / TURSO_SRC_TOKEN set):
    .venv\\Scripts\\python.exe scripts\\pg_fix_drift.py
"""

import os

import libsql
import psycopg

PG_DSN = "host=127.0.0.1 port=25061 dbname=rc-pool user=doadmin password=%s sslmode=require" % (
    os.environ.get("PG_PASSWORD", "")
)
SRC_URL = os.environ["TURSO_SRC_URL"]
SRC_TOKEN = os.environ["TURSO_SRC_TOKEN"]

# table -> {column: postgres_type}. All drifted columns are TEXT except the
# calibration_failed boolean flag (Turso: BOOLEAN DEFAULT 0).
DRIFT: dict[str, dict[str, str]] = {
    "compass_songs": {
        "chart_position_letter": "TEXT", "activations": "TEXT",
        "calibration_failed": "BOOLEAN DEFAULT FALSE", "message_analysis": "TEXT",
        "expression_analysis": "TEXT", "intention_analysis": "TEXT",
    },
    "library_songs": {
        "activations": "TEXT", "calibration_failed": "BOOLEAN DEFAULT FALSE",
        "message_analysis": "TEXT", "expression_analysis": "TEXT",
        "intention_analysis": "TEXT",
    },
    "submitted_songs": {
        "activations": "TEXT", "calibration_failed": "BOOLEAN DEFAULT FALSE",
    },
    "agent_draft_songs": {
        "activations": "TEXT", "calibration_failed": "BOOLEAN DEFAULT FALSE",
        "message_analysis": "TEXT", "expression_analysis": "TEXT",
        "intention_analysis": "TEXT",
    },
    "calibration_runs": {
        "activations": "TEXT", "calibration_failed": "BOOLEAN DEFAULT FALSE",
        "reasoning": "TEXT",
    },
    "song_recalibration_proposals": {
        "activations": "TEXT", "calibration_failed": "BOOLEAN DEFAULT FALSE",
    },
}

BOOL_COLS = {"calibration_failed"}


class _P:
    def __init__(self, c):
        object.__setattr__(self, "_c", c)

    def create_function(self, *a, **k):
        return None

    def __getattr__(self, n):
        return getattr(self._c, n)


def main():
    src = _P(libsql.connect(database=SRC_URL, auth_token=SRC_TOKEN))
    pg = psycopg.connect(PG_DSN)

    for table, cols in DRIFT.items():
        colnames = list(cols)
        # 1. add missing columns (idempotent)
        for col, pgtype in cols.items():
            pg.execute(f'ALTER TABLE "{table}" ADD COLUMN IF NOT EXISTS "{col}" {pgtype}')
        pg.commit()

        # 2. backfill values from Turso by id
        select_cols = ", ".join(f'"{c}"' for c in colnames)
        rows = src.execute(f'SELECT id, {select_cols} FROM "{table}"').fetchall()
        set_clause = ", ".join(f'"{c}" = %s' for c in colnames)
        params = []
        for r in rows:
            rid, vals = r[0], list(r[1:])
            vals = [
                (bool(v) if v is not None else False) if colnames[i] in BOOL_COLS else v
                for i, v in enumerate(vals)
            ]
            params.append((*vals, rid))
        with pg.cursor() as cur:
            cur.executemany(f'UPDATE "{table}" SET {set_clause} WHERE id = %s', params)
        pg.commit()
        print(f"{table}: +{len(cols)} cols, backfilled {len(rows)} rows")

    pg.close()
    print("drift fixed.")


if __name__ == "__main__":
    main()
