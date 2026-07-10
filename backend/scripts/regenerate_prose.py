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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # make `app` importable for the tell-guard
load_dotenv(ROOT / ".env")

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

DD_TXT = Path.home() / "Dropbox" / "Debug" / "dd.txt"


def main() -> int:
    p = argparse.ArgumentParser(description="Regenerate prose for one song")
    p.add_argument("--source", default="songs",
                   choices=["songs", "compass", "library", "submitted", "stream"],
                   help="Which id space (default: songs = the unified song id; "
                        "the legacy names map via song_id_map)")
    p.add_argument("--song-id", type=int, required=True, dest="song_id",
                   help="Primary key of the song row")
    p.add_argument("--lyrics-file", default=None, dest="lyrics_file",
                   help=f"Path to lyrics file (default: {DD_TXT})")
    p.add_argument("--url", default="http://localhost:8000",
                   help="Backend base URL (default: http://localhost:8000)")
    p.add_argument("--listener-file", default=None, dest="listener_file",
                   help="Path to supplied listener_effects_prose (Claude Code is "
                        "the model; turns server generation OFF)")
    p.add_argument("--societal-file", default=None, dest="societal_file",
                   help="Path to supplied societal_effects_prose")
    p.add_argument("--deadpan", default=None,
                   help="Supplied deadpan_line (corrected ether naming)")
    p.add_argument("--topics", default=None,
                   help="Supplied topic slugs, comma-separated (validated against the ether taxonomy)")
    p.add_argument("--negative", action="store_true",
                   help="Song is genuinely degraded/corrupted (orange/red): allow real "
                        "corrosion language (turns the tell-guard Rule-O teardown check OFF).")
    p.add_argument("--force", action="store_true",
                   help="Write even if the tell-guard finds HARD AI-tells (bypass the gate).")
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

    payload_d = {
        "source": args.source,
        "song_id": args.song_id,
        "lyrics": lyrics,
    }
    if args.listener_file:
        payload_d["listener_effects_prose"] = Path(args.listener_file).read_text(encoding="utf-8").strip()
    if args.societal_file:
        payload_d["societal_effects_prose"] = Path(args.societal_file).read_text(encoding="utf-8").strip()
    if args.deadpan is not None:
        payload_d["deadpan_line"] = args.deadpan
    if args.topics is not None:
        payload_d["topics"] = [t.strip() for t in args.topics.split(",") if t.strip()]

    # AI-tell guard (deterministic, zero model calls). Terminal-supplied prose
    # bypasses the server's tell-guard + semantic judge, so lint it HERE before it
    # writes. HARD findings block the write unless --force. The semantic tells the
    # regex cannot see (circular / redundant / summary / flourish / downside) stay
    # the operator's job -- Claude Code is the judge on this path.
    from app.services.prose_tell_guard import hard_findings, scan, summarize
    tell_blocking = []
    for lane, field in (("listener", "listener_effects_prose"),
                        ("societal", "societal_effects_prose")):
        text = payload_d.get(field)
        if not text:
            continue
        hard = hard_findings(text, lane=lane, allow_deficit=args.negative)
        review = [f for f in scan(text, lane, allow_deficit=args.negative)
                  if f.severity == "review"]
        if review:
            print(f"[{lane}] review tells (non-blocking): {summarize(review)}", file=sys.stderr)
        if hard:
            tell_blocking.append(lane)
            print(f"[{lane}] HARD tells: {summarize(hard)}", file=sys.stderr)
            for f in hard:
                print(f"    {f.code}  {f.name}\n        -> {f.snippet!r}", file=sys.stderr)
    if tell_blocking and not args.force:
        print("Refusing to write tell-ridden prose (see HARD tells above). Fix them, "
              "or pass --force to override.", file=sys.stderr)
        return 2
    if tell_blocking and args.force:
        print("--force set: writing despite HARD tells.", file=sys.stderr)

    payload = json.dumps(payload_d).encode("utf-8")

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
