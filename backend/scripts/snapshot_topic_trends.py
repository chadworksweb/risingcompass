"""Snapshot the Topic Trends chart data for the recalibration plan.

Step 0 tooling of RISING-COMPASS-TOPIC-TRENDS-RECALIBRATION.md: every step of
the recalibration ends with a snapshot + diff, and this script is the snapshot.
It hits the live /api/topic-trends and /api/topic-trends/trailing endpoints on
prod and writes:

  - <date>-<label>.md            compact per-year + per-month tables (one row
                                 per period; a column for every diversity
                                 measure the endpoint exposes, discovered
                                 dynamically so Step-2's parallel measures
                                 appear without a script change)
  - <date>-<label>-yearly.json   raw yearly payload, verbatim
  - <date>-<label>-trailing.json raw trailing payload, verbatim

into "plans and docs/topic-trends-snapshots/" (Dropbox). Diffing two snapshot
.md files is how each step proves what it changed; the .json files carry the
full distributions for deeper digs.

Read-only against prod; safe to run any time.

Run:
    cd backend && .venv/Scripts/python.exe scripts/snapshot_topic_trends.py --label step0-baseline
"""
import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

from app.config import settings

# Fetch from the machine-API host, NOT the public root: the public root sits
# behind Cloudflare's bot challenge + the Scrape Shield, which 403 non-browser
# clients. A service-tier key bypasses the shield (same posture as
# bake_chart_ssr.py). Falls back to the public read key when unset.
API_BASE = os.environ.get("RC_API_BASE", "https://api.risingcompass.net")
API_KEY = settings.rc_service_key or "6f1fdd977f03bb39a1ee267fa1d9b6b534996745b1f56ef38994da94c7061e4b"

DEFAULT_OUT_DIR = (
    Path.home()
    / "Dropbox/Rising Compass/plans and docs/topic-trends-snapshots"
)

# Point fields that are structure, not measures. Any OTHER scalar field on a
# year/period point is treated as a diversity measure and gets its own column,
# so the parallel measures Step 2 adds show up here automatically.
STRUCTURAL_KEYS = {
    "year", "key", "label", "songs_with_topics", "total_pairs",
    "distinct_topics", "distribution",
}


def _fetch(path: str) -> dict:
    resp = httpx.get(API_BASE + path, headers={"X-Api-Key": API_KEY}, timeout=60)
    resp.raise_for_status()
    return resp.json()


def _measure_keys(points: list[dict]) -> list[str]:
    """Every non-structural scalar key across the points, first-seen order."""
    keys: list[str] = []
    for p in points:
        for k, v in p.items():
            if k in STRUCTURAL_KEYS or k in keys:
                continue
            if isinstance(v, (int, float)):
                keys.append(k)
    return keys


def _top_topic(point: dict) -> str:
    dist = point.get("distribution") or []
    if not dist:
        return "-"
    top = dist[0]
    return f"{top['topic']} {round(top['percent'] * 100, 1)}%"


def _table(points: list[dict], period_field: str) -> list[str]:
    measures = _measure_keys(points)
    header = (
        [period_field, "songs", "pairs", "tags/song", "distinct"]
        + measures + ["top topic"]
    )
    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "|".join("---" for _ in header) + "|",
    ]
    for p in points:
        songs = p.get("songs_with_topics") or 0
        pairs = p.get("total_pairs") or 0
        tps = round(pairs / songs, 2) if songs else 0.0
        row = [
            str(p.get(period_field)),
            str(songs),
            str(pairs),
            f"{tps:.2f}",
            str(p.get("distinct_topics") or 0),
        ]
        for m in measures:
            v = p.get(m)
            row.append("-" if v is None else str(v))
        row.append(_top_topic(p))
        lines.append("| " + " | ".join(row) + " |")
    return lines


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--label", required=True,
                    help="snapshot label, e.g. step0-baseline")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR),
                    help="snapshot directory (default: the Dropbox plan folder)")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    base = f"{stamp}-{args.label}"

    yearly = _fetch("/api/topic-trends")
    trailing = _fetch("/api/topic-trends/trailing")

    (out_dir / f"{base}-yearly.json").write_text(
        json.dumps(yearly, indent=2), encoding="utf-8", newline="\n")
    (out_dir / f"{base}-trailing.json").write_text(
        json.dumps(trailing, indent=2), encoding="utf-8", newline="\n")

    years = yearly.get("years", [])
    periods = trailing.get("periods", [])
    coverage = yearly.get("coverage", {})

    md: list[str] = []
    md.append(f"# Topic Trends snapshot: {base}")
    md.append("")
    md.append(f"- Captured: {stamp}")
    md.append(f"- Source: {API_BASE}/api/topic-trends (+ /trailing)")
    md.append(f"- Taxonomy size: {len(yearly.get('taxonomy', []))} topics, "
              f"{len(yearly.get('themes', []))} themes")
    md.append(f"- Coverage: corpus {coverage.get('corpus_year_range')}, "
              f"tagged {coverage.get('topic_year_range')}, "
              f"{coverage.get('years_with_topics')} years with topics, "
              f"early_signal={coverage.get('is_early_signal')}")
    md.append(f"- Raw payloads: `{base}-yearly.json`, `{base}-trailing.json`")
    md.append("")
    md.append("## Yearly series")
    md.append("")
    md.extend(_table(years, "year"))
    md.append("")
    md.append("## Trailing 12 months")
    md.append("")
    md.extend(_table(periods, "key"))
    md.append("")

    md_path = out_dir / f"{base}.md"
    md_path.write_text("\n".join(md), encoding="utf-8", newline="\n")

    print(f"Snapshot written: {md_path}")
    print(f"  years: {len(years)} ({years[0]['year']}-{years[-1]['year']})"
          if years else "  years: 0")
    print(f"  trailing months: {len(periods)}")


if __name__ == "__main__":
    main()
