"""record_prose_versions: lane selection, dedupe, and fail-soft, with no DB.

The SQL itself is exercised against real Postgres separately (migration 145's
DDL + the DISTINCT ON lookup); this pins the branching, which is what decides
whether a version is written at all.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.prose_versions import record_prose_versions  # noqa: E402


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class FakeSession:
    """Answers the two SELECTs the service issues and records the INSERTs."""

    def __init__(self, song_row, latest_rows=(), fail=False):
        self.song_row = song_row
        self.latest_rows = list(latest_rows)
        self.fail = fail
        self.inserts = []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if self.fail:
            raise RuntimeError("database is down")
        if sql.lstrip().upper().startswith("INSERT"):
            self.inserts.append(params)
            return _Result([])
        if "FROM songs" in sql:
            return _Result([self.song_row] if self.song_row else [])
        if "FROM song_prose_versions" in sql:
            return _Result(self.latest_rows)
        raise AssertionError(f"unexpected statement: {sql[:60]}")


SONG = {
    "title": "A Song", "artist": "An Artist",
    "rubric_color": "green", "charge_value": 6,
    "listener_effects_prose": "listener text",
    "societal_effects_prose": "societal text",
    "psyche_facts": '{"purpose": "x"}',
    "societal_prose_model": "terminal_supplied",
    "societal_prose_generated_at": None,
}


def test_writes_one_row_per_populated_lane():
    db = FakeSession(SONG)
    assert record_prose_versions(db, 1, trigger="terminal") == 3
    assert {i["lane"] for i in db.inserts} == {"listener", "societal", "psyche_facts"}


def test_skips_lane_matching_newest_version():
    db = FakeSession(SONG, latest_rows=[
        {"lane": "listener", "prose": "listener text"},
        {"lane": "societal", "prose": "societal text"},
        {"lane": "psyche_facts", "prose": '{"purpose": "x"}'},
    ])
    assert record_prose_versions(db, 1, trigger="terminal") == 0
    assert db.inserts == []


def test_records_only_the_lane_that_changed():
    db = FakeSession(SONG, latest_rows=[
        {"lane": "listener", "prose": "an older listener block"},
        {"lane": "societal", "prose": "societal text"},
        {"lane": "psyche_facts", "prose": '{"purpose": "x"}'},
    ])
    assert record_prose_versions(db, 1, trigger="admin_recal") == 1
    assert db.inserts[0]["lane"] == "listener"
    assert db.inserts[0]["trigger"] == "admin_recal"


def test_carries_the_read_the_prose_was_written_for():
    db = FakeSession(SONG)
    record_prose_versions(db, 1, trigger="terminal")
    assert all(i["rubric_color"] == "green" and i["charge_value"] == 6 for i in db.inserts)


def test_empty_lane_is_not_recorded():
    song = dict(SONG, societal_effects_prose=None, psyche_facts="   ")
    db = FakeSession(song)
    assert record_prose_versions(db, 1, trigger="terminal") == 1
    assert db.inserts[0]["lane"] == "listener"


def test_lane_filter_narrows_the_write():
    db = FakeSession(SONG)
    assert record_prose_versions(db, 1, trigger="terminal", lanes=["societal"]) == 1
    assert db.inserts[0]["lane"] == "societal"


def test_no_song_id_is_a_noop():
    db = FakeSession(SONG)
    assert record_prose_versions(db, None, trigger="terminal") == 0
    assert db.inserts == []


def test_missing_song_is_a_noop():
    db = FakeSession(None)
    assert record_prose_versions(db, 999, trigger="terminal") == 0


def test_fails_soft_when_the_database_errors():
    db = FakeSession(SONG, fail=True)
    assert record_prose_versions(db, 1, trigger="terminal") == 0


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
