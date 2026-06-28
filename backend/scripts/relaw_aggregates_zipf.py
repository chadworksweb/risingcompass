"""One-off: re-law every STORED group aggregate onto the scale-invariant Zipf
rank weighting (compass_calc.position_weight = 1/rank**0.7, shipped 2026-06-28).

Group charges are persisted, not recomputed on read:
  - DailyReading.compass_degree / charge_level  (the Daily Listens dial + the
    Calendar + the recent-readings sparkline all serve the STORED value, see
    routers/compass.py)
  - ChartSnapshot.compass_degree / charge_level (denormalised per published
    (date, chart_source) day, painted by the Calendar)

Both were stamped under the OLD size-scaled linear ramp. This recomputes them
with the live compute_degree (now Zipf) so history is shown under one law.
The live-year / drift views already recompute on read, so they need nothing;
this script only touches the two persisted stores.

OVERWRITES published historical numbers BY DESIGN (the new law is the correct
one; see RISING-COMPASS-CHARGE-WEIGHTING.md section 6). Effectively idempotent:
re-running recomputes to the same values. Does NOT touch contamination_count,
editorial, or any song-level calibration -- only the rank-weighted aggregate.

Run inside the backend container AFTER the Zipf change is deployed:

    docker compose exec -T backend python -m scripts.relaw_aggregates_zipf
"""

from app.database import SessionLocal
from app.models import ChartSnapshot, DailyReading
from app.services.charge_calc import degree_to_charge
from app.services.compass_calc import compute_degree
from app.services.song_store import find_song_by_title_artist


def _relaw_daily_readings(db) -> tuple[int, float]:
    """Recompute every DailyReading from its ReadingSong -> Song. Returns
    (rows_changed, max_abs_degree_shift)."""
    readings = (
        db.query(DailyReading)
        .order_by(DailyReading.date)
        .all()
    )
    changed = 0
    max_shift = 0.0
    for reading in readings:
        song_dicts = [
            {
                "position": rs.position,
                "charge_value": rs.song.charge_value if rs.song else None,
                "rubric_color": rs.song.rubric_color if rs.song else "green",
            }
            for rs in reading.songs
        ]
        if not song_dicts:
            continue
        new_degree = compute_degree(song_dicts)
        new_charge = degree_to_charge(new_degree)
        old_degree = reading.compass_degree
        if old_degree is None or abs(new_degree - old_degree) >= 0.05 \
                or reading.charge_level != new_charge:
            shift = abs(new_degree - (old_degree if old_degree is not None else new_degree))
            max_shift = max(max_shift, shift)
            print(f"  daily {reading.date}: {old_degree} -> {new_degree} "
                  f"({reading.charge_level} -> {new_charge})")
            reading.compass_degree = new_degree
            reading.charge_level = new_charge
            changed += 1
    return changed, max_shift


def _relaw_chart_snapshots(db) -> tuple[int, float]:
    """Recompute every published (date, chart_source) snapshot day, excluding
    preorder rows from the aggregate (per the model contract). Returns
    (rows_changed, max_abs_degree_shift)."""
    pairs = (
        db.query(ChartSnapshot.chart_source, ChartSnapshot.date)
        .filter(ChartSnapshot.published.is_(True))
        .distinct()
        .all()
    )
    changed = 0
    max_shift = 0.0
    for chart_source, snap_date in pairs:
        rows = (
            db.query(ChartSnapshot)
            .filter(
                ChartSnapshot.chart_source == chart_source,
                ChartSnapshot.date == snap_date,
                ChartSnapshot.published.is_(True),
            )
            .all()
        )
        song_dicts = []
        for r in rows:
            if r.preorder:
                continue  # excluded from the snapshot aggregate
            song = find_song_by_title_artist(db, r.title, r.artist)
            song_dicts.append({
                "position": r.position,
                "charge_value": song.charge_value if song else None,
                "rubric_color": song.rubric_color if song else "green",
            })
        if not song_dicts:
            continue
        new_degree = compute_degree(song_dicts)
        new_charge = degree_to_charge(new_degree)
        old_degree = rows[0].compass_degree
        if old_degree is None or abs(new_degree - old_degree) >= 0.05 \
                or rows[0].charge_level != new_charge:
            shift = abs(new_degree - (old_degree if old_degree is not None else new_degree))
            max_shift = max(max_shift, shift)
            print(f"  {chart_source} {snap_date}: {old_degree} -> {new_degree} "
                  f"({rows[0].charge_level} -> {new_charge}) [{len(rows)} rows]")
            for r in rows:
                r.compass_degree = new_degree
                r.charge_level = new_charge
            changed += len(rows)
    return changed, max_shift


def main():
    db = SessionLocal()
    try:
        print("=== DailyReading ===")
        d_changed, d_shift = _relaw_daily_readings(db)
        print("=== ChartSnapshot ===")
        c_changed, c_shift = _relaw_chart_snapshots(db)
        db.commit()
        print(f"\nDONE. DailyReading rows changed: {d_changed} "
              f"(max degree shift {round(d_shift, 1)}). "
              f"ChartSnapshot rows changed: {c_changed} "
              f"(max degree shift {round(c_shift, 1)}).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
