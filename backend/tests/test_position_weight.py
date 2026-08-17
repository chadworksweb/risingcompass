"""The rank-weighting law (compass_calc.position_weight) must stay
scale-invariant: a rank's weight depends on the rank alone, never on how many
songs are in the group. This is what keeps every chart (daily-20, annual-100,
album, any future chart) on one law and keeps years of different depth
comparable. See RISING-COMPASS-CHARGE-WEIGHTING.md.
"""

from app.services.compass_calc import position_weight, compute_degree, ZIPF_S


def test_weight_is_inverse_rank_power():
    for r in (1, 2, 5, 20, 100):
        assert position_weight(r) == 1.0 / (r ** ZIPF_S)


def test_scale_invariant_total_is_ignored():
    # Same rank -> same weight no matter what `total` a legacy caller passes.
    assert position_weight(5) == position_weight(5, total=20)
    assert position_weight(5, total=20) == position_weight(5, total=100)


def test_ratios_match_design_band():
    # s = 1.0 (Zipf's law proper, set 2026-07-03): #1 counts 20x the #20 and
    # 100x the #100. These assertions previously encoded the s = 0.7 targets
    # (8.1x and 25.1x) and had been failing since that change went in, unnoticed
    # because pytest is not installed in the backend venv. Derive from ZIPF_S so
    # the next tune cannot silently strand them again.
    assert round(position_weight(1) / position_weight(20), 1) == round(20 ** ZIPF_S, 1)
    assert round(position_weight(1) / position_weight(100), 1) == round(100 ** ZIPF_S, 1)


def test_top_outweighs_tail_independent_of_depth():
    # A violet #1 over a long red tail stays violet-leaning whether the tail is
    # 19 songs or 99 -- the aggregate must not flatten just because N grows.
    def degree(tail_len):
        songs = [{"charge_value": 100, "position": 1}]
        songs += [{"charge_value": -100, "position": p}
                  for p in range(2, tail_len + 2)]
        return compute_degree(songs)

    d20 = degree(19)
    d100 = degree(99)
    # Both stay on the negative side of neutral (degree > 90), and adding 80
    # more tail songs moves the number only modestly (no collapse to ~180).
    assert d20 > 90 and d100 > 90
    assert abs(d100 - d20) < 30


def test_rank_floor_guards_bad_data():
    # Rank 0 / negative must not blow up; clamp to rank 1.
    assert position_weight(0) == position_weight(1)
    assert position_weight(-3) == position_weight(1)
