"""Relink a draft song to an existing canonical Library song (cache-hit stamp).

Use this for the "exists" case: a feeder listed a song that is ALREADY in the
Library, but a crufty feeder title/artist stopped identity resolution from
cache-hitting it at draft-creation, so it landed on the awaiting-lyrics list.

DO NOT run calibrate_song.py on an already-existing song -- that re-derives a
calibration WITHOUT lyrics (void) and mints a DUPLICATE Library row off the
crufty title. The correct move is to reproduce what a cache hit does: point the
draft-song row at the canonical `songs` row and copy its stored calibration onto
the draft row. No new Library row, no calibration_run, no lyrics, no fabrication.

This ALSO records the identity assertion in `song_identity_aliases` (rung 1b of
resolve_song_identity), so the relink is self-extinguishing: tomorrow the feeder
sends the same unreachable string and the alias resolves it to a free cache hit.
Before that table existed the mapping was discarded on exit and the same relink
recurred every single day (MORNING DEW / Beyonce and Dancing with the Enemy /
DisneyMusic each ran for days). The alias is skipped when the raw string's exact
canonical_key already resolves (rung 1 wins first, so the row would be dead).

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

from sqlalchemy import text

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

    # Capture the RAW feeder strings BEFORE the clean-display overwrite below --
    # the alias must key on what the feeder actually sent (that is the string that
    # will arrive again tomorrow), never on the cleaned display we are about to
    # write over it.
    raw_title, raw_artist = ds.title, ds.artist

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

    # Persist the identity assertion (rung 1b). THIS is what makes the relink the
    # LAST relink for this string: without it the feeder re-sends the same
    # unreachable title/artist tomorrow and the song re-lists as awaiting-lyrics
    # forever (MORNING DEW and Dancing with the Enemy each recurred for days).
    # Idempotent on alias_key (UNIQUE); a re-point to a different song wins, since
    # the newer human assertion is the better one. Fail-soft: an alias write must
    # never cost us the relink itself.
    alias_note = ""
    try:
        from app.services.song_identity import compute_canonical_key, compute_canonical_key_clean
        alias_key = compute_canonical_key_clean(raw_title, raw_artist)
        # An alias whose key is already a live canonical_key would never be read
        # (rung 1 wins first), so writing one is noise. Skip it.
        if not alias_key:
            alias_note = "  (alias skipped: empty key)"
        elif db.execute(
            text("SELECT 1 FROM songs WHERE canonical_key = :k LIMIT 1"),
            {"k": compute_canonical_key(raw_title, raw_artist)},
        ).first():
            alias_note = "  (alias skipped: exact key already resolves)"
        else:
            db.execute(text(
                "INSERT INTO song_identity_aliases "
                "  (alias_key, song_id, alias_title, alias_artist, source) "
                "VALUES (:k, :sid, :t, :a, 'relink') "
                "ON CONFLICT (alias_key) DO UPDATE SET "
                "  song_id = EXCLUDED.song_id, "
                "  alias_title = EXCLUDED.alias_title, "
                "  alias_artist = EXCLUDED.alias_artist"
            ), {"k": alias_key, "sid": s.id, "t": raw_title, "a": raw_artist})
            alias_note = f"  alias={raw_title!r} / {raw_artist!r} -> song {s.id}"
    except Exception as e:
        alias_note = f"  (alias write soft-fail: {e})"

    db.commit()
    print(f"RELINKED draft_song={ds.id} -> song {s.id} "
          f"{s.title!r} / {s.artist!r}  {ds.rubric_color}/{ds.charge_value}  "
          f"display_title={ds.title!r}{alias_note}")
finally:
    db.close()
