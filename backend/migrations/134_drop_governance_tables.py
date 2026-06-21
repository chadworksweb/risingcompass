"""Drop the governance tables -- Decoupling Part 2 Cut 2 (RC strip).

The Motion Desk, Deliberation Chamber, and the instrument-level rubric-change log
moved to the Libra Engine Compass governance venue (lecg.libraengine.com). RC no
longer hosts the constitution or its legislature, so its governance tables are
dropped here. The data-migration dry-run confirmed 0 motions / 0 arguments, so
there is no live participation data to lose; the rubric-change history lives in
the venue + le-scaffold's changelog.

account_verifications is intentionally KEPT (dormant) -- it is identity, not
rubric/calibration. Verified-human becomes a shared Clerk credential at Clerk Pro
(Decision 3); the table is retired then, not now.

PG-compatible (drop dependents first; CASCADE covers the FKs regardless).
"""

from sqlalchemy import text


def up(conn):
    # motion_arguments FKs motions; rubric_changes FKs motions -> drop them first.
    conn.execute(text("DROP TABLE IF EXISTS motion_arguments CASCADE"))
    conn.execute(text("DROP TABLE IF EXISTS rubric_changes CASCADE"))
    conn.execute(text("DROP TABLE IF EXISTS motions CASCADE"))
