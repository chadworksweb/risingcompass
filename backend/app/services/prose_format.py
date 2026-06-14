"""Shared post-processing for the per-song effects-prose generators.

The listener/societal prompts ask for a word cap, but the model routinely
overruns it (Cry came back at 195 words against a 165 cap, and 297 against 200).
A prompt cap is advisory; this is the hard guarantee, applied in code with no
extra Anthropic call. It trims to the last COMPLETE sentence under the cap so the
prose never ends mid-thought, and preserves paragraph breaks.
"""

from __future__ import annotations

import re

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def count_words(text: str) -> int:
    return len((text or "").split())


def trim_to_word_cap(text: str, max_words: int) -> str:
    """Trim `text` to roughly `max_words`, cutting only at sentence boundaries.

    PARAGRAPH-PRESERVING: the budget is split evenly across the paragraphs the
    model produced and each paragraph is trimmed to its own share, keeping whole
    sentences. This never deletes a whole paragraph -- a naive global cap would
    chop the tail and silently turn a two-paragraph reading into one when the
    model front-loads paragraph 1. Every paragraph keeps at least its first
    sentence (even if that alone exceeds its share), so the result is never empty
    and the paragraph count is preserved. Total stays at or under the cap except
    in the rare case where the per-paragraph first sentences are themselves long.
    """
    if not text or count_words(text) <= max_words:
        return text

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    if not paragraphs:
        return text

    per_paragraph = max(1, max_words // len(paragraphs))
    kept_paras: list[str] = []

    for para in paragraphs:
        kept_sents: list[str] = []
        used = 0
        for sent in _SENT_SPLIT.split(para):
            if not sent:
                continue
            words = count_words(sent)
            # Always keep a paragraph's first sentence; cap-check the rest.
            if kept_sents and used + words > per_paragraph:
                break
            kept_sents.append(sent)
            used += words
        if kept_sents:
            kept_paras.append(" ".join(kept_sents))

    result = "\n\n".join(kept_paras).strip()
    return result or text
