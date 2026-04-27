"""Supply lyrics for a pending draft song from the terminal.

Usage:
    cat lyrics.txt | python supply_lyrics.py <draft_ref> <song_id>
    # or
    python supply_lyrics.py <draft_ref> <song_id> < lyrics.txt

Reads lyrics from stdin (so they don't sit on disk), pulls
RC_LYRICS_SUPPLY_KEY from backend/.env, POSTs to api.risingcompass.net.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Windows default cp1252 stdout chokes on unicode glyphs in the response
# print. Force UTF-8 so the script never crashes after a successful POST.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: supply_lyrics.py <draft_ref> <song_id>", file=sys.stderr)
        return 2

    draft_ref, song_id_str = sys.argv[1], sys.argv[2]
    try:
        song_id = int(song_id_str)
    except ValueError:
        print(f"song_id must be int, got {song_id_str!r}", file=sys.stderr)
        return 2

    key = os.environ.get("RC_LYRICS_SUPPLY_KEY")
    if not key:
        print("RC_LYRICS_SUPPLY_KEY not set in backend/.env", file=sys.stderr)
        return 2

    lyrics = sys.stdin.read()
    if not lyrics.strip():
        print("No lyrics on stdin", file=sys.stderr)
        return 2

    url = (
        f"https://api.risingcompass.net/api/admin/agent/drafts/"
        f"{draft_ref}/songs/{song_id}/lyrics"
    )
    body = json.dumps({"lyrics": lyrics}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Lyrics-Supply-Key": key,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()}", file=sys.stderr)
        return 1

    song = next(
        (s for s in resp.get("songs", []) if s["id"] == song_id), None
    )
    if song:
        print(
            f"OK  pos={song['position']}  {song['title']!r}  "
            f"→  {song['rubric_color']}  charge={song.get('charge_value')}  "
            f"conf={song.get('confidence')}"
        )
    else:
        print("OK (calibrated, song not in response)")

    remaining = sum(
        1 for s in resp.get("songs", []) if s.get("rubric_color") is None
    )
    print(f"Remaining needs-lyrics in draft: {remaining}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
