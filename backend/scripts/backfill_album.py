"""Terminal-mode ALBUM backfill. Claude Code is the model. NO Anthropic calls.

The album analogue of calibrate_song.py. Where calibrate_song.py supplies one
song's Claude-Code-produced calibration to the API endpoint, this writes a whole
album straight through the native storage chokepoint (SessionLocal +
store_calibrated_song + the Release assembly), bypassing every Anthropic path:
the per-track calibration AND prose AND ether are operator-supplied, and so is
the album-level reading. The API boundary lockdown (see
feedback_rc_no_api_in_terminal) forbids running operator batch work through the
backend's key; this tool never imports the Anthropic client.

What it writes, in one transaction:
  - each track -> a `songs` row via store_calibrated_song(source="library" ->
    editorial ingestion, authoritative), carrying rubric_color/charge_value/
    charge_summary/contaminated/dogma + listener_effects_prose + societal_effects_prose +
    deadpan_line + topics(+topic_audit). societal prose is provenance-sealed
    model="terminal_supplied".
  - a Release(release_type) for the album: aggregate via compute_release_charge,
    track/calibrated/contamination counts, the FULL album reading
    (charge_summary/arc_prose/listener_effects_prose/societal_effects_prose/deadpan_line/topics/
    topic_audit), source, release_year/date, and one ReleaseSong per track.
  Existing songs (by canonical_key) UPDATE in place and get linked -- a track
  that also charted is ONE atomic song with both a chart_appearance and this
  ReleaseSong (the song-entity renovation model).

Run INSIDE the container (DB-direct), payload on stdin:
  ssh root@<host> "cd /root/rising-compass && docker compose exec -T backend \\
      python3 scripts/backfill_album.py" < album_payload.json
  add --dry-run to validate + print the plan without writing.

Payload (JSON):
{
  "album": {
    "title": "...", "artist": "...",
    "release_type": "album|ep|single",          # default "album"
    "release_year": 1991, "release_date": "1991-11-26",  # date optional (ISO)
    "source": "album_backfill",                  # default "album_backfill"
    "reading": {                                 # the album-level reading
      "charge_summary": "...", "arc_prose": "...",
      "listener_effects_prose": "...", "societal_effects_prose": "...",
      "deadpan_line": "...", "topics": ["slug", ...], "topic_audit": null
    }
  },
  "tracks": [
    {
      "track_number": 1, "title": "...", "artist": "...",   # artist optional
      "calibration": {
        "rubric_color": "green", "charge_value": 12, "charge_summary": "...",
        "contaminated": false, "contamination_note": null,
        "dogma_referenced": false, "dogma_note": null, "confidence": 0.9,
        "listener_effects_prose": "...", "societal_effects_prose": "...",
        "deadpan_line": "...", "topics": ["slug", ...], "topic_audit": null
      },
      "artist_entries": [{"name": "...", "role": "primary", "position": 0}]
    }
  ]
}

Topics (per track AND album) must be slugs from the closed taxonomy
(app/services/ether_taxonomy.VALID_SLUGS), dominant-first, max 3 -- or [] with a
topic_audit object {reason, proposed_tag, rationale}.

Multi-disc / double albums (codified 2026-06-09):
  A multi-disc album is calibrated as N DISTINCT releases -- one per disc, each
  with its OWN album-level reading. NEVER collapse a double album into a single
  arc across all discs: disc 1 and disc 2 are different statements and read
  separately. Run this script once per disc:
    - One payload per disc. album.title = "<Album Title> (Disc N: <Disc Subtitle>)"
      -- e.g. "HIStory: Past, Present and Future, Book I (Disc 1: HIStory Begins)".
      Drop the subtitle when a disc has no name: "<Album Title> (Disc N)". The
      UNIQUE(artist_id, title) constraint and the slug-keyed release page both
      require distinct titles; the parenthetical keeps the discs grouped under
      the parent album on the artist page.
    - track_number restarts at 1 WITHIN each disc (per-disc numbering).
    - album.reading is that disc's arc only, and the aggregate is over that
      disc's tracks (the script already aggregates only the payload it is given).
  A song that appears on more than one disc stays ONE song row (canonical_key);
  it just gets a ReleaseSong under each disc's release. Single-disc albums are
  unchanged -- one payload, one release, no "(Disc N)" suffix.
"""

import argparse
import json
import sys
from datetime import datetime, date, timezone
from pathlib import Path

# Make `app` importable whether run in-container or locally (mirrors calibrate_song.py).
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

ROOT_HINT = "run inside the backend container (app on sys.path) or with the DB tunnel up"

try:
    from app.database import SessionLocal
    from app.services.song_sync import store_calibrated_song
    from app.services.artist_linker import upsert_artist
    from app.services.artist_utils import compute_release_charge
    from app.services.song_identity import compute_canonical_key
    from app.services.ether_taxonomy import VALID_SLUGS
    from app.models import Release, ReleaseSong
    from sqlalchemy import text
except ImportError as e:  # pragma: no cover
    print(f"import error ({e}); {ROOT_HINT}", file=sys.stderr)
    raise

TIER_RANGES = {
    "violet": (75, 100), "blue": (25, 74), "green": (-24, 24),
    "orange": (-74, -25), "red": (-100, -75),
}
VALID_COLORS = set(TIER_RANGES)


def _err(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(2)


def _validate_ether(deadpan, topics, topic_audit, where: str) -> None:
    """Mirror the calibrate_song.py contract: topics-XOR-audit, slugs valid, max 3."""
    if topics is None:
        topics = []
    has_audit = bool(topic_audit)
    if topics and has_audit:
        _err(f"{where}: topics and topic_audit are mutually exclusive")
    if topics:
        if len(topics) > 3:
            _err(f"{where}: max 3 topics, got {len(topics)}")
        bad = [t for t in topics if t not in VALID_SLUGS]
        if bad:
            _err(f"{where}: invalid taxonomy slug(s) {bad}. valid: {sorted(VALID_SLUGS)}")
    elif has_audit:
        for k in ("reason", "proposed_tag", "rationale"):
            if not topic_audit.get(k):
                _err(f"{where}: topic_audit needs reason/proposed_tag/rationale")


def _validate(payload: dict) -> None:
    if "album" not in payload or "tracks" not in payload:
        _err("payload needs 'album' and 'tracks'")
    a = payload["album"]
    for k in ("title", "artist"):
        if not a.get(k):
            _err(f"album.{k} required")
    if a.get("release_type", "album") not in ("album", "ep", "single"):
        _err("album.release_type must be album|ep|single")
    r = a.get("reading", {})
    _validate_ether(r.get("deadpan_line"), r.get("topics"), r.get("topic_audit"), "album.reading")
    tracks = payload["tracks"]
    if not tracks:
        _err("no tracks")
    seen = set()
    for t in tracks:
        tn = t.get("track_number")
        title = t.get("title")
        if not title:
            _err("a track is missing title")
        if tn in seen:
            _err(f"duplicate track_number {tn}")
        seen.add(tn)
        c = t.get("calibration") or {}
        color = c.get("rubric_color")
        charge = c.get("charge_value")
        if color not in VALID_COLORS:
            _err(f"track '{title}': bad rubric_color {color!r}")
        lo, hi = TIER_RANGES[color]
        if charge is None or not (lo <= charge <= hi):
            _err(f"track '{title}': charge {charge} out of {color} band [{lo},{hi}]")
        if not c.get("charge_summary"):
            _err(f"track '{title}': charge_summary required")
        _validate_ether(c.get("deadpan_line"), c.get("topics"), c.get("topic_audit"),
                        f"track '{title}'")


def main() -> int:
    p = argparse.ArgumentParser(description="Terminal album backfill (no API).")
    p.add_argument("--payload-file", default=None, help="JSON payload path (default: stdin).")
    p.add_argument("--dry-run", action="store_true", help="Validate + print the plan, write nothing.")
    args = p.parse_args()

    raw = open(args.payload_file, encoding="utf-8").read() if args.payload_file else sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        _err(f"invalid JSON: {e}")
    _validate(payload)

    a = payload["album"]
    artist_name = a["artist"]
    title = a["title"]
    release_type = a.get("release_type", "album")
    source = a.get("source", "album_backfill")
    release_year = a.get("release_year")
    release_date = None
    if a.get("release_date"):
        release_date = date.fromisoformat(a["release_date"])
        if release_year is None:
            release_year = release_date.year
    reading = a.get("reading", {})
    tracks = sorted(payload["tracks"], key=lambda t: (t.get("track_number") or 0))
    charges = [t["calibration"]["charge_value"] for t in tracks]
    agg = compute_release_charge(charges)  # (avg, color, label, hex)

    print(f"ALBUM: {title!r} by {artist_name}  [{release_type}, {release_year}]  "
          f"aggregate {agg[1]} {agg[0]:+d}  ({len(tracks)} tracks)")
    if args.dry_run:
        for t in tracks:
            c = t["calibration"]
            print(f"  #{t.get('track_number'):>2} {t['title'][:34]:<34} {c['rubric_color']}/{c['charge_value']:+d}"
                  f"  deadpan={c.get('deadpan_line')!r}")
        print("DRY RUN -- nothing written.")
        return 0

    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        links = []
        contamination_count = 0
        print("=== tracks ===")
        for t in tracks:
            c = dict(t["calibration"])
            if c.get("contaminated"):
                contamination_count += 1
            if c.get("societal_effects_prose"):
                c.setdefault("societal_prose_model", "terminal_supplied")
                c.setdefault("societal_prose_generated_at", now)
            track_artist = t.get("artist") or artist_name
            entries = t.get("artist_entries") or [
                {"name": track_artist, "role": "primary", "position": 0}]
            key = compute_canonical_key(t["title"], track_artist)
            prior = db.execute(
                text("SELECT rubric_color, charge_value FROM songs WHERE canonical_key=:k"),
                {"k": key}).mappings().first()
            song_id, created = store_calibrated_song(
                db, source="library", title=t["title"], artist=track_artist,
                calibration=c, ingestion_detail={"source": source}, artist_entries=entries)
            links.append((t.get("track_number"), song_id))
            flag = ""
            if not created and prior and (
                    prior["rubric_color"] != c["rubric_color"]
                    or prior["charge_value"] != c["charge_value"]):
                flag = f"  [CHANGED from {prior['rubric_color']}/{prior['charge_value']}]"
            print(f"  #{t.get('track_number'):>2} {t['title'][:30]:<30} -> id={song_id} "
                  f"{'NEW ' if created else 'upd '}{c['rubric_color']}/{c['charge_value']:+d}{flag}")
        db.flush()

        artist = upsert_artist(db, artist_name)
        db.flush()
        rel = db.query(Release).filter(
            Release.artist_id == artist.id, Release.title == title).first()
        rel_new = rel is None
        if rel_new:
            rel = Release(artist_id=artist.id, title=title, release_type=release_type)
            db.add(rel)
        else:
            rel.release_type = release_type
            db.query(ReleaseSong).filter(ReleaseSong.release_id == rel.id).delete()
        if release_year is not None:
            rel.release_year = release_year
        if release_date is not None:
            rel.release_date = release_date
        rel.charge_value = agg[0]
        rel.rubric_color = agg[1]
        rel.track_count = len(tracks)
        rel.calibrated_count = len(tracks)
        rel.contamination_count = contamination_count
        rel.charge_summary = reading.get("charge_summary")
        rel.arc_prose = reading.get("arc_prose")
        rel.listener_effects_prose = reading.get("listener_effects_prose")
        rel.societal_effects_prose = reading.get("societal_effects_prose")
        rel.deadpan_line = reading.get("deadpan_line")
        rel.topics = json.dumps(reading["topics"]) if reading.get("topics") else None
        rel.topic_audit = json.dumps(reading["topic_audit"]) if reading.get("topic_audit") else None
        rel.source = source
        rel.submitted_at = now
        db.flush()
        for tn, sid in links:
            if sid is not None:
                db.add(ReleaseSong(release_id=rel.id, song_id=sid, track_number=tn))
        db.commit()

        n = db.query(ReleaseSong).filter(ReleaseSong.release_id == rel.id).count()
        print(f"=== release id={rel.id} {'NEW' if rel_new else 'updated'}  "
              f"artist_slug={artist.slug}  links={n} ===")
        print("DONE")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
