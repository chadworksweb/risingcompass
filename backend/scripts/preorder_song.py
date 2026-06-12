"""Null a draft song as PRE-ORDER from the terminal (no lyrics, no calibration).

Use when a chart feeder (especially iTunes Downloads) lists a single that is
charting on pre-order: it has no lyrics yet because it is not released. This is
the temporary sibling of an instrumental null -- it exempts the song from the
approval gate without inventing a tier, excludes it from every aggregate, and
(unlike an instrumental) does NOT become a cache hit: the song re-lists as
awaiting-lyrics on each later day until real lyrics drop and a normal
calibrate_song.py run supersedes it.

Usage:
    python preorder_song.py <draft_ref> <song_id>
    python preorder_song.py <draft_ref> <song_id> --clear   # undo

POSTs to api.risingcompass.net with X-Lyrics-Supply-Key from backend/.env --
same auth + no-tunnel posture as calibrate_song.py. No lyrics are sent.
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
    p.add_argument("song_id", type=int)
    p.add_argument("--clear", action="store_true", help="Clear the pre-order flag instead of setting it.")
    args = p.parse_args()

    key = os.environ.get("RC_LYRICS_SUPPLY_KEY")
    if not key:
        print("RC_LYRICS_SUPPLY_KEY not set in backend/.env", file=sys.stderr)
        return 2

    url = (
        f"https://api.risingcompass.net/api/admin/agent/drafts/"
        f"{args.draft_ref}/songs/{args.song_id}/preorder"
    )
    body = json.dumps({"preorder": not args.clear}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "X-Lyrics-Supply-Key": key},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()}", file=sys.stderr)
        return 1

    song = next((s for s in resp.get("songs", []) if s["id"] == args.song_id), None)
    state = "cleared" if args.clear else "pre-order"
    if song:
        print(f"OK  pos={song['position']}  {song['title']!r}  -> {state}")
    else:
        print(f"OK ({state}; song not in response)")

    remaining = sum(
        1 for s in resp.get("songs", [])
        if s.get("rubric_color") is None and not s.get("preorder")
    )
    print(f"Remaining needs-lyrics in draft: {remaining}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
