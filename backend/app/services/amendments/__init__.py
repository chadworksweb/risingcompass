"""Amendment log loader. Reads log.json and returns it for the public endpoint.

The log is the public record of what occurs in the deliberation venue (Discord
or elsewhere). Rising Compass holds the record; the discourse is off-site.
"""

import json
from functools import lru_cache
from pathlib import Path

_LOG_PATH = Path(__file__).parent / "log.json"


@lru_cache(maxsize=1)
def load_amendment_log() -> dict:
    with _LOG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)
