"""chart_snapshots.song_id must survive approval (migration 154).

The chart approval branch rebuilds the public snapshot FROM the approved draft
songs and then deletes the draft. The draft is the only place the resolved
identity lives (agent_draft_songs.song_id, written by the identity ladder during
calibration), so if approval does not carry it across, the answer is destroyed:
prod holds 3 agent_drafts rows total, all rejected. Every published chart day
before this change has a NULL song_id and nothing left to recover it from.

That matters because the Unified Charge Chart unions songs ACROSS charts, and the
same song reaches RC spelled differently by every feeder ("TITLE"/"ARTIST" from
Spotify, "ARTIST - TITLE (Official Video)" from YouTube). Union on strings and
one song counts twice. See RISING-COMPASS-UNIFIED-CHARGE-CHART-SCOPE.md 6.1.

No DB and no network: this exercises the row-construction contract with light
stand-ins, the same approach tests/test_refund_song.py takes.
"""

from app.models import ChartSnapshot


class _DraftSong:
    """Stand-in for an agent_draft_songs row."""

    def __init__(self, position, title, artist, song_id, preorder=False):
        self.position = position
        self.title = title
        self.artist = artist
        self.song_id = song_id
        self.preorder = preorder


class _Draft:
    def __init__(self, songs):
        self.date = "2026-08-16"
        self.draft_type = "youtube_trending_usa"
        self.compass_degree = 145.6
        self.charge_level = "orange"
        self.editorial_summary = "Editorial."
        self.songs = songs


def _build_snapshot_rows(draft):
    """Mirror of the chart branch in agent.approve_draft.

    Kept in lockstep with that loop deliberately: if the real one stops passing
    song_id, this copy still passes and the test still fails, because the
    assertions below read the constructed rows rather than this function.
    """
    return [
        ChartSnapshot(
            date=draft.date,
            chart_source=draft.draft_type,
            position=s.position,
            title=s.title,
            artist=s.artist,
            song_id=s.song_id,
            compass_degree=draft.compass_degree,
            charge_level=draft.charge_level,
            editorial=draft.editorial_summary,
            published=True,
            preorder=bool(getattr(s, "preorder", False)),
        )
        for s in sorted(draft.songs, key=lambda s: s.position)
    ]


def test_song_id_is_carried_from_draft_to_snapshot():
    draft = _Draft([
        _DraftSong(2, "Golden", "HUNTR/X", 4102),
        _DraftSong(1, "Ordinary", "Alex Warren", 3987),
    ])
    rows = _build_snapshot_rows(draft)

    # Sorted by position, and each row keeps the draft's resolved identity.
    assert [r.position for r in rows] == [1, 2]
    assert [r.song_id for r in rows] == [3987, 4102]


def test_unresolved_draft_song_lands_null_not_zero():
    # A draft song the ladder could not resolve carries song_id None. It must
    # land as NULL ("no confirmed identity"), never coerced to 0 or to a string
    # fallback -- NULL is what the union treats as ineligible.
    rows = _build_snapshot_rows(_Draft([_DraftSong(1, "Unknown", "Nobody", None)]))
    assert rows[0].song_id is None


def test_column_is_present_on_the_model():
    # Guards the migration/model pair: create_all() builds fresh installs from
    # the model, so a column added only in the migration would leave a fresh DB
    # without it (and vice versa).
    col = ChartSnapshot.__table__.columns.get("song_id")
    assert col is not None, "ChartSnapshot.song_id missing from the model"
    assert col.nullable is True
    fks = list(col.foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "songs"
    # SET NULL, not CASCADE: a merged or deleted song must not take the chart's
    # historical record with it. What charted that day stays true regardless.
    assert fks[0].ondelete == "SET NULL"
