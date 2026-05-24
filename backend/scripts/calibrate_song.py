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
        [--societal-prose-file path/to/societal.txt] \\
        [--deadpan "Doomed-romance lament"] \\
        [--topic breakup --topic longing] \\
        [--topic-audit-reason "..." --topic-audit-tag slug \\
         --topic-audit-rationale "..."]

Ether Art Chart fields (deadpan_line + topics) are Claude-Code-supplied here
so the server skips the ether_tagger Anthropic call. topics must be slugs from
the closed 24-topic taxonomy (app/services/ether_taxonomy.ETHER_TAXONOMY),
ordered dominant-first, max 3. If no slug honestly fits, omit --topic and pass
the three --topic-audit-* args instead (mutually exclusive with --topic).

Supplying any ether field REQUIRES --societal-prose-file: writing topics turns
on the societal-effects-prose hook in record_and_reconcile, which would call
Anthropic if the prose were missing. Supplying it pre-fills the column so the
hook stays silent. See feedback_rc_no_api_in_terminal.
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
    sys.path.insert(0, str(ROOT))  # make `app` importable for taxonomy validation
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
    p.add_argument("--deadpan", default=None, help="Ether Art Chart deadpan_line: flat literal naming of the song (no period, ~len(artist)+len(title) chars).")
    p.add_argument("--topic", action="append", dest="topics", default=None, help="Ether taxonomy slug, dominant-first. Repeatable, max 3. Mutually exclusive with --topic-audit-*.")
    p.add_argument("--topic-audit-reason", default=None, help="Audit escape hatch: why no taxonomy slug fits (use when --topic is omitted).")
    p.add_argument("--topic-audit-tag", default=None, help="Audit escape hatch: proposed new slug.")
    p.add_argument("--topic-audit-rationale", default=None, help="Audit escape hatch: one-sentence rationale for the proposed slug.")
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

    # --- Ether Art Chart fields (deadpan_line + topics | topic_audit) ---
    audit_parts = [args.topic_audit_reason, args.topic_audit_tag, args.topic_audit_rationale]
    has_audit = any(audit_parts)
    ether_supplied = bool(args.deadpan) or bool(args.topics) or has_audit
    if ether_supplied:
        if not args.deadpan:
            print("--deadpan is required when supplying ether topics/audit", file=sys.stderr)
            return 2
        if args.topics and has_audit:
            print("--topic and --topic-audit-* are mutually exclusive", file=sys.stderr)
            return 2
        if not args.topics and not has_audit:
            print("supply either --topic (1-3) or all three --topic-audit-* args", file=sys.stderr)
            return 2
        if has_audit and not all(audit_parts):
            print("--topic-audit-reason, --topic-audit-tag, and --topic-audit-rationale must all be set", file=sys.stderr)
            return 2
        # Supplying topics turns on the societal-prose Anthropic hook in
        # record_and_reconcile; pre-filling the column keeps it silent.
        if not args.societal_prose_file:
            print("--societal-prose-file is required when supplying ether fields (see header)", file=sys.stderr)
            return 2
        if args.topics:
            from app.services.ether_taxonomy import VALID_SLUGS
            if len(args.topics) > 3:
                print(f"max 3 topics, got {len(args.topics)}", file=sys.stderr)
                return 2
            invalid = [t for t in args.topics if t not in VALID_SLUGS]
            if invalid:
                print(f"invalid taxonomy slug(s): {invalid}. Valid: {sorted(VALID_SLUGS)}", file=sys.stderr)
                return 2
            calibration["deadpan_line"] = args.deadpan
            calibration["topics"] = args.topics
            calibration["topic_audit"] = None
        else:
            calibration["deadpan_line"] = args.deadpan
            calibration["topics"] = []
            calibration["topic_audit"] = {
                "reason": args.topic_audit_reason,
                "proposed_tag": args.topic_audit_tag,
                "rationale": args.topic_audit_rationale,
            }

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

    if "deadpan_line" in calibration:
        if calibration.get("topic_audit"):
            print(f"    ether: {calibration['deadpan_line']!r}  topics=[] (audit: {calibration['topic_audit']['proposed_tag']})")
        else:
            print(f"    ether: {calibration['deadpan_line']!r}  topics={calibration['topics']}")

    remaining = sum(1 for s in resp.get("songs", []) if s.get("rubric_color") is None)
    print(f"Remaining needs-lyrics in draft: {remaining}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
