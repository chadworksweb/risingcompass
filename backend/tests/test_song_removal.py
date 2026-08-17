"""Unit tests for song_removal.remove_song -- the ONE song-deletion routine.

WHY THIS EXISTS. Until 2026-08-17 this routine was copy-pasted into four places,
each with its own hand-typed table list, and they had drifted:
`stream.delete_stream_song` never checked `release_songs`, so deleting a stream
entry could delete a song that was still a track on a release and leave the
release pointing at nothing. Consolidating the callers fixed that instance. These
tests are what stop the NEXT one, by pinning the hard-reference set and the
owned-table set as assertions rather than as four things to remember.

Fake session, no DB required -- the point is which tables get consulted and in
which order, not what Postgres does with the answer.

Run standalone:  python tests/test_song_removal.py
Or via pytest:   python -m pytest tests/test_song_removal.py
"""

import os
import re
import sys

# Allow `import app...` when run directly from the backend/ dir.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import song_removal


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value

    def first(self):
        return self._value


class _FakeSession:
    """Records every statement remove_song issues and answers the existence
    probes from `held`, a set of table names that still reference the song."""

    def __init__(self, held=(), song=("Test Title", "Test Artist")):
        self.held = set(held)
        self.song = song
        self.selected = []   # tables probed, in order
        self.deleted = []    # tables deleted from, in order

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if sql.startswith("SELECT title, artist FROM songs"):
            return _Result(self.song)
        probe = re.match(r"SELECT 1 FROM (\w+) WHERE song_id", sql)
        if probe:
            table = probe.group(1)
            self.selected.append(table)
            return _Result(1 if table in self.held else None)
        delete = re.match(r"DELETE FROM (\w+) WHERE", sql)
        if delete:
            self.deleted.append(delete.group(1))
            return _Result(None)
        raise AssertionError(f"unexpected statement: {sql}")


# --- the hard-reference set -------------------------------------------------- #

def test_every_hard_reference_keeps_the_song():
    """Each hard reference on its own must block the delete. This is the
    assertion that would have caught the stream/release_songs drift."""
    for table, _reason in song_removal._HARD_REFERENCES:
        sess = _FakeSession(held={table})
        result = song_removal.remove_song(sess, 1)
        assert result["song_removed"] is False, f"{table} did not keep the song"
        assert result["kept_reason"], f"{table} gave no reason"
        assert sess.deleted == [], f"{table} was kept but rows were still deleted"


def test_release_songs_is_a_hard_reference():
    """Named explicitly, not just covered by the loop above: this is the exact
    table the stream path was missing."""
    tables = [t for t, _ in song_removal._HARD_REFERENCES]
    assert "release_songs" in tables


def test_kept_reason_is_reader_facing():
    """The clutter queue shows kept_reason to a human in a 409 body."""
    for _table, reason in song_removal._HARD_REFERENCES:
        assert reason.startswith("it "), f"{reason!r} does not read as a sentence"


# --- the ingestion_holds axis ------------------------------------------------ #

def test_ingestion_does_not_hold_by_default():
    """The clutter contract: an ingestion is provenance of the thing being
    removed, not a reason to keep it. It is deleted, not consulted."""
    sess = _FakeSession(held={"song_ingestions"})
    result = song_removal.remove_song(sess, 1)
    assert result["song_removed"] is True
    assert "song_ingestions" not in sess.selected
    assert "song_ingestions" in sess.deleted


def test_ingestion_holds_when_requested():
    """The feeder contract: a row left by a DIFFERENT feeder means another
    surface still claims this song."""
    sess = _FakeSession(held={"song_ingestions"})
    result = song_removal.remove_song(sess, 1, ingestion_holds=True)
    assert result["song_removed"] is False
    assert result["kept_reason"] == song_removal._INGESTION_REFERENCE[1]
    assert sess.deleted == []


def test_ingestion_holds_is_checked_first():
    """Cheapest and most specific reason wins, so a feeder caller is told the
    song is still ingested rather than something downstream of that."""
    sess = _FakeSession(held=())
    song_removal.remove_song(sess, 1, ingestion_holds=True)
    assert sess.selected[0] == "song_ingestions"


def test_feeder_path_deletes_when_nothing_holds():
    sess = _FakeSession(held=())
    result = song_removal.remove_song(sess, 1, ingestion_holds=True)
    assert result["song_removed"] is True
    assert result["kept_reason"] is None


# --- the owned-table set ----------------------------------------------------- #

def test_owned_tables_are_all_deleted():
    """Most FKs onto songs.id are SET NULL, not CASCADE, so anything missing
    from this list is left stranded pointing at nothing."""
    sess = _FakeSession(held=())
    song_removal.remove_song(sess, 1)
    for table in song_removal._OWNED_TABLES:
        assert table in sess.deleted, f"{table} was not deleted"
    assert "song_id_map" in sess.deleted
    assert "songs" in sess.deleted


def test_songs_row_is_deleted_last():
    """Deleting the parent first would break any FK that is not SET NULL."""
    sess = _FakeSession(held=())
    song_removal.remove_song(sess, 1)
    assert sess.deleted[-1] == "songs"


def test_song_slugs_is_owned():
    """A stranded song_slugs row resolves to NULL and 404s a live URL."""
    assert "song_slugs" in song_removal._OWNED_TABLES


# --- idempotency ------------------------------------------------------------- #

def test_missing_song_is_not_an_error():
    """A double-resolve on the same audit row must not fail."""
    sess = _FakeSession(song=None)
    result = song_removal.remove_song(sess, 999)
    assert result["song_removed"] is False
    assert result["kept_reason"] == "song no longer exists"
    assert result["title"] is None
    assert sess.deleted == []


def test_title_and_artist_are_returned_on_the_kept_branch():
    """Callers surface these in the queue UI without re-querying."""
    sess = _FakeSession(held={"chart_appearances"}, song=("Song", "Artist"))
    result = song_removal.remove_song(sess, 1)
    assert (result["title"], result["artist"]) == ("Song", "Artist")


# --- the callers ------------------------------------------------------------- #

def test_no_caller_reimplements_the_routine():
    """The regression guard for the whole module: if a router grows its own
    orphan check again, this fails. Searches for the shape of the old inline
    copy (a raw existence probe against a song-reference table)."""
    routers = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "routers",
    )
    guarded = {t for t, _ in song_removal._HARD_REFERENCES}
    offenders = []
    for name in os.listdir(routers):
        if not name.endswith(".py"):
            continue
        with open(os.path.join(routers, name), encoding="utf-8") as fh:
            body = fh.read()
        for table in guarded:
            if re.search(rf"SELECT 1 FROM {table} WHERE song_id", body):
                offenders.append(f"{name}: probes {table} directly")
    assert not offenders, (
        "these routers reimplement the orphan check instead of calling "
        "song_removal.remove_song -- " + "; ".join(offenders)
    )


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
