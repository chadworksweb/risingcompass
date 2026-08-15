"""Flag song cover-art picks that point at the wrong release, using the calendar.

THE FAILURE THIS CATCHES. The artist-credit check in musicbrainz._pick_release_group
stops a wrong ARTIST (the "Top Medleys" class). What it cannot see is a RIGHT artist
on the WRONG RELEASE: an archival box set, a reissue, or an unrelated later single
that MusicBrainz never tagged with a secondary-type and whose title dodges
HITS_COMPILATION_RE. Those are invisible to title and type heuristics, and they are
the dominant wrong-art failure.

They are not invisible to the calendar. A song that charted in 1972 pointing at a
release group first issued in 2022 is wrong, and saying so needs no knowledge of the
record itself -- which is the whole point, because the Library is far too large to
check by ear.

THE RULE, AND ONLY THIS RULE. Flag a song when its release group was first issued
MORE THAN --tolerance years AFTER the song first charted.

Deliberately one-directional. A release group DATED EARLIER than the chart year is
normal and is never flagged: a 1977 album track charts in 1977-78, and a decades-old
song can chart on a sync placement or a revival (charting in 2022 off a 1985 album is
correct, not a fault). Only the later direction indicates a reissue or an unrelated
release, so only the later direction is a signal.

Songs with no chart appearance (Lyrical-Charger-born, terminal-born) have no calendar
to check against and are reported as unauditable rather than guessed at.

  --tolerance N   years of slack before flagging (default 2; a late-in-the-year
                  chart run trails its release into the next year, and MB dates
                  a fair number of legitimate singles a year off the album)
  --reset         record each flagged pick as REJECTED, then clear the rows'
                  mbid + date + checked_at so the next backfill_song_cover_art.py
                  run re-resolves them. Prints and asks before writing.

                  The rejection is the load-bearing half, and it was missing at
                  first. Clearing alone re-runs an unchanged resolver over
                  unchanged MusicBrainz data and lands on the identical wrong
                  group: deterministic, so the whole pass is ~2 lookups per song
                  spent to arrive back where it started. Writing a confirmed
                  `song_cover_art_reports` row first is what excludes that group
                  from the candidate scan, so the re-resolve can reach the
                  next-best release instead. Same table, same mechanism, as a
                  reader-filed report -- the audit is just another witness that
                  a particular cover is wrong for a particular song.
  --limit N       cap the report

Usage:
    cd backend
    .venv\\Scripts\\python.exe scripts\\audit_song_cover_art.py
    .venv\\Scripts\\python.exe scripts\\audit_song_cover_art.py --reset
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from app.config import settings
from app.database import SessionLocal

# MB dates are variable-precision ("1972", "1972-02", "1972-02-01"), so the year is
# always the leading 4 characters when there is a date at all.
_ROWS_SQL = """
SELECT s.id, s.title, s.artist, s.release_group_date, s.release_group_mbid,
       MIN(ca.year) AS first_chart_year
FROM songs s
LEFT JOIN chart_appearances ca ON ca.song_id = s.id AND ca.year IS NOT NULL
WHERE s.release_group_mbid IS NOT NULL
  AND s.release_group_date IS NOT NULL
  AND s.release_group_date <> ''
GROUP BY s.id, s.title, s.artist, s.release_group_date, s.release_group_mbid
"""


def _year(raw: str | None) -> int | None:
    if not raw or len(raw) < 4 or not raw[:4].isdigit():
        return None
    return int(raw[:4])


def main() -> None:
    ap = argparse.ArgumentParser(description="Flag calendar-implausible cover-art picks.")
    ap.add_argument("--tolerance", type=int, default=2,
                    help="Years after the chart year before a pick is flagged.")
    ap.add_argument("--limit", type=int, default=None, help="Cap the report.")
    ap.add_argument("--reset", action="store_true",
                    help="Queue flagged songs for re-resolve on the next backfill.")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        rows = db.execute(text(_ROWS_SQL)).fetchall()
    finally:
        db.close()

    flagged, unauditable, ok = [], 0, 0
    for sid, title, artist, rg_date, mbid, chart_year in rows:
        rg_year = _year(rg_date)
        if rg_year is None or chart_year is None:
            unauditable += 1
            continue
        gap = rg_year - int(chart_year)
        if gap > args.tolerance:
            # The MBID rides along because --reset has to name the group it is
            # rejecting; the report alone only needs the human-readable fields.
            flagged.append((gap, sid, title, artist, rg_date, int(chart_year), mbid))
        else:
            ok += 1

    # Worst offenders first: the bigger the gap, the less arguable the fault.
    flagged.sort(reverse=True)
    shown = flagged[:args.limit] if args.limit else flagged

    print(f"Auditable: {ok + len(flagged)}   "
          f"No chart year or no MB date (unauditable): {unauditable}")
    print(f"Flagged (release group issued >{args.tolerance}y after first chart): "
          f"{len(flagged)}\n")
    for gap, sid, title, artist, rg_date, chart_year, _mbid in shown:
        print(f"  +{gap:>3}y  [{sid}] {title} - {artist}")
        print(f"          charted {chart_year}, release group dated {rg_date}")

    if not args.reset or not flagged:
        if flagged and not args.reset:
            print(f"\nRe-run with --reset to queue these {len(flagged)} for re-resolve.")
        return

    print(f"\n--reset will record {len(flagged)} rejected release group(s), then "
          f"clear release_group_mbid + date + checked_at on those songs.")
    print("They lose their (wrong) art now and are re-resolved on the next "
          "backfill_song_cover_art.py run, which will skip the rejected group.")
    if input("Proceed? [y/N] ").strip().lower() != "y":
        print("Aborted, nothing written.")
        return

    ids = [f[1] for f in flagged]
    db = SessionLocal()
    try:
        # Record the rejection BEFORE clearing, in one transaction. The clear is
        # what makes the song re-resolvable; the rejection is what stops the
        # re-resolve returning the same group. Doing the clear alone leaves a
        # deterministic no-op, which is what this originally shipped as.
        for gap, sid, title, artist, rg_date, chart_year, mbid in flagged:
            db.execute(
                text("""
                    INSERT INTO song_cover_art_reports
                        (song_id, reported_mbid, mbid_source, note, status,
                         environment, resolved_at, resolved_by)
                    VALUES
                        (:sid, :mbid, 'song', :note, 'confirmed',
                         :env, NOW(), 'date_audit')
                """),
                {
                    "sid": sid,
                    "mbid": mbid,
                    "note": (f"Date audit: charted {chart_year}, release group "
                             f"dated {rg_date} (+{gap}y)."),
                    "env": settings.environment,
                },
            )
        db.execute(
            text("UPDATE songs SET release_group_mbid = NULL, "
                 "release_group_date = NULL, release_group_checked_at = NULL "
                 "WHERE id = ANY(:ids)"),
            {"ids": ids},
        )
        db.commit()
    finally:
        db.close()
    print(f"Rejected {len(ids)} release group(s) and reset those songs. "
          f"Re-run the backfill to re-resolve them.")


if __name__ == "__main__":
    main()
