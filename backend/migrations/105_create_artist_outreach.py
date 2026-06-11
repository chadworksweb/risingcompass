"""Create artist_outreach -- the manual outreach-touch ledger for the Artist
CRM (Hockey Stick Build 8).

One row per outbound touch Chad logs by hand: which artist, which song's charge
was sent, on what channel, and WHEN. Single-song outreach only for now (no
album/catalog aggregate). Separate from the inbound
`artist_verification_inquiries` and from the `artist_verifications` funnel
record -- this is the "what did we send, which song, and when" history. Hangs
off the artist, so a touch can be logged before/without a funnel row.

PG-compatible (063+). create_all() handles fresh installs; this is the
idempotent prod path.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS artist_outreach (
            id SERIAL PRIMARY KEY,
            artist_id INTEGER NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
            song_id INTEGER REFERENCES songs(id) ON DELETE SET NULL,
            song_title TEXT,
            channel VARCHAR(20) NOT NULL DEFAULT 'email',
            contact_used TEXT,
            sent_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            notes TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_artist_outreach_artist_sent "
        "ON artist_outreach(artist_id, sent_at)"
    ))
