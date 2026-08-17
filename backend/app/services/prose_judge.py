"""Semantic judge for RC editorial prose -- the layer above the regex scanner.

The regex guard (`prose_tell_guard`) catches the KNOWN surface tells A-P
deterministically. It cannot see the SEMANTIC tells: a circular / tautological
clause, a sentence that restates another, a bare proposition that reads as summary
instead of an effect, a literary flourish, or a manufactured downside on a
positive reading (tell Q and its relatives). Those need a model asked narrow
questions -- detection is far more reliable than avoidance.

Same pipeline, both paths. On the LC / autonomous path the judge model is Opus;
on the terminal path the judge model is Claude Code (the same Opus-vs-terminal
split the generators already use for generation). Findings are treated HARD: they
feed the regen correction and, if they survive, the fail-closed NULL.

Fail-soft: any error (API down, unparseable output) returns NO findings and logs,
so a broken judge can never block a reading. The regex floor still applies
underneath it.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from anthropic import Anthropic

from app.config import settings
from app.services.claude_meter import tracked_create

logger = logging.getLogger(__name__)

AGENT_MODEL = settings.agent_model

VALID_CODES = {"circular", "redundant", "summary", "flourish", "downside"}

JUDGE_SYSTEM = """You are a strict line editor checking a two-paragraph Rising Compass reading for AI tells that a regex cannot see. You are NOT rewriting; you only flag.

Flag ONLY these semantic problems:
- circular: a clause that defines a thing by itself and adds no information (e.g. "the relationships worth keeping are the ones you stay in" -- worth-keeping just means staying).
- redundant: a sentence or clause that restates another in different words.
- summary: a sentence that states a bare fact about the song or the world instead of an effect on the listener or society (no "you"/"a society" framing; it reads like a plot recap).
- flourish: a phrase adding decorative or literary texture instead of stating the effect plainly.
- downside: on a POSITIVE reading only, a clause that runs the song down, hedges the good effect, or dwells on what it fails to do.

Be conservative: flag only clear cases, not matters of taste. Output ONLY a JSON array. Each element is {"code": one of the five words above, "quote": "the exact offending span copied from the passage", "why": "at most 12 words"}. If the passage is clean, output exactly []. No prose, no markdown fences, no commentary -- a JSON array and nothing else."""


@dataclass(frozen=True)
class SemanticFinding:
    code: str
    quote: str
    why: str

    @property
    def name(self) -> str:
        return f"{self.code} (judge): {self.why}"

    @property
    def snippet(self) -> str:
        return self.quote


def _parse(raw: str) -> list[SemanticFinding]:
    """Parse the judge's JSON array. Tolerant of stray code fences / prose around
    it; returns [] on anything unparseable (fail-soft)."""
    if not raw:
        return []
    txt = raw.strip()
    # Strip a ```json ... ``` fence if the model added one.
    fence = re.search(r"\[.*\]", txt, re.DOTALL)
    if not fence:
        return []
    try:
        data = json.loads(fence.group(0))
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    out: list[SemanticFinding] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code", "")).strip().lower()
        if code not in VALID_CODES:
            continue
        out.append(SemanticFinding(
            code=code,
            quote=str(item.get("quote", "")).strip(),
            why=str(item.get("why", "")).strip(),
        ))
    return out


def judge(text: str, lane: str = "listener", *, positive: bool = True) -> list[SemanticFinding]:
    """Run the semantic judge over a finished passage. Returns the flagged
    semantic tells (all treated HARD by callers). Fail-soft: [] on any error.

    `positive` gates the 'downside' check: True for a positive/decent reading
    (manufactured downside is a tell), False for a genuinely negative reading
    (real corrosion language is correct, so downside is not flagged).
    """
    if not text or not text.strip():
        return []
    sign = "positive" if positive else "negative or neutral"
    user = (
        f"Lane: {lane}\n"
        f"Reading sign: {sign}\n\n"
        f"Passage:\n{text.strip()}"
    )
    try:
        client = Anthropic(api_key=settings.anthropic_api_key)
        response = tracked_create(
            client,
            call_site="prose_judge",
            context={"lane": lane},
            model=AGENT_MODEL,
            max_tokens=600,
            temperature=0.0,
            system=JUDGE_SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
        raw = (response.content[0].text or "").strip()
    except Exception:
        logger.exception("prose_judge call failed (non-fatal); returning no findings")
        return []
    findings = _parse(raw)
    # On a positive reading the downside check is live; on a negative reading,
    # drop any 'downside' flags the model raised anyway (real corrosion is fine).
    if not positive:
        findings = [f for f in findings if f.code != "downside"]
    return findings


def summarize(findings) -> str:
    fs = list(findings)
    return ", ".join(f.code for f in fs) if fs else "clean"
