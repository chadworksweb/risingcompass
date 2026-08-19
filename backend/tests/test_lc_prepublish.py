"""The prepublish hold/close/sweep state machine.

Run: .venv/Scripts/python.exe tests/test_lc_prepublish.py

Throwaway in-memory SQLite, no network, no prod DB. `publish_read` is stubbed
out: it drives store_calibrated_song and record_and_reconcile across most of the
schema, which is an integration concern. What is tested here is the part that
decides WHETHER a reading publishes, which is where a mistake is expensive and
silent -- a sweep that picks up the wrong rows either publishes contested
readings or strands good ones forever.
"""

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.database import Base  # noqa: E402
from app.models import LcPrepublishRead  # noqa: E402
from app.services import lc_publish  # noqa: E402
from app.config import settings  # noqa: E402

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print("PASS", name)
    else:
        failed += 1
        print("FAIL", name)


engine = create_engine("sqlite://")
LcPrepublishRead.__table__.create(engine)
Session = sessionmaker(bind=engine)
db = Session()

ENV = settings.environment


def hold(**kw):
    defaults = dict(
        job_token="tok-" + str(kw.pop("n", 1)),
        title="A Song", artist="An Artist", source="lyrical_charger",
        calibration={"rubric_color": "green", "charge_value": 4},
        result_payload={"status": "scored"},
        lyrics="we drank until morning in the yard",
        lyrics_fingerprint=None,
    )
    defaults.update(kw)
    return lc_publish.hold_read(db, **defaults)


# --- hold -------------------------------------------------------------------
row = hold(n=1)
check("hold lands as 'held'", row.status == "held")
check("hold stamps the running environment", row.environment == ENV)
check("hold stores no lyrics anywhere",
      "drank until morning" not in (row.calibration_json + (row.result_json or "")))
check("a first reading has no contest pointer", row.contest_of_id is None)

# The argument is scrubbed at hold time, while the lyrics are still in hand.
scrubbed = hold(n=2, calibration={
    "rubric_color": "green",
    "reasoning": "the singer says we drank until morning in the yard, which is a wake",
})
check("hold scrubs a verbatim lyric run out of the argument",
      "drank until morning" not in scrubbed.calibration_json)

kept = hold(n=3, calibration={"rubric_color": "green",
                              "reasoning": "the closing turn carries the whole reading"})
check("hold keeps an argument that quotes nothing",
      "closing turn carries" in kept.calibration_json)

# --- close ------------------------------------------------------------------
c = hold(n=4)
lc_publish.close_read(db, c, "contested")
check("close moves a held row to a terminal status", c.status == "contested")

lc_publish.close_read(db, c, "declined")
check("close is a no-op on an already-closed row", c.status == "contested")

# close_read must NOT be able to stamp 'published'. A row marked published
# without the four writes behind it is a reading silently lost: the sweep skips
# it forever and it looks exactly like a success.
try:
    lc_publish.close_read(db, hold(n=5), "published")
    raised_pub = False
except ValueError:
    raised_pub = True
check("close refuses to stamp 'published'", raised_pub)

try:
    lc_publish.close_read(db, hold(n=6), "nonsense")
    raised = False
except ValueError:
    raised = True
check("close rejects an unknown status", raised)

# --- the publish claim -------------------------------------------------------
# Two callers reach the same row in the same second at the 30-minute mark: the
# reader clicking "Looks right" and the sweep arriving for it. A Python-side
# status check lets both through and logs the reading TWICE, and that duplicate
# run counts toward _most_run -- the exact leak holding was built to prevent.
claimable = hold(n=7)
check("a held row can be claimed", lc_publish._claim_for_publish(db, claimable) is True)
check("the claim is visible on the row", claimable.status == "publishing")
check("the same row cannot be claimed twice",
      lc_publish._claim_for_publish(db, claimable) is False)

already_closed = hold(n=8)
lc_publish.close_read(db, already_closed, "contested")
check("a closed row cannot be claimed",
      lc_publish._claim_for_publish(db, already_closed) is False)
check("a lost claim leaves the status alone", already_closed.status == "contested")

# --- sweep selection --------------------------------------------------------
published = []
lc_publish.publish_read = lambda d, r, reason="accepted": (
    published.append((r.id, reason)) or r.__setattr__("status", "published")
)

# Silence publishes, but never silently: a reading that drew an objection and
# then went quiet emails the admin to confirm. Stubbed so the test never reaches
# Resend, and so we can assert WHICH rows earn one.
notified = []
lc_publish._notify_abandoned = lambda d, r: notified.append(r.id)

for r in db.query(LcPrepublishRead).all():
    db.delete(r)
db.commit()

old = timedelta(minutes=31)
fresh = hold(n=10)
stale = hold(n=11)
stale_contested = hold(n=12)
other_env = hold(n=13)
stale_reread = hold(n=14, contest_of_id=stale_contested.id,
                    contest_axis="missed_frame", contest_note="a pointer")

now = datetime.now(timezone.utc)
for r in (stale, stale_contested, other_env, stale_reread):
    r.created_at = now - old
stale_contested.status = "contested"
other_env.environment = "somewhere-else"
db.commit()

report = lc_publish.sweep_expired(db)
swept = {rid for rid, _ in published}

check("sweep publishes a reading held past the TTL", stale.id in swept)
check("sweep leaves a reading still inside the TTL", fresh.id not in swept)
check("sweep never touches a contested row", stale_contested.id not in swept)
check("sweep is environment-filtered", other_env.id not in swept)
check("contested-then-abandoned publishes the RE-READ", stale_reread.id in swept)
check("sweep reports what it did", report["published"] == len(swept) and report["failed"] == 0)
check("sweep reason is recorded as swept, not accepted",
      all(reason == "swept" for _, reason in published))

check("an abandoned contest emails the admin", stale_reread.id in notified)
check("an ordinary silent publish emails nobody", stale.id not in notified)

report2 = lc_publish.sweep_expired(db)
check("a second sweep finds nothing left", report2["published"] == 0)

# --- the re-read that never ran ----------------------------------------------
# When the model call dies, lc_contest stamps the axis + note onto the row that
# is still held and returns 503. Nothing else marks it: contest_of_id stays NULL
# so the reader keeps their one re-read. If they never come back, the sweep
# publishes the very reading they objected to -- which is the ruling (silence is
# acceptance, no refund) and exactly why it has to be confirmable by a person.
notified.clear()
published.clear()
failed_reread = hold(n=30, contest_axis="missed_frame",
                     contest_note="the wake line is the whole point")
failed_reread.created_at = datetime.now(timezone.utc) - old
db.commit()

lc_publish.sweep_expired(db)
check("a failed re-read still publishes on the timer",
      failed_reread.id in {rid for rid, _ in published})
check("and it emails the admin to confirm", failed_reread.id in notified)
check("the objection survived on the row without spending the re-read",
      failed_reread.contest_axis == "missed_frame"
      and failed_reread.contest_of_id is None)

# --- rows stranded mid-publish ----------------------------------------------
# A process that dies between claiming a row and finishing the writes leaves it
# in 'publishing'. Nothing sweeps that status, so without the reclaim the
# reading is lost in the one way that looks like success -- worse than the
# duplicate the claim exists to prevent.
published.clear()
claimed_fresh = hold(n=20)
claimed_stale = hold(n=21)
lc_publish._claim_for_publish(db, claimed_fresh)
lc_publish._claim_for_publish(db, claimed_stale)

now = datetime.now(timezone.utc)
claimed_stale.created_at = now - old
claimed_stale.updated_at = now - timedelta(minutes=16)
claimed_fresh.created_at = now - old
db.commit()

report3 = lc_publish.sweep_expired(db)
swept3 = {rid for rid, _ in published}
check("sweep reclaims a row stranded past PUBLISHING_STALE", claimed_stale.id in swept3)
check("sweep leaves a claim that is still working alone", claimed_fresh.id not in swept3)
check("a live claim keeps its status", claimed_fresh.status == "publishing")
check("the reclaim is reported as a publish", report3["published"] == len(swept3))

print()
print(f"{passed}/{passed + failed} passed")
sys.exit(1 if failed else 0)
