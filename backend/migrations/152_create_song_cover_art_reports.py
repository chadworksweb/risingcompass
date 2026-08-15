"""Reader-filed reports that a song page is showing the wrong cover art.

WHY A REPORT PATH AT ALL. Two automated checks now stand between a song and a
wrong cover: the artist-credit check in `musicbrainz._pick_release_group` catches
a wrong ARTIST, and `scripts/audit_song_cover_art.py` catches a right artist on a
release issued years after the song charted. Neither can catch the remaining
case -- a right artist on a contemporaneous but wrong release, a 1990 single
carrying a 1990 album track's art. Nothing in the data distinguishes that from a
correct pick. A person looking at the page does it instantly.

So the last check is the reader, and the report is what carries what they saw
back. Filing takes no account: wrong art is a factual claim anyone can see, and a
sign-in wall would drop nearly all of the signal for an attribution nobody needs.
Honeypot + per-IP rate limit + one report per device per song per MBID are the
abuse controls, and they are enough because a report CHANGES NOTHING on its own.

  reported_mbid  -- the release group that was serving the art WHEN THE REPORT
                    WAS FILED, not the song's current one. A re-resolve between
                    filing and review would otherwise make the report look like a
                    complaint about art nobody ever saw, and the admin would clear
                    a pick that may well be correct. It is also the rejection key
                    (below), which only means anything pinned to a specific pick.

  status         -- open | confirmed | dismissed. `confirmed` is the durable
                    record that THIS release group is wrong for THIS song.

THE CONFIRMED ROWS ARE THE REJECTION LIST. On confirm, the song's
release_group_mbid + release_group_date are cleared while release_group_checked_at
is deliberately LEFT SET -- the codified "recorded miss", so the backfill skips
the song and the wrong art stays gone rather than being re-picked on the next run.
A `--recheck-misses` pass would re-search it, which is exactly when the rejection
matters: the backfill loads the confirmed (song_id, reported_mbid) pairs and
excludes them from the candidate scan, so a deliberate retry can find a better
release but can never land back on the one a reader already rejected. Deriving
that list from the reports rather than storing it twice means the two can't drift.
This is the same lesson as `release_suppressions` (migration 147): a correction
made by hand does not survive the next automated pass unless something durable
records it.

Matched on MBID here, unlike release_suppressions' normalised title, because the
claim is genuinely about the identifier: "this release group's cover is not this
song's cover". If MB re-files the group under a new id, re-resolving to it and
being told again is the correct outcome.

Env-tagged like `clutter_audits` and Faultline -- local dev shares the prod DB
through the tunnel, so the admin queue must filter by environment.

create_all() builds this from the SongCoverArtReport model on fresh installs;
this migration is the explicit, idempotent add for existing databases.
PG-compatible.
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS song_cover_art_reports (
            id             SERIAL PRIMARY KEY,
            song_id        INTEGER NOT NULL REFERENCES songs(id) ON DELETE CASCADE,

            -- The pick being complained about, and where it came from: 'song'
            -- (songs.release_group_mbid) or 'release' (the linked Release's
            -- MBID). They are fixed in different places, so the queue has to
            -- say which -- clearing the song column does nothing to art the
            -- song inherits from its release.
            reported_mbid  TEXT,
            mbid_source    VARCHAR(10),

            note           TEXT,
            device_id      TEXT,
            ip_address     TEXT,

            status         VARCHAR(12) NOT NULL DEFAULT 'open',
            environment    VARCHAR(10) NOT NULL DEFAULT 'prod',

            created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            resolved_at    TIMESTAMP,
            resolved_by    VARCHAR(120),
            resolution_note TEXT
        )
    """))

    # The queue's own shape: open reports, newest first, one environment.
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_cover_art_reports_queue "
        "ON song_cover_art_reports (environment, status, created_at DESC)"
    ))
    # Both the per-song count on the queue and the backfill's rejection lookup
    # read by song.
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_cover_art_reports_song "
        "ON song_cover_art_reports (song_id)"
    ))
    # One report per device per song per pick. Scoped to the MBID rather than the
    # song so that a re-resolve to different art can be reported again by the
    # same person -- it is a new claim about a new picture. Devices that send no
    # id are left to the rate limiter.
    conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_cover_art_report_device "
        "ON song_cover_art_reports (song_id, device_id, reported_mbid) "
        "WHERE device_id IS NOT NULL"
    ))
