"""Rename recalibration_type -> lens and trigger_source -> pipeline.

There is ONE recalibrate mechanism. Pipelines feed into it; the lens is
how the agent re-reads. The old schema collapsed both onto
`recalibration_type`, which mixed "what kind of re-read" (satire vs
standard) with "what triggered it" (rubric change vs satirical flag vs
consensus drift).

New shape:
  - lens      : standard | satire               (how the agent re-reads)
  - pipeline  : manual | rubric_update |        (what triggered the re-read)
                satirical_flag | vibe_gap |
                consensus_drift

Mapping of existing rows:
  - recalibration_type=satire              -> lens=satire
  - recalibration_type=rubric_update       -> lens=standard
  - recalibration_type=public_interest     -> lens=standard (never written)
  - recalibration_type=consensus_drift     -> lens=standard, pipeline=consensus_drift
  - trigger_source=admin_manual            -> pipeline=manual
  - trigger_source=satirical_flag          -> pipeline=satirical_flag
  - trigger_source=rubric_update           -> pipeline=rubric_update
  - trigger_source=vibe_gap                -> pipeline=vibe_gap
  - trigger_source=calibration_runs        -> pipeline=consensus_drift
"""

from sqlalchemy import text


def up(conn):
    for table in ("song_recalibrations", "song_recalibration_proposals"):
        conn.execute(text(f"ALTER TABLE {table} RENAME COLUMN recalibration_type TO lens"))
        conn.execute(text(f"ALTER TABLE {table} RENAME COLUMN trigger_source TO pipeline"))

        # Map lens values
        conn.execute(text(
            f"UPDATE {table} SET lens = 'standard' "
            "WHERE lens IN ('rubric_update', 'public_interest', 'consensus_drift')"
        ))
        # Map pipeline values
        conn.execute(text(
            f"UPDATE {table} SET pipeline = 'manual' WHERE pipeline = 'admin_manual'"
        ))
        conn.execute(text(
            f"UPDATE {table} SET pipeline = 'consensus_drift' WHERE pipeline = 'calibration_runs'"
        ))
