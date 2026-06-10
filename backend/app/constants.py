# Tier display constants — single source of truth
COLOR_LABELS = {
    "violet": "Ascended", "blue": "Elevated", "green": "Decent",
    "orange": "Degraded", "red": "Corrupted",
}
COLOR_HEX = {
    "violet": "#9933ff", "blue": "#3388ff", "green": "#33cc55",
    "orange": "#ffbb33", "red": "#ff3333",
}
COLOR_BG = {
    "violet": "#f3e8ff", "blue": "#e8f0ff", "green": "#e8fae8",
    "orange": "#fff5e0", "red": "#ffe8e8",
}

# Chart sources that represent popular music consciousness.
# Any chart_source in this set = charting song.
# "manual" (and any future non-chart source) = non-chart.
TIER_LABELS_REVERSE = {
    "Ascended": "violet", "Elevated": "blue", "Decent": "green",
    "Degraded": "orange", "Corrupted": "red",
}

CHART_SOURCES = {
    "billboard_hot_100",
    "billboard_200",
    "billboard_yearend_2024",
    "billboard_yearend_2025",
    "spotify",
    "spotify_top50_usa",
    "spotify_global_daily",
}

# Unified song-entity renovation: maps a legacy compass_songs.chart_source value
# to a `charts.slug`. Consumed by scripts/unify_songs.py (Phase 2) when building
# chart_appearances, and by the post-cutover write paths. Sources NOT present
# here (NON_CHART_SOURCES) produce NO chart_appearance -- the structural
# "non-chart" boundary that replaces today's CHART_SOURCES membership test.
CHART_SOURCE_TO_CHART_SLUG = {
    "billboard_hot_100": "billboard_yearend_hot100",
    "billboard_yearend_2024": "billboard_yearend_hot100",
    "billboard_yearend_2025": "billboard_yearend_hot100",
    "billboard_200": "billboard_200",
    "spotify_top50_usa": "spotify_top50_usa",
    "itunes_download_usa": "itunes_download_usa",
    "shazam_top200_usa": "shazam_top200_usa",
    "youtube_trending_usa": "youtube_trending_usa",
    "spotify_global_daily": "spotify_global_daily",
    "spotify": "spotify",
}
NON_CHART_SOURCES = {"manual", "backfill_console"}

# Chart slugs whose appearances count toward the compass charge + year/decade
# aggregates -- the unified-model equivalent of CHART_SOURCES. Everything except
# the secondary-panel snapshot chart (the iTunes Download Chart): a side
# snapshot, never part of the main compass charge. Songs can still chart on it
# (a real chart) without polluting the aggregate.
AGGREGATING_CHART_SLUGS = {
    "billboard_yearend_hot100", "billboard_200", "spotify",
    "spotify_top50_usa", "spotify_global_daily",
}

# Degree mapping for legacy (pre-5-tier) songs. Old 3-tier system had
# no violet tier, so most songs were green or orange. Blue is mapped to 65
# (upper Elevated, nearly Decent) to reflect that coarseness honestly.
HISTORICAL_DEGREES = {
    "violet": 0.0,
    "blue": 65.0,
    "green": 90.0,
    "orange": 135.0,
    "red": 180.0,
}


# Human-readable display names per AgentDraft.draft_type. Single source of
# truth for "what do we call this draft" in emails, approval pages, admin
# UI. Add a new chart by registering it both here and in CHART_REGISTRY
# (routers/chart_snapshots.py).
# Branded names ("for now"). The daily reading (Spotify Top 50 stream chart) is
# the Daily Listens chart; the iTunes download chart is Daily Downloads. Internal
# draft_type keys + table names are unchanged -- this is display only.
DRAFT_TYPE_DISPLAY_NAMES = {
    "daily": "Daily Listens",
    "manual": "Manual Reading",
    "spotify_top50_usa": "Daily Listens",
    "itunes_download_usa": "Daily Downloads",
    "shazam_top200_usa": "Shazam Top 200 - USA",
    "youtube_trending_usa": "YouTube Trending - USA",
}


def draft_display_name(draft_type) -> str:
    """Human label for a draft.draft_type. Falls back to 'Daily Listens' for
    legacy/null types and to the raw slug for unregistered charts."""
    if not draft_type:
        return "Daily Listens"
    return DRAFT_TYPE_DISPLAY_NAMES.get(draft_type, draft_type)


# Real chart names keyed by chart_source -- for a song's origin_chart ("first
# surfaced on") line. Distinct from DRAFT_TYPE_DISPLAY_NAMES, which carries the
# front-facing "Daily Listens"/"Daily Downloads" rebrand; origin provenance
# names the actual chart a song first appeared on. Build 7.
CHART_SOURCE_LABELS = {
    "spotify_top50_usa": "Spotify Top 50 - USA",
    "spotify_global_daily": "Spotify Global Daily",
    "spotify": "Spotify",
    "itunes_download_usa": "iTunes Download Chart - USA",
    "shazam_top200_usa": "Shazam Top 200 - USA",
    "youtube_trending_usa": "YouTube Trending - USA",
    "billboard_hot_100": "Billboard Year-End Hot 100",
    "billboard_yearend_2024": "Billboard Year-End Hot 100",
    "billboard_yearend_2025": "Billboard Year-End Hot 100",
    "billboard_200": "Billboard 200",
}


def chart_source_label(chart_source) -> str | None:
    """Human chart name for a song's origin_chart (a chart_source key). Returns
    None for empty input; falls back to the raw key for unregistered sources."""
    if not chart_source:
        return None
    return CHART_SOURCE_LABELS.get(chart_source, chart_source)


def is_chart_draft_type(draft_type) -> bool:
    """True if this draft_type names a chart-snapshot (iTunes chart, etc.) rather
    than the canonical daily/manual reading. Used by every cleanup, approval,
    and naming branch that must not treat chart drafts the same as readings.
    """
    if not draft_type:
        return False
    return draft_type not in ("daily", "manual")
