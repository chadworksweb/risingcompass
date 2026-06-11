"""Calibrator v3 server composition layer: the model reads, the server computes.

The v3 read procedure has the model emit COMPONENTS only -- visceral read,
route classification, two-axis read (harm / transcendence), precedent-placed
center, and a pole-agnostic intensity vernier. This module owns everything a
model should not: it validates the components, decides the governing axis,
composes the charge, derives the tier, cross-derives the contamination flag,
and evaluates the escalation triggers. The model NEVER names the final number
or the tier; those exist only here.

Pure functions, no DB, no Anthropic -- importable from the calibrator, the
terminal supply path (routers/agent.py), and calibration_corpus without
cycles. Spec: plans and docs/RISING-COMPASS-CALIBRATOR-V3.md section 2.2.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Gut-vs-verdict divergence past this many points both demands the
# reconciliation explanation in the read and trips the escalation flag.
# One tier-band width; ruled 2026-06-11 (spec section 5).
DIVERGENCE_THRESHOLD = 25

# The seven v3 routes. Each carries only its own checks and caps in the
# operating manual; here a route implies which axis governs a non-pervasive
# read.
ROUTES = frozenset({
    "internal_work",
    "collective_stance",
    "encouragement",
    "witness_critique",
    "doctrinal",
    "static_portrait",
    "negative_payload",
})

AXIS_HARM = "harm"
AXIS_TRANSCENDENCE = "transcendence"
AXIS_NEUTRAL = "neutral"

ROUTE_AXIS = {
    "internal_work": AXIS_TRANSCENDENCE,
    "collective_stance": AXIS_TRANSCENDENCE,
    "encouragement": AXIS_TRANSCENDENCE,
    # Witness can EARN Elevated, but a sour warning legitimately sits slightly
    # negative (sanity rehearsal 2026-06-11: Maneater) -- mapping it neutral
    # lets the center sign govern silently instead of firing
    # route_center_conflict on every healthy sour-witness read.
    "witness_critique": AXIS_NEUTRAL,
    "doctrinal": AXIS_NEUTRAL,
    "static_portrait": AXIS_NEUTRAL,
    "negative_payload": AXIS_HARM,
}

VERNIER_KEYS = ("sat", "res", "reg", "reach")
VERNIER_MIN, VERNIER_MAX = -2, 2
# Total vernier shift is clamped to one half tier-band step in either
# direction. Coprime weights (2,1,1,1) keep parity honest: every integer
# shift in [-5, +5] is reachable, so verdicts do not snap to an even grid.
SHIFT_CAP = 5


class CompositionError(ValueError):
    """Raised when the model's component JSON fails validation. The caller
    turns this into the explicit needs-human-review failure -- never a
    defaulted verdict."""


def derive_tier(charge: int) -> str:
    """Map a charge value to its rubric color. THE single tier function:
    tier is derived from charge everywhere, never model-chosen."""
    if charge >= 75:
        return "violet"
    if charge >= 25:
        return "blue"
    if charge >= -24:
        return "green"
    if charge >= -74:
        return "orange"
    return "red"


@dataclass
class Components:
    """The validated v3 component set, as emitted by the model."""
    visceral_charge: int
    route: str
    harm_value: int            # 0..-100
    harm_pervasive: bool
    transcendence_value: int   # 0..+100
    center: int                # precedent-placed center, -100..+100
    vernier: dict              # {sat, res, reg, reach}, each -2..+2
    precedent_refs: list = field(default_factory=list)


@dataclass
class Composed:
    """The server's composition of one read."""
    charge: int
    rubric_color: str
    governing_axis: str
    shift: int                 # signed vernier shift actually applied
    gut_divergence: int        # abs(charge - visceral_charge)
    contaminated: bool         # cross-derived from the axis data
    signals: list              # incoherence signal slugs (recorded, not fatal)


def _require_int(raw: dict, key: str, lo: int, hi: int, problems: list) -> int:
    value = raw.get(key)
    try:
        value = int(value)
    except (TypeError, ValueError):
        problems.append(f"{key} missing or not an integer")
        return 0
    if not lo <= value <= hi:
        problems.append(f"{key} out of range [{lo}, {hi}]: {value}")
        return max(lo, min(hi, value))
    return value


def validate_components(raw: dict) -> Components:
    """Validate the model's v3 JSON into a Components set, or raise
    CompositionError naming every problem at once."""
    problems: list[str] = []

    route = raw.get("route")
    if route not in ROUTES:
        problems.append(f"route invalid: {route!r}")

    harm = raw.get("harm") or {}
    transcendence = raw.get("transcendence") or {}
    if not isinstance(harm, dict):
        problems.append("harm is not an object")
        harm = {}
    if not isinstance(transcendence, dict):
        problems.append("transcendence is not an object")
        transcendence = {}

    visceral = _require_int(raw, "visceral_charge", -100, 100, problems)
    harm_value = _require_int(harm, "value", -100, 0, problems)
    transcendence_value = _require_int(transcendence, "value", 0, 100, problems)
    center = _require_int(raw, "center", -100, 100, problems)

    vernier_raw = raw.get("vernier")
    vernier: dict[str, int] = {}
    if not isinstance(vernier_raw, dict):
        problems.append("vernier missing or not an object")
    else:
        for key in VERNIER_KEYS:
            vernier[key] = _require_int(
                vernier_raw, key, VERNIER_MIN, VERNIER_MAX, problems)

    refs = raw.get("precedent_refs")
    if refs is None:
        refs = []
    if not isinstance(refs, list):
        problems.append("precedent_refs is not a list")
        refs = []

    if problems:
        raise CompositionError("; ".join(problems))

    return Components(
        visceral_charge=visceral,
        route=route,
        harm_value=harm_value,
        harm_pervasive=bool(harm.get("pervasive", False)),
        transcendence_value=transcendence_value,
        center=center,
        vernier=vernier,
        precedent_refs=[str(r) for r in refs],
    )


def _governing_axis(c: Components, signals: list) -> str:
    """The governance rule. A harm payload marked PERVASIVE governs regardless
    of the transcendence claim -- the Flag-7 hatch is impossible by
    construction. Otherwise the route's axis governs; neutral routes resolve
    by the sign of the precedent-placed center. A route/center contradiction
    is recorded as a signal and the center sign governs: the server composes
    from the model's own placement, it never invents a number."""
    if c.harm_pervasive:
        return AXIS_HARM
    axis = ROUTE_AXIS.get(c.route, AXIS_NEUTRAL)
    center_axis = (
        AXIS_TRANSCENDENCE if c.center > 0
        else AXIS_HARM if c.center < 0
        else AXIS_NEUTRAL
    )
    if axis == AXIS_NEUTRAL:
        return center_axis
    if center_axis != AXIS_NEUTRAL and center_axis != axis:
        signals.append("route_center_conflict")
        return center_axis
    return axis


def compose(c: Components) -> Composed:
    """Compose the charge from validated components:

        charge = center + sign(governing axis) * clamp(2*sat + res + reg + reach, -5, +5)

    Pole-agnostic vernier components, server-applied sign. Tier is derived
    from the composed charge. Incoherence is recorded as signals, never
    silently resolved."""
    signals: list[str] = []
    governing = _governing_axis(c, signals)

    v = c.vernier
    raw_shift = 2 * v["sat"] + v["res"] + v["reg"] + v["reach"]
    magnitude = max(-SHIFT_CAP, min(SHIFT_CAP, raw_shift))
    if governing == AXIS_HARM:
        sign = -1
    elif governing == AXIS_TRANSCENDENCE:
        sign = 1
    else:
        sign = 1 if c.center > 0 else -1 if c.center < 0 else 0
    shift = sign * magnitude
    charge = max(-100, min(100, c.center + shift))
    color = derive_tier(charge)
    divergence = abs(charge - c.visceral_charge)

    # Mandatory mixed signs on the vernier: an all-one-direction reading is
    # recorded as a signal (the model skipped the relative judgment), not
    # failed.
    nonzero = [v[k] for k in VERNIER_KEYS if v[k] != 0]
    if len(nonzero) >= 2 and (all(x > 0 for x in nonzero) or all(x < 0 for x in nonzero)):
        signals.append("vernier_uniform_sign")

    # A pervasive harm payload coexisting with a positive transcendence claim
    # is the hatch shape -- legal (a song can score on both axes) but always
    # recorded.
    if c.harm_pervasive and c.transcendence_value > 0:
        signals.append("hatch_shape")

    contaminated = derive_contamination(c, governing, color)

    return Composed(
        charge=charge,
        rubric_color=color,
        governing_axis=governing,
        shift=shift,
        gut_divergence=divergence,
        contaminated=contaminated,
        signals=signals,
    )


def derive_contamination(c: Components, governing: str, color: str) -> bool:
    """Contamination, cross-derived from the axis data: a DISCRETE harm
    artifact (harm scored, not pervasive) sitting on a read the harm axis does
    not govern, landing in a tier that can carry the flag. A pervasive payload
    governs the charge instead (and red/orange already calibrate as harmful,
    so they are never contaminated)."""
    return (
        governing != AXIS_HARM
        and not c.harm_pervasive
        and c.harm_value < 0
        and color in ("violet", "blue", "green")
    )


def evaluate_escalation(
    composed: Composed,
    c: Components,
    *,
    guard_trip_survived: bool = False,
    translated: bool = False,
    confidence: float | None = None,
    confidence_floor: float = 0.6,
) -> list[str]:
    """Evaluate the escalation triggers (spec 2.4). Any one is enough to
    escalate; the caller decides whether a re-pass actually runs (config).
    Self-reported confidence is deliberately the weakest signal, not the
    gate."""
    triggers: list[str] = []
    if composed.gut_divergence > DIVERGENCE_THRESHOLD:
        triggers.append("gut_divergence")
    if c.harm_pervasive and ROUTE_AXIS.get(c.route) == AXIS_TRANSCENDENCE:
        triggers.append("harm_governance_conflict")
    if guard_trip_survived:
        triggers.append("guard_trip_survived")
    if translated:
        triggers.append("translated_source")
    if "hatch_shape" in composed.signals:
        triggers.append("hatch_shape")
    if confidence is not None and confidence < confidence_floor:
        triggers.append("low_confidence")
    return triggers
