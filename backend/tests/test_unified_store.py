"""The Unified Charge Chart persistence contract (services/unified_store.py).

Fake session, no database, mirroring tests/test_refund_song.py. The unified
models carry PG-specific server_defaults ((now() at time zone 'utc')), so
create_all against SQLite is not available and a stand-in is the honest option.

What is guaranteed here, and why each rule exists:

  - A recompose NEVER unpublishes. Publication is a deliberate act (the editorial
    write); a background job triggered by a late chart approval must not undo it.
  - A recompose NEVER overwrites the editorial. The prose is terminal-supplied
    and expensive to produce.
  - When a recompose MOVES the numbers on an already-published reading, the
    editorial is flagged stale rather than left silently attached to figures it
    was not written against.
  - A single-constituent day is not stored at all. A "unified" chart of one chart
    is that chart under another name.
  - The weight vector hashes stably, so a published number stays reproducible and
    a re-weight is visible as a version change.

See RISING-COMPASS-UNIFIED-CHARGE-CHART-SCOPE.md sections 5, 6.3 and 8.6.
"""

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.constants import UNIFIED_CONSTITUENT_SLUGS  # noqa: E402
from app.models import UnifiedReading  # noqa: E402
from app.services import unified_store  # noqa: E402
from app.services.unified_chart import (  # noqa: E402
    ChartContribution,
    ComposedReading,
    ComposedSong,
)

DAY = date(2026, 8, 16)
SPOTIFY, ITUNES, SHAZAM, YOUTUBE = UNIFIED_CONSTITUENT_SLUGS


def composed(degree=110.0, songs=3, sources=(SPOTIFY, ITUNES), contam=0):
    return ComposedReading(
        date=DAY,
        compass_degree=degree,
        charge_level="green",
        contamination_count=contam,
        songs=[ComposedSong(song_id=i + 1, title=f"S{i}", artist=f"A{i}",
                            charge_value=0, rubric_color="green",
                            contaminated=False, unified_weight=1.0 / (i + 1),
                            sources={sources[0]: i + 1})
               for i in range(songs)],
        sources_included=[
            ChartContribution(slug=s, source_weight=1.0, slots=20, eligible=20,
                              coverage=1.0, total_rank_weight=3.5977)
            for s in sources
        ],
        sources_excluded=[],
        weights=unified_store.default_weights(),
    )


class _Query:
    def __init__(self, session, model):
        self._s = session
        self._model = model

    def filter(self, *a, **k):
        return self

    def one_or_none(self):
        return self._s.existing if self._model is UnifiedReading else None

    def all(self):
        return self._s.weight_rows

    def delete(self, **k):
        self._s.deleted += 1
        return 0


class _Session:
    """Records what the store does without touching a database."""

    def __init__(self, existing=None, weight_rows=()):
        self.existing = existing
        self.weight_rows = list(weight_rows)
        self.added = []
        self.deleted = 0
        self.committed = False
        self.rolled_back = False

    def query(self, model):
        return _Query(self, model)

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass

    def commit(self):
        self.committed = True

    def refresh(self, obj):
        pass

    def rollback(self):
        self.rolled_back = True


class _WeightRow:
    def __init__(self, slug, weight):
        self.slug = slug
        self.weight = weight


# --- weight vector ----------------------------------------------------------

def test_weights_version_is_stable_and_order_independent():
    a = {SPOTIFY: 1.0, ITUNES: 1.0, SHAZAM: 1.0, YOUTUBE: 1.0}
    b = {YOUTUBE: 1.0, SHAZAM: 1.0, ITUNES: 1.0, SPOTIFY: 1.0}
    assert unified_store.weights_version(a) == unified_store.weights_version(b)
    assert unified_store.weights_version(a) == unified_store.weights_version(a)


def test_weights_version_changes_when_a_weight_changes():
    a = unified_store.default_weights()
    b = dict(a, youtube_trending_usa=0.5)
    assert unified_store.weights_version(a) != unified_store.weights_version(b)


def test_load_weights_reads_the_table():
    s = _Session(weight_rows=[_WeightRow(YOUTUBE, 0.25)])
    w = unified_store.load_weights(s)
    assert w[YOUTUBE] == 0.25
    assert w[SPOTIFY] == 1.0, "unlisted constituents keep the default"


def test_load_weights_falls_back_to_equal_not_to_zero():
    # An empty or unreadable table must read as "equal weights". Falling back to
    # zero would silently delete every source from the reading.
    assert unified_store.load_weights(_Session()) == unified_store.default_weights()

    class Boom(_Session):
        def query(self, model):
            raise RuntimeError("table gone")

    assert unified_store.load_weights(Boom()) == unified_store.default_weights()


def test_load_weights_rolls_back_after_a_read_failure():
    """The rollback is what makes the fallback actually soft on Postgres.

    A failed statement aborts the whole PG transaction, so catching the error
    without rolling back leaves the session poisoned and the NEXT query dies with
    "current transaction is aborted" -- a hard failure one call downstream,
    disguised as a graceful degrade. This is a real bug that shipped and was
    caught by running the backfill before migration 155 was applied.
    """

    class Boom(_Session):
        def query(self, model):
            raise RuntimeError("table gone")

    s = Boom()
    unified_store.load_weights(s)
    assert s.rolled_back, "a swallowed read error must roll back the transaction"


def test_unknown_slug_in_the_table_is_ignored():
    s = _Session(weight_rows=[_WeightRow("spotify_nmf_usa", 9.0)])
    assert "spotify_nmf_usa" not in unified_store.load_weights(s)


# --- store: the insert path -------------------------------------------------

def test_new_day_is_inserted_with_the_full_record():
    s = _Session(existing=None)
    row = unified_store.store(s, composed(degree=110.0, songs=3))

    assert isinstance(row, UnifiedReading)
    assert row.date == DAY
    assert row.compass_degree == 110.0
    assert row.song_count == 3
    assert row.source_count == 2
    assert row.weights_version == unified_store.weights_version(
        unified_store.default_weights())
    assert s.committed
    # One reading + three ranked songs.
    assert len([a for a in s.added if not isinstance(a, UnifiedReading)]) == 3


def test_ranked_songs_are_positioned_in_order():
    s = _Session(existing=None)
    unified_store.store(s, composed(songs=4))
    songs = [a for a in s.added if not isinstance(a, UnifiedReading)]
    assert [x.position for x in songs] == [1, 2, 3, 4]
    # position renders the ranking; unified_weight IS the ranking.
    assert songs[0].unified_weight > songs[-1].unified_weight


# --- store: the recompose path ---------------------------------------------

def _published(degree=110.0, editorial="Prose.", songs=3, sources=2):
    row = UnifiedReading(
        date=DAY, compass_degree=degree, charge_level="green",
        contamination_count=0, song_count=songs, source_count=sources,
        sources_included="[]", sources_excluded="[]",
        weights="{}", weights_version="old",
        editorial=editorial, published=True, editorial_stale=False,
    )
    row.id = 7
    return row


def test_recompose_never_unpublishes():
    row = _published()
    s = _Session(existing=row)
    out = unified_store.store(s, composed(degree=112.0, songs=3))
    assert out.published is True


def test_recompose_never_overwrites_the_editorial():
    row = _published(editorial="The supplied prose.")
    s = _Session(existing=row)
    out = unified_store.store(s, composed(degree=112.0))
    assert out.editorial == "The supplied prose."


def test_moved_numbers_on_a_published_reading_flag_the_editorial_stale():
    row = _published(degree=110.0, songs=3, sources=2)
    s = _Session(existing=row)
    out = unified_store.store(s, composed(degree=118.0, songs=3))
    assert out.editorial_stale is True, "prose was written against 110, not 118"


def test_float_noise_does_not_flag_stale():
    row = _published(degree=110.0, songs=3, sources=2)
    s = _Session(existing=row)
    out = unified_store.store(s, composed(degree=110.02, songs=3))
    assert out.editorial_stale is False


def test_a_new_source_landing_flags_stale_even_at_the_same_degree():
    # A late chart approving is exactly the case 8.6 cares about. The composition
    # changed even if the number happened not to move much.
    row = _published(degree=110.0, songs=3, sources=2)
    s = _Session(existing=row)
    out = unified_store.store(s, composed(degree=110.0, songs=3,
                                          sources=(SPOTIFY, ITUNES, SHAZAM)))
    assert out.editorial_stale is True


def test_unpublished_reading_is_never_flagged_stale():
    row = _published(degree=110.0)
    row.published = False
    s = _Session(existing=row)
    out = unified_store.store(s, composed(degree=130.0))
    assert out.editorial_stale is False, "nothing was shown to anyone yet"


def test_recompose_replaces_the_ranked_chart():
    s = _Session(existing=_published())
    unified_store.store(s, composed(songs=2))
    assert s.deleted == 1, "old ranked rows are cleared before the rewrite"
    assert len([a for a in s.added if not isinstance(a, UnifiedReading)]) == 2


# --- policy: minimum sources ------------------------------------------------

def test_single_source_day_is_not_stored(monkeypatch_compose=None):
    s = _Session()
    import app.services.unified_store as mod
    original = mod.compose
    mod.compose = lambda db, d, weights=None: composed(sources=(SPOTIFY,))
    try:
        assert mod.recompose(s, DAY) is None
        assert not s.added, "nothing written for a one-chart 'union'"
    finally:
        mod.compose = original


def test_two_sources_is_enough():
    s = _Session()
    import app.services.unified_store as mod
    original = mod.compose
    mod.compose = lambda db, d, weights=None: composed(sources=(SPOTIFY, ITUNES))
    try:
        assert mod.recompose(s, DAY) is not None
    finally:
        mod.compose = original


def test_nothing_composed_leaves_an_existing_reading_alone():
    # A transient gap must not erase a good stored day.
    s = _Session(existing=_published())
    import app.services.unified_store as mod
    original = mod.compose
    mod.compose = lambda db, d, weights=None: None
    try:
        assert mod.recompose(s, DAY) is None
        assert s.deleted == 0 and not s.added
    finally:
        mod.compose = original


def test_recompose_safe_swallows_and_rolls_back():
    s = _Session()
    import app.services.unified_store as mod
    original = mod.compose

    def boom(db, d, weights=None):
        raise RuntimeError("composition exploded")

    mod.compose = boom
    try:
        assert mod.recompose_safe(s, DAY) is None
        assert s.rolled_back, "the caller's session must stay usable"
    finally:
        mod.compose = original


# --- publish: the editorial IS the gate -------------------------------------

def test_publish_attaches_prose_and_flips_published():
    row = _published(editorial=None)
    row.published = False
    row.published_at = None
    s = _Session(existing=row)
    out = unified_store.publish(s, DAY, "The reading holds at center.")

    assert out.published is True
    assert out.published_at is not None
    assert out.editorial == "The reading holds at center."


def test_publish_clears_the_stale_flag():
    row = _published()
    row.editorial_stale = True
    s = _Session(existing=row)
    out = unified_store.publish(s, DAY, "Rewritten against the new figures.")
    assert out.editorial_stale is False


def test_publish_on_a_missing_day_returns_none():
    assert unified_store.publish(_Session(existing=None), DAY, "x") is None
