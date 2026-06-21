"""Load the Audience Resonance DEMO set into the DB (hard-fenced, is_synthetic).

Maps each fictional seed song to a REAL calibrated song of the SAME charge tier,
then inserts the seed's resonances onto those real songs as is_synthetic=true
rows. So the corpus ternary and one rich example song are populated with demo
data that lives ON real songs (dots link to real pages, the anomaly lands on a
real Corrupted song), while the public reads only surface this set in DARK mode
(feature_flags.is_audience_resonance_enabled == False) and exclude it the moment
the feature goes live.

Idempotent: clears existing is_synthetic resonances before loading. Reversible:
  python scripts/load_ar_demo.py --purge   # delete every is_synthetic row

Run with the track venv (DB reached via the tunnel, like the migrations):
  .venv/Scripts/python.exe scripts/load_ar_demo.py
This talks to the DB directly; it does NOT require the backend to be running.
"""

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # make `app` importable
load_dotenv(ROOT / ".env")

from app.database import SessionLocal  # noqa: E402
from app.models import Resonance, Song  # noqa: E402

SEED_PATH = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "..", "frontend",
    "audience-resonance", "seed-resonances.json",
))


def purge(db) -> int:
    n = db.query(Resonance).filter(Resonance.is_synthetic.is_(True)).delete(
        synchronize_session=False)
    db.commit()
    return n


def load(db) -> None:
    with open(SEED_PATH, encoding="utf-8") as f:
        seed = json.load(f)
    seed_songs = seed["songs"]
    seed_resonances = seed["resonances"]

    # Group seed songs by tier, then claim a distinct real song per seed song
    # from the matching rubric_color (deterministic: lowest id first).
    needed = {}
    for s in seed_songs:
        needed[s["tier"]] = needed.get(s["tier"], 0) + 1

    real_by_tier = {}
    for tier, count in needed.items():
        rows = (
            db.query(Song)
            .filter(Song.rubric_color == tier, Song.charge_value.isnot(None))
            .order_by(Song.id.asc())
            .limit(count)
            .all()
        )
        if len(rows) < count:
            raise SystemExit(
                f"Not enough real '{tier}' songs ({len(rows)}/{count}) to map the demo set."
            )
        real_by_tier[tier] = rows

    # seed song id -> real Song
    mapping = {}
    cursor = {tier: 0 for tier in real_by_tier}
    for s in seed_songs:
        tier = s["tier"]
        real = real_by_tier[tier][cursor[tier]]
        cursor[tier] += 1
        mapping[s["id"]] = real

    cleared = purge(db)

    inserted = 0
    for r in seed_resonances:
        real = mapping.get(r["song_id"])
        if real is None:
            continue
        db.add(Resonance(
            song_id=real.id,
            user_id=None,
            username=r["username"],
            story_text=r["story"],
            prop_true=r["true"],
            prop_camouflage=r["camouflage"],
            prop_adjacent=r["adjacent"],
            consent_tier=r.get("consent", "publish"),
            flag_state=r.get("flag", "none"),
            is_synthetic=True,
        ))
        inserted += 1
    db.commit()

    print(f"Cleared {cleared} prior synthetic rows.")
    print(f"Mapped {len(mapping)} seed songs to real songs across "
          f"{len(real_by_tier)} tiers; inserted {inserted} demo resonances.")
    # Echo the anomaly mapping (the Corrupted seed song with True-heavy stories).
    for s in seed_songs:
        if s["id"] == 2001 and 2001 in mapping:
            real = mapping[2001]
            print(f"Anomaly example '{s['title']}' -> real song "
                  f"id={real.id} '{real.title}' by {real.artist} "
                  f"(tier={real.rubric_color}, charge={real.charge_value}).")


def main():
    db = SessionLocal()
    try:
        if "--purge" in sys.argv:
            print(f"Purged {purge(db)} synthetic resonance rows.")
            return
        load(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
