"""One-off backfill for the 2026-04-20 drop-dead correction.

On 2026-04-20 the agent calibrated "drop dead" by Olivia Rodrigo as Decent
+12, not contaminated. The lyric reframes online stalking as "feminine
intuition" — the agent took the reframing at face value. Chad corrected it
to Decent -15, contaminated, with a contamination_note documenting the
pattern. The correction was applied via direct SQL UPDATE on compass_songs
before the pre_publish_corrections pipeline existed, so no audit row was
written.

This script writes that audit row retroactively. It lands promoted_to_feed=1
because the event was specifically flagged as worth documenting publicly —
it's the first Laundered Behavior pattern captured in the Calibration Log.

Idempotent — re-running is a no-op (checks for existing row with same
compass_song_id + before_charge_value=12).
"""
import os
import sys

import libsql


COMPASS_SONG_ID = 852  # drop dead by Olivia Rodrigo

# Reconstructed agent output (before the correction).
BEFORE_RUBRIC_COLOR = "green"
BEFORE_CHARGE_VALUE = 12
BEFORE_CONTAMINATED = 0  # False
BEFORE_CONTAMINATION_NOTE = None

# Known corrected values (after the correction). charge_summary is queried
# from the current DB row because the session notes indicate only
# charge_value, contaminated, and contamination_note changed — the summary
# is the agent's original and survives on both sides.
AFTER_RUBRIC_COLOR = "green"
AFTER_CHARGE_VALUE = -15
AFTER_CONTAMINATED = 1  # True
AFTER_CONTAMINATION_NOTE = (
    "Normalizes online stalking of a romantic interest by reframing it as "
    "feminine intuition."
)

OCCURRED_AT = "2026-04-20 18:00:00"  # approximate — afternoon of 2026-04-20

HUMAN_RATIONALE = (
    "Laundered Behavior — the lyric reframes stalking an internet profile as "
    "\"feminine intuition,\" and the agent accepted the reframing at face "
    "value. The song stays Decent (it is infatuation, not something deeply "
    "corrupt), but the charge drops into negative territory because a "
    "fantasy reel with zero self-awareness should not reward giddy energy "
    "with positive charge, and the contamination flag fires on "
    "stalking-reframed-as-intuition as normalized destructive behavior.\n\n"
    "This is the first captured instance of Laundered Behavior — a "
    "destructive act is explicitly relabeled as virtue or instinct inside "
    "the lyric, and the agent accepts the reframing. Candidate for a future "
    "rubric-update entry formalizing the pattern."
)

TAGS = "laundered-behavior,agent-miss,contamination"


def main() -> int:
    url = os.environ["DATABASE_URL"]
    token = os.environ["TURSO_AUTH_TOKEN"]
    conn = libsql.connect(database=url, auth_token=token)

    # Idempotency check.
    existing = conn.execute(
        "SELECT id FROM pre_publish_corrections "
        "WHERE compass_song_id = ? AND before_charge_value = ?",
        (COMPASS_SONG_ID, BEFORE_CHARGE_VALUE),
    ).fetchone()
    if existing:
        print(f"already backfilled: pre_publish_corrections id={existing[0]}")
        return 0

    # Pull current summary (it's both the before and after — the correction
    # didn't change it per the session notes).
    row = conn.execute(
        "SELECT charge_summary FROM compass_songs WHERE id = ?",
        (COMPASS_SONG_ID,),
    ).fetchone()
    if not row:
        print(f"compass_songs id={COMPASS_SONG_ID} not found", file=sys.stderr)
        return 1
    current_summary = row[0]

    conn.execute(
        "INSERT INTO pre_publish_corrections ("
        "  draft_id, draft_song_id, compass_song_id, occurred_at,"
        "  before_rubric_color, before_charge_value, before_contaminated,"
        "  before_contamination_note, before_summary,"
        "  after_rubric_color, after_charge_value, after_contaminated,"
        "  after_contamination_note, after_summary,"
        "  human_rationale, tags, promoted_to_feed, promoted_at"
        ") VALUES ("
        "  NULL, NULL, ?, ?,"
        "  ?, ?, ?, ?, ?,"
        "  ?, ?, ?, ?, ?,"
        "  ?, ?, 1, ?"
        ")",
        (
            COMPASS_SONG_ID, OCCURRED_AT,
            BEFORE_RUBRIC_COLOR, BEFORE_CHARGE_VALUE, BEFORE_CONTAMINATED,
            BEFORE_CONTAMINATION_NOTE, current_summary,
            AFTER_RUBRIC_COLOR, AFTER_CHARGE_VALUE, AFTER_CONTAMINATED,
            AFTER_CONTAMINATION_NOTE, current_summary,
            HUMAN_RATIONALE, TAGS, OCCURRED_AT,
        ),
    )
    conn.commit()

    new_id = conn.execute(
        "SELECT id FROM pre_publish_corrections "
        "WHERE compass_song_id = ? AND before_charge_value = ?",
        (COMPASS_SONG_ID, BEFORE_CHARGE_VALUE),
    ).fetchone()[0]
    print(f"wrote pre_publish_corrections id={new_id} (promoted)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
