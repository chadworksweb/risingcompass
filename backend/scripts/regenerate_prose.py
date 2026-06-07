"""Regenerate listener_effects_prose + societal_effects_prose for one song from the terminal.

Archives the previous prose to the prior_* columns before overwriting.
Re-stamps the provenance seal so the 16:00 UTC sweep re-anchors the new version.

Auth: RC_LYRICS_SUPPLY_KEY from backend/.env (same key used by supply_lyrics /
correct_song). No admin session required.

Lyrics must be provided -- they are never stored in the DB. Default source
is Dropbox/Debug/dd.txt (the standard lyrics drop path). Pass --lyrics-file
to override.

Usage:
    python scripts/regenerate_prose.py --source compass --song-id 1234
    python scripts/regenerate_prose.py --source library --song-id 56 --lyrics-file path/to/lyrics.txt
    python scripts/regenerate_prose.py --source submitted --song-id 789 --url http://localhost:8000

Sources: compass, library, submitted, stream
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

DD_TXT = Path.home() / "Dropbox" / "Debug" / "dd.txt"


def main() -> int:
    p = argparse.ArgumentParser(description="Regenerate prose for one song")
    p.add_argument("--source", default="compass",
                   choices=["compass", "library", "submitted", "stream"],
                   help="Which table (default: compass)")
    p.add_argument("--song-id", type=int, required=True, dest="song_id",
                   help="Primary key of the song row")
    p.add_argument("--lyrics-file", default=None, dest="lyrics_file",
                   help=f"Path to lyrics file (default: {DD_TXT})")
    p.add_argument("--url", default="http://localhost:8000",
                   help="Backend base URL (default: http://localhost:8000)")
    args = p.parse_args()

    key = os.environ.get("RC_LYRICS_SUPPLY_KEY")
    if not key:
        print("RC_LYRICS_SUPPLY_KEY not set in backend/.env", file=sys.stderr)
        return 2

    lyrics_path = Path(args.lyrics_file) if args.lyrics_file else DD_TXT
    if not lyrics_path.exists():
        print(f"Lyrics file not found: {lyrics_path}", file=sys.stderr)
        print("Drop lyrics into dd.txt or pass --lyrics-file <path>", file=sys.stderr)
        return 2

    lyrics = lyrics_path.read_text(encoding="utf-8").strip()
    if not lyrics:
        print(f"Lyrics file is empty: {lyrics_path}", file=sys.stderr)
        return 2

    payload = json.dumps({
        "source": args.source,
        "song_id": args.song_id,
        "lyrics": lyrics,
    }).encode("utf-8")

    url = f"{args.url.rstrip('/')}/api/admin/prose/regenerate"
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-Lyrics-Supply-Key": key,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"HTTP {e.code}: {body}", file=sys.stderr)
        return 1

    print(f"source:  {result['source']}")
    print(f"song_id: {result['song_id']}")
    print(f"title:   {result['title']}")
    print(f"artist:  {result['artist']}")
    print()
    if result.get("listener_effects_prose_changed"):
        print("[listener_effects_prose] regenerated")
        if result.get("listener_effects_prose"):
            print(result["listener_effects_prose"][:300] + "..." if len(result["listener_effects_prose"]) > 300 else result["listener_effects_prose"])
    else:
        print("[listener_effects_prose] skipped (generation failed -- check logs)")
    print()
    if result.get("societal_prose_changed"):
        print(f"[societal_effects_prose] regenerated  model={result.get('societal_prose_model')}")
        if result.get("societal_effects_prose"):
            snippet = result["societal_effects_prose"]
            print(snippet[:300] + "..." if len(snippet) > 300 else snippet)
    else:
        print("[societal_effects_prose] skipped (generation failed -- check logs)")
    print()
    if result.get("prior_listener_effects_prose"):
        print("[prior_listener_effects_prose] archived (first 120 chars):")
        print(result["prior_listener_effects_prose"][:120] + "...")
    else:
        print("[prior_listener_effects_prose] was NULL -- nothing to archive")
    return 0


if __name__ == "__main__":
    sys.exit(main())
