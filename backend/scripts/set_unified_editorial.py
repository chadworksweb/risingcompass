"""Attach the editorial to a Unified Charge Chart day, which PUBLISHES it.

The unified twin of set_editorial.py. That script keys on a draft_ref; the
unified chart is derived and has no AgentDraft, so this keys on the DATE.

SUPPLYING THE EDITORIAL IS WHAT PUBLISHES THE READING. The unified reading
composes automatically as its four constituents are approved through the day, and
then sits unpublished. This call attaches the prose and flips it public in one
step, which is what keeps the number and the prose in lockstep: an editorial
written against a three-of-four composition would describe a figure the fourth
approval moves. See RISING-COMPASS-UNIFIED-CHARGE-CHART-SCOPE.md 8.6.

WRITE IT LAST. After all four constituent charts are approved. It is the
synthesis pass and the only editorial on the site written from a position where
every daily reading is visible at once.

By default the server REFUSES to publish a partial day (fewer than four
constituents composed) and returns 409 listing what is missing. Pass --force to
publish a partial day deliberately.

REGISTER + HARD RULES (identical to every other RC editorial, server-enforced by
services/agents/summary_guard.summary_violations):
  - Two sentences, present tense, reader-facing. Name the dominant charge of the
    reading, then the undercurrent.
  - NO song titles. Describe the reading's charge, not its track list.
  - NO musical-genre words. The compass reads lyrics; words have no genre.
  - NO tier color names. Use the LABEL (Corrupted / Degraded / Decent / Elevated
    / Ascended), never the color.
  - Global voice rules on top: no em-dashes, no clause triplets, no lists of
    exactly three.

THE GUARD BITES HARDER HERE. A single chart bans its 20 titles from the prose.
The union bans roughly 65, and one-word titles count (multiword_only is off, the
same setting the album lane keeps deliberately, because a one-word title is the
leak at scale). Expect more rejected first drafts and write around it.

WHAT THIS EDITORIAL CAN SAY THAT NO OTHER CAN: the spread between the highest and
lowest platform on the same day, the purchase-versus-algorithm divergence, which
songs corroborate across all four sources versus which are one-platform
artifacts, and the direction of travel of the whole line.

Usage:
    cd backend
    .venv\\Scripts\\python.exe scripts\\set_unified_editorial.py --editorial "..."
    .venv\\Scripts\\python.exe scripts\\set_unified_editorial.py --date 2026-08-16 \\
        --editorial-file path\\to\\editorial.txt
    .venv\\Scripts\\python.exe scripts\\set_unified_editorial.py --editorial "..." --force
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

BASE = os.environ.get("RC_API_BASE", "https://api.risingcompass.net")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--date", help="Reading date YYYY-MM-DD (default: today on the server).")
    p.add_argument("--force", action="store_true",
                   help="Publish even if fewer than four constituents composed.")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--editorial", help="The editorial text.")
    g.add_argument("--editorial-file", help="Path to a UTF-8 file with the editorial.")
    args = p.parse_args()

    key = os.environ.get("RC_LYRICS_SUPPLY_KEY")
    if not key:
        print("RC_LYRICS_SUPPLY_KEY not set in backend/.env", file=sys.stderr)
        return 2

    editorial = (
        Path(args.editorial_file).read_text(encoding="utf-8").strip()
        if args.editorial_file else args.editorial.strip()
    )
    if len(editorial) < 10:
        print("editorial must be at least 10 chars", file=sys.stderr)
        return 2

    url = f"{BASE}/api/admin/unified/editorial"
    if args.date:
        url += "?" + urllib.parse.urlencode({"reading_date": args.date})

    body = json.dumps({"editorial": editorial, "force": args.force}).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json", "X-Lyrics-Supply-Key": key},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode()
        print(f"HTTP {e.code}: {detail}", file=sys.stderr)
        if e.code == 409:
            print("\nA partial day was refused. Approve the remaining constituent "
                  "charts, or re-run with --force to publish it deliberately.",
                  file=sys.stderr)
        if e.code == 404:
            print("\nNothing composed for that date. Approve its constituents, or "
                  "POST /api/admin/unified/recompose first.", file=sys.stderr)
        return 1

    score = round((90 - resp["compass_degree"]) * 100 / 90)
    print(f"OK  unified {resp['date']} PUBLISHED  "
          f"{score:+d} ({resp['charge_level']})  "
          f"{resp['song_count']} songs from {resp['source_count']} charts  "
          f"({len(editorial)} chars)")
    if resp.get("editorial_stale"):
        print("WARNING: reading still flagged editorial_stale", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
