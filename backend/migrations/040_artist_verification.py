"""Artist Verified — three new tables.

artist_verifications: one-to-one with artists. Tracks the funnel stage
(lead -> contacted -> in_conversation -> active), the chosen verification
method (in_person / video_call / audio_call / prior_relationship), and a
five-item deepfake-busting checklist that gates the transition to
'active' when method = video_call. Conversation log is admin free text.

artist_verification_blocks: one row per (artist, song). Curated content
the artist provided about a song -- text plus optional video and audio
URLs. published flag drives whether the block + Artist Verified badge
appears on the public song page. Polymorphic via (song_source, song_id).

artist_verification_inquiries: inbound 'are you the artist?' submissions
from the song page. Mirrors the misread_submissions pattern (device_id,
ip_address, status). Triage flow: dismiss, mark contacted, or promote
to a funnel entry on an existing artist record (sets artist_id link).
"""

from sqlalchemy import text


def up(conn):
    # --- artist_verifications ---
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS artist_verifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artist_id INTEGER NOT NULL UNIQUE,
            funnel_stage VARCHAR(30) NOT NULL DEFAULT 'lead',
            verification_method VARCHAR(30),
            contact_email TEXT,
            contact_phone TEXT,
            contact_handle TEXT,
            conversation_log TEXT,
            notes TEXT,
            deepfake_live_challenge_passed BOOLEAN NOT NULL DEFAULT 0,
            deepfake_cross_channel_confirmed BOOLEAN NOT NULL DEFAULT 0,
            deepfake_two_sessions_completed BOOLEAN NOT NULL DEFAULT 0,
            deepfake_reference_match_confirmed BOOLEAN NOT NULL DEFAULT 0,
            deepfake_recording_archived BOOLEAN NOT NULL DEFAULT 0,
            deepfake_recording_url TEXT,
            contacted_at DATETIME,
            verified_at DATETIME,
            created_at DATETIME NOT NULL DEFAULT (datetime('now')),
            updated_at DATETIME NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (artist_id) REFERENCES artists(id) ON DELETE CASCADE
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_artist_verifications_stage "
        "ON artist_verifications(funnel_stage)"
    ))

    # --- artist_verification_blocks ---
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS artist_verification_blocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artist_id INTEGER NOT NULL,
            song_source VARCHAR(20) NOT NULL,
            song_id INTEGER NOT NULL,
            block_text TEXT,
            video_url TEXT,
            audio_url TEXT,
            published BOOLEAN NOT NULL DEFAULT 0,
            published_at DATETIME,
            internal_notes TEXT,
            created_at DATETIME NOT NULL DEFAULT (datetime('now')),
            updated_at DATETIME NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (artist_id) REFERENCES artists(id) ON DELETE CASCADE,
            UNIQUE(artist_id, song_source, song_id)
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_av_blocks_song "
        "ON artist_verification_blocks(song_source, song_id, published)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_av_blocks_artist "
        "ON artist_verification_blocks(artist_id)"
    ))

    # --- artist_verification_inquiries ---
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS artist_verification_inquiries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at DATETIME NOT NULL DEFAULT (datetime('now')),
            song_title TEXT NOT NULL,
            song_artist TEXT NOT NULL,
            song_source VARCHAR(20),
            song_id INTEGER,
            song_color VARCHAR(20),
            song_position INTEGER,
            claimant_name TEXT NOT NULL,
            claimant_email TEXT NOT NULL,
            claimant_role VARCHAR(40),
            proof_links TEXT,
            message TEXT NOT NULL,
            device_id TEXT,
            ip_address TEXT,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            artist_id INTEGER,
            FOREIGN KEY (artist_id) REFERENCES artists(id) ON DELETE SET NULL
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_av_inquiries_status "
        "ON artist_verification_inquiries(status, created_at DESC)"
    ))
