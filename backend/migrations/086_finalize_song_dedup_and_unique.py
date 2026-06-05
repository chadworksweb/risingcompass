"""Unified song-entity renovation -- Phase 5c-1 (finalize dedup + UNIQUE).

Runs AFTER the 5b full cutover held across a daily reading cycle. Two safe,
NO-code-change steps (the live writers already maintain these invariants):

1. Collapse the duplicate reference rows the 22 merged songs left behind. These
   are LEGACY-keyed rows only -- the dedup joins `song_id_map`, so native
   post-cutover rows (which carry `unified_song_id` directly, with no legacy
   (song_source, song_id) map entry) are never touched. This is the same logic
   as scripts/unify_songs.py:_finalize_dedup, inlined here so the migration is a
   self-contained, immutable artifact.
     - audience_vibe_needles: COMBINE (sum push totals onto the kept row, then
       drop the extras).
     - user_calibrations / song_artists / audience_vibe_pushes /
       artist_verification_blocks: keep one row per (new_song_id, *extra).

2. Backfill straggler calibration_runs.unified_song_id from song_id_map. (The
   ~10 NULL-uid runs that map to nothing are dead orphans on deleted rows and
   stay NULL by design.)

3. Add the UNIQUE constraints on unified_song_id, mirroring each table's old
   (song_source, song_id, *extra) UNIQUE with the legacy pair replaced by
   unified_song_id. unified_song_id is NULLable; in Postgres NULLs are distinct,
   so unmapped/dead rows never collide.

Validated on prod via a transaction-rollback dry-run (2026-06-05): only 8
song_artists duplicate credits existed; after dedup every composite key was
unique and all five constraints added cleanly. The legacy (song_source, song_id)
columns + their old constraints are dropped in Phase 5d; the unified_song_id ->
song_id rename is Phase 5c-2.

FROZEN -- not touched: prose_provenance_anchors. PG-compatible; retry-safe
(single migration transaction + DROP CONSTRAINT IF EXISTS before each ADD).
"""

from sqlalchemy import text

# (table, [extra key cols besides new_song_id], order-keep within a dup group)
_COLLISION_SPECS = [
    ("user_calibrations", ["user_id"], "calibrated_at"),
    ("song_artists", ["artist_id"], "id"),
    ("audience_vibe_pushes", ["device_id", "push_year"], "id"),
    ("artist_verification_blocks", ["artist_id"], "id"),
]

# (table, constraint_name, [unique columns]) -- mirrors the old per-table UNIQUE
# with (song_source, song_id) collapsed to unified_song_id.
_UNIQUE_CONSTRAINTS = [
    ("user_calibrations", "uq_user_calibration_song_uid", ["unified_song_id", "user_id"]),
    ("song_artists", "uq_song_artists_uid_artist", ["unified_song_id", "artist_id"]),
    ("audience_vibe_needles", "uq_vibe_needles_uid", ["unified_song_id"]),
    ("audience_vibe_pushes", "uq_vibe_pushes_uid_device_year",
     ["unified_song_id", "device_id", "push_year"]),
    ("artist_verification_blocks", "uq_av_blocks_artist_uid", ["artist_id", "unified_song_id"]),
]


def up(conn):
    # 1a. audience_vibe_needles -- COMBINE colliding needles onto the kept row.
    conn.execute(text("""
        WITH grp AS (
            SELECT m.new_song_id AS sid,
                   sum(n.pushes_up_total) AS up, sum(n.pushes_down_total) AS dn,
                   sum(n.pushes_agree_total) AS ag, max(n.last_push_at) AS lp,
                   min(n.id) AS keep_id, count(*) AS c
            FROM audience_vibe_needles n
            JOIN song_id_map m ON m.old_source = n.song_source AND m.old_id = n.song_id
            GROUP BY m.new_song_id
        )
        UPDATE audience_vibe_needles n SET
            pushes_up_total = g.up, pushes_down_total = g.dn,
            pushes_agree_total = g.ag, last_push_at = g.lp
        FROM grp g WHERE n.id = g.keep_id AND g.c > 1
    """))
    conn.execute(text("""
        DELETE FROM audience_vibe_needles n USING (
            SELECT n2.id,
                   row_number() OVER (PARTITION BY m.new_song_id ORDER BY n2.id) AS rn
            FROM audience_vibe_needles n2
            JOIN song_id_map m ON m.old_source = n2.song_source AND m.old_id = n2.song_id
        ) d WHERE n.id = d.id AND d.rn > 1
    """))

    # 1b. generic collision dedupe: keep one legacy-keyed row per (new_song_id, *extra).
    for table, extra, keep in _COLLISION_SPECS:
        part = ", ".join(["m.new_song_id"] + ["x.%s" % e for e in extra])
        order = "x.calibrated_at DESC NULLS LAST, x.id" if keep == "calibrated_at" else "x.id"
        conn.execute(text("""
            DELETE FROM {t} z USING (
                SELECT x.id,
                       row_number() OVER (PARTITION BY {part} ORDER BY {order}) AS rn
                FROM {t} x
                JOIN song_id_map m ON m.old_source = x.song_source AND m.old_id = x.song_id
            ) d WHERE z.id = d.id AND d.rn > 1
        """.format(t=table, part=part, order=order)))

    # 2. backfill straggler calibration_runs.unified_song_id (dead orphans stay NULL).
    conn.execute(text("""
        UPDATE calibration_runs r SET unified_song_id = m.new_song_id
        FROM song_id_map m
        WHERE r.unified_song_id IS NULL
          AND r.song_source = m.old_source AND r.song_id = m.old_id
    """))

    # 3. add the UNIQUE constraints (DROP IF EXISTS first -> retry-safe).
    for table, name, cols in _UNIQUE_CONSTRAINTS:
        conn.execute(text("ALTER TABLE %s DROP CONSTRAINT IF EXISTS %s" % (table, name)))
        conn.execute(text(
            "ALTER TABLE %s ADD CONSTRAINT %s UNIQUE (%s)" % (table, name, ", ".join(cols))))
