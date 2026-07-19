"""Mark a draft song as LYRICS-UNAVAILABLE from the terminal.

For a RELEASED song whose lyrics are genuinely unobtainable (not published on
any source), so it cannot be calibrated and should stop re-listing as awaiting-
lyrics every day. POSTs to the prod lyrics-unavailable endpoint (auth header
X-Lyrics-Supply-Key from backend/.env), which exempts the song from the approval
gate AND persists the disposition on the unified songs row so the feeder cache-
hits it on every later run. A later real calibration clears the hold.

Usage:
    python mark_lyrics_unavailable.py <draft_ref> <song_id>
    python mark_lyrics_unavailable.py <draft_ref> <song_id> --clear

No lyrics, no calibration, no Anthropic call -- this is a pure disposition flag.
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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # make `app` importable for the shared disposition predicate
load_dotenv(ROOT / ".env")

from app.constants import song_needs_lyrics  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("draft_ref")
    p.add_argument("song_id", type=int)
    p.add_argument("--clear", action="store_true",
                   help="Clear the hold instead of setting it.")
    args = p.parse_args()

    key = os.environ.get("RC_LYRICS_SUPPLY_KEY")
    if not key:
        print("RC_LYRICS_SUPPLY_KEY not set in backend/.env", file=sys.stderr)
        return 2

    url = (
        f"https://api.risingcompass.net/api/admin/agent/drafts/"
        f"{args.draft_ref}/songs/{args.song_id}/lyrics-unavailable"
    )
    body = json.dumps({"lyrics_unavailable": not args.clear}).encode()
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

    song = next((s for s in resp.get("songs", []) if s["id"] == args.song_id), None)
    state = "cleared" if args.clear else "lyrics_unavailable"
    if song:
        print(f"OK  pos={song['position']}  {song['title']!r}  -> {state}  "
              f"song_id={song.get('compass_song_id') or song.get('song_id')}")
    else:
        print(f"OK ({state}; song not in response)")

    remaining = sum(1 for s in resp.get("songs", []) if song_needs_lyrics(s))
    print(f"Remaining needs-lyrics in draft: {remaining}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
