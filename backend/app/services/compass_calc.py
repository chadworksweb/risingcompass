"""Compass degree calculation from song charge values and chart positions."""

# Legacy fixed degrees per color (used when charge_value is missing)
COLOR_DEGREES = {
    "violet": 0.0,
    "blue": 45.0,
    "green": 90.0,
    "orange": 135.0,
    "red": 180.0,
}


def charge_to_degree(charge_value: int) -> float:
    """Convert a charge_value (+100 to -100) to internal degree (0 to 180)."""
    return round(90.0 - (charge_value * 0.9), 1)


# In-chart consumption slope for the rank-weighting law. The weight of a
# chart position is a function of RANK ALONE -- it never references how many
# songs are in the group. That scale-invariance is the whole point: a
# daily-20, an annual-100, a 12-track album, and any future chart all obey
# ONE law, and years stored at different depths (top-10 vs top-100) stay on
# the same axis because ranks 1..10 carry identical weights regardless of how
# deep the chart goes.
#
# The law is Zipf / inverse-rank-power: weight = 1 / rank**ZIPF_S.
#   s = 1.0  -> pure Zipf, #1 is 100x the #100 (very top-heavy)
#   s < 1.0  -> the deep tail still registers
# Population music consumption is a power law, and the chart is the top
# truncation of it. s = 0.7 puts #1:#20 ~= 8x and #1:#100 ~= 25x, which sits
# in the band of real Hot-100 stream ratios -- top-heavy, but the #100 song
# still counts (it is hugely more consumed than the #1000 or a random indie
# release, which are simply not in the group at all). Tune here; every
# downstream aggregate is a weighted MEAN, so only the RATIOS between weights
# matter, never their absolute scale.
# Full rationale + worked tables: RISING-COMPASS-CHARGE-WEIGHTING.md.
ZIPF_S = 0.7


def position_weight(position: int, total: int | None = None) -> float:
    """Scale-invariant rank weight: 1 / position**ZIPF_S.

    Higher chart position (position 1 = #1) gets more weight. The weight
    depends on RANK ALONE; `total` is accepted only for backward
    compatibility with existing callers and is deliberately ignored -- the
    group size must not change how loudly a given rank counts.

    Returns a float in (0, 1]; callers normalize via a weighted mean, so the
    absolute scale is irrelevant.

    # future: when real per-song consumption volume (streams / sales) is
    # available, weight by that normalized magnitude directly instead of by
    # this rank proxy. It self-calibrates the #100-vs-#1000 gap and needs no
    # ZIPF_S knob; rank-power is the honest stand-in until then.
    """
    r = max(1, int(position))
    return 1.0 / (r ** ZIPF_S)


def compute_degree(songs: list[dict], color_degrees: dict | None = None) -> float:
    """
    Compute weighted average compass degree from a list of songs.

    Uses charge_value when available (precise per-song scoring).
    Falls back to fixed degrees per color for legacy data.

    Each song dict needs 'rubric_color' and 'position' (or 'chart_position').
    Optionally 'charge_value' for precise scoring.

    Args:
        color_degrees: Override color-to-degree mapping for the fallback.
                       Use constants.HISTORICAL_DEGREES when mixing old 3-tier data.

    Returns degree 0-180.
    """
    if not songs:
        return 90.0  # neutral if no data

    fallback = color_degrees or COLOR_DEGREES
    total_weight = 0.0
    weighted_sum = 0.0

    for song in songs:
        pos = song.get("chart_position") or song.get("position", 5)
        w = position_weight(pos)

        # Use charge_value if available, otherwise fall back to fixed color degree
        cv = song.get("charge_value")
        if cv is not None:
            deg = charge_to_degree(cv)
        else:
            color = song.get("rubric_color", "green")
            deg = fallback.get(color, 90.0)

        weighted_sum += deg * w
        total_weight += w

    if total_weight == 0:
        return 90.0

    return round(weighted_sum / total_weight, 1)


def compute_live_year_degree(songs: list[dict]) -> float:
    """
    Compute weighted average compass degree for a live year (2026+).

    Each song dict needs:
      - 'charge_value': the charge from its most recent appearance
      - 'effective_weight': sum of position_weight() across all daily appearances

    Songs with higher effective_weight (more appearances at higher positions)
    have more influence on the year aggregate.

    Returns degree 0-180.
    """
    if not songs:
        return 90.0

    total_weight = 0
    weighted_sum = 0.0

    for song in songs:
        cv = song.get("charge_value")
        w = song.get("effective_weight", 1)

        if cv is not None:
            deg = charge_to_degree(cv)
        else:
            color = song.get("rubric_color", "green")
            deg = COLOR_DEGREES.get(color, 90.0)

        weighted_sum += deg * w
        total_weight += w

    if total_weight == 0:
        return 90.0

    return round(weighted_sum / total_weight, 1)
