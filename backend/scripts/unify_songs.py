"""Unified song-entity renovation -- Phase 2 backfill.

Collapses the four legacy song tables (compass_songs, library_songs,
submitted_songs, cl_stream_songs) into the unified `songs` table, builds
`chart_appearances` + `song_ingestions`, the `song_id_map`, and repoints every
song reference. Idempotent + single-transaction.

Modes:
  python -m scripts.unify_songs                 # DRY RUN (default): report only, no writes
  python -m scripts.unify_songs --apply --yes   # APPLY: reset + rebuild in one txn, verify, commit

Authoritative-first calibration wins (chart_reading/editorial beat crowd
lyrical_charger/stream); canonical identity from app.services.song_identity.
See RISING-COMPASS-SONG-ENTITY-RENOVATION.md. NEVER touches prose_provenance_anchors.
"""

import argparse
import json
from collections import defaultdict
from datetime import datetime

from sqlalchemy import text

from app.database import engine
from app.services.song_identity import compute_canonical_key
from app.constants import CHART_SOURCE_TO_CHART_SLUG, NON_CHART_SOURCES

# precedence order: compass > library > submitted > stream
SOURCES = ["compass", "library", "submitted", "stream"]
SOURCE_TABLE = {
    "compass": "compass_songs", "library": "library_songs",
    "submitted": "submitted_songs", "stream": "cl_stream_songs",
}
SOURCE_PRECEDENCE = {"compass": 3, "library": 2, "submitted": 1, "stream": 0}
AUTH_SOURCES = {"compass", "library"}
METHOD_BY_SOURCE = {
    "compass": "chart_reading", "library": "editorial",
    "submitted": "lyrical_charger", "stream": "stream",
}
ENRICH_FIELDS = [
    "charge_value", "charge_summary", "effects_prose", "societal_effects_prose",
    "topics", "deadpan_line", "message_analysis", "expression_analysis",
    "intention_analysis",
]
CALIB_COLS = [
    "rubric_color", "charge_value", "charge_summary", "contaminated",
    "contamination_note", "dogma_referenced", "dogma_note", "instrumental",
    "confidence", "effects_prose", "societal_effects_prose",
    "societal_prose_generated_at", "societal_prose_model", "prior_effects_prose",
    "prior_societal_effects_prose", "prior_societal_prose_generated_at",
    "prior_societal_prose_model", "deadpan_line", "topics", "topic_audit",
    "activations", "calibration_failed", "message_analysis",
    "expression_analysis", "intention_analysis",
]

# Standard polymorphic (song_source, song_id) tables -> unified_song_id.
POLY_STD = [
    "user_calibrations", "song_artists", "release_songs", "song_slugs",
    "calibration_runs", "audience_vibe_needles", "audience_vibe_pushes",
    "audience_vibe_review_cases", "misread_submissions", "song_recalibrations",
    "song_recalibration_proposals", "song_resets", "artist_verification_blocks",
    "artist_verification_inquiries",
]
# Hard-FK / specially-named legacy pointers -> new song_id (source is fixed).
HARD = [
    ("reading_songs", "compass_song_id", "compass", "song_id"),
    ("agent_draft_songs", "compass_song_id", "compass", "song_id"),
    ("pre_publish_corrections", "compass_song_id", "compass", "song_id"),
    ("lc_events", "submission_id", "submitted", "song_id"),
]
# UNIQUE-collapse tables: when two source rows merge, these collide and must be
# deduped/combined BEFORE the repoint UPDATE.
COLLISION_SPECS = [
    {"table": "user_calibrations", "extra": ["user_id"], "keep": "calibrated_at"},
    {"table": "song_artists", "extra": ["artist_id"], "keep": "id"},
    {"table": "audience_vibe_pushes", "extra": ["device_id", "push_year"], "keep": "id"},
    {"table": "artist_verification_blocks", "extra": ["artist_id"], "keep": "id"},
    # audience_vibe_needles is COMBINEd (summed), handled separately.
]


# --------------------------------------------------------------------------- #
# Loading + grouping
# --------------------------------------------------------------------------- #
def load_sources(conn):
    data = {}
    for src in SOURCES:
        rows = conn.execute(text(f"SELECT * FROM {SOURCE_TABLE[src]}")).mappings().all()
        data[src] = [dict(r) for r in rows]
    return data


def build_groups(data):
    groups = defaultdict(list)
    for src in SOURCES:
        for row in data[src]:
            key = compute_canonical_key(row.get("title"), row.get("artist"))
            groups[key].append({"source": src, "id": row["id"], "row": row, "key": key})
    return groups


def enrichment_score(row):
    return sum(1 for f in ENRICH_FIELDS if row.get(f) not in (None, "", "[]", []))


def member_sort_key(m):
    row = m["row"]
    auth = 2 if m["source"] in AUTH_SOURCES else 1
    gen = row.get("societal_prose_generated_at") or datetime.min
    return (auth, enrichment_score(row), gen, SOURCE_PRECEDENCE[m["source"]], -m["id"])


def pick_winner(members):
    return max(members, key=member_sort_key)


def classify(members):
    if len(members) == 1:
        return "single"
    if all(m["source"] == "compass" for m in members):
        return "cross_year"
    return "cross_table"


def divergent_artist(members):
    arts = {(m["row"].get("artist") or "").strip().lower() for m in members}
    return len(arts) > 1


def get_created(row, source):
    return row.get("submitted_at") if source == "submitted" else row.get("created_at")


# --------------------------------------------------------------------------- #
# Dry-run report
# --------------------------------------------------------------------------- #
def report(conn, data, groups):
    src_counts = {s: len(data[s]) for s in SOURCES}
    total_members = sum(src_counts.values())
    merge_groups = {k: ms for k, ms in groups.items() if len(ms) > 1}
    cross_year = {k: ms for k, ms in merge_groups.items() if classify(ms) == "cross_year"}
    cross_table = {k: ms for k, ms in merge_groups.items() if classify(ms) == "cross_table"}
    divergent = {k: ms for k, ms in merge_groups.items() if divergent_artist(ms)}

    print("=" * 78)
    print("UNIFY SONGS -- DRY RUN MERGE REPORT")
    print("=" * 78)
    print(f"\nSource rows: " + ", ".join(f"{s}={src_counts[s]}" for s in SOURCES)
          + f"  (total {total_members})")
    print(f"Unified songs (canonical groups): {len(groups)}")
    print(f"Rows eliminated by merge: {total_members - len(groups)}")
    print(f"  merge groups: {len(merge_groups)}  "
          f"(cross_year={len(cross_year)}, cross_table={len(cross_table)})")

    print(f"\n--- CROSS-YEAR groups ({len(cross_year)}) -- expect 11 ---")
    for k, ms in sorted(cross_year.items()):
        w = pick_winner(ms)
        mem = ", ".join(f"{m['source']}:{m['id']}(y{m['row'].get('year')})" for m in ms)
        print(f"  {ms[0]['row'].get('title')[:36]:38} | {mem} -> WIN {w['source']}:{w['id']}")

    print(f"\n--- CROSS-TABLE groups ({len(cross_table)}) ---")
    for k, ms in sorted(cross_table.items()):
        w = pick_winner(ms)
        mem = ", ".join(f"{m['source']}:{m['id']}" for m in ms)
        t = (ms[0]['row'].get('title') or '?')[:32]
        a = (ms[0]['row'].get('artist') or '?')[:22]
        print(f"  {t:34} | {a:24} | {mem} -> WIN {w['source']}:{w['id']} "
              f"(method={METHOD_BY_SOURCE[w['source']]})")

    print(f"\n--- *** DIVERGENT-ARTIST merge groups ({len(divergent)}) -- MANUAL REVIEW *** ---")
    if not divergent:
        print("  (none -- no merge collapses rows with differing artist strings)")
    for k, ms in sorted(divergent.items()):
        print(f"  title={ms[0]['row'].get('title')!r}")
        for m in ms:
            print(f"      {m['source']}:{m['id']}  artist={m['row'].get('artist')!r}")

    # chart_appearances preview
    ap_by_chart = defaultdict(int)
    non_chart = defaultdict(int)
    for ms in groups.values():
        for m in ms:
            if m["source"] != "compass":
                continue
            cs = m["row"].get("chart_source") or "billboard_hot_100"
            slug = CHART_SOURCE_TO_CHART_SLUG.get(cs)
            if slug:
                ap_by_chart[slug] += 1
            else:
                non_chart[cs] += 1
    print(f"\n--- chart_appearances to build: {sum(ap_by_chart.values())} ---")
    for slug, n in sorted(ap_by_chart.items(), key=lambda x: -x[1]):
        print(f"    {slug:28} {n}")
    if non_chart:
        print(f"  non-chart compass rows (NO appearance): {sum(non_chart.values())}")
        for cs, n in sorted(non_chart.items()):
            print(f"    {cs:28} {n}")

    # song_ingestions preview
    print(f"\n--- song_ingestions to build (1 per source row) ---")
    ing = defaultdict(int)
    for ms in groups.values():
        for m in ms:
            ing[METHOD_BY_SOURCE[m["source"]]] += 1
    for meth, n in sorted(ing.items(), key=lambda x: -x[1]):
        print(f"    {meth:20} {n}")

    # reference repoint preview + collision detection
    key_of = {}  # (source, id) -> canonical_key
    for k, ms in groups.items():
        for m in ms:
            key_of[(m["source"], m["id"])] = k
    print(f"\n--- reference repoint preview ---")
    _repoint_preview(conn, key_of)
    _collision_preview(conn, key_of)
    print("\n" + "=" * 78)
    print("END DRY RUN -- no changes written.")
    print("=" * 78)


def _repoint_preview(conn, key_of):
    # standard polymorphic tables
    for t in POLY_STD:
        rows = conn.execute(text(
            f"SELECT song_source, song_id FROM {t} WHERE song_source IS NOT NULL"
        )).fetchall()
        mapped = sum(1 for r in rows if (r[0], r[1]) in key_of)
        unmapped = len(rows) - mapped
        extra = f" (UNMAPPED/dangling: {unmapped})" if unmapped else ""
        print(f"    {t:32} repoint {mapped}/{len(rows)}{extra}")
    # backfill_job_rows
    rows = conn.execute(text(
        "SELECT result_song_source, result_song_id FROM backfill_job_rows "
        "WHERE result_song_id IS NOT NULL")).fetchall()
    print(f"    {'backfill_job_rows':32} repoint {sum(1 for r in rows if (r[0],r[1]) in key_of)}/{len(rows)}")
    # comments (song targets only)
    rows = conn.execute(text(
        "SELECT target_source, target_id FROM comments WHERE target_type='song'")).fetchall()
    print(f"    {'comments(song)':32} repoint {sum(1 for r in rows if (r[0],r[1]) in key_of)}/{len(rows)}")
    # hard-FK tables
    for t, idcol, src, _new in HARD:
        rows = conn.execute(text(f"SELECT {idcol} FROM {t} WHERE {idcol} IS NOT NULL")).fetchall()
        mapped = sum(1 for r in rows if (src, r[0]) in key_of)
        print(f"    {t+'.'+idcol:32} repoint {mapped}/{len(rows)} (src={src})")


def _collision_preview(conn, key_of):
    print(f"\n--- UNIQUE-collapse collisions (rows to dedupe/combine on merge) ---")
    for spec in COLLISION_SPECS:
        t, extra = spec["table"], spec["extra"]
        cols = ", ".join(["song_source", "song_id"] + extra)
        rows = conn.execute(text(f"SELECT {cols} FROM {t} WHERE song_source IS NOT NULL")).fetchall()
        seen = defaultdict(int)
        for r in rows:
            key = key_of.get((r[0], r[1]))
            if key is None:
                continue
            seen[(key,) + tuple(r[2:])] += 1
        dupes = sum(v - 1 for v in seen.values() if v > 1)
        coll_groups = sum(1 for v in seen.values() if v > 1)
        print(f"    {t:30} collisions: {coll_groups} groups, {dupes} rows to remove "
              f"(keep by {spec['keep']})")
    # audience_vibe_needles -- COMBINE
    rows = conn.execute(text(
        "SELECT song_source, song_id FROM audience_vibe_needles WHERE song_source IS NOT NULL"
    )).fetchall()
    seen = defaultdict(int)
    for r in rows:
        k = key_of.get((r[0], r[1]))
        if k:
            seen[k] += 1
    comb = sum(1 for v in seen.values() if v > 1)
    print(f"    {'audience_vibe_needles':30} COMBINE: {comb} needle groups to merge (sum totals)")


# --------------------------------------------------------------------------- #
# Apply (idempotent, single transaction)
# --------------------------------------------------------------------------- #
def apply(conn, data, groups):
    print("APPLY: resetting unified tables + repoint pointers...")
    # 1. reset (idempotent): null pointers first (remove FK refs), then clear.
    for t in POLY_STD + ["backfill_job_rows", "comments"]:
        conn.execute(text(f"UPDATE {t} SET unified_song_id = NULL"))
    for t, _idcol, _src, newcol in HARD:
        conn.execute(text(f"UPDATE {t} SET {newcol} = NULL"))
    conn.execute(text("DELETE FROM song_id_map"))
    conn.execute(text("DELETE FROM chart_appearances"))
    conn.execute(text("DELETE FROM song_ingestions"))
    conn.execute(text("DELETE FROM songs"))

    # chart slug -> id
    chart_id = {r[0]: r[1] for r in conn.execute(text("SELECT slug, id FROM charts")).fetchall()}

    # 2-4. build songs + map
    new_id_of = {}  # (source, id) -> new_song_id
    key_to_song = {}
    for key, members in groups.items():
        w = pick_winner(members)
        wr = w["row"]
        # album linkage from any library member
        album_id = track_number = None
        for m in members:
            if m["source"] == "library":
                album_id = m["row"].get("album_id")
                track_number = m["row"].get("track_number")
                break
        created = min((get_created(m["row"], m["source"]) or datetime.utcnow()) for m in members)
        vals = {c: wr.get(c) for c in CALIB_COLS}
        vals.update({
            "title": wr.get("title"), "artist": wr.get("artist"), "canonical_key": key,
            "album_id": album_id, "track_number": track_number,
            "canonical_calibration_method": METHOD_BY_SOURCE[w["source"]],
            "created_at": created,
        })
        cols = list(vals.keys())
        placeholders = ", ".join(f":{c}" for c in cols)
        sid = conn.execute(
            text(f"INSERT INTO songs ({', '.join(cols)}) VALUES ({placeholders}) RETURNING id"),
            vals,
        ).scalar()
        key_to_song[key] = sid
        for m in members:
            new_id_of[(m["source"], m["id"])] = sid
            conn.execute(text(
                "INSERT INTO song_id_map (old_source, old_id, new_song_id, canonical_key) "
                "VALUES (:s, :i, :n, :k)"),
                {"s": m["source"], "i": m["id"], "n": sid, "k": key})

    # 5. chart_appearances (compass members)
    ap = 0
    for key, members in groups.items():
        sid = key_to_song[key]
        for m in members:
            if m["source"] != "compass":
                continue
            r = m["row"]
            cs = r.get("chart_source") or "billboard_hot_100"
            slug = CHART_SOURCE_TO_CHART_SLUG.get(cs)
            if not slug:
                continue
            conn.execute(text(
                "INSERT INTO chart_appearances (song_id, chart_id, year, position, position_letter) "
                "VALUES (:sid, :cid, :yr, :pos, :pl) ON CONFLICT DO NOTHING"),
                {"sid": sid, "cid": chart_id[slug], "yr": r.get("year"),
                 "pos": r.get("chart_position"), "pl": r.get("chart_position_letter") or ""})
            ap += 1

    # 6. song_ingestions (1 per source member)
    for key, members in groups.items():
        sid = key_to_song[key]
        for m in members:
            r, src = m["row"], m["source"]
            detail = {}
            if src == "stream":
                detail = {"source_url": r.get("source_url"), "platform": r.get("source_platform"),
                          "status": r.get("status"), "promoted_to": r.get("promoted_to")}
            elif src == "submitted":
                detail = {"source": r.get("source")}
            elif src == "compass":
                detail = {"chart_source": r.get("chart_source")}
            elif src == "library":
                detail = {"source": r.get("source")}
            conn.execute(text(
                "INSERT INTO song_ingestions (song_id, method, ip_address, detail, created_at) "
                "VALUES (:sid, :m, :ip, :d, :ca)"),
                {"sid": sid, "m": METHOD_BY_SOURCE[src],
                 "ip": r.get("ip_address"), "d": json.dumps(detail),
                 "ca": get_created(r, src) or datetime.utcnow()})

    # 7. dedupe UNIQUE-collapse tables, 8. repoint
    _dedupe_and_repoint(conn)

    print(f"APPLY: built {len(key_to_song)} songs, {ap} chart_appearances, "
          f"{sum(len(m) for m in groups.values())} ingestions.")


def _dedupe_and_repoint(conn):
    # COMBINE vibe needles that collapse to one song
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
            SELECT n2.id, m.new_song_id,
                   row_number() OVER (PARTITION BY m.new_song_id ORDER BY n2.id) AS rn
            FROM audience_vibe_needles n2
            JOIN song_id_map m ON m.old_source = n2.song_source AND m.old_id = n2.song_id
        ) d WHERE n.id = d.id AND d.rn > 1
    """))

    # generic collision dedupe: keep one row per (new_song_id, *extra)
    for spec in COLLISION_SPECS:
        t, extra = spec["table"], spec["extra"]
        keep = spec["keep"]
        part = ", ".join(["m.new_song_id"] + [f"x.{e}" for e in extra])
        order = "x.calibrated_at DESC NULLS LAST, x.id" if keep == "calibrated_at" else "x.id"
        conn.execute(text(f"""
            DELETE FROM {t} z USING (
                SELECT x.id,
                       row_number() OVER (PARTITION BY {part} ORDER BY {order}) AS rn
                FROM {t} x
                JOIN song_id_map m ON m.old_source = x.song_source AND m.old_id = x.song_id
            ) d WHERE z.id = d.id AND d.rn > 1
        """))

    # repoint standard polymorphic tables
    for t in POLY_STD:
        conn.execute(text(f"""
            UPDATE {t} SET unified_song_id = m.new_song_id
            FROM song_id_map m
            WHERE {t}.song_source = m.old_source AND {t}.song_id = m.old_id
        """))
    conn.execute(text("""
        UPDATE backfill_job_rows SET unified_song_id = m.new_song_id
        FROM song_id_map m
        WHERE backfill_job_rows.result_song_source = m.old_source
          AND backfill_job_rows.result_song_id = m.old_id
    """))
    conn.execute(text("""
        UPDATE comments SET unified_song_id = m.new_song_id
        FROM song_id_map m
        WHERE comments.target_type = 'song'
          AND comments.target_source = m.old_source AND comments.target_id = m.old_id
    """))
    # hard-FK tables
    for t, idcol, src, newcol in HARD:
        conn.execute(text(f"""
            UPDATE {t} SET {newcol} = m.new_song_id
            FROM song_id_map m
            WHERE m.old_source = :src AND {t}.{idcol} = m.old_id
        """), {"src": src})


def verify(conn, groups):
    print("\nVERIFY:")
    ok = True

    def check(label, cond, detail=""):
        nonlocal ok
        ok = ok and cond
        print(f"  [{'PASS' if cond else 'FAIL'}] {label} {detail}")

    n_songs = conn.execute(text("SELECT count(*) FROM songs")).scalar()
    check("songs count == canonical groups", n_songs == len(groups), f"({n_songs} vs {len(groups)})")
    n_map = conn.execute(text("SELECT count(*) FROM song_id_map")).scalar()
    total_src = sum(conn.execute(text(f"SELECT count(*) FROM {SOURCE_TABLE[s]}")).scalar() for s in SOURCES)
    check("song_id_map covers all source rows", n_map == total_src, f"({n_map} vs {total_src})")
    orphan = conn.execute(text(
        "SELECT count(*) FROM song_id_map m LEFT JOIN songs s ON s.id=m.new_song_id WHERE s.id IS NULL")).scalar()
    check("no map orphans", orphan == 0, f"({orphan})")
    # provenance frozen
    anchors = conn.execute(text("SELECT count(*) FROM prose_provenance_anchors")).scalar()
    print(f"  [INFO] prose_provenance_anchors untouched: {anchors} rows")
    # every repointed pointer resolves
    for t in POLY_STD:
        bad = conn.execute(text(
            f"SELECT count(*) FROM {t} WHERE unified_song_id IS NOT NULL "
            f"AND unified_song_id NOT IN (SELECT id FROM songs)")).scalar()
        check(f"{t}.unified_song_id resolves", bad == 0, f"({bad} bad)" if bad else "")
    return ok


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    ap.add_argument("--yes", action="store_true", help="required confirmation with --apply")
    args = ap.parse_args()

    conn = engine.connect()
    trans = conn.begin()
    try:
        data = load_sources(conn)
        groups = build_groups(data)
        if not args.apply:
            report(conn, data, groups)
            trans.rollback()
            return
        if not args.yes:
            print("Refusing to --apply without --yes.")
            trans.rollback()
            return
        apply(conn, data, groups)
        if verify(conn, groups):
            trans.commit()
            print("\nCOMMITTED.")
        else:
            trans.rollback()
            print("\nVERIFY FAILED -> ROLLED BACK. No changes written.")
    except Exception:
        trans.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
