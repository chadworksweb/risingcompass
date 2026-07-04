"""Relink a draft song to an existing canonical Library song (cache-hit stamp).

Use this for the "exists" case: a feeder listed a song that is ALREADY in the
Library, but a crufty feeder title/artist stopped identity resolution from
cache-hitting it at draft-creation, so it landed on the awaiting-lyrics list.

DO NOT run calibrate_song.py on an already-existing song -- that re-derives a
calibration WITHOUT lyrics (void) and mints a DUPLICATE Library row off the
crufty title. The correct move is to reproduce what a cache hit does: point the
draft-song row at the canonical `songs` row and copy its stored calibration onto
the draft row. No new Library row, no calibration_run, no lyrics, no fabrication.

Runs INSIDE the prod backend container (server_only), a trusted DB source:

    ssh deploy@<droplet> "docker exec -i rc-backend python - <draft_song_id> <canonical_song_id> ['Clean Title'] ['Clean Artist']" \
        < scripts/server_only/relink_draft_song.py

Args:
  draft_song_id     the agent_draft_songs.id from list_pending_draft.py
  canonical_song_id the existing songs.id to link to (find it first)
  Clean Title       optional: overwrite the draft row's crufty title for a clean
  Clean Artist      optional: reading display (recommended for feeder cruft)

If the canonical song itself is a crufty-title DUPLICATE of a cleaner row, do NOT
relink to the dup -- merge the dup into the clean canonical first via the
song-merge admin (POST /api/admin/songs/{dup}/merge-into {target_id}) or
services.song_merge.merge_songs, then relink to the survivor.
"""
import sys

from app.database import SessionLocal
from app.models import AgentDraftSong, Song

if len(sys.argv) < 3:
    print("usage: relink_draft_song.py <draft_song_id> <canonical_song_id> "
          "['Clean Title'] ['Clean Artist']", file=sys.stderr)
    raise SystemExit(2)

ds_id = int(sys.argv[1])
canon_id = int(sys.argv[2])
clean_title = sys.argv[3] if len(sys.argv) > 3 else None
clean_artist = sys.argv[4] if len(sys.argv) > 4 else None

db = SessionLocal()
try:
    ds = db.query(AgentDraftSong).filter(AgentDraftSong.id == ds_id).first()
    s = db.query(Song).filter(Song.id == canon_id).first()
    if not ds:
        print(f"draft song {ds_id} not found", file=sys.stderr); raise SystemExit(1)
    if not s:
        print(f"canonical song {canon_id} not found", file=sys.stderr); raise SystemExit(1)
    if s.rubric_color is None:
        print(f"canonical song {canon_id} is not calibrated -- refusing to relink "
              f"to a stub", file=sys.stderr); raise SystemExit(1)

    # cache-hit stamp: link + copy the canonical's denormalized calibration onto
    # the draft-song row (deadpan/topics/prose live on the Song and render from
    # the link, so they are not copied here).
    ds.song_id = s.id
    ds.rubric_color = s.rubric_color
    ds.charge_value = s.charge_value
    ds.charge_summary = s.charge_summary
    ds.contaminated = bool(getattr(s, "contaminated", False))
    ds.contamination_note = getattr(s, "contamination_note", None)
    ds.dogma_referenced = bool(getattr(s, "dogma_referenced", False))
    ds.dogma_note = getattr(s, "dogma_note", None)
    ds.confidence = getattr(s, "confidence", None)
    ds.lyrics_available = True
    ds.calibration_failed = False
    if clean_title:
        ds.title = clean_title
    if clean_artist:
        ds.artist = clean_artist
    db.flush()

    # origin_chart record (Build 7): an existing song newly surfacing on a chart
    # still logs its chart_reading ingestion. Idempotent + fail-soft.
    try:
        from app.services.song_sync import record_chart_ingestion
        record_chart_ingestion(db, s.id, ds.chart_source)
    except Exception as e:
        print(f"(origin-chart record soft-fail: {e})")

    db.commit()
    print(f"RELINKED draft_song={ds.id} -> song {s.id} "
          f"{s.title!r} / {s.artist!r}  {ds.rubric_color}/{ds.charge_value}  "
          f"display_title={ds.title!r}")
finally:
    db.close()
