"""Terminal-mode song calibration. Claude Code is the model.

Sends lyrics + a Claude-Code-supplied calibration to the supply-lyrics
endpoint. Server skips every Anthropic call (calibrator, ether tagger,
effects prose, societal effects prose, editorial regen) because the
calibration object is supplied. The compass_songs row is created via
_store_calibration and linked to the draft_song.

Why not supply_lyrics.py: that script sends lyrics only and hits the
on-server Anthropic calibrator. Why not correct_song.py: that script
only updates draft_song values and only mirrors to compass_songs if the
draft_song already has a compass_song_id, so a brand-new uncalibrated
song's calibration is lost on draft publish.

Usage:
    cat lyrics.txt | python calibrate_song.py <draft_ref> <song_id> \\
        --color blue --charge 30 \\
        --summary "Witness's lament for..." \\
        [--contaminated --contam-note "..."] \\
        [--dogma --dogma-note "..."] \\
        [--effects-prose-file path/to/prose.txt] \\
        [--societal-prose-file path/to/societal.txt]
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
    p.add_argument("--color", required=True, choices=["violet", "blue", "green", "orange", "red"])
    p.add_argument("--charge", required=True, type=int)
    p.add_argument("--summary", required=True)
    p.add_argument("--contaminated", action="store_true")
    p.add_argument("--contam-note", default=None)
    p.add_argument("--dogma", action="store_true")
    p.add_argument("--dogma-note", default=None)
    p.add_argument("--confidence", type=float, default=1.0)
    p.add_argument("--effects-prose-file", default=None, help="Path to a UTF-8 text file with the two-paragraph effects prose.")
    p.add_argument("--societal-prose-file", default=None, help="Path to a UTF-8 text file with the societal effects prose.")
    p.add_argument("--lyrics-file", default=None, help="Read lyrics from a file instead of stdin.")
    args = p.parse_args()

    key = os.environ.get("RC_LYRICS_SUPPLY_KEY")
    if not key:
        print("RC_LYRICS_SUPPLY_KEY not set in backend/.env", file=sys.stderr)
        return 2

    if args.lyrics_file:
        lyrics = Path(args.lyrics_file).read_text(encoding="utf-8")
    else:
        lyrics = sys.stdin.read()
    if len(lyrics.strip()) < 50:
        print("lyrics must be at least 50 chars", file=sys.stderr)
        return 2

    calibration: dict = {
        "rubric_color": args.color,
        "charge_value": args.charge,
        "charge_summary": args.summary,
        "contaminated": bool(args.contaminated),
        "contamination_note": args.contam_note,
        "dogma_referenced": bool(args.dogma),
        "dogma_note": args.dogma_note,
        "confidence": args.confidence,
    }
    if args.effects_prose_file:
        calibration["effects_prose"] = Path(args.effects_prose_file).read_text(encoding="utf-8").strip()
    if args.societal_prose_file:
        calibration["societal_effects_prose"] = Path(args.societal_prose_file).read_text(encoding="utf-8").strip()

    url = (
        f"https://api.risingcompass.net/api/admin/agent/drafts/"
        f"{args.draft_ref}/songs/{args.song_id}/lyrics"
    )
    body = json.dumps({"lyrics": lyrics, "calibration": calibration}).encode()
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
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()}", file=sys.stderr)
        return 1

    song = next((s for s in resp.get("songs", []) if s["id"] == args.song_id), None)
    if song:
        print(
            f"OK  pos={song['position']}  {song['title']!r}  "
            f"-> {song['rubric_color']}/{song.get('charge_value')}  "
            f"compass_song_id={song.get('compass_song_id')}"
        )
    else:
        print("OK (song not in response)")

    remaining = sum(1 for s in resp.get("songs", []) if s.get("rubric_color") is None)
    print(f"Remaining needs-lyrics in draft: {remaining}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
