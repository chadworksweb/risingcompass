"""Rewrite the editorial on an ALREADY-APPROVED chart or daily reading.

`set_editorial.py` only accepts a PENDING draft (the endpoint 400s once the
draft is approved), so a published day's editorial has no terminal path. This
is that path: it writes the same column approval would have written, through
the same guard the endpoint runs, so a rewrite cannot land looser than the
original.

Runs INSIDE the prod backend container (server_only), a trusted DB source:

    ssh deploy@<droplet> "docker exec -i rc-backend python - <date> <target> <b64>" \
        < scripts/server_only/set_published_editorial.py

Args:
  date    YYYY-MM-DD
  target  a chart_snapshots.chart_source (itunes_download_usa,
          shazam_top200_usa, youtube_trending_usa, spotify_top50_usa) or the
          literal `daily` for the homepage reading.
  b64     the editorial text, base64-encoded. Encoded because the text travels
          through PowerShell -> ssh -> remote bash, which mangles apostrophes
          and parentheses (feedback_wpe_ssh_quoting).

`daily` writes daily_readings.editorial_summary AND any spotify_top50_usa
snapshot rows for the date, because the homepage panel and the standalone chart
page read different columns; a rewrite that moved only one would leave the two
surfaces disagreeing.

The unified chart is NOT handled here: its editorial is re-suppliable at any
time through scripts/set_unified_editorial.py, published or not.
"""
import base64
import sys
from datetime import date as _date

from app.database import SessionLocal
from app.models import ChartSnapshot, DailyReading, ReadingSong, Song
from app.services.agents.summary_guard import SUMMARY_RULES_NUDGE, summary_violations

if len(sys.argv) < 4:
    print("usage: set_published_editorial.py <YYYY-MM-DD> <chart_source|daily> <b64>",
          file=sys.stderr)
    raise SystemExit(2)

d = _date.fromisoformat(sys.argv[1])
target = sys.argv[2]
editorial = base64.b64decode(sys.argv[3]).decode("utf-8").strip()

if len(editorial) < 10:
    print("editorial must be at least 10 chars", file=sys.stderr)
    raise SystemExit(2)

db = SessionLocal()
try:
    if target == "daily":
        reading = db.query(DailyReading).filter(DailyReading.date == d).one_or_none()
        if not reading:
            print(f"no daily reading for {d}", file=sys.stderr); raise SystemExit(1)
        ids = [x.song_id for x in db.query(ReadingSong)
               .filter(ReadingSong.reading_id == reading.id).all()]
        titles = [t for (t,) in db.query(Song.title).filter(Song.id.in_(ids)).all()]
        snaps = db.query(ChartSnapshot).filter(
            ChartSnapshot.date == d,
            ChartSnapshot.chart_source == "spotify_top50_usa").all()
    else:
        snaps = db.query(ChartSnapshot).filter(
            ChartSnapshot.date == d, ChartSnapshot.chart_source == target).all()
        if not snaps:
            print(f"no {target} snapshot rows for {d}", file=sys.stderr); raise SystemExit(1)
        reading = None
        titles = [s.title for s in snaps]

    # Same guard the pending-draft endpoint runs: no song titles, no genre words,
    # no tier color names. A rewrite is held to the original's standard.
    viol = summary_violations(editorial, titles=titles, check_absence=False,
                              titles_multiword_only=False)
    if viol:
        print("editorial tripped the summary guard: " + "; ".join(viol) + ". "
              + SUMMARY_RULES_NUDGE, file=sys.stderr)
        raise SystemExit(1)

    if reading is not None:
        reading.editorial_summary = editorial
    for s in snaps:
        s.editorial = editorial
    db.commit()
    print(f"OK  {target} {d}: daily_readings={'yes' if reading is not None else 'no'} "
          f"snapshot_rows={len(snaps)}  ({len(editorial)} chars)")
finally:
    db.close()
