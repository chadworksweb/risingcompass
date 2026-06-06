"""Unit tests for billing.refund_song.

refund_song has no live trigger in the current calibrate flow (the charge
happens only AFTER the song commits, so "charged but unsaved" cannot occur), so
it is never exercised end-to-end. These tests cover its logic directly with a
fake session -- no DB required -- so a regression in the bucket-reversal math or
idempotency is caught.

Run standalone:  python tests/test_refund_song.py
Or via pytest:   python -m pytest tests/test_refund_song.py
"""

import os
import sys

# Allow `import app...` when run directly from the backend/ dir.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.exc import IntegrityError

from app.services import billing


class _FakeQuery:
    def __init__(self, user):
        self._user = user

    def filter(self, *a, **k):
        return self

    def with_for_update(self):
        return self

    def first(self):
        return self._user


class _FakeSession:
    """Minimal stand-in for a SQLAlchemy session that records what refund_song
    does without touching a database."""

    def __init__(self, user, fail_on_commit=False):
        self.user = user
        self.fail_on_commit = fail_on_commit
        self.added = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def query(self, model):
        return _FakeQuery(self.user)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        if self.fail_on_commit:
            raise IntegrityError("INSERT", {}, Exception("duplicate key"))
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class _FakeUser:
    def __init__(self, allowance=0, purchased=0):
        self.allowance_credits = allowance
        self.purchased_credits = purchased


def _patch_session(monkey_session):
    """Point billing.SessionLocal at a factory returning our fake; return a
    restore() callable."""
    original = billing.SessionLocal
    billing.SessionLocal = lambda: monkey_session
    return lambda: setattr(billing, "SessionLocal", original)


def test_refund_reverses_bucket_split():
    user = _FakeUser(allowance=5, purchased=10)
    sess = _FakeSession(user)
    restore = _patch_session(sess)
    try:
        ok = billing.refund_song(
            42, {"allowance_spent": 2, "purchased_spent": 3}, ref_id="song-42",
        )
    finally:
        restore()

    assert ok is True
    # Denormalised balances bumped back up by exactly what was spent.
    assert user.allowance_credits == 7
    assert user.purchased_credits == 13
    # Two positive ledger rows, correct buckets/deltas, :refund ref_id.
    assert len(sess.added) == 2
    by_bucket = {row.bucket: row for row in sess.added}
    assert by_bucket["allowance"].delta == 2
    assert by_bucket["purchased"].delta == 3
    for row in sess.added:
        assert row.reason == "song_refund"
        assert row.ref_id == "song-42:refund"
        assert row.ref_type == "submitted_song"
    assert sess.committed is True


def test_refund_only_purchased_bucket():
    user = _FakeUser(allowance=0, purchased=4)
    sess = _FakeSession(user)
    restore = _patch_session(sess)
    try:
        ok = billing.refund_song(
            7, {"allowance_spent": 0, "purchased_spent": 1}, ref_id="song-7",
        )
    finally:
        restore()

    assert ok is True
    assert user.allowance_credits == 0
    assert user.purchased_credits == 5
    assert len(sess.added) == 1
    assert sess.added[0].bucket == "purchased"
    assert sess.added[0].delta == 1


def test_zero_charge_is_noop_without_session():
    """A comp / daily_free / free charge moved no credits -> no session at all."""
    called = {"opened": False}

    original = billing.SessionLocal

    def _factory():
        called["opened"] = True
        return _FakeSession(_FakeUser())

    billing.SessionLocal = _factory
    try:
        ok = billing.refund_song(
            1, {"allowance_spent": 0, "purchased_spent": 0}, ref_id="song-1",
        )
    finally:
        billing.SessionLocal = original

    assert ok is False
    assert called["opened"] is False


def test_refund_replay_is_idempotent():
    """A second refund for the same charge collides on the unique index ->
    IntegrityError -> rolled back, returns False, no balance change."""
    user = _FakeUser(allowance=1, purchased=1)
    sess = _FakeSession(user, fail_on_commit=True)
    restore = _patch_session(sess)
    try:
        ok = billing.refund_song(
            9, {"allowance_spent": 1, "purchased_spent": 0}, ref_id="song-9",
        )
    finally:
        restore()

    assert ok is False
    assert sess.rolled_back is True
    assert sess.committed is False


def test_refund_user_not_found():
    sess = _FakeSession(user=None)
    restore = _patch_session(sess)
    try:
        ok = billing.refund_song(
            404, {"allowance_spent": 5, "purchased_spent": 0}, ref_id="song-404",
        )
    finally:
        restore()

    assert ok is False
    assert sess.committed is False


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
