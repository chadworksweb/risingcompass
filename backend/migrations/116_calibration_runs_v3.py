"""Calibrator v3: component + incoherence-signal columns on calibration_runs.

The v3 read procedure has the model emit COMPONENTS (visceral read, route,
two-axis read, precedent placement, pole-agnostic vernier) and the server
compose the charge + derive the tier. Each run stores its components so the
composition is auditable, plus the incoherence signals that feed the
escalation gate (gut-vs-verdict divergence, guard trips, parse retries).
Axis values stay run-internal: no new columns on songs, nothing public.

Schema-only and nullable/defaulted on purpose -- it must be safe to apply
from a local startup against the shared DB while prod still runs v2 (old
code ignores the columns). The v3 era boundary (corpus-wide supersede) is
NOT here; it fires once, deliberately, via the admin era-supersede endpoint
on deploy day.

PG-compatible (063+). Base.metadata.create_all() builds the columns on fresh
installs from the model in app/models.py.
"""

from sqlalchemy import text

_COLUMNS = [
    ("visceral_charge", "INTEGER"),
    ("route", "VARCHAR(40)"),
    ("harm_value", "INTEGER"),
    ("harm_pervasive", "BOOLEAN NOT NULL DEFAULT false"),
    ("transcendence_value", "INTEGER"),
    ("governing_axis", "VARCHAR(15)"),
    ("center", "INTEGER"),
    ("vernier", "TEXT"),
    ("precedent_refs", "TEXT"),
    ("gut_divergence", "INTEGER"),
    ("guard_trips", "INTEGER NOT NULL DEFAULT 0"),
    ("parse_retries", "INTEGER NOT NULL DEFAULT 0"),
    ("escalation_flags", "TEXT"),
    ("escalated", "BOOLEAN NOT NULL DEFAULT false"),
    ("translated", "BOOLEAN NOT NULL DEFAULT false"),
]


def up(conn):
    for name, ddl_type in _COLUMNS:
        conn.execute(text(
            f"ALTER TABLE calibration_runs ADD COLUMN IF NOT EXISTS {name} {ddl_type}"
        ))
