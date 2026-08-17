"""Unit test for the destructive song_merge service (no live DB).

Builds the full schema on in-memory SQLite (stripping the PG-only now()
server-defaults so the DDL is portable), seeds a duplicate pair with references
across several tables, runs merge_songs, and asserts every reference repointed,
the per-method ingestion dedup held, the source row was deleted, and the audit
event was written.

NOTE: SQLite does not enforce FKs by default, so the ON DELETE SET NULL on the
merge-candidate rows is NOT exercised here (it works on Postgres); the endpoint
supersedes candidates explicitly BEFORE the delete, which this test does not
cover. Run standalone:  python tests/test_song_merge.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text

from app.models import Base
from app.services.song_merge import merge_songs
from app.services.song_identity import compute_canonical_key, compute_canonical_key_clean


def _build_engine():
    # Strip ONLY the PG now()-expression server-defaults (they aren't valid
    # SQLite DDL); keep boolean/CURRENT_TIMESTAMP defaults so NOT NULL holds.
    for t in Base.metadata.sorted_tables:
        for col in t.columns:
            sd = col.server_default
            if sd is not None and "now(" in str(getattr(sd, "arg", "")).lower():
                col.server_default = None
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return eng


def test_merge_repoints_and_deletes():
    eng = _build_engine()
    with eng.begin() as c:
        for sid, t, a in [(2778, "stupid song", "OliviaRodrigoVEVO"),
                          (3293, "Olivia Rodrigo - stupid song (Official Music Video)", "Olivia Rodrigo")]:
            c.execute(text(
                "INSERT INTO songs (id,title,artist,canonical_key,canonical_key_clean,rubric_color,charge_value) "
                "VALUES (:i,:t,:a,:k,:ck,'green',5)"),
                {"i": sid, "t": t, "a": a,
                 "k": compute_canonical_key(t, a), "ck": compute_canonical_key_clean(t, a)})
        c.execute(text("INSERT INTO song_ingestions (song_id,method) VALUES "
                       "(2778,'chart_reading'),(3293,'chart_reading'),(2778,'lyrical_charger')"))
        c.execute(text("INSERT INTO song_slugs (slug,title,artist,song_id) VALUES ('old-slug','x','y',2778)"))
        c.execute(text("INSERT INTO chart_appearances (song_id,chart_id,year,position,position_letter) "
                       "VALUES (2778,1,2026,4,'')"))
        c.execute(text("INSERT INTO user_calibrations (song_id,user_id,rubric_color) VALUES (2778,99,'green')"))
        c.execute(text("INSERT INTO reading_songs (song_id,reading_id,position,title,artist) VALUES (2778,1,4,'x','y')"))

    with eng.begin() as c:
        rw = merge_songs(c, 2778, 3293, actor="tester", notes="dupe", environment="local")

    with eng.connect() as c:
        assert c.execute(text("SELECT count(*) FROM songs WHERE id=2778")).scalar() == 0
        assert c.execute(text("SELECT song_id FROM song_slugs WHERE slug='old-slug'")).scalar() == 3293
        assert c.execute(text("SELECT song_id FROM chart_appearances WHERE position=4")).scalar() == 3293
        assert c.execute(text("SELECT song_id FROM user_calibrations WHERE user_id=99")).scalar() == 3293
        assert c.execute(text("SELECT song_id FROM reading_songs WHERE position=4")).scalar() == 3293
        ing = sorted(r[0] for r in c.execute(
            text("SELECT method FROM song_ingestions WHERE song_id=3293")).fetchall())
        assert ing == ["chart_reading", "lyrical_charger"], ing  # method-dedup held
        assert c.execute(text("SELECT count(*) FROM song_ingestions WHERE song_id=2778")).scalar() == 0
        ev = c.execute(text("SELECT source_song_id,target_song_id,actor FROM song_merge_events")).fetchone()
        assert ev == (2778, 3293, "tester"), ev
    # the repoint counters are populated
    assert rw["song_slugs"] == 1 and rw["chart_appearances"] == 1 and rw["song_ingestions"] == 1


def test_merge_carries_the_whole_panel_and_published_slots():
    """The two failures found on 2026-08-17, both silent:

    1. A merge into a STUB dropped psyche_facts / effects_pl (and left the stub's
       permanent no-lyrics hold standing over a real reading), so the row looked
       calibrated while the Psyche Facts panel was gone.
    2. unified_reading_songs (CASCADE) and chart_snapshots (SET NULL) were not
       repointed, so a merge quietly shrank a PUBLISHED unified day and orphaned
       published chart positions.
    """
    eng = _build_engine()
    with eng.begin() as c:
        # target = stub carrying the permanent hold; source = today's real reading
        c.execute(text(
            "INSERT INTO songs (id,title,artist,canonical_key,rubric_color,lyrics_unavailable) "
            "VALUES (10,'Leo Leo Remix','Bulin 47','k1',NULL,1)"))
        c.execute(text(
            "INSERT INTO songs (id,title,artist,canonical_key,rubric_color,charge_value,"
            "psyche_facts,effects_pl,lyrics_unavailable) "
            "VALUES (11,'Bulin 47 - Leo Leo Remix (Video Oficial)','Bulin 47 Oficial','k2',"
            "'green',-7,'{\"purpose\":\"p\"}','[\"fires-you-up\"]',0)"))
        c.execute(text("INSERT INTO unified_readings (id,date,compass_degree,charge_level,contamination_count,"
                       "song_count,sources_included,sources_excluded,source_count,weights_version,weights,composed_at) "
                       "VALUES (1,'2026-08-17',111.1,'green',0,62,'[]','[]',4,'v1','{}','2026-08-17 12:00:00')"))
        c.execute(text("INSERT INTO unified_reading_songs (reading_id,song_id,position,unified_weight,chart_count,sources) "
                       "VALUES (1,11,45,0.05,1,'{}')"))
        c.execute(text("INSERT INTO chart_snapshots (date,chart_source,position,title,artist,song_id) "
                       "VALUES ('2026-08-17','youtube_trending_usa',14,"
                       "'Bulin 47 - Leo Leo Remix (Video Oficial)','Bulin 47 Oficial',11)"))

    with eng.begin() as c:
        rw = merge_songs(c, 11, 10, actor="tester", environment="local")

    with eng.connect() as c:
        row = c.execute(text(
            "SELECT rubric_color,charge_value,psyche_facts,effects_pl,lyrics_unavailable "
            "FROM songs WHERE id=10")).fetchone()
        assert row[0] == "green" and row[1] == -7, row
        assert row[2] and row[3], f"psyche panel dropped by the merge: {row}"
        assert not row[4], "the stub's no-lyrics hold survived a real reading"
        assert c.execute(text(
            "SELECT song_id FROM unified_reading_songs WHERE reading_id=1")).scalar() == 10
        assert c.execute(text(
            "SELECT song_id FROM chart_snapshots WHERE position=14")).scalar() == 10
        # the chart's own historical string is provenance and stays put
        assert "Video Oficial" in c.execute(text(
            "SELECT title FROM chart_snapshots WHERE position=14")).scalar()
    assert rw["unified_reading_songs"] == 1 and rw["chart_snapshots"] == 1
    assert rw["calibration_copied_from_source"] is True


def test_merge_dedupes_a_shared_unified_day():
    """Both rows charting into the SAME published day: the target's slot survives
    and the source's is dropped, rather than tripping UNIQUE(reading_id, song_id)."""
    eng = _build_engine()
    with eng.begin() as c:
        c.execute(text("INSERT INTO songs (id,title,artist,canonical_key,rubric_color,charge_value) "
                       "VALUES (20,'t','a','k1','green',5),(21,'t2','a2','k2','green',6)"))
        c.execute(text("INSERT INTO unified_readings (id,date,compass_degree,charge_level,contamination_count,"
                       "song_count,sources_included,sources_excluded,source_count,weights_version,weights,composed_at) "
                       "VALUES (1,'2026-08-17',111.1,'green',0,62,'[]','[]',4,'v1','{}','2026-08-17 12:00:00')"))
        c.execute(text("INSERT INTO unified_reading_songs (reading_id,song_id,position,unified_weight,chart_count,sources) "
                       "VALUES (1,20,3,0.6,3,'{}'),(1,21,40,0.05,1,'{}')"))
    with eng.begin() as c:
        merge_songs(c, 21, 20, actor="tester", environment="local")
    with eng.connect() as c:
        rows = c.execute(text(
            "SELECT song_id,position FROM unified_reading_songs WHERE reading_id=1")).fetchall()
        assert [tuple(r) for r in rows] == [(20, 3)], rows


def test_self_merge_rejected():
    from app.services.song_merge import MergeError
    eng = _build_engine()
    with eng.begin() as c:
        c.execute(text("INSERT INTO songs (id,title,artist,canonical_key) VALUES (1,'a','b','k')"))
    with eng.begin() as c:
        try:
            merge_songs(c, 1, 1, actor="t")
            raise AssertionError("expected MergeError on self-merge")
        except MergeError:
            pass


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t(); print(f"PASS  {t.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1; print(f"FAIL  {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run() else 0)
