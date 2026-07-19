"""Correct a draft song's title and/or artist from the terminal, with an audit row.

For a feeder row credited to an UPLOAD CHANNEL rather than a performer
("DisneyMusic", a label account), or a title still wrapped in upload cruft (an
artist prefix, a "(Video Oficial)" tail, a trailing emoji). POSTs to the prod
recredit endpoint (auth header X-Lyrics-Supply-Key from backend/.env), which
writes a `draft_song_edits` audit row in the SAME transaction as the change.

DO THIS BEFORE CALIBRATING. The title + artist pair is what mints the `songs`
row and its canonical key, so a bad credit corrected now costs a string, while
the same correction after calibration costs a song merge. The endpoint enforces
this: it REFUSES a song that already has a reading or a linked Library row.

This is the sanctioned replacement for a raw UPDATE against `agent_draft_songs`.
A raw UPDATE works and leaves no record of what the value was or why it changed.

Note the feeder cleaner cannot do this job for you. `feeder_clean` resolves a
channel to a real artist only when the title itself carries the artist (the
quoted-title upload format); "Mad" by "DisneyMusic" contains no such signal.
Channel-to-performer is content knowledge, not a string transform.

Usage:
    python recredit_draft_song.py <draft_ref> <song_id> --artist "Descendants Cast"
    python recredit_draft_song.py <draft_ref> <song_id> --title "Un Vicio"
    python recredit_draft_song.py <draft_ref> <song_id> --title "X" --artist "Y" \
        --reason "credited to the upload channel"

No lyrics, no calibration, no Anthropic call -- this is a string correction.
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
    p.add_argument("--title", default=None, help="Corrected title. Omit to leave unchanged.")
    p.add_argument("--artist", default=None, help="Corrected artist. Omit to leave unchanged.")
    p.add_argument("--reason", default=None,
                   help="Why the credit was wrong. Recorded on the audit row; worth writing.")
    args = p.parse_args()

    if not args.title and not args.artist:
        print("supply --title, --artist, or both", file=sys.stderr)
        return 2

    key = os.environ.get("RC_LYRICS_SUPPLY_KEY")
    if not key:
        print("RC_LYRICS_SUPPLY_KEY not set in backend/.env", file=sys.stderr)
        return 2

    url = (
        f"https://api.risingcompass.net/api/admin/agent/drafts/"
        f"{args.draft_ref}/songs/{args.song_id}/recredit"
    )
    payload = {}
    if args.title:
        payload["title"] = args.title
    if args.artist:
        payload["artist"] = args.artist
    if args.reason:
        payload["reason"] = args.reason

    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", "X-Lyrics-Supply-Key": key},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()}", file=sys.stderr)
        return 1

    song = next((s for s in resp.get("songs", []) if s["id"] == args.song_id), None)
    if song:
        print(f"OK  pos={song['position']}  -> {song['title']!r} by {song['artist']!r}")
    else:
        print("OK (recredited; song not in response)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
