"""The Unified Charge Chart composition law (services/unified_chart.py).

Exercises compose_from_slots, which is the real arithmetic with the ORM lifted
out, so these assert on the shipping code path rather than on a mocked database.

The guarantees under test, and why each one matters:

  1. Corroboration is additive. A song on four charts outweighs the same song on
     one, with no bonus multiplier anywhere in the code.
  2. Depth is not weight. A chart handed 20 slots and the same chart handed 100
     contribute the same total, so deepening a feed can never silently re-weight
     the instrument. This is RISING-COMPASS-CHARGE-WEIGHTING.md section 1 one
     level up.
  3. A chart below the coverage floor drops out, and the survivors renormalize
     rather than the day quietly shrinking.
  4. One chart alone reproduces that chart's own degree exactly, so the unified
     law is a generalization of the per-chart law and not a second opinion.
  5. Composing twice gives the same answer, which idempotent recomposition
     (scope 6.3) depends on.

See RISING-COMPASS-UNIFIED-CHARGE-CHART-SCOPE.md sections 4 and 6.
"""

from datetime import date

from app.constants import UNIFIED_CONSTITUENT_SLUGS
from app.services.compass_calc import compute_degree
from app.services.unified_chart import Slot, compose_from_slots, default_weights

DAY = date(2026, 8, 16)
SPOTIFY, ITUNES, SHAZAM, YOUTUBE = UNIFIED_CONSTITUENT_SLUGS


class FakeSong:
    """Stand-in for a songs row. Only the fields eligibility and the aggregate read."""

    def __init__(self, song_id, charge_value, title=None, artist=None,
                 contaminated=False, preorder=False, instrumental=False,
                 lyrics_unavailable=False, calibration_failed=False,
                 rubric_color="green"):
        self.id = song_id
        self.title = title or f"Song {song_id}"
        self.artist = artist or f"Artist {song_id}"
        self.charge_value = charge_value
        self.rubric_color = rubric_color
        self.contaminated = contaminated
        self.preorder = preorder
        self.instrumental = instrumental
        self.lyrics_unavailable = lyrics_unavailable
        self.calibration_failed = calibration_failed


def chart(*songs, start=1):
    """Build a chart's slots from songs, ranked from `start` in order given."""
    return [Slot(position=start + i, song_id=s.id, song=s) for i, s in enumerate(songs)]


def filler(n, charge=0, first_id=900):
    return [FakeSong(first_id + i, charge) for i in range(n)]


def weight_of(reading, song_id):
    return next(s.unified_weight for s in reading.songs if s.song_id == song_id)


# --- 1. corroboration is additive -------------------------------------------

def test_song_on_all_four_charts_outweighs_song_on_one():
    everywhere = FakeSong(1, 50)
    only_spotify = FakeSong(2, 50)
    tail = filler(3)

    slots = {
        SPOTIFY: chart(everywhere, only_spotify, *tail),
        ITUNES: chart(everywhere, *tail),
        SHAZAM: chart(everywhere, *tail),
        YOUTUBE: chart(everywhere, *tail),
    }
    r = compose_from_slots(DAY, slots)

    assert r.songs[0].song_id == 1, "the corroborated song must rank first"
    assert weight_of(r, 1) > weight_of(r, 2)
    assert r.songs[0].chart_count == 4
    assert next(s for s in r.songs if s.song_id == 2).chart_count == 1


def test_corroboration_needs_no_bonus_multiplier():
    # Same rank on all four charts accumulates ~4x the single-chart weight, from
    # the sum alone. If someone adds a consensus coefficient later, this breaks.
    song = FakeSong(1, 0)
    tail = filler(4)
    one = compose_from_slots(DAY, {SPOTIFY: chart(song, *tail)})
    four = compose_from_slots(DAY, {
        SPOTIFY: chart(song, *tail), ITUNES: chart(song, *tail),
        SHAZAM: chart(song, *tail), YOUTUBE: chart(song, *tail),
    })
    assert round(weight_of(four, 1) / weight_of(one, 1), 6) == 4.0


# --- 2. depth is coverage, never weight -------------------------------------

def test_chart_depth_does_not_change_its_contribution():
    """A 20-deep chart and a 100-deep chart hand out the same total vote."""
    top = FakeSong(1, 100)
    shallow = compose_from_slots(DAY, {SPOTIFY: chart(top, *filler(19))})
    deep = compose_from_slots(DAY, {SPOTIFY: chart(top, *filler(99))})

    assert round(sum(s.unified_weight for s in shallow.songs), 6) == 1.0
    assert round(sum(s.unified_weight for s in deep.songs), 6) == 1.0


def test_deepening_one_chart_does_not_re_weight_the_others():
    # The failure this guards: take Shazam to its full Top 200 and it must not
    # gain say over iTunes purely by being longer.
    a, b = FakeSong(1, 80), FakeSong(2, -80)
    shallow = compose_from_slots(DAY, {
        ITUNES: chart(a, *filler(19)),
        SHAZAM: chart(b, *filler(19, first_id=800)),
    })
    deep = compose_from_slots(DAY, {
        ITUNES: chart(a, *filler(19)),
        SHAZAM: chart(b, *filler(199, first_id=800)),
    })
    # iTunes' #1 carries the same weight in both compositions.
    assert round(weight_of(shallow, 1), 6) == round(weight_of(deep, 1), 6)


# --- 3. the coverage floor --------------------------------------------------

def test_chart_below_floor_is_excluded_and_survivors_renormalize():
    good = FakeSong(1, 60)
    # A chart whose #1 and #2 are unidentified loses most of its weight mass.
    broken = [Slot(position=1, song_id=None, song=None),
              Slot(position=2, song_id=None, song=None)]
    broken += chart(*filler(3), start=3)

    r = compose_from_slots(DAY, {
        SPOTIFY: chart(good, *filler(4)),
        YOUTUBE: broken,
    })

    excluded = {e["slug"]: e for e in r.sources_excluded}
    assert YOUTUBE in excluded
    assert excluded[YOUTUBE]["reason"] == "below_coverage_floor"
    assert [c.slug for c in r.sources_included] == [SPOTIFY]
    # Spotify still hands out its full 1.0 rather than the day shrinking.
    assert round(sum(s.unified_weight for s in r.songs), 6) == 1.0


def test_exclusions_inside_a_kept_chart_renormalize_to_full_weight():
    # One ineligible slot (a pre-order) must not dock the chart's total vote.
    keep, pre = FakeSong(1, 40), FakeSong(2, 0, preorder=True)
    r = compose_from_slots(DAY, {SPOTIFY: chart(keep, pre, *filler(8))})

    assert round(sum(s.unified_weight for s in r.songs), 6) == 1.0
    assert all(s.song_id != 2 for s in r.songs), "pre-order carries no reading"
    assert r.sources_included[0].eligible == 9
    assert r.sources_included[0].slots == 10


def test_every_null_disposition_and_failure_is_ineligible():
    # Floor off, to isolate eligibility from the floor. With the floor ON this
    # exact chart is legitimately excluded outright -- see the next test.
    live = FakeSong(1, 30)
    dead = [
        FakeSong(2, 0, preorder=True),
        FakeSong(3, 0, instrumental=True),
        FakeSong(4, 0, lyrics_unavailable=True),
        FakeSong(5, 0, calibration_failed=True),
    ]
    r = compose_from_slots(DAY, {SPOTIFY: chart(live, *dead, *filler(5))},
                           coverage_floor=0.0)
    assert {s.song_id for s in r.songs}.isdisjoint({2, 3, 4, 5})
    assert {s.song_id for s in r.songs} == {1, 900, 901, 902, 903, 904}


def test_ineligibles_at_the_TOP_can_sink_a_whole_chart():
    """Documented consequence, not a bug: the floor is weight share, not a count.

    Four dead slots at ranks 2-5 strip ~44% of a 10-song chart's weight, so the
    chart drops below the 0.80 floor and contributes nothing. The same four dead
    slots at the BOTTOM cost almost nothing and the chart stays in. That is the
    floor doing its job (a chart whose top is missing is not a fair picture of
    that chart), but it means a pre-order-heavy chart day can legitimately vanish
    from the union, and the reading needs to say so rather than look complete.
    """
    live = FakeSong(1, 30)
    dead = [FakeSong(i, 0, preorder=True) for i in (2, 3, 4, 5)]

    top_heavy = compose_from_slots(DAY, {SPOTIFY: chart(live, *dead, *filler(5))})
    assert top_heavy is None, "no constituent survived"

    bottom_heavy = compose_from_slots(
        DAY, {SPOTIFY: chart(live, *filler(5), *dead)})
    assert bottom_heavy is not None
    assert [c.slug for c in bottom_heavy.sources_included] == [SPOTIFY]


def test_unidentified_slot_never_counts():
    # NULL song_id is the whole reason chart_snapshots.song_id exists. It must
    # be ineligible, never matched by title.
    live = FakeSong(1, 20)
    slots = chart(live, *filler(8)) + [Slot(position=10, song_id=None, song=None)]
    r = compose_from_slots(DAY, {SPOTIFY: slots})
    assert len(r.songs) == 9
    assert r.sources_included[0].eligible == 9


# --- 4. one chart reproduces that chart's own law ---------------------------

def test_single_chart_reproduces_its_own_degree():
    """The unified law generalizes the per-chart law; it does not replace it."""
    songs = [FakeSong(i + 1, c) for i, c in enumerate([80, 40, 0, -40, -80])]
    r = compose_from_slots(DAY, {SPOTIFY: chart(*songs)})

    per_chart = compute_degree(
        [{"charge_value": s.charge_value, "position": i + 1}
         for i, s in enumerate(songs)]
    )
    assert r.compass_degree == per_chart


def test_four_identical_charts_reproduce_the_single_chart_degree():
    # Equal weights over identical inputs must not drift the number.
    songs = [FakeSong(i + 1, c) for i, c in enumerate([60, 20, -20, -60])]
    one = compose_from_slots(DAY, {SPOTIFY: chart(*songs)})
    four = compose_from_slots(DAY, {s: chart(*songs) for s in UNIFIED_CONSTITUENT_SLUGS})
    assert four.compass_degree == one.compass_degree


# --- 5. idempotence + bookkeeping -------------------------------------------

def test_composing_twice_is_identical():
    songs = [FakeSong(i + 1, (i * 17) % 90 - 45) for i in range(12)]
    slots = {SPOTIFY: chart(*songs[:8]), ITUNES: chart(*songs[4:])}
    a = compose_from_slots(DAY, slots)
    b = compose_from_slots(DAY, slots)

    assert a.compass_degree == b.compass_degree
    assert [s.song_id for s in a.songs] == [s.song_id for s in b.songs]
    assert [round(s.unified_weight, 9) for s in a.songs] \
        == [round(s.unified_weight, 9) for s in b.songs]


def test_contamination_counts_songs_not_slots():
    # A contaminated song on three charts is ONE contaminated song. Counting
    # slots would inflate the number by corroboration.
    bad = FakeSong(1, -70, contaminated=True)
    r = compose_from_slots(DAY, {
        SPOTIFY: chart(bad, *filler(4)),
        ITUNES: chart(bad, *filler(4)),
        SHAZAM: chart(bad, *filler(4)),
    })
    assert r.contamination_count == 1


def test_no_constituents_returns_none():
    assert compose_from_slots(DAY, {}) is None
    assert compose_from_slots(DAY, {SPOTIFY: []}) is None


def test_zero_weight_chart_is_excluded_not_counted():
    song = FakeSong(1, 50)
    w = default_weights()
    w[YOUTUBE] = 0.0
    yt_ids = {700, 701, 702, 703, 704}
    r = compose_from_slots(DAY, {
        SPOTIFY: chart(song, *filler(4)),
        YOUTUBE: chart(*filler(5, charge=-90, first_id=700)),
    }, weights=w)

    assert {e["slug"]: e["reason"] for e in r.sources_excluded}[YOUTUBE] == "zero_weight"
    assert {s.song_id for s in r.songs}.isdisjoint(yt_ids)
    assert [c.slug for c in r.sources_included] == [SPOTIFY]


def test_source_weight_shifts_the_reading_predictably():
    # Doubling one chart's weight must move the number toward that chart.
    high = FakeSong(1, 90)
    low = FakeSong(2, -90)
    slots = {SPOTIFY: chart(high), YOUTUBE: chart(low)}

    even = compose_from_slots(DAY, slots)
    w = default_weights()
    w[YOUTUBE] = 3.0
    tilted = compose_from_slots(DAY, slots, weights=w)

    # Higher degree = more negative charge, so tilting toward YouTube raises it.
    assert tilted.compass_degree > even.compass_degree


def test_nmf_is_not_a_constituent():
    # Decision 6: New Music Friday is a promotion signal, not a consumption one.
    assert "spotify_nmf_usa" not in UNIFIED_CONSTITUENT_SLUGS
    assert len(UNIFIED_CONSTITUENT_SLUGS) == 4
