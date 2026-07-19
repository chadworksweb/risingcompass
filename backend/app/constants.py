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

# Unified song-entity renovation: maps a legacy compass_songs.chart_source value
# to a `charts.slug`. Consumed by scripts/unify_songs.py (Phase 2) when building
# chart_appearances, and by the post-cutover write paths. Sources NOT present
# here produce NO chart_appearance -- the structural non-chart boundary.
CHART_SOURCE_TO_CHART_SLUG = {
    "billboard_hot_100": "billboard_yearend_hot100",
    "billboard_yearend_2024": "billboard_yearend_hot100",
    "billboard_yearend_2025": "billboard_yearend_hot100",
    "spotify_top50_usa": "spotify_top50_usa",
    "itunes_download_usa": "itunes_download_usa",
    "shazam_top200_usa": "shazam_top200_usa",
    "youtube_trending_usa": "youtube_trending_usa",
    "spotify_nmf_usa": "spotify_nmf_usa",
}

# Chart slugs whose appearances count toward the compass charge -- the only
# charts that actually feed a charge measurement. Exactly two have a live feed:
# spotify_top50_usa (the daily charge, "Daily Listens") and billboard_yearend_hot100
# (the historical year/decade charge, the Billboard Year-End backfill). Every
# other chart (iTunes/Daily Downloads, Shazam, YouTube, all-time lists) is a
# display chart, excluded here so it never pollutes the aggregate. The legacy
# placeholders billboard_200 / spotify / spotify_global_daily were removed in
# migration 118 (no live feed; their few stray rows were repointed/cleared).
AGGREGATING_CHART_SLUGS = {
    "billboard_yearend_hot100", "spotify_top50_usa",
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
    "spotify_nmf_usa": "New Music Friday - USA",
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
    "itunes_download_usa": "iTunes Download Chart - USA",
    "shazam_top200_usa": "Shazam Top 200 - USA",
    "youtube_trending_usa": "YouTube Trending - USA",
    "spotify_nmf_usa": "New Music Friday - USA",
    "billboard_hot_100": "Billboard Year-End Hot 100",
    "billboard_yearend_2024": "Billboard Year-End Hot 100",
    "billboard_yearend_2025": "Billboard Year-End Hot 100",
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


# --- Draft-song null dispositions ------------------------------------------ #
# The three per-song dispositions that carry NO reading by design: pre-order
# (charting before release), lyrics-unavailable (released, lyrics unobtainable),
# and instrumental (nothing to read at all). Each exempts the song from the
# approval gate and from every aggregate.
#
# This lives in constants.py, a module with ZERO imports, on purpose: the
# terminal CLI scripts need the same answer as the server, and they should not
# have to drag httpx + the identity/search chain in to get one boolean.
#
# SINGLE SOURCE. The predicate used to be re-typed at every call site, and the
# copies drifted: the approval gate checked all three flags while the approval
# EMAIL and the terminal scripts checked only two, so a song correctly marked
# instrumental kept being reported as awaiting-lyrics on every feeder run. Call
# these instead of restating the rule.
NULL_DISPOSITIONS = ("preorder", "lyrics_unavailable", "instrumental")


def _disposition_flag(song, name):
    """Read one disposition flag off an ORM row OR a JSON response dict, so the
    server and the CLI scripts share one predicate over their two shapes."""
    if isinstance(song, dict):
        return bool(song.get(name))
    return bool(getattr(song, name, False))


def has_null_disposition(song):
    """True when the song carries any disposition exempting it from the approval
    gate and the aggregates. Accepts an ORM row or a response dict."""
    return any(_disposition_flag(song, f) for f in NULL_DISPOSITIONS)


def song_needs_lyrics(song):
    """True when the song still BLOCKS approval: no reading on it and no
    exempting disposition. THE definition of awaiting-lyrics -- the approval
    gate, the approval email, and every terminal script must agree, so they all
    call this. Accepts an ORM row or a response dict."""
    if isinstance(song, dict):
        rubric = song.get("rubric_color")
    else:
        rubric = getattr(song, "rubric_color", None)
    return rubric is None and not has_null_disposition(song)
