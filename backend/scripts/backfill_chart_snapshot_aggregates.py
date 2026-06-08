"""One-off: stamp compass_degree + charge_level on already-published chart
snapshots written BEFORE migration 095 (when the per-day aggregate wasn't
stored). Without this, those days have no value for the chart-agnostic Calendar
to paint, so the Daily Downloads dial would skip them.

Recomputes the aggregate exactly as the daily reading does: each song's charge
looked up live against the unified songs table, weighted by chart position
(compute_degree). Idempotent -- only touches published rows where
compass_degree IS NULL. Safe to re-run.

Run inside the backend container AFTER migration 095 has applied (the columns
must exist):

    docker compose exec -T backend python scripts/backfill_chart_snapshot_aggregates.py
"""

from app.database import SessionLocal
from app.models import ChartSnapshot
from app.services.charge_calc import degree_to_charge
from app.services.compass_calc import compute_degree
from app.services.song_store import find_song_by_title_artist


def main():
    db = SessionLocal()
    try:
        pairs = (
            db.query(ChartSnapshot.chart_source, ChartSnapshot.date)
            .filter(
                ChartSnapshot.published.is_(True),
                ChartSnapshot.compass_degree.is_(None),
            )
            .distinct()
            .all()
        )
        if not pairs:
            print("Nothing to backfill -- all published snapshots already have an aggregate.")
            return

        updated = 0
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
                song = find_song_by_title_artist(db, r.title, r.artist)
                song_dicts.append({
                    "position": r.position,
                    "charge_value": song.charge_value if song else None,
                    "rubric_color": song.rubric_color if song else "green",
                })
            degree = compute_degree(song_dicts)
            charge = degree_to_charge(degree)
            for r in rows:
                r.compass_degree = degree
                r.charge_level = charge
            updated += len(rows)
            print(f"  {chart_source} {snap_date}: degree={degree} charge={charge} ({len(rows)} rows)")

        db.commit()
        print(f"DONE: {updated} rows updated across {len(pairs)} snapshot day(s).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
