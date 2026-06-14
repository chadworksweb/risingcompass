"""Regression guard for the double-calibration bug.

Before the fix, _read_v3 fired a SECOND full calibration call whenever a soft
output guard (the mandatory "Contamination:" line, or charge_summary framing)
tripped -- even when the first read already produced valid components and a
composable charge. Every public charge therefore ran the calibrator twice.

After the fix:
  - a valid first read SHIPS on call 1 (the guard drift is recorded on the
    read as guard_trip_survived, not retried away), so exactly ONE model call;
  - a genuinely unusable read (unparseable JSON / failed component validation)
    STILL retries, because there is no result without a second attempt.

The test fakes the metered Anthropic call and counts invocations -- no DB, no
Opus spend, no shared-prod-DB writes.
"""

import asyncio
import json

from app.services.agents import calibrator


def _valid_components_json() -> str:
    # A pervasive negative payload -- the shape a harmful track (e.g. the -77 /
    # -79 readings that surfaced this bug) validates into.
    return json.dumps({
        "route": "negative_payload",
        "visceral_charge": -75,
        "harm": {"value": -80, "pervasive": True},
        "transcendence": {"value": 0},
        "center": -75,
        "vernier": {"sat": 0, "res": -1, "reg": 0, "reach": 1},
        "precedent_refs": [],
        "confidence": 0.9,
        "charge_summary": "The track drives a degraded payload through its hook.",
    })


class _FakeContent:
    def __init__(self, text):
        self.text = text


class _FakeResponse:
    def __init__(self, text):
        self.content = [_FakeContent(text)]


def _install_fake(text_for_attempt):
    """Replace the metered call with a counter. Returns the call-count dict."""
    calls = {"n": 0}

    async def fake(client, *, call_site, context=None, **kwargs):
        calls["n"] += 1
        return _FakeResponse(text_for_attempt(calls["n"]))

    calibrator.tracked_create_async = fake
    return calls


def test_guard_trip_with_valid_components_calls_once():
    # Reasoning deliberately omits the "Contamination:" line -> contam guard
    # trips. Components are valid, so the read is usable and must ship as-is.
    text = "VERDICT: degraded. The negative payload governs.\n" + _valid_components_json()
    calls = _install_fake(lambda n: text)

    read = asyncio.run(calibrator._read_v3(
        None, "test-model", "system", "user",
        title="Test Song", artist="Test Artist", target_year=None,
    ))

    assert calls["n"] == 1, f"expected ONE model call on a soft-guard trip, got {calls['n']}"
    assert read is not None, "a valid first read must ship, not return None"
    assert read["guard_trip_survived"] is True, "guard drift must be recorded, not silently dropped"


def test_unparseable_json_still_retries():
    # No JSON at all -> no usable read -> the corrective retry is the only path
    # to a result, so the second attempt MUST still fire.
    calls = _install_fake(lambda n: "no parseable json in this response at all")

    read = asyncio.run(calibrator._read_v3(
        None, "test-model", "system", "user",
        title="Test Song", artist="Test Artist", target_year=None,
    ))

    assert calls["n"] == 2, f"expected TWO attempts on unparseable JSON, got {calls['n']}"
    assert read is None, "an unusable read must return None for the human-review fallback"
