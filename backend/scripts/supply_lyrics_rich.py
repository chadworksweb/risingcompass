"""Richer wrapper around supply_lyrics.py — POSTs lyrics and prints the full
agent calibration (color, charge, summary, contamination, dogma, confidence)
so the operator can review what the agent did, the same way the daily
reading review SOP works.

Usage:
    cat lyrics.txt | python supply_lyrics_rich.py <draft_ref> <song_id>
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

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: supply_lyrics_rich.py <draft_ref> <song_id>", file=sys.stderr)
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
        headers={"Content-Type": "application/json", "X-Lyrics-Supply-Key": key},
    )
    try:
        with urllib.request.urlopen(req, timeout=240) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()}", file=sys.stderr)
        return 1

    song = next((s for s in resp.get("songs", []) if s["id"] == song_id), None)
    if not song:
        print("OK (calibrated, song not in response)")
        return 0

    cv = song.get("charge_value")
    cv_str = f"{'+' if cv is not None and cv > 0 else ''}{cv}" if cv is not None else "?"
    print(f"=== #{song['position']}  {song['title']} — {song['artist']}  (song_id={song_id}) ===")
    print(f"  color:        {song['rubric_color']}")
    print(f"  charge:       {cv_str}")
    print(f"  confidence:   {song.get('confidence')}")
    print(f"  contaminated: {song.get('contaminated')}")
    if song.get("contamination_note"):
        print(f"    note: {song['contamination_note']}")
    print(f"  dogma_ref:    {song.get('dogma_referenced')}")
    if song.get("dogma_note"):
        print(f"    note: {song['dogma_note']}")
    print(f"  charge_summary:")
    print(f"    {song.get('charge_summary') or '(empty)'}")

    remaining = sum(1 for s in resp.get("songs", []) if s.get("rubric_color") is None)
    print(f"\n  remaining needs-lyrics in draft: {remaining}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
