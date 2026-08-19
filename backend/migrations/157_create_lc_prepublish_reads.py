"""The Lyrical Charger prepublish log: a reading held, shown, and not yet committed.

WHY HOLD AT ALL. A public reader who gets a wrong reading has one move today --
file a misread report and wait for an admin. The rung that actually works is the
one from terminal: one flat reply pointing at what the read missed, one re-read,
done. This table is what makes that rung possible without the corrected reading
having to overwrite a published one.

PREPUBLISH RATHER THAN OVERWRITE. The first design published the reading and
superseded it on contest. That leaks in two places, because a superseded run is
deliberately NOT invisible: the song page runs timeline shows superseded rows as
history (recalibrations.py, correct for a rubric_update supersede), and _most_run
in charger_activity counts every logged run including superseded ones, so a
contested song would rank twice on "most calibrated". Holding instead means there
is no first row to hide. If the reader takes the second reading, the first never
entered the ledger at all.

  status  -- held        : shown to the reader, awaiting accept / contest / sweep
             publishing  : TRANSIENT. One caller has claimed this row and is
                           running the publish writes. The claim is what makes
                           the reader's accept and the TTL sweep safe to race:
                           the UPDATE that sets it is guarded on 'held', so
                           exactly one of them wins. A row still here after
                           lc_publish.PUBLISHING_STALE is a process that died
                           mid-write, and the sweep returns it to 'held'.
             published   : committed to the library (the ONLY status the public
                           surfaces ever see)
             contested   : this reading drew an objection and a re-read replaced
                           it. Terminal. It is never published, and its successor
                           carries contest_of_id back to it.
             declined    : the reader rejected this reading and it escalated to
                           misread_submissions. Terminal, never published.
             discarded   : closed without publishing (cleanup / abandoned error).

NO LYRICS HERE, EVER. "Lyrics text is never stored, anywhere" is a hard legal
constraint that predates this feature (LC-LYRICS-GUARDS.md). A contested re-read
needs the lyrics again, and it gets them by having the READER PASTE THEM AGAIN --
charger.js clears the lyrics input the moment a reading lands, so the text is
already gone from the page and there is nothing to silently resend. What this
table keeps is lyrics_fingerprint, the same one-way MinHash the Layer 2
divergence guard uses, so the re-pasted text can be checked (max_jaccard against
DIVERGENCE_THRESHOLD) as the same song without the song itself being retained.
Near-identical re-pastes pass; another song's lyrics fail.

contest_of_id -- set on the RE-READ row, pointing back at the reading that drew
the objection, together with the axis and the reader's pointer. Both readings
survive as rows whether or not the tier moved, and that is the point: contests
where the tier did NOT move, filed repeatedly on the same axis against the same
song, are a rubric-gap detector and feed the consensus_drift pipeline.

The reader's objection is stored, but their proposed VERDICT never is, because
the guard rejects the contest outright if it contains one. See the contest guard
in services/contest_guard.py -- axes are a closed set and tier language is a hard
fail, the same way deadpan_guard fails a bad line rather than repairing it.

Env-tagged like clutter_audits, song_cover_art_reports and Faultline: local dev
shares the prod DB through the tunnel, so the sweep and the admin queue must
filter by environment or a local run publishes prod rows.

create_all() builds this from the LcPrepublishRead model on fresh installs; this
migration is the explicit, idempotent add for existing databases. PG-compatible.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS lc_prepublish_reads (
            id                SERIAL PRIMARY KEY,

            -- The reader's handle on this held reading. Ties back to the
            -- calibrate_jobs row that produced it; not unique, because a
            -- contested job owns two rows (the original and the re-read).
            job_token         VARCHAR(64) NOT NULL,

            -- Set on the RE-READ only: the reading this one was contested
            -- against. NULL on a first reading.
            contest_of_id     INTEGER REFERENCES lc_prepublish_reads(id) ON DELETE CASCADE,

            title             TEXT NOT NULL,
            artist            TEXT NOT NULL,
            source            VARCHAR(50),

            -- One-way MinHash. Never the lyrics.
            lyrics_fingerprint BYTEA,

            -- The full calibration dict, held unpublished, plus the
            -- LyricsCalibrateOut payload exactly as the reader saw it.
            calibration_json  TEXT NOT NULL,
            result_json       TEXT,

            user_id           INTEGER REFERENCES users(id),
            device_id         TEXT,
            ip_address        TEXT,

            status            VARCHAR(20) NOT NULL DEFAULT 'held',

            -- Populated on the re-read row only. contest_note is the reader's
            -- POINTER at a line, never a proposed tier (guard-enforced).
            contest_axis      VARCHAR(40),
            contest_note      TEXT,
            tier_moved        BOOLEAN,

            -- Set when status becomes 'published'.
            published_song_id INTEGER,
            published_at      TIMESTAMPTZ,

            environment       VARCHAR(10) NOT NULL DEFAULT 'prod',
            created_at        TIMESTAMPTZ DEFAULT now(),
            updated_at        TIMESTAMPTZ DEFAULT now()
        )
    """))

    # The reader's accept/contest/decline calls all arrive by token.
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_lc_prepublish_job_token "
        "ON lc_prepublish_reads (job_token)"
    ))

    # The TTL sweep scans held rows by age within one environment. Partial
    # index: published rows are the overwhelming majority over time and the
    # sweep never looks at them.
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_lc_prepublish_sweep "
        "ON lc_prepublish_reads (environment, created_at) "
        "WHERE status = 'held'"
    ))

    # The gap detector reads contests by axis per song identity.
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_lc_prepublish_contest "
        "ON lc_prepublish_reads (contest_of_id) "
        "WHERE contest_of_id IS NOT NULL"
    ))
