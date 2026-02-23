"""Add M/E/I columns to reading_songs and backfill from agent_draft_songs."""

from sqlalchemy import text


def _column_exists(conn, table: str, column: str) -> bool:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return any(row[1] == column for row in rows)


def up(conn):
    if not _column_exists(conn, "reading_songs", "message_analysis"):
        conn.execute(text("ALTER TABLE reading_songs ADD COLUMN message_analysis TEXT"))
    if not _column_exists(conn, "reading_songs", "expression_analysis"):
        conn.execute(text("ALTER TABLE reading_songs ADD COLUMN expression_analysis TEXT"))
    if not _column_exists(conn, "reading_songs", "intention_analysis"):
        conn.execute(text("ALTER TABLE reading_songs ADD COLUMN intention_analysis TEXT"))

    # Best-effort backfill: join reading_songs → agent_draft_songs via
    # (title, artist, position) within the same date to recover M/E/I
    conn.execute(text("""
        UPDATE reading_songs
        SET message_analysis = (
            SELECT ads.message_analysis
            FROM agent_draft_songs ads
            JOIN agent_drafts ad ON ads.draft_id = ad.id
            JOIN daily_readings dr ON dr.id = reading_songs.reading_id
            WHERE lower(ads.title) = lower(reading_songs.title)
              AND lower(ads.artist) = lower(reading_songs.artist)
              AND ads.position = reading_songs.position
              AND ad.date = dr.date
              AND ads.message_analysis IS NOT NULL
            LIMIT 1
        ),
        expression_analysis = (
            SELECT ads.expression_analysis
            FROM agent_draft_songs ads
            JOIN agent_drafts ad ON ads.draft_id = ad.id
            JOIN daily_readings dr ON dr.id = reading_songs.reading_id
            WHERE lower(ads.title) = lower(reading_songs.title)
              AND lower(ads.artist) = lower(reading_songs.artist)
              AND ads.position = reading_songs.position
              AND ad.date = dr.date
              AND ads.expression_analysis IS NOT NULL
            LIMIT 1
        ),
        intention_analysis = (
            SELECT ads.intention_analysis
            FROM agent_draft_songs ads
            JOIN agent_drafts ad ON ads.draft_id = ad.id
            JOIN daily_readings dr ON dr.id = reading_songs.reading_id
            WHERE lower(ads.title) = lower(reading_songs.title)
              AND lower(ads.artist) = lower(reading_songs.artist)
              AND ads.position = reading_songs.position
              AND ad.date = dr.date
              AND ads.intention_analysis IS NOT NULL
            LIMIT 1
        )
        WHERE message_analysis IS NULL
    """))
