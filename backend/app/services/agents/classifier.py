"""Backwards-compat shim — classifier.py renamed to calibrator.py.

All new code should import from calibrator directly.
"""

from app.services.agents.calibrator import (  # noqa: F401
    calibrate_song,
    lookup_calibrated,
    AGENT_MODEL,
    VALID_COLORS,
    TIER_RANGES,
    TIER_MIDPOINTS,
)

# Old names for backwards compat
classify_song = calibrate_song
lookup_classified = lookup_calibrated
