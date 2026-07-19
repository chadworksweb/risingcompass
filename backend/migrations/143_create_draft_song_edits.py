"""Audit trail for hand-corrections to a draft song's credit.

The feeders publish whatever the platform hands them, so a draft row can arrive
credited to an upload channel rather than a performer (a YouTube music channel,
a label account) or carrying a title still wrapped in upload cruft. Correcting
that BEFORE calibration matters: the title + artist pair is what mints the
`songs` row and its canonical key, so a bad credit at that moment becomes a
permanent duplicate identity rather than a display nit.

There was no path to make that correction. `PUT /drafts/{ref}` edits colors and
summaries only, and no script touches title or artist, so the corrections were
made with raw UPDATEs straight against prod. They worked, but nothing recorded
that they happened, what the value had been, or why. Artists have
`artist_admin_events` and songs have `song_merge_events`; the draft layer had
nothing.

This is deliberately NOT folded into `artist_admin_events`. That table is shaped
around an Artist entity (`artist_name_before` / `artist_slug_before` are NOT
NULL, and every row keys to an artists row). A draft recredit has no source
entity at all -- the whole point is that the credited string was never an artist,
just a channel name -- so reusing it would mean inventing slugs for something
that never existed.

Env-tagged like `clutter_audits` and `sentinel_findings`: local dev shares the
prod DB over the tunnel, so a local correction must be filterable out.

Idempotent; PG-compatible (063+). Base.metadata.create_all() builds the table on
fresh installs from the models.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS draft_song_edits (
            id               SERIAL PRIMARY KEY,
            occurred_at      TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
            actor            VARCHAR(64),

            draft_song_id    INTEGER,
            draft_label      TEXT,
            position         INTEGER,

            -- The song row the draft pointed at, when it had one. Nullable and
            -- deliberately NOT a foreign key: the audit must outlive the song.
            song_id          INTEGER,

            title_before     TEXT,
            title_after      TEXT,
            artist_before    TEXT,
            artist_after     TEXT,

            reason           TEXT,
            environment      VARCHAR(16) NOT NULL DEFAULT 'prod'
        )
    """))

    # Query shape is "what happened to this draft" and "what happened lately".
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_draft_song_edits_label "
        "ON draft_song_edits (draft_label)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_draft_song_edits_occurred "
        "ON draft_song_edits (occurred_at DESC)"
    ))
