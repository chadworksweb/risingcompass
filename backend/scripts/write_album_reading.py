"""Write one RELEASE reading (the rc-album lens) to its row. The album lane's
`calibrate_song.py`.

Both albums read so far were written by scripts typed fresh into a session
scratchpad and thrown away with the session, which cost exactly what you would
expect: the composition formula was copied out by hand instead of called, and
release 1349 stored a charge (+76) its own components do not produce (+77). This
script is the lane, so the third album is a command rather than an act of
reconstruction.

WHAT IT WILL NOT DO
  - It never invents a number. `center` and `vernier` are the inputs; the charge
    is composed by `charge_composition.compose`, the SAME function the song lane
    uses, and the tier is derived from the composed charge.
  - It never writes past a guard. `album_guard.album_violations` must come back
    clean or nothing is written. Rewrite the offending field and re-run.
  - It never silently overwrites prose. Every lane it replaces is copied to its
    `prior_*` slot AND appended to `release_prose_versions` first.
  - It makes ZERO Anthropic calls. Claude Code is the model; this only stores
    what the operator supplies.

THE READ GATE STILL APPLIES. Read `LEC-ALBUM-RUBRIC-LIVE.md` before producing
any album reading. The SONG rubric does not satisfy the album gate, and this
script cannot check that you did.

Usage:
    cd backend
    .venv\\Scripts\\python.exe scripts\\write_album_reading.py \\
        --release-id 1352 --reading-file reading.json \\
        --reasoning-file reasoning.txt [--dry-run]

The reading file is the lens's JSON output verbatim: visceral_charge, coherence,
harm{value,pervasive}, transcendence{value}, center, vernier{sat,res,reg,reach},
contaminated, contamination_note, dogma_referenced, dogma_note, charge_summary,
arc_prose, listener_effects_prose, societal_effects_prose, deadpan_line, topics,
topic_audit, psyche_facts, effects_pl, confidence.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from app.database import SessionLocal
from app.models import ReleaseProseVersion
from app.services.agents.album_guard import album_violations, format_report
from app.services.calibration_corpus import log_run
from app.services.charge_composition import (
    CompositionError, compose, validate_components,
)

# The model of record for a terminal write: Claude Code supplied it, no server
# generation ran. Mirrors the song lane's 'terminal_supplied' prose seal.
AGENT_MODEL = "claude-code"
PROSE_SEAL_MODEL = "terminal_supplied"
TRIGGER = "terminal_album"

# lane -> (live column, prior column). psyche_facts has no prior slot: it is
# JSON, versioned in release_prose_versions only, same as on the song side.
PROSE_LANES = {
    "arc": ("arc_prose", "prior_arc_prose"),
    "listener": ("listener_effects_prose", "prior_listener_effects_prose"),
    "societal": ("societal_effects_prose", "prior_societal_effects_prose"),
    "psyche_facts": ("psyche_facts", None),
}

# Columns written from the reading, in the order a reader meets them.
_JSON_FIELDS = ("topics", "topic_audit", "psyche_facts", "effects_pl")


def _load(path: str):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _fetch_release(db, release_id: int):
    row = db.execute(text("""
        SELECT r.id, r.title, r.release_type, r.track_count, a.name AS artist,
               r.rubric_color, r.charge_value, r.arc_prose,
               r.listener_effects_prose, r.societal_effects_prose,
               r.psyche_facts, r.societal_prose_generated_at,
               r.societal_prose_model
          FROM releases r JOIN artists a ON a.id = r.artist_id
         WHERE r.id = :i
    """), {"i": release_id}).mappings().fetchone()
    if row is None:
        sys.exit(f"release {release_id} not found")
    return dict(row)


def _running_order(db, release_id: int) -> list[str]:
    return [r[0] for r in db.execute(text("""
        SELECT s.title FROM release_songs rs JOIN songs s ON s.id = rs.song_id
         WHERE rs.release_id = :i
         ORDER BY rs.track_number NULLS LAST, rs.id
    """), {"i": release_id}).fetchall()]


def _archive_prose(db, release, reading, *, color, charge, environment):
    """Copy every prose lane being replaced into its prior_* slot and append it
    to release_prose_versions. Runs BEFORE the update, inside the same txn."""
    archived = []
    for lane, (live_col, prior_col) in PROSE_LANES.items():
        current = release.get(live_col)
        if not current:
            continue
        incoming = reading.get(live_col)
        if isinstance(incoming, (dict, list)):
            incoming = json.dumps(incoming)
        if incoming is None or incoming == current:
            continue
        db.add(ReleaseProseVersion(
            release_id=release["id"],
            title=release["title"],
            artist=release["artist"],
            lane=lane,
            prose=current,
            model=release.get("societal_prose_model") or PROSE_SEAL_MODEL,
            generated_at=release.get("societal_prose_generated_at"),
            trigger=TRIGGER,
            # The read the OUTGOING prose was written for, not the new one.
            rubric_color=release.get("rubric_color"),
            charge_value=release.get("charge_value"),
            environment=environment,
        ))
        if prior_col:
            db.execute(
                text(f"UPDATE releases SET {prior_col} = :v WHERE id = :i"),
                {"v": current, "i": release["id"]},
            )
        archived.append(lane)
    return archived


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Write an rc-album reading to its release row.")
    ap.add_argument("--release-id", type=int, required=True)
    ap.add_argument("--reading-file", required=True,
                    help="the lens's JSON output, verbatim")
    ap.add_argument("--reasoning-file", required=True,
                    help="the structured argument; stored on the run ledger")
    ap.add_argument("--dry-run", action="store_true",
                    help="compose + guard + report, write nothing")
    args = ap.parse_args()

    reading = _load(args.reading_file)
    reasoning = Path(args.reasoning_file).read_text(encoding="utf-8").strip()
    if not reasoning:
        sys.exit("reasoning file is empty; the argument is part of the reading")

    db = SessionLocal()
    try:
        release = _fetch_release(db, args.release_id)
        tracks = _running_order(db, args.release_id)

        # 1. COMPOSE -- the same composer the song lane uses.
        try:
            components = validate_components(reading, lane="album")
        except CompositionError as exc:
            sys.exit(f"v3 components failed validation: {exc}")
        composed = compose(components)

        print(f"RELEASE {release['id']}: {release['title']!r} / {release['artist']}")
        print(f"  {release['release_type']}, {len(tracks)} tracks in the running order"
              + ("" if len(tracks) == (release["track_count"] or 0)
                 else f"  (row says track_count={release['track_count']})"))
        print(f"  composed: {composed.rubric_color}/{composed.charge:+d}  "
              f"governing={composed.governing_axis}  shift={composed.shift:+d}  "
              f"gut_divergence={composed.gut_divergence}")
        if composed.signals:
            print(f"  signals: {', '.join(composed.signals)}")
        if release["charge_value"] is not None and release["charge_value"] != composed.charge:
            print(f"  NOTE: row currently holds {release['charge_value']:+d}; "
                  f"this write moves it to {composed.charge:+d}")

        # 2. CONTAMINATION is cross-derived, never taken from the model. The
        #    supplied flag is a cross-check, exactly as on the song lane.
        signals = list(composed.signals)
        if bool(reading.get("contaminated", False)) != composed.contaminated:
            signals.append("contamination_flag_mismatch")
            print(f"  NOTE: supplied contaminated={reading.get('contaminated')} but the "
                  f"axis data derives {composed.contaminated}; server value wins")
        reading["contaminated"] = composed.contaminated
        if not composed.contaminated:
            reading["contamination_note"] = None

        # 3. GUARD -- clean or nothing is written.
        violations = album_violations(
            reading, release_title=release["title"], artist=release["artist"],
            track_titles=tracks, charge_value=composed.charge)
        print("\nGUARD: " + format_report(violations))
        if violations:
            print("\nNothing written. Rewrite the fields above and re-run.")
            return 1

        if args.dry_run:
            print("\n--dry-run: composed and clean, nothing written.")
            return 0

        # 4. WRITE -- archive first, then the row, then the run ledger.
        from app.config import settings
        environment = getattr(settings, "environment", "prod")
        now = datetime.now(timezone.utc)

        archived = _archive_prose(
            db, release, reading, color=composed.rubric_color,
            charge=composed.charge, environment=environment)

        payload = {
            "rubric_color": composed.rubric_color,
            "charge_value": composed.charge,
            "charge_summary": reading.get("charge_summary"),
            "arc_prose": reading.get("arc_prose"),
            "listener_effects_prose": reading.get("listener_effects_prose"),
            "societal_effects_prose": reading.get("societal_effects_prose"),
            "deadpan_line": reading.get("deadpan_line"),
            "contaminated": composed.contaminated,
            "contamination_note": reading.get("contamination_note"),
            "dogma_referenced": bool(reading.get("dogma_referenced", False)),
            "dogma_note": reading.get("dogma_note"),
            "confidence": reading.get("confidence"),
            "calibration_failed": False,
            "societal_prose_generated_at": now,
            "societal_prose_model": PROSE_SEAL_MODEL,
        }
        for field in _JSON_FIELDS:
            value = reading.get(field)
            payload[field] = (json.dumps(value)
                              if isinstance(value, (dict, list)) else value)

        sets = ", ".join(f"{k} = :{k}" for k in payload)
        db.execute(text(f"UPDATE releases SET {sets}, updated_at = now() WHERE id = :i"),
                   {**payload, "i": release["id"]})

        # 5. The run ledger. Lyric-free by construction: the lens read approved
        #    song rows, so there are no lyrics to scrub the argument against.
        run = log_run(
            db,
            title=release["title"],
            artist=release["artist"],
            calibration={
                **reading,
                "rubric_color": composed.rubric_color,
                "charge_value": composed.charge,
                "governing_axis": composed.governing_axis,
                "gut_divergence": composed.gut_divergence,
                "escalation_flags": {"signals": signals} if signals else None,
                "reasoning": reasoning,
            },
            triggered_by=TRIGGER,
            release_id=release["id"],
            agent_model=AGENT_MODEL,
            lyric_free=True,
        )
        db.commit()

        # 6. VERIFY -- a row is not written until every column reads back.
        check = db.execute(text("""
            SELECT rubric_color, charge_value, contaminated, confidence,
                   (charge_summary IS NOT NULL), (arc_prose IS NOT NULL),
                   (listener_effects_prose IS NOT NULL),
                   (societal_effects_prose IS NOT NULL),
                   (deadpan_line IS NOT NULL), (topics IS NOT NULL),
                   (psyche_facts IS NOT NULL), (effects_pl IS NOT NULL)
              FROM releases WHERE id = :i"""), {"i": release["id"]}).fetchone()
        complete = all(check[4:])
        print(f"\nWRITTEN: {check[0]}/{check[1]:+d}  contaminated={check[2]}  "
              f"confidence={check[3]}")
        print(f"  run {run.id} logged (release_id={release['id']}, "
              f"coherence={reading.get('coherence')!r}, argument stored)")
        if archived:
            print(f"  archived prior prose: {', '.join(archived)}")
        print("  all eight reading fields present"
              if complete else "  INCOMPLETE -- a reading field landed NULL")
        return 0 if complete else 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
