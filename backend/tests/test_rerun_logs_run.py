"""Unit tests for run-logging on RE-calibration (no DB, no network).

Before 2026-08-07 `_store_calibration` logged a calibration run only when the
songs row was newly created ("re-reads of an already-known song do NOT re-log").
That guard was aimed at phantom runs from daily-chart re-listings, which the
cache-hit short-circuit in run_compass_agent already prevents, and its real
effect was to silently drop the run -- and the stored v3 reasoning with it --
for every operator RE-calibration. 21 arguments were lost that way in the
2024/2025 year-end reruns.

Covered here:
  1. supersede_live_runs flips only the LIVE runs and stamps reason + time,
     so live_run_count (the public re-run cap) keeps counting one verdict and
     compute_consensus (which needs run_count >= 2) never averages a fresh
     operator read against stale runs.
  2. The "Most Calibrated" public feed excludes operator triggers, so a rerun
     cannot nudge a public ranking.

Run standalone:  python tests/test_rerun_logs_run.py
Or via pytest:   python -m pytest tests/test_rerun_logs_run.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAILURES = []


def check(label, got, want):
    if got != want:
        FAILURES.append(f"{label}: got {got!r}, want {want!r}")


# --- 1. supersede_live_runs ------------------------------------------------

class FakeRun:
    def __init__(self, rid, superseded=False):
        self.id = rid
        self.superseded = superseded
        self.superseded_reason = None
        self.superseded_at = None


class FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *_a, **_k):
        return self

    def all(self):
        return self._rows


class FakeSession:
    def __init__(self, rows):
        self._rows = rows

    def query(self, *_a, **_k):
        # Only the live rows come back; the real filter is on superseded.is_(False).
        return FakeQuery([r for r in self._rows if not r.superseded])


from app.services.calibration_corpus import supersede_live_runs  # noqa: E402

live_a, live_b = FakeRun(1), FakeRun(2)
already = FakeRun(3, superseded=True)
already.superseded_reason = "rubric_change_x"
db = FakeSession([live_a, live_b, already])

n = supersede_live_runs(db, song_id=99, reason="hot100_11to20_backfill")
check("flipped count", n, 2)
check("live_a superseded", live_a.superseded, True)
check("live_b superseded", live_b.superseded, True)
check("reason stamped", live_a.superseded_reason, "hot100_11to20_backfill")
check("time stamped", live_a.superseded_at is not None, True)
# An already-retired run keeps its original reason (never re-stamped).
check("prior reason untouched", already.superseded_reason, "rubric_change_x")

# Nothing live -> no-op, and no crash on an empty ledger.
check("no live runs is a no-op", supersede_live_runs(FakeSession([]), 1, "x"), 0)


# --- 2. Most Calibrated excludes the house's own passes --------------------

from app.routers.charger_activity import OPERATOR_TRIGGERS  # noqa: E402

for t in ("hot100_11to20_backfill", "terminal_recalibration", "manual",
          "seed", "v2_validation_batch", "backfill_chadlewine_catalog"):
    check(f"{t} is operator-triggered", t in OPERATOR_TRIGGERS, True)

# The audience lanes must stay IN the feed -- excluding them would empty it.
for t in ("compass_daily", "lyrical_charger"):
    check(f"{t} stays public", t in OPERATOR_TRIGGERS, False)

if FAILURES:
    print("FAIL (%d)" % len(FAILURES))
    for f in FAILURES:
        print("  -", f)
    sys.exit(1)
print("ok: all rerun-logging checks passed")
