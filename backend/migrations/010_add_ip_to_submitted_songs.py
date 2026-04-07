"""Add ip_address column to submitted_songs for abuse detection."""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        ALTER TABLE submitted_songs ADD COLUMN ip_address VARCHAR(45)
    """))
