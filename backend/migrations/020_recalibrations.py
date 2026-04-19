"""Recalibration suite — proposals (pending AI output before admin acts) and
applied recalibrations (audit log of every change ever made to a song's
calibration).

Both tables key polymorphically into the three song tables via
(song_source, song_id), matching the pattern already used by song_slugs,
release_songs, and the satirical-flag extension to misread_submissions.

recalibration_type covers all current and future trigger frameworks:
  - 'satire'           — AI-only re-read with the satire tenet set
  - 'public_interest'  — manual admin recalibration honoring crowd signal
                         (workflow not yet built; type slot reserved)

Proposals exist as a holding pen between "AI ran" and "admin acted." On
accept, a song_recalibrations row is written with a snapshot of the public
flag/vibe state at that moment. On reject, the proposal stays in the table
with status='rejected' for audit; nothing is written to song_recalibrations.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS song_recalibration_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recalibration_type VARCHAR(20) NOT NULL,
            song_source VARCHAR(20) NOT NULL,
            song_id INTEGER NOT NULL,
            trigger_source VARCHAR(40),
            trigger_ref_id INTEGER,
            original_charge INTEGER,
            original_color VARCHAR(20),
            proposed_charge INTEGER,
            proposed_color VARCHAR(20),
            proposed_summary TEXT,
            ai_rationale TEXT,
            ai_model TEXT,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            review_notes TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            resolved_at DATETIME
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_recal_proposals_song "
        "ON song_recalibration_proposals(song_source, song_id)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_recal_proposals_status "
        "ON song_recalibration_proposals(status, created_at DESC)"
    ))

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS song_recalibrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recalibration_type VARCHAR(20) NOT NULL,
            song_source VARCHAR(20) NOT NULL,
            song_id INTEGER NOT NULL,
            proposal_id INTEGER REFERENCES song_recalibration_proposals(id) ON DELETE SET NULL,
            trigger_source VARCHAR(40),
            trigger_ref_id INTEGER,
            before_charge INTEGER,
            before_color VARCHAR(20),
            after_charge INTEGER NOT NULL,
            after_color VARCHAR(20) NOT NULL,
            ai_rationale TEXT,
            public_summary TEXT NOT NULL,
            internal_notes TEXT,
            flag_count_snapshot TEXT,
            vibe_snapshot TEXT,
            applied_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_recalibrations_song "
        "ON song_recalibrations(song_source, song_id, applied_at DESC)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_recalibrations_type "
        "ON song_recalibrations(recalibration_type, applied_at DESC)"
    ))
