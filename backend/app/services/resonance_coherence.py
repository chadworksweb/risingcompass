"""Coherence check -- a FABRICATION SIGNAL, never a truth verdict.

For a submitted resonance, assess whether the testimony plausibly belongs to the
song it is pinned to. A low result routes the row to human review; it NEVER
auto-rejects and NEVER decides whether the story is true. The classic tell -- a
story that does not track with its song (a survival vigil pinned to a flex
track) -- is what this is built to surface.

Design honesty about what each layer can do:

  HEURISTIC (always on, no model -- this module):
    - FARMING: the same story pasted across different songs. This is the strong,
      cheap, high-precision signal and the main reason to run a check at all.
    - GENERICNESS: a testimony too thin/empty to be about anything.
    - song-word OVERLAP: how much the story shares vocabulary with the song's own
      words (title/artist/topics/summary/prose). Reported as a SOFT score only --
      it never flags on its own, because a real quiet testimony ("it just sat
      with me") legitimately shares little vocabulary with the song's analysis.
      Punishing that would gut the most valuable submissions.

  MODEL (the semantic "does this testimony belong to a song with THIS charge and
  these themes?" judgment) is the real mismatch detector, but it needs the model
  and so ships dark, exactly like the slicer. It is intentionally NOT wired here
  (no Anthropic from terminal/server); when it lands it runs in the slice worker
  (server-side, never shown to the submitter) and feeds the same review routing.

assess_coherence() is fail-soft: any error returns coherent=True (fail OPEN -- a
broken check must never silently bury real testimony). Result shape:
  {coherent: bool, score: 0..1, reasons: [str], layer: 'heuristic'}
"""

import logging
import re

logger = logging.getLogger(__name__)

# Below this many distinct content words the testimony is too thin to be about a
# specific song -- generic filler / accidental submit.
MIN_CONTENT_WORDS = 5

# Token-set Jaccard at/above this against a story on a DIFFERENT song = farming.
FARMING_JACCARD = 0.85

# Cap the farming scan so a submit never does unbounded work.
FARMING_SCAN_LIMIT = 500

_WORD_RE = re.compile(r"[a-z0-9']+")

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "of", "to", "in", "on", "for",
    "with", "as", "at", "by", "from", "is", "was", "were", "be", "been", "being",
    "are", "am", "it", "its", "this", "that", "these", "those", "i", "me", "my",
    "we", "us", "our", "you", "your", "he", "she", "they", "them", "his", "her",
    "their", "so", "not", "no", "do", "did", "does", "had", "has", "have", "just",
    "than", "then", "there", "here", "when", "what", "who", "how", "all", "any",
    "can", "will", "would", "could", "about", "into", "out", "up", "down", "over",
    "song", "this", "really", "very", "more", "most", "some", "like", "one",
}


def _content_words(text: str) -> set:
    return {w for w in _WORD_RE.findall((text or "").lower())
            if len(w) > 2 and w not in _STOPWORDS}


def _song_words(song) -> set:
    """Bag of the song's OWN meaningful words, from whatever fields it carries."""
    parts = [getattr(song, "title", None), getattr(song, "artist", None),
             getattr(song, "charge_summary", None),
             getattr(song, "listener_effects_prose", None),
             getattr(song, "societal_effects_prose", None),
             getattr(song, "deadpan_line", None)]
    topics = getattr(song, "topics", None)
    if topics:
        parts.append(str(topics).replace("-", " "))
    return _content_words(" ".join(p for p in parts if p))


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / len(a | b)


def assess_coherence(db, story: str, song) -> dict:
    """Heuristic coherence assessment for one (story, song) pair. Fail-open."""
    try:
        words = _content_words(story)
        reasons = []
        coherent = True

        # 1. Genericness floor.
        if len(words) < MIN_CONTENT_WORDS:
            coherent = False
            reasons.append(f"thin: only {len(words)} content words")

        # 2. Farming: same story on a different song.
        from app.models import Resonance
        rows = (
            db.query(Resonance.song_id, Resonance.story_text)
            .order_by(Resonance.id.desc())
            .limit(FARMING_SCAN_LIMIT)
            .all()
        )
        song_id = getattr(song, "id", None)
        worst_dupe = 0.0
        for other_song_id, other_story in rows:
            if other_song_id == song_id:
                continue
            j = _jaccard(words, _content_words(other_story))
            if j > worst_dupe:
                worst_dupe = j
            if j >= FARMING_JACCARD:
                coherent = False
                reasons.append(f"near-duplicate of a story on song {other_song_id} "
                               f"(jaccard {j:.2f})")
                break

        # 3. Song-word overlap -- SOFT score only, never a flag on its own.
        overlap = _jaccard(words, _song_words(song))

        # Blended 0..1 score (for ranking the review queue): overlap weighted low,
        # length normalized, duplication penalized. Not the flag decision.
        length_norm = min(1.0, len(words) / 30.0)
        score = max(0.0, round(0.5 * length_norm + 0.5 * overlap - worst_dupe * 0.5, 3))

        return {"coherent": coherent, "score": score, "reasons": reasons,
                "layer": "heuristic", "overlap": round(overlap, 3)}
    except Exception:
        logger.exception("coherence check failed; failing open (coherent)")
        return {"coherent": True, "score": None, "reasons": ["check_error"],
                "layer": "heuristic"}
