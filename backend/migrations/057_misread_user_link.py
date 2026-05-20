"""Public Participation Phase 2.1 -- account-link misread_submissions.

Adds user_id FK and relaxes first_name / last_name / email to NULL so
new account-linked submissions can skip them (handle on the linked user
is the display).

Legacy rows already in the table keep their first_name / last_name /
email; their user_id stays NULL. The plan calls this transitional state
'legacy' rather than backfilling -- we'd be guessing at the user
identity and the cost of a wrong mapping is higher than the cost of a
NULL.

device_id stays on the row through this transition so the misread/check
ban endpoint keeps working for legacy anonymous abusers. New submissions
from authenticated users still record device_id alongside user_id.

SQLite can't ALTER COLUMN to drop NOT NULL, so the canonical recreate
pattern is used.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS misread_submissions_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at DATETIME DEFAULT (datetime('now')),
            song_title TEXT NOT NULL,
            song_artist TEXT NOT NULL,
            song_color TEXT NOT NULL,
            song_position INTEGER,
            user_id INTEGER,
            first_name TEXT,
            last_name TEXT,
            email TEXT,
            message TEXT NOT NULL,
            device_id TEXT,
            ip_address TEXT,
            status VARCHAR(20) DEFAULT 'pending',
            report_type VARCHAR(20) NOT NULL DEFAULT 'misread',
            proof_context TEXT,
            song_source VARCHAR(20),
            song_id INTEGER,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """))
    conn.execute(text("""
        INSERT INTO misread_submissions_new (
            id, created_at, song_title, song_artist, song_color, song_position,
            user_id, first_name, last_name, email, message, device_id, ip_address,
            status, report_type, proof_context, song_source, song_id
        )
        SELECT
            id, created_at, song_title, song_artist, song_color, song_position,
            NULL, first_name, last_name, email, message, device_id, ip_address,
            status, report_type, proof_context, song_source, song_id
        FROM misread_submissions
    """))
    conn.execute(text("DROP TABLE misread_submissions"))
    conn.execute(text("ALTER TABLE misread_submissions_new RENAME TO misread_submissions"))

    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_misread_user "
        "ON misread_submissions(user_id, created_at DESC)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_misread_device "
        "ON misread_submissions(device_id, created_at DESC)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_misread_status "
        "ON misread_submissions(status, created_at DESC)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_misread_song_resolved "
        "ON misread_submissions(song_source, song_id, report_type)"
    ))
