"""Output guard for a RELEASE reading (the rc-album lens).

The album lane shipped with no enforcement at all. Its two live readings were
checked by hand, in a throwaway script, that pointed the SONG guards at album
fields and got the answer right by operator discipline. That is the same
unguarded shape that let deadpan drift degrade 464 song rows between 2026-06-27
and 2026-07-25 before `deadpan_guard` was written, and the album lane is more
exposed, not less: a release row goes straight to a public page on write, with no
approval gate anywhere in front of it.

This module is that enforcement. It composes the three song guards rather than
restating them -- `summary_guard` (absence framing, genre words, tier colors,
named titles), `deadpan_guard` (placard form + length cap), `prose_tell_guard`
(the A-R tells) -- and adds only what is genuinely album-scale:

  RECORD          Chad's ruling: say "album", never "record". Nothing encoded it,
                  so release 1349 shipped prose that opens "The record opens...",
                  and the LENS ITSELF still says "what contaminates THE RECORD".
                  A vocabulary ruling that lives only in a session transcript is
                  not a rule, it is a memory.

  TITLES          Track titles stay out of every album-level field, checked with
                  `multiword_only` DISABLED. A song's own guard may let a
                  one-word title pass as genuine content; at album scale a
                  one-word track name ("Novelty", "Taboo") is exactly the leak.

  SHAPE           Each prose lane's paragraph / sentence / word contract, taken
                  from the lens's own field spec.

  LIST CADENCE    The arc's named failure mode: enumerating positions ("the
                  second position does X, the third does Y") reports sequence
                  while saying nothing about movement.

  REVIEW DRIFT    The lens has not heard the album and never will. Sound,
                  production, performance, arrangement and vocal are outside what
                  it can see, so naming them is invention.

Everything numeric (visceral, harm, transcendence, center, vernier, coherence)
is deliberately NOT re-checked here: `charge_composition.validate_components(
raw, lane="album")` already owns it and raises on any problem. One owner per
rule.

Hard-fail is the only correct posture on the terminal write path, exactly as in
`deadpan_guard`: never silent-strip a form violation, because a truncated
placard is worse than a rejected one, and the operator is right there to rewrite.
"""

import json
import re
import sys

from app.services.agents.summary_guard import summary_violations, text_names_titles
from app.services.prose_tell_guard import hard_findings

# Lens field spec: (exact paragraph count, max total words).
PROSE_SHAPE = {
    "arc_prose": (2, 130),
    "listener_effects_prose": (2, 130),
    "societal_effects_prose": (2, 150),
}
SENTENCES_PER_PARAGRAPH = (2, 3)

# Every reader-facing album field, for the vocabulary + title sweeps.
READER_FIELDS = (
    "charge_summary", "arc_prose", "listener_effects_prose",
    "societal_effects_prose", "contamination_note", "dogma_note",
)

# "album" / "release", never "record". Matches the noun and its plural; leaves
# "recorded" / "recording" alone, which are different words doing honest work.
_RECORD_RE = re.compile(r"(?i)\brecords?\b")

# Enumerated running-order positions. One is a legitimate anchor ("the closing
# position"); two or more is the list cadence the lens forbids by name.
_ORDINAL_POSITION_RE = re.compile(
    r"(?i)\b(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|"
    r"tenth|eleventh|twelfth|penultimate|last|final|opening|closing)\s+"
    r"(?:position|track|song|cut|number)\b"
)
LIST_CADENCE_LIMIT = 2

# What the lens cannot have perceived. Kept tight on purpose: only words whose
# musical sense dominates, so a metaphorical use is rare enough that rephrasing
# is cheaper than a carve-out. ("sound", "harmony" and "arrangement" are
# deliberately absent -- their everyday meanings are far more common.)
#
# The lens text names "sound ... arrangement" among the things it cannot see, and
# this list still does not carry them, on purpose. The lens rule is SEMANTIC (do
# not describe how the album sounds); this guard is a WORD match, and it can only
# be as wide as the words whose musical reading is the likely one. "Arrangement"
# came off the list 2026-08-17 after it blocked a legitimate non-musical use: RC's
# own live prose exemplars use it that way twice ("a punishing arrangement",
# "arrangements that run on human depletion"), which is the same everyday-meaning-
# dominates test that kept "sound" off from the start.
_REVIEW_TERMS = (
    "production", "produced", "instrumentation", "vocal",
    "vocals", "vocalist", "melody", "melodic", "guitar", "guitars", "drums",
    "bass", "synth", "synths", "tempo", "falsetto", "riff", "riffs",
    "mixing", "mastering", "sonics", "timbre",
)
_REVIEW_RE = re.compile(
    r"(?i)\b(?:" + "|".join(re.escape(t) for t in _REVIEW_TERMS) + r")\b"
)

PSYCHE_STRING_KEYS = ("purpose", "do_not_use_if", "directions", "onset",
                      "duration", "warning")
PSYCHE_ARRAY_KEYS = ("indicated_for",)
INDICATED_FOR_COUNT = 4   # the album spec says exactly four; the song spec does not
MAX_TOPICS = 3
EFFECTS_PL_RANGE = (1, 4)


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]


def _sentences(paragraph: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", paragraph.strip()) if s]


def _prose_shape_violations(field: str, text: str) -> list[str]:
    """Paragraph / sentence / word contract for one prose lane."""
    want_paras, max_words = PROSE_SHAPE[field]
    out: list[str] = []
    paras = _paragraphs(text)
    if len(paras) != want_paras:
        out.append(f"{len(paras)} paragraphs, spec says exactly {want_paras}")
    words = len(text.split())
    if words > max_words:
        out.append(f"{words} words, spec cap is {max_words}")
    lo, hi = SENTENCES_PER_PARAGRAPH
    for i, para in enumerate(paras, 1):
        n = len(_sentences(para))
        if not lo <= n <= hi:
            out.append(f"paragraph {i} has {n} sentences, spec says {lo} to {hi}")
    return out


def _psyche_violations(psyche) -> list[str]:
    out: list[str] = []
    if psyche is None:
        return ["missing (the prescription is part of the reading, not an extra)"]
    if isinstance(psyche, str):
        try:
            psyche = json.loads(psyche)
        except ValueError:
            return ["not valid JSON"]
    if not isinstance(psyche, dict):
        return ["not an object"]
    for key in PSYCHE_STRING_KEYS:
        value = psyche.get(key)
        if not isinstance(value, str) or not value.strip():
            out.append(f"{key} missing or empty")
    for key in PSYCHE_ARRAY_KEYS:
        value = psyche.get(key)
        if not isinstance(value, list) or not all(
                isinstance(v, str) and v.strip() for v in value):
            out.append(f"{key} missing or not a list of strings")
        elif len(value) != INDICATED_FOR_COUNT:
            out.append(
                f"{key} has {len(value)} entries, the album spec says exactly "
                f"{INDICATED_FOR_COUNT}")
    unknown = set(psyche) - set(PSYCHE_STRING_KEYS) - set(PSYCHE_ARRAY_KEYS)
    if unknown:
        out.append("unknown keys (they are dropped on write): "
                   + ", ".join(sorted(unknown)))
    return out


def _effects_pl_violations(effects) -> list[str]:
    from app.services.effects_pl_vocab import VALID_EFFECTS_PL
    if effects is None:
        return ["missing (part of the psyche facts family, not a separate feature)"]
    if isinstance(effects, str):
        try:
            effects = json.loads(effects)
        except ValueError:
            return ["not valid JSON"]
    if not isinstance(effects, list):
        return ["not a list"]
    out: list[str] = []
    lo, hi = EFFECTS_PL_RANGE
    if not lo <= len(effects) <= hi:
        out.append(f"{len(effects)} slugs, spec says {lo} to {hi}")
    unknown = [s for s in effects if s not in VALID_EFFECTS_PL]
    if unknown:
        out.append("not in the closed vocabulary: " + ", ".join(map(repr, unknown)))
    return out


def album_violations(reading: dict, *, release_title: str = "", artist: str = "",
                     track_titles=(), charge_value: int | None = None) -> dict:
    """Every album-output violation, as {field: [reasons]}. Empty dict == clean.

    `charge_value` is the COMPOSED charge (see charge_composition.compose), used
    only to decide whether deficit language is the honest reading or a
    manufactured downside -- pass it so a degraded album is not flagged for
    reading degraded. `track_titles` is the running order.
    """
    out: dict[str, list[str]] = {}

    def add(field, reasons):
        if reasons:
            out.setdefault(field, []).extend(reasons)

    titles = list(track_titles or ())
    allow_deficit = bool(charge_value is not None and charge_value < 0)

    # 1. Vocabulary + track-title leaks across every reader-facing field.
    for field in READER_FIELDS:
        text = reading.get(field)
        if not isinstance(text, str) or not text.strip():
            continue
        if _RECORD_RE.search(text):
            add(field, ['says "record" -- the album lane says "album" or '
                        '"release", never "record"'])
        named = text_names_titles(text, titles, multiword_only=False, min_chars=4)
        if named:
            add(field, ["names a track title: " + ", ".join(map(repr, named))])
        review = sorted({m.group(0).lower() for m in _REVIEW_RE.finditer(text)})
        if review:
            add(field, ["music-review drift (the lens never heard the album): "
                        + ", ".join(review)])

    # 2. The summary carries the song guard's full rule set. Titles are NOT
    #    passed here -- the sweep above already checked every reader-facing
    #    field against the running order, and passing them twice reports one
    #    leak as two violations.
    summary = reading.get("charge_summary")
    if not isinstance(summary, str) or not summary.strip():
        add("charge_summary", ["missing"])
    else:
        add("charge_summary", summary_violations(summary, titles=()))

    # 3. Prose shape + the A-R tells.
    for field, lane in (("arc_prose", None),
                        ("listener_effects_prose", "listener"),
                        ("societal_effects_prose", "societal")):
        text = reading.get(field)
        if not isinstance(text, str) or not text.strip():
            add(field, ["missing"])
            continue
        add(field, _prose_shape_violations(field, text))
        if lane:
            findings = hard_findings(text, lane, allow_deficit)
            add(field, [f"tell {f.code} ({f.name}): {f.snippet!r}" for f in findings])

    # 4. The arc's own failure mode.
    arc = reading.get("arc_prose")
    if isinstance(arc, str):
        hits = _ORDINAL_POSITION_RE.findall(arc)
        if len(hits) >= LIST_CADENCE_LIMIT:
            add("arc_prose", [
                f"list cadence: {len(hits)} enumerated positions -- the arc "
                "names turns, it does not walk the tracklist"])

    # 5. Albums carry NO placard. Chad's ruling 2026-08-20: "albums are too
    # complex for deadpan" -- a museum placard names one thing, and a release is
    # a dozen. The field is not part of the album lens at all, so supplying one
    # is an error rather than an optional extra, and the guard says so. The
    # reader surfaces already treat it as absent by default (release.js leaves
    # the element hidden, ssr_release renders it only `if deadpan`), so a NULL
    # needs nothing downstream.
    if (reading.get("deadpan_line") or "").strip():
        add("deadpan_line", [
            "supplied, and albums do not carry a placard -- the field belongs to "
            "the song lens only"])

    # 6. Ether entry, prescription, per-listen effects.
    topics = reading.get("topics")
    if topics is None or (isinstance(topics, list) and not topics):
        if not reading.get("topic_audit"):
            add("topics", ["empty and no topic_audit explaining why"])
    elif not isinstance(topics, list):
        add("topics", ["not a list"])
    elif len(topics) > MAX_TOPICS:
        add("topics", [f"{len(topics)} topics, spec cap is {MAX_TOPICS}"])

    add("psyche_facts", _psyche_violations(reading.get("psyche_facts")))
    add("effects_pl", _effects_pl_violations(reading.get("effects_pl")))

    # 7. Flags and their notes travel together, both directions.
    for flag, note in (("contaminated", "contamination_note"),
                       ("dogma_referenced", "dogma_note")):
        if reading.get(flag) and not (reading.get(note) or "").strip():
            add(note, [f"{flag} is true but the note is empty"])
        if not reading.get(flag) and (reading.get(note) or "").strip():
            add(note, [f"note written but {flag} is false"])

    confidence = reading.get("confidence")
    if not isinstance(confidence, (int, float)) or not 0.0 <= float(confidence) <= 1.0:
        add("confidence", [f"missing or out of range [0, 1]: {confidence!r}"])

    return out


def format_report(violations: dict) -> str:
    if not violations:
        return "CLEAN"
    lines = []
    for field in sorted(violations):
        for reason in violations[field]:
            lines.append(f"  {field}: {reason}")
    return f"{sum(len(v) for v in violations.values())} violation(s)\n" + "\n".join(lines)


def _main(argv) -> int:
    """CLI: album_guard.py <reading.json> --title T --artist A [--track T]..."""
    if not argv:
        print(_main.__doc__)
        return 2
    path = argv[0]
    title = artist = ""
    tracks: list[str] = []
    charge = None
    i = 1
    while i < len(argv):
        flag, value = argv[i], (argv[i + 1] if i + 1 < len(argv) else "")
        if flag == "--title":
            title = value
        elif flag == "--artist":
            artist = value
        elif flag == "--track":
            tracks.append(value)
        elif flag == "--charge":
            charge = int(value)
        i += 2
    with open(path, encoding="utf-8") as fh:
        reading = json.load(fh)
    violations = album_violations(
        reading, release_title=title, artist=artist, track_titles=tracks,
        charge_value=charge)
    print(format_report(violations))
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
