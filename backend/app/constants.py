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

# Degree mapping for uncalibrated (pre-5-tier) songs. Old 3-tier system had
# no violet tier, so most songs were green or orange. Blue is mapped to 65
# (upper Elevated, nearly Decent) to reflect that coarseness honestly.
HISTORICAL_DEGREES = {
    "violet": 0.0,
    "blue": 65.0,
    "green": 90.0,
    "orange": 135.0,
    "red": 180.0,
}
