"""Set a daily/chart reading draft's editorial summary from terminal.

Claude Code writes the editorial during a reading calibration session and supplies
it here, the same way calibrate_song.py supplies per-song calibration. With
settings.editorial_terminal_only on, the server makes zero Anthropic calls for the
editorial -- this is the supply path that keeps the reading pipeline API-free
(see feedback_rc_no_api_in_terminal).

Run this AFTER every song in the draft is calibrated, BEFORE approval. Approval
does not overwrite a supplied editorial (the regen no-ops under the flag and
fail-softs otherwise), so what you set here is what publishes.

Editorial register (match the daily readings): two sentences, present tense,
reader-facing -- name the dominant charge of the reading, then the undercurrent.
Follow the global voice rules (no em-dashes, no clause triplets, no lists of
exactly three).

Usage:
    python set_editorial.py <draft_ref> --editorial "Two-sentence summary..."
    python set_editorial.py <draft_ref> --editorial-file path/to/editorial.txt
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("draft_ref")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--editorial", help="The editorial summary text.")
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

    url = f"https://api.risingcompass.net/api/admin/agent/drafts/{args.draft_ref}/editorial"
    body = json.dumps({"editorial_summary": editorial}).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json", "X-Lyrics-Supply-Key": key},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()}", file=sys.stderr)
        return 1

    print(f"OK  editorial set on {resp.get('label', args.draft_ref)} ({len(editorial)} chars)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
