"""The naive/aware invariant, pinned as assertions.

RC moved from naive UTC to timezone-aware UTC on 2026-08-17 (migration 156 plus
the code sweep). The end state is safe and the old state was safe; the dangerous
thing is HALF of it, because Python refuses to compare a naive datetime with an
aware one and raises TypeError on whichever path happens to mix them first.

Two ways to reintroduce the mix, both easy and both silent:

  1. Add a new `Column(DateTime)` without `timezone=True`. The column is created
     as `timestamp without time zone`, and every aware value written to it gets
     silently coerced using the session timezone.
  2. Write `default=datetime.utcnow` -- a bare function REFERENCE, no parens.
     The original sweep nearly missed all 143 of these for exactly that reason:
     a regex hunting `<name>()` call sites does not match a bare reference.

These tests fail on either. No DB required.

Run standalone:  python tests/test_timezone_aware.py
Or via pytest:   python -m pytest tests/test_timezone_aware.py
"""

import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_MODELS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "models.py")


def _models_src():
    with open(_MODELS, encoding="utf-8") as fh:
        return fh.read()


def test_every_datetime_column_is_timezone_aware():
    """A bare `Column(DateTime)` is `timestamp without time zone` in Postgres."""
    src = _models_src()
    bare = re.findall(r"Column\(\s*DateTime(?!\s*\()", src)
    assert not bare, (
        f"{len(bare)} DateTime column(s) declared without timezone=True. "
        "Use Column(DateTime(timezone=True), ...)")


def test_no_multiline_bare_datetime_column():
    """The same thing written across lines: `Column(\\n    DateTime, ...)`."""
    src = _models_src()
    bare = re.findall(r"(?m)^\s+DateTime,\s*$", src)
    assert not bare, (
        f"{len(bare)} multi-line DateTime column(s) missing timezone=True")


def test_models_never_reference_utcnow():
    """`default=datetime.utcnow` is a naive stamp on every insert. The bare
    reference is the shape a call-site sweep misses, so it is pinned here."""
    src = _models_src()
    # Skip the docstring of the _utcnow helper, which names the old form on
    # purpose to explain what it replaced.
    body = src.split("def _utcnow():", 1)[-1].split('"""', 2)[-1]
    hits = re.findall(r"(?<![\w.])datetime\.utcnow", body)
    assert not hits, (
        f"{len(hits)} reference(s) to datetime.utcnow in models.py. "
        "Use the module-level _utcnow callable, which returns an aware value.")


def test_utcnow_helper_returns_aware_utc():
    from app.models import _utcnow
    now = _utcnow()
    assert now.tzinfo is not None, "_utcnow() returned a naive datetime"
    assert now.utcoffset() == timezone.utc.utcoffset(None), "_utcnow() is not UTC"


def test_aware_and_naive_really_do_not_compare():
    """The failure this guards against, demonstrated. If this ever stops raising,
    the reasoning behind the whole migration changed and these tests need a
    rethink rather than a green tick."""
    naive = datetime(2026, 1, 1)
    aware = datetime(2026, 1, 1, tzinfo=timezone.utc)
    try:
        naive < aware
    except TypeError:
        return
    raise AssertionError("expected TypeError comparing naive to aware")


def test_migration_156_is_present_and_data_driven():
    """156 converts the stored values. Code-only is the harmful half, so the
    migration must ship alongside it -- and it must ask the DATABASE which
    columns exist rather than carry a hand-typed list that goes stale."""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "migrations", "156_timestamptz.py")
    assert os.path.exists(path), "migration 156_timestamptz.py is missing"
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    assert "information_schema.columns" in src, "156 must be data-driven"
    assert "timestamp without time zone" in src, "156 must select naive columns"
    assert "SET LOCAL TimeZone = 'UTC'" in src, (
        "156 must pin the session timezone to UTC or the ALTER rewrites tables")


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
