"""The per-listen effects vocabulary (effects_pl): a CLOSED, song-agnostic set of
short effect labels tagged onto a song, the "psyche_effects tag axis" the Song
model reserves alongside the Psyche Facts family.

This is the RC-owned canonical copy so RC is the single source and chadlewine /
the DBM proliferator pull from the badge instead of each keeping their own list
(which drifts). Mirrors the ether-taxonomy pattern: a closed vocabulary, a
per-song assignment of 0-N members, terminal-supplied + validated, badge-exposed.

Stored on songs.effects_pl as a JSON-encoded list of SLUGS (not labels). The slug
is the stable key: a label can be reworded (e.g. "keeps you in longing" ->
"perpetuates longing") without orphaning any song's assignment. Consumers read the
badge's effects_pl (slugs) and effects_pl_labels (resolved display strings).

Insertion order is the canonical sort order (mirrors chadlewine psyche_effects
sort_order), so labels_for() returns members in that order regardless of the order
they were supplied.
"""
from __future__ import annotations

# slug -> display label. Order = canonical sort order.
EFFECTS_PL_VOCAB: dict[str, str] = {
    "dissolves-separateness": "dissolves separateness",
    "wakes-you-up": "wakes you up",
    "returns-your-power": "returns your power",
    "strengthens-fortitude": "strengthens fortitude",
    "stands-you-in-your-truth": "stands you in your truth",
    "releases-what-you-carry": "releases what you carry",
    "opens-the-heart": "opens the heart",
    "meets-your-grief": "meets your grief",
    "restores-hope": "restores hope",
    "grounds-you-in-gratitude": "grounds you in gratitude",
    "fires-you-up": "fires you up",
    "settles-the-spin": "settles the spin",
    "invites-growth": "invites growth",
    "lifts-your-mood": "lifts your mood",
    "drops-you-into-the-body": "drops you into the body",
    "commiserates": "commiserates",
    "feeds-the-ego": "feeds the ego",
    "glorifies-the-escape": "glorifies the escape",
    "sinks-into-despair": "sinks into despair",
    "perpetuates-longing": "perpetuates longing",
}

# The canonical index of each slug, for stable ordering of a supplied set.
_ORDER = {slug: i for i, slug in enumerate(EFFECTS_PL_VOCAB)}

VALID_EFFECTS_PL = frozenset(EFFECTS_PL_VOCAB.keys())


def label_for(slug: str) -> str:
    """Display label for a slug, or the slug itself if unknown."""
    return EFFECTS_PL_VOCAB.get(slug, slug)


def clean_effects_pl(slugs) -> list[str]:
    """Allowlist + de-dup + canonical-order a supplied list of effect slugs.
    Silently drops unknown slugs. Returns [] for anything unusable."""
    if not isinstance(slugs, (list, tuple)):
        return []
    seen = []
    for s in slugs:
        if isinstance(s, str):
            s = s.strip()
            if s in VALID_EFFECTS_PL and s not in seen:
                seen.append(s)
    return sorted(seen, key=lambda s: _ORDER[s])


def labels_for(slugs) -> list[str]:
    """Resolve a list of slugs to display labels, in canonical order."""
    return [EFFECTS_PL_VOCAB[s] for s in clean_effects_pl(slugs)]
