"""Apply a pre-publish correction to a draft song from the terminal.

Same pattern as supply_lyrics.py — uses RC_LYRICS_SUPPLY_KEY, no admin
session needed. Use this when the agent's calibration is wrong and you
want to override it before approval. The correction is logged in
pre_publish_corrections and mirrored to compass_songs.

Usage:
    python correct_song.py <draft_ref> <song_id> --color green --charge 18 \\
        [--contaminated] [--contam-note "..."] [--summary "..."] \\
        [--rationale "..."] [--tags "inflation,..."]

Color must be one of: violet, blue, green, orange, red.
Charge must fall inside the tier's range; the server enforces this.
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
    p.add_argument("--color", choices=["violet", "blue", "green", "orange", "red"])
    p.add_argument("--charge", type=int)
    p.add_argument("--contaminated", action="store_true")
    p.add_argument("--not-contaminated", action="store_true")
    p.add_argument("--contam-note", default=None)
    p.add_argument("--summary", default=None)
    p.add_argument("--rationale", default=None)
    p.add_argument("--tags", default=None)
    args = p.parse_args()

    key = os.environ.get("RC_LYRICS_SUPPLY_KEY")
    if not key:
        print("RC_LYRICS_SUPPLY_KEY not set in backend/.env", file=sys.stderr)
        return 2

    body: dict = {}
    if args.color is not None:
        body["rubric_color"] = args.color
    if args.charge is not None:
        body["charge_value"] = args.charge
    if args.contaminated:
        body["contaminated"] = True
    if args.not_contaminated:
        body["contaminated"] = False
    if args.contam_note is not None:
        body["contamination_note"] = args.contam_note
    if args.summary is not None:
        body["charge_summary"] = args.summary
    if args.rationale is not None:
        body["human_rationale"] = args.rationale
    if args.tags is not None:
        body["tags"] = args.tags

    if not body:
        print("nothing to change", file=sys.stderr)
        return 2

    url = (
        f"https://api.risingcompass.net/api/admin/agent/drafts/"
        f"{args.draft_ref}/songs/{args.song_id}/correct"
    )
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

    c = resp.get("correction", {})
    print(
        f"OK  draft_song_id={c.get('draft_song_id')}  "
        f"{c.get('before_rubric_color')}/{c.get('before_charge_value')} -> "
        f"{c.get('after_rubric_color')}/{c.get('after_charge_value')}"
    )
    if c.get("human_rationale"):
        print(f"  rationale: {c['human_rationale']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
