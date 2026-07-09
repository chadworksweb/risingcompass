"""AI-tell scanner for RC editorial prose (the voice retune).

Post-generation lint for the listener / societal effects prose. Encodes the
tell inventory A-P + the lane-bleed checks from
`plans and docs/RC-VOICE-RETUNE-STANDARD.md`. Pure regex, zero model calls, so it
runs on both the server path (public prose generation) and the terminal path
(where Claude Code is the model). Two uses:

  1. As a lint: `scan(text, lane)` returns the tells a passage trips, so a caller
     can regenerate-once-with-feedback (server) or a human/agent can fix (terminal).
  2. As a proof tool: run it over a batch of old vs new prose to measure the
     tell-hit rate before and after the retune.

Severity:
  - "hard"   -> a clear structural tell or a lane bleed. Should not appear in
               finished prose; the four gold exemplars trip zero of these.
  - "review" -> a likely tell that needs a human/agent eye (some legitimate uses
               exist), e.g. a physical-object metaphor or an inverse descriptor.

Design rule: the four approved gold exemplars (RC-VOICE-RETUNE-STANDARD.md) must
score zero HARD findings. `python -m app.services.prose_tell_guard --selftest`
asserts that.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

# Sentence-start anchor: start of string, after . ! ? + space, or after a newline.
_SS = r"(?:^|[.!?]\s+|\n\s*)"


@dataclass(frozen=True)
class TellPattern:
    code: str
    name: str
    severity: str              # "hard" | "review"
    regex: "re.Pattern[str]"
    lanes: Optional[frozenset]  # None = all lanes; else subset of {"listener","societal"}


def _p(code, name, severity, pattern, lanes=None, flags=re.IGNORECASE):
    return TellPattern(
        code=code,
        name=name,
        severity=severity,
        regex=re.compile(pattern, flags),
        lanes=frozenset(lanes) if lanes else None,
    )


# --- Tell inventory A-P (see the standard doc for the prose definition) ---------
TELLS: list[TellPattern] = [
    _p("A", "subject-lock (The words / The lyrics as subject)", "hard",
       _SS + r"(?:The|These)\s+(?:words|lyrics)\b"),
    _p("B", '"What ... is" pseudo-cleft closer', "hard",
       _SS + r"What\b[^.!?\n]{2,60}?\bis\b"),
    _p("C", "stock paragraph pivot (Take these words in / Run this and / Hearing this)", "hard",
       r"\b(?:Take these (?:words|lyrics) in|Run (?:this|it)(?:\s+\w+){0,2}?\s+and|"
       r"Hearing this|Sit with (?:it|this) and)\b"),
    _p("D", "societal stock opener (Run at scale / At scale / A population absorbing)", "hard",
       _SS + r"(?:Run at scale|At scale,|A population absorbing|When a population runs|"
       r"Run through millions)\b", lanes={"societal"}),
    _p("E", "install / house-word verb (installs / start treating / come to)", "review",
       r"\b(?:installs?|installed|start(?:s|ed)?\s+treating|"
       r"comes?\s+to\s+(?:treat|see|recognize|weigh))\b"),
    _p("F", "give / hand / land / read-as verb", "hard",
       r"\b(?:hands?\s+you|handing\b|lands?\s+(?:as|hardest)|reads?\s+as|"
       r"give\s+you\s+permission|permission\s+to\s+feel)\b"),
    _p("G", "who-it-reaches catalog (for anyone who / reaches anyone)", "review",
       r"\b(?:reaches|meets|stings|hits)\s+(?:anyone|those|listeners|people)\b|"
       r"\bfor\s+(?:anyone|listeners|people)\s+who\b"),
    _p("H", '"stays [adjective]" flatline closer', "review",
       r"\bstays?\s+(?:vague|general|golden|gentler|quiet|a\s+garnish|a\s+symbol)\b"),
    _p("I", "size / volume metaphor for emotion", "review",
       r"\b(?:at\s+full\s+(?:volume|size)|larger\s+than\s+yourself|its\s+own\s+size|"
       r"full-size)\b"),
    _p("K", "inverse descriptor (X not Y / instead of / rather than / less like ... more like)", "review",
       r",\s+not\s+\w+|\b(?:instead of|rather than)\b|"
       r"\bless like\b[^.!?\n]*\bmore like\b|\bnot just\b[^.!?\n]*\bbut\b"),
    _p("L", "softened verb (come through / pass away)", "review",
       r"\bcomes?\s+through\b|\bpass(?:es|ed)?\s+(?:away|on)\b"),
    _p("M", "emotion as physical object (hold / carry / set down / weight / under a loss)", "review",
       r"\b(?:holds?|holding|carry|carries|carrying|set\s+down|the\s+weight\s+of|"
       r"under\s+(?:the\s+)?(?:weight|a\s+loss))\b"),
    _p("N", "trailing flourish appositive (, something worth ...)", "review",
       r",\s+(?:something|a\s+kind\s+of|the\s+sort\s+of)\s+\w+[^.!?\n]*\b(?:worth|that\s+matters)\b"),
    _p("P", "slang / labeling verdict (marks you as / makes you soft)", "review",
       r"\bmarks?\s+you\s+as\b|\bbrands?\s+you\b|\bmakes?\s+you\s+soft\b"),
]

# --- Lane-bleed checks ----------------------------------------------------------
LANE_BLEED: list[TellPattern] = [
    _p("BLEED-collective", "collective noun in listener prose (listener -> societal bleed)", "hard",
       r"\b(?:a\s+nation|the\s+nation|a\s+culture|the\s+culture|a\s+society|a\s+population|"
       r"the\s+population|communities|civic|the\s+collective|a\s+whole\s+people)\b",
       lanes={"listener"}),
    _p("BLEED-narrator", "narrator / performer as subject (summary bleed)", "hard",
       _SS + r"(?:The\s+narrator|The\s+singer|The\s+rapper|The\s+artist)\b"),
]

# --- Rule O: no manufactured downside -------------------------------------------
# Only checked when the effect is NOT genuinely negative (allow_deficit=False).
# Deficit / teardown language on a positive or decent song is the reviewer's-caveat
# tell, and the scanner CAN catch it because the caller knows the tier / charge.
# Genuinely degraded / corrupted songs pass allow_deficit=True so real corrosion
# language ("your empathy erodes", "you lose the reflex") is allowed.
O_TEARDOWN: list[TellPattern] = [
    _p("O", "rule-O teardown (deficit language on a non-negative song)", "hard",
       r"\bno sense of\b|"
       r"\bnever\s+(?:points|goes anywhere|leads anywhere|amounts to|adds up|resolves|arrives)\b|"
       r"\bmistaken for\b|"
       r"\bleaves you where you started\b|"
       r"\bfails?\s+to\b|"
       r"\bstops?\s+short\b|"
       r"\blittle more than\b|"
       r"\bwithout ever\b|"
       r"\bnothing\s+more\b|"
       r"\baimless\b"),
]

ALL_PATTERNS: list[TellPattern] = TELLS + LANE_BLEED


@dataclass(frozen=True)
class Finding:
    code: str
    name: str
    severity: str
    snippet: str


def scan(text: str, lane: str = "listener", allow_deficit: bool = False) -> list[Finding]:
    """Return every tell the passage trips, given its lane ('listener' | 'societal').

    A pattern with `lanes=None` applies to every lane; otherwise only when `lane`
    is in its set. Order follows ALL_PATTERNS (A-P, then bleed checks).

    `allow_deficit` gates the rule-O teardown check: pass True ONLY for a
    genuinely negative-effect song (degraded / corrupted), where deficit language
    is the real reading. On a positive or decent song leave it False, so teardown
    language ("no sense of", "mistaken for", "aimless") is flagged as a
    manufactured downside.
    """
    if not text:
        return []
    patterns = list(ALL_PATTERNS)
    if not allow_deficit:
        patterns = patterns + O_TEARDOWN
    findings: list[Finding] = []
    for pat in patterns:
        if pat.lanes is not None and lane not in pat.lanes:
            continue
        m = pat.regex.search(text)
        if m:
            snip = m.group(0).strip()
            findings.append(Finding(pat.code, pat.name, pat.severity, snip))
    return findings


def hard_findings(text: str, lane: str = "listener", allow_deficit: bool = False) -> list[Finding]:
    return [f for f in scan(text, lane, allow_deficit) if f.severity == "hard"]


def summarize(findings: Iterable[Finding]) -> str:
    fs = list(findings)
    if not fs:
        return "clean"
    return ", ".join(f"{f.code}:{f.severity}" for f in fs)


# --- Gold exemplars (must trip zero HARD findings) ------------------------------
# Verbatim from RC-VOICE-RETUNE-STANDARD.md, the four approved pieces.
_EX_LISTENER_POS = (
    "Candle in the Wind 1997 teaches you that grieving is normal and healthy. You "
    "come away valuing people for their kindness, and judging a whole life by what "
    "someone did for others. It also makes mourning feel dignified.\n\n"
    "Your own grief gets easier to bear and process. Loss frightens you less, and "
    "you grow gentler with other people who are hurting. You meet your own future "
    "losses more steadily."
)
_EX_LISTENER_NEG = (
    "Kill You trains you to hear graphic violence against women as normal. You "
    "start taking rape and murder as punchlines and entertainment. Your own "
    "discomfort feels like weakness, and laughing along becomes the natural "
    "response.\n\nWith repeated exposure, your sensitivity to hearing women "
    "brutalized dulls. Cruelty toward them turns funny, and you lose the reflex to "
    "object. If you have survived that kind of violence, you hear your own trauma "
    "turned into a joke and learn to suppress your reaction."
)
_EX_SOCIETAL_POS = (
    "A society that listens to Candle in the Wind 1997 gets better at grieving out "
    "loud. Private loss becomes a public event, something a whole population stops "
    "for and moves through together. People measure the dead by their kindness, and "
    "public mourning becomes an ordinary civic act.\n\nPeople who sing along with "
    "this mourn without shame, so communities stay intact through loss that would "
    "otherwise splinter them into private silence. The public standard for "
    "admiration shifts toward compassion, and people demand more kindness from the "
    "figures they elevate. Over time, they grow gentler with anyone who is suffering."
)
_EX_SOCIETAL_NEG = (
    "A society that sings along with Kill You learns to hear graphic violence "
    "against women as entertainment. Rape and murder become punchlines a crowd "
    "shouts back in unison, and an appetite for shock grows, rewarding whoever goes "
    "furthest. Outrage only spreads the song wider, so objecting to it backfires.\n\n"
    "With enough plays, the population's threshold for hearing women brutalized "
    "climbs. Casual cruelty toward women becomes common public speech, and real "
    "violence stops sounding serious because the words for it are already a chorus "
    "people sing. Women live in a public where descriptions of their own "
    "destruction circulate as a shared joke."
)

# (lane, allow_deficit, text). Positive songs -> allow_deficit False (rule-O
# active); negative songs -> True (real corrosion language allowed).
GOLD = [
    ("listener", False, _EX_LISTENER_POS),
    ("listener", True, _EX_LISTENER_NEG),
    ("societal", False, _EX_SOCIETAL_POS),
    ("societal", True, _EX_SOCIETAL_NEG),
]


def _selftest() -> int:
    failures = 0
    # Note: the societal exemplars legitimately use collective nouns; the
    # BLEED-collective check only fires on the LISTENER lane, so they are clean.
    for lane, allow_deficit, text in GOLD:
        hard = hard_findings(text, lane, allow_deficit)
        if hard:
            failures += 1
            print(f"FAIL gold {lane}: {summarize(hard)}")
        else:
            print(f"ok   gold {lane}: {summarize(scan(text, lane, allow_deficit))}")
    print("SELFTEST", "PASS" if failures == 0 else f"FAIL ({failures})")
    return failures


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(1 if _selftest() else 0)
    lane = "societal" if "--societal" in sys.argv else "listener"
    # --negative marks a genuinely degraded/corrupted song, so deficit language
    # is allowed (rule-O check off).
    allow_deficit = "--negative" in sys.argv
    data = sys.stdin.read()
    for f in scan(data, lane, allow_deficit):
        print(f"{f.severity:6} {f.code:16} {f.name}\n       -> {f.snippet!r}")
