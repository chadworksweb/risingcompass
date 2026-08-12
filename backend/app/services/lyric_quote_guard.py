"""Verbatim-lyric guard for generated prose.

Rising Compass's original commentary (listener_effects_prose, societal_effects_prose) must
describe and paraphrase the lyrics it analyzes -- never reproduce them. The prompts
instruct against quoting, but LLMs occasionally quote anyway, so this is the
deterministic LOCK in the calibration path: after generation, any run of >= MIN_RUN
consecutive words appearing verbatim in BOTH the prose and the lyrics is treated as
a quote. Sentences carrying such a run are stripped; if nothing usable survives, the
caller fails soft (stores NULL; the page hides/falls back).

A 6-word window catches real lyric reproduction while not flagging incidental short
phrases ("i love you", "in the dark") that are not protected expression. Tunable via
MIN_RUN. Matching is whitespace/punctuation/case-insensitive.

## The title carve-out (2026-08-12)

Both prose voices REQUIRE naming the exact song title once in paragraph 1. For most
pop songs the title IS a lyric line (the hook and the title are the same words), so
naming the title was indistinguishable from quoting the song: "So Easy (To Fall In
Love)" normalizes to a 6-word run sitting in the chorus, and BOTH lanes silently lost
their thesis sentence on regen.

`title=` excuses ONE title mention per prose block, and only when the run that
matched is contained WITHIN that mention. A second mention, or a title embedded in a
longer quoted run ("so easy to fall in love with me tonight"), still strips: the
extra words push the matched window past the title span, which is exactly the case
the guard exists for. A title shorter than min_run cannot trigger a hit on its own,
so passing it changes nothing there.

Notes (`contamination_note` / `dogma_note`) get NO carve-out. They are required to
paraphrase, never to name the title, so the stricter rule stands.

## Reporting

The strip used to be silent: callers got shorter text and no signal. It is now
reportable via `strip_verbatim_quotes_detailed`, which returns the removed sentences
so a caller can log or surface them. `strip_verbatim_quotes` keeps its
(cleaned, stripped_any) shape for existing callers.
"""

from __future__ import annotations

import re

# Consecutive shared words that count as a verbatim quote. Lower = stricter
# (more false positives); higher = looser (misses short hooks). 6 is the balance.
MIN_RUN = 6


def _normalize_words(text: str) -> list[str]:
    """Lowercase, drop punctuation, split on whitespace."""
    return re.sub(r"[^\w\s]", " ", (text or "").lower()).split()


def _lyric_ngrams(lyrics: str, n: int) -> set[str]:
    words = _normalize_words(lyrics)
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


def has_verbatim_overlap(prose: str, lyrics: str, min_run: int = MIN_RUN) -> bool:
    """True if any run of >= min_run consecutive words appears in both texts."""
    if not prose or not lyrics:
        return False
    grams = _lyric_ngrams(lyrics, min_run)
    if not grams:
        return False
    pwords = _normalize_words(prose)
    return any(
        " ".join(pwords[i:i + min_run]) in grams
        for i in range(len(pwords) - min_run + 1)
    )


PROSE_FIELDS = ("listener_effects_prose", "societal_effects_prose")
NOTE_FIELDS = ("contamination_note", "dogma_note")


def scrub_calibration_quotes(
    calibration: dict,
    lyrics: str | None,
    min_run: int = MIN_RUN,
    title: str | None = None,
) -> list[str]:
    """In-place scrub of a calibration dict's public-facing text fields against
    the lyrics. This is the universal lock applied at the single storage
    chokepoint (`_store_calibration`), so it catches verbatim lyric quotes no
    matter which path produced them -- terminal Claude-Code-supplied OR server AI.

    Prose fields get offending sentences stripped (set to None if that guts them
    below the usable threshold, so the page falls back), with one title mention
    excused when `title` is passed. Note fields are cleared if they carry a
    verbatim run (the contaminated / dogma_referenced flags stay, so the
    indicator still shows) and get NO title carve-out. Returns the names of
    fields that were altered, for logging. No-op without lyrics.
    """
    if not lyrics or not calibration:
        return []
    altered: list[str] = []
    for field in PROSE_FIELDS:
        val = calibration.get(field)
        if not val:
            continue
        cleaned, stripped = strip_verbatim_quotes(val, lyrics, min_run, title=title)
        if stripped:
            altered.append(field)
            calibration[field] = cleaned if (cleaned and len(cleaned) >= 100) else None
    for field in NOTE_FIELDS:
        val = calibration.get(field)
        if val and has_verbatim_overlap(val, lyrics, min_run):
            altered.append(field)
            calibration[field] = None
    return altered


def _split_sentences(text: str) -> list[str]:
    return re.split(r"(?<=[.!?])\s+", text)


def _title_spans(words: list[str], title_words: list[str]) -> list[tuple[int, int]]:
    """Half-open [start, end) index spans where the title appears as a whole unit."""
    n = len(title_words)
    if not n or n > len(words):
        return []
    return [
        (i, i + n)
        for i in range(len(words) - n + 1)
        if words[i:i + n] == title_words
    ]


def strip_verbatim_quotes_detailed(
    prose: str,
    lyrics: str,
    min_run: int = MIN_RUN,
    title: str | None = None,
) -> tuple[str, list[str]]:
    """Drop sentences that reproduce a verbatim lyric run of >= min_run words.

    Returns (cleaned_prose, removed_sentences). Paragraph structure (blank-line
    breaks) is preserved. The caller re-validates length/paragraphs and fails
    soft if the strip gutted the prose.

    When `title` is given, the FIRST sentence whose every matched run sits inside
    a single title mention is kept, and the allowance is then spent. Anything
    that reaches past the title span is a quote and still goes.
    """
    if not prose or not lyrics:
        return prose, []
    grams = _lyric_ngrams(lyrics, min_run)
    if not grams:
        return prose, []

    title_words = _normalize_words(title) if title else []
    allowance = bool(title_words)

    removed: list[str] = []
    out_paras: list[str] = []
    for para in prose.split("\n\n"):
        kept: list[str] = []
        for sent in _split_sentences(para):
            words = _normalize_words(sent)
            hits = [
                i for i in range(len(words) - min_run + 1)
                if " ".join(words[i:i + min_run]) in grams
            ]
            if not hits:
                kept.append(sent)
                continue

            # Excused only if ONE title mention contains every matched run.
            excused = False
            if allowance:
                excused = any(
                    all(h >= start and h + min_run <= end for h in hits)
                    for start, end in _title_spans(words, title_words)
                )
            if excused:
                allowance = False
                kept.append(sent)
                continue

            removed.append(sent.strip())
        if kept:
            out_paras.append(" ".join(kept).strip())
    cleaned = "\n\n".join(p for p in out_paras if p).strip()
    return cleaned, removed


def strip_verbatim_quotes(
    prose: str,
    lyrics: str,
    min_run: int = MIN_RUN,
    title: str | None = None,
) -> tuple[str, bool]:
    """Back-compatible wrapper: (cleaned_prose, stripped_any).

    Prefer `strip_verbatim_quotes_detailed` in new code so the caller can report
    WHAT was removed instead of silently shipping shorter prose.
    """
    cleaned, removed = strip_verbatim_quotes_detailed(prose, lyrics, min_run, title=title)
    return cleaned, bool(removed)
