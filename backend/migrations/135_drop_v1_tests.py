"""Drop the v1_tests table -- Decoupling Part 2 (in-process rubric strip).

The admin "V1 Test" tab and the frozen v1 calibrator (app/v1_frozen/) were retired
when RC's in-process rubric apparatus was removed: LEC is the sole scorer and the
v1-vs-current comparison tool no longer has a frozen rubric to run. v1_tests was an
isolated control-run store (never part of the canonical corpus), so dropping it
loses no live data.

PG-compatible; IF EXISTS keeps it idempotent under the filename-keyed runner.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("DROP TABLE IF EXISTS v1_tests CASCADE"))
