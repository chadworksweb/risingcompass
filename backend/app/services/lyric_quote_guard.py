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


def scrub_calibration_quotes(calibration: dict, lyrics: str | None, min_run: int = MIN_RUN) -> list[str]:
    """In-place scrub of a calibration dict's public-facing text fields against
    the lyrics. This is the universal lock applied at the single storage
    chokepoint (`_store_calibration`), so it catches verbatim lyric quotes no
    matter which path produced them -- terminal Claude-Code-supplied OR server AI.

    Prose fields get offending sentences stripped (set to None if that guts them
    below the usable threshold, so the page falls back). Note fields are cleared
    if they carry a verbatim run (the contaminated / dogma_referenced flags stay,
    so the indicator still shows). Returns the names of fields that were altered,
    for logging. No-op without lyrics.
    """
    if not lyrics or not calibration:
        return []
    altered: list[str] = []
    for field in PROSE_FIELDS:
        val = calibration.get(field)
        if not val:
            continue
        cleaned, stripped = strip_verbatim_quotes(val, lyrics, min_run)
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


def strip_verbatim_quotes(prose: str, lyrics: str, min_run: int = MIN_RUN) -> tuple[str, bool]:
    """Drop sentences that reproduce a verbatim lyric run of >= min_run words.

    Returns (cleaned_prose, stripped_any). Paragraph structure (blank-line breaks)
    is preserved. The caller re-validates length/paragraphs and fails soft if the
    strip gutted the prose.
    """
    if not prose or not lyrics:
        return prose, False
    grams = _lyric_ngrams(lyrics, min_run)
    if not grams:
        return prose, False

    stripped_any = False
    out_paras: list[str] = []
    for para in prose.split("\n\n"):
        kept: list[str] = []
        for sent in _split_sentences(para):
            words = _normalize_words(sent)
            hit = any(
                " ".join(words[i:i + min_run]) in grams
                for i in range(len(words) - min_run + 1)
            )
            if hit:
                stripped_any = True
                continue
            kept.append(sent)
        if kept:
            out_paras.append(" ".join(kept).strip())
    cleaned = "\n\n".join(p for p in out_paras if p).strip()
    return cleaned, stripped_any
