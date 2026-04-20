"""Drop collections + recommendations.

Rising Compass is the instrument only — no editorial. Editorial surfaces
(curated collections, recommendation picks) moved to consumer sites that read
the compass via API (chadlewine.com, The Illus Caden). These tables are no
longer read or written by any router.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("DROP TABLE IF EXISTS recommendations"))
    conn.execute(text("DROP TABLE IF EXISTS collections"))
