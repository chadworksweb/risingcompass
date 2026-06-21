"""Apply a Claude-Code-supplied verdict to an already-PUBLISHED library song.

The published-song analogue of correct_song.py (which is draft-only). Uses
RC_LYRICS_SUPPLY_KEY, no admin session needed. The server runs NO model: it
writes the supplied tier/charge straight onto the song, supersedes prior runs,
and logs the correction as a fresh calibration_run (visible in the Runs admin),
plus an immutable SongRecalibration audit row.

Prose + ether are OPTIONAL and, like the tier/charge, terminal-SUPPLIED (zero
Anthropic). When --listener-effects-prose-file / --societal-prose-file are
passed, the server archives the prior prose to prior_* and re-seals the societal
provenance as terminal_supplied. Omit them and the prior prose stands. (The only
server prose-REGENERATION path runs Opus and is not used from terminal.)

Usage:
    python correct_published_song.py <song_id> --color red --charge -82 \\
        --not-contaminated \\
        --summary "..." \\
        --public-summary "Why this changed (>=20 chars, public-facing)." \\
        [--rubric-note "charge-anchor corpus correction"] \\
        [--reasoning-file path] [--lyrics-file path] \\
        [--listener-effects-prose-file path] [--societal-prose-file path] \\
        [--deadpan-line "..."] [--topic slug --topic slug]

Color must be one of: violet, blue, green, orange, red. The charge must fall in
that color's tier band and orange/red cannot be contaminated -- the server
enforces both. Topics (max 3) require --deadpan-line + --societal-prose-file and
must be valid ether taxonomy slugs -- the server enforces that too.
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

API_BASE = os.environ.get("RC_API_BASE", "https://api.risingcompass.net")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("song_id", type=int)
    p.add_argument("--color", required=True,
                   choices=["violet", "blue", "green", "orange", "red"])
    p.add_argument("--charge", type=int, required=True)
    p.add_argument("--contaminated", action="store_true")
    p.add_argument("--not-contaminated", action="store_true")
    p.add_argument("--contam-note", default=None)
    p.add_argument("--summary", default=None)
    p.add_argument("--public-summary", required=True,
                   help="Public-facing story of the change (>=20 chars).")
    p.add_argument("--internal-notes", default=None)
    p.add_argument("--rubric-note", default=None)
    p.add_argument("--reasoning", default=None)
    p.add_argument("--reasoning-file", default=None)
    p.add_argument("--lyrics-file", default=None,
                   help="Lyrics file: used only to scrub the stored reasoning; never persisted.")
    p.add_argument("--listener-effects-prose-file", default=None,
                   help="UTF-8 file with the two-paragraph listener-effects prose (supplied, not generated).")
    p.add_argument("--societal-prose-file", default=None,
                   help="UTF-8 file with the societal-effects prose. Required when --topic is supplied.")
    p.add_argument("--deadpan-line", default=None,
                   help="Ether deadpan_line: flat literal naming of the song. Required when --topic is supplied.")
    p.add_argument("--topic", action="append", dest="topics", default=None,
                   help="Ether taxonomy slug, dominant-first. Repeatable, max 3.")
    p.add_argument("--model", default="claude-code")
    args = p.parse_args()

    # Local guard mirrors the server: topics need a deadpan line + societal prose.
    if args.topics:
        if not args.deadpan_line:
            print("--deadpan-line is required when --topic is supplied", file=sys.stderr)
            return 2
        if not args.societal_prose_file:
            print("--societal-prose-file is required when --topic is supplied", file=sys.stderr)
            return 2

    key = os.environ.get("RC_LYRICS_SUPPLY_KEY")
    if not key:
        print("RC_LYRICS_SUPPLY_KEY not set in backend/.env", file=sys.stderr)
        return 2

    reasoning = args.reasoning
    if args.reasoning_file:
        reasoning = Path(args.reasoning_file).read_text(encoding="utf-8")
    lyrics = None
    if args.lyrics_file:
        lyrics = Path(args.lyrics_file).read_text(encoding="utf-8")

    body: dict = {
        "song_source": "songs",
        "song_id": args.song_id,
        "rubric_color": args.color,
        "charge_value": args.charge,
        "public_summary": args.public_summary,
    }
    if args.contaminated:
        body["contaminated"] = True
    if args.not_contaminated:
        body["contaminated"] = False
    if args.contam_note is not None:
        body["contamination_note"] = args.contam_note
    if args.summary is not None:
        body["charge_summary"] = args.summary
    if args.internal_notes is not None:
        body["internal_notes"] = args.internal_notes
    if args.rubric_note is not None:
        body["rubric_change_note"] = args.rubric_note
    if reasoning is not None:
        body["reasoning"] = reasoning
    if lyrics is not None:
        body["lyrics"] = lyrics
    if args.model:
        body["agent_model"] = args.model
    if args.listener_effects_prose_file:
        body["listener_effects_prose"] = Path(args.listener_effects_prose_file).read_text(encoding="utf-8").strip()
    if args.societal_prose_file:
        body["societal_effects_prose"] = Path(args.societal_prose_file).read_text(encoding="utf-8").strip()
    if args.deadpan_line is not None:
        body["deadpan_line"] = args.deadpan_line
    if args.topics:
        body["topics"] = args.topics

    url = f"{API_BASE}/api/admin/recalibrations/apply-supplied"
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "X-Lyrics-Supply-Key": key},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()}", file=sys.stderr)
        return 1

    b = resp.get("before", {})
    a = resp.get("after", {})
    print(
        f"OK  song_id={resp.get('song_id')} ({resp.get('title')})  "
        f"{b.get('color')}/{b.get('charge')} -> {a.get('color')}/{a.get('charge')}  "
        f"run_id={resp.get('run_id')}  superseded={resp.get('superseded_runs')}  "
        f"reasoning_stored={resp.get('reasoning_stored')}  "
        f"prose={resp.get('prose_rewritten')}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
