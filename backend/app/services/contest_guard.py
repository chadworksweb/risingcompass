"""The contest guard: what separates a reader redirecting attention from a
reader ordering a tier.

THE WHOLE FEATURE TURNS ON THIS FILE. A contested re-read is one extra Opus call
triggered by an anonymous stranger's free text, which is two different problems
at once: prompt injection, and tier-shopping. If "read it greener" works, the
Lyrical Charger becomes a vending machine and the corpus rots.

The terminal exchange this feature reproduces worked for a specific reason. The
correcting reply POINTED AT EVIDENCE IN THE LYRIC ("that is a wake, not a
funeral") and never named a tier. The model re-derived from the text. Nothing
about that was a negotiation over a number. This guard enforces structurally
what that reply did by instinct:

  1. The AXIS is a closed set. The reader picks the KIND of miss, not the answer.
  2. The NOTE must point at the lyric, and the pointer is checked against the
     re-pasted text. An argument with no anchor in the song is not a contest.
  3. VERDICT LANGUAGE IS A HARD FAIL. Any tier name, colour, "should be", or
     directional demand rejects the whole contest with an explanation of what to
     send instead. It is not stripped and it is not silently ignored, for the
     same reason deadpan_guard fails a bad line rather than repairing it: a
     guard that quietly fixes its input teaches the caller nothing and hides
     the drift it exists to catch.

The reader directs attention. The rubric decides. That invariant is why a
contest can be offered to the public at all.
"""

import re
import unicodedata

# The closed set. Each axis names a way a read goes wrong that a reader can
# genuinely SEE without knowing the rubric, and each one directs the re-read at
# a specific question rather than at a specific verdict. Deliberately short: a
# long menu invites the reader to shop for the one that sounds most likely to
# move the number, which is tier-shopping wearing a costume.
CONTEST_AXES = {
    "missed_frame": {
        "label": "The read took a scene for the position",
        "directs": "whether the passage names a state the song OCCUPIES or one "
                   "it DEPICTS.",
    },
    "character_as_speaker": {
        "label": "A character was read as the singer",
        "directs": "who is actually speaking the contested lines, and whether "
                   "the song endorses them or renders them.",
    },
    "missed_turn": {
        "label": "The song turns and the read stayed put",
        "directs": "whether a turn occurs in the lyric and what the song lands "
                   "on AFTER it.",
    },
    "took_image_literally": {
        "label": "An image was read literally",
        "directs": "whether the contested image is literal statement or figure.",
    },
    "wrong_referent": {
        "label": "The read had the wrong subject",
        "directs": "what the contested lines are actually about.",
    },
}

NOTE_MIN = 10
NOTE_MAX = 400


def axis_label(axis: str | None) -> str:
    """The reader-facing name of one axis, for alerts and admin surfaces. Lives
    here rather than in a caller so the router and the sweep name an axis the
    same way -- they both email the same person about the same contest."""
    entry = CONTEST_AXES.get(axis or "")
    return entry["label"] if entry else (axis or "unknown")

# Tier vocabulary, in every form a reader would reach for. Sourced by hand
# rather than from COLOR_LABELS because the guard has to catch the COLLOQUIAL
# forms ("greener", "in the red") that the constant never contains.
_TIER_WORDS = {
    "ascended", "elevated", "decent", "degraded", "corrupted",
    "violet", "purple", "blue", "green", "greener", "orange", "red", "redder",
    "tier", "rating", "score", "grade", "charge value",
}

# Directional demands. These are the ones that carry a verdict without naming a
# tier, and they are the more common shape by far: nobody writes "this should be
# Elevated", they write "way too harsh".
_DIRECTIONAL = (
    r"too (harsh|negative|positive|low|high|dark|generous|kind|mean|critical)",
    r"(should|ought to|needs? to|has to) be\b",
    r"(rate|score|grade|read|mark) it (higher|lower|better|worse)",
    r"(more|less) (positive|negative|generous|harsh|favourabl|favorabl)",
    r"\b(raise|lower|bump|downgrade|upgrade)\b",
    r"not (that|so) (bad|dark|negative)",
    r"deserves? (better|more|higher)",
)

# Instruction-shaped text aimed at the model rather than at the song. A contest
# is testimony about a lyric; anything addressing the reader of the note is not.
_INJECTION = (
    # Qualifiers stack ("ignore all previous instructions"), so allow a short
    # run of words between the verb and its object rather than one optional
    # group -- the single-group version missed the most common phrasing there is.
    r"ignore (?:\w+\s+){0,3}(above|instructions?|rules?|rubric|prompt)",
    r"disregard (?:\w+\s+){0,3}(above|previous|prior|instructions?|rules?)",
    r"you (are|must|should|will) (now )?",
    r"(new|updated|revised) (instructions?|rules?|system prompt)",
    r"system prompt",
    r"act as\b",
    r"pretend (to be|that)",
)

# Markup-shaped injection, held apart from the patterns above because it is the
# one trigger the lyric exemption must NOT reach. `_normalise` strips the angle
# brackets that make "</system>" dangerous, leaving the bare word "system" --
# which a song can perfectly well contain. Checked with no exemption at all.
_INJECTION_TAGS = r"</?(system|instructions?|prompt)>"


# Straight and smart quote marks, built from code points so this file stays
# ASCII-only (project standard) while still matching the curly quotes a reader
# gets for free from a word processor or a lyrics site.
_QUOTE_CHARS = "\"'" + chr(0x201C) + chr(0x201D) + chr(0x2018) + chr(0x2019)
_QUOTE_RE = "[%s]([^%s]{4,})[%s]" % (_QUOTE_CHARS, _QUOTE_CHARS, _QUOTE_CHARS)


def _normalise(s: str) -> str:
    """Lowercase, strip accents and punctuation, collapse whitespace. Used for
    the pointer check so a reader quoting from memory (or from a lyric sheet
    with different punctuation) still matches.

    NON-LATIN SCRIPTS SURVIVE THIS. The first version stripped accents with
    encode("ascii", "ignore"), which drops the combining marks NFKD produces --
    and every Korean, Japanese, Chinese, Cyrillic, Greek and Arabic character
    with them. Normalised lyrics for those songs came out EMPTY, so
    `_points_at_lyric` could never match and every contest on a non-Latin song
    was rejected with "quote the line you mean" even when the reader had quoted
    it exactly. Accents come off by dropping combining marks directly instead;
    the NFC afterwards recomposes decomposed Hangul, so Korean tokenises into
    words rather than into jamo.
    """
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = unicodedata.normalize("NFC", s).lower()
    # Letters and digits in ANY script survive; everything else becomes a space.
    s = "".join(c if (c.isalnum() or c.isspace()) else " " for c in s)
    return re.sub(r"\s+", " ", s).strip()


# Scripts that do not separate words with spaces (Japanese, Chinese, Thai) leave
# a whole line as one token, so "four consecutive words" can never fire for them.
# There a run of CHARACTERS stands in for a run of words. Both numbers apply ONLY
# to unspaced lyrics; spaced scripts keep the stricter four-word rule.
_UNSPACED_AVG_TOKEN = 12
_UNSPACED_MIN_RUN = 6


def _looks_unspaced(norm_lyrics: str) -> bool:
    tokens = norm_lyrics.split()
    return bool(tokens) and (len(norm_lyrics) / len(tokens)) > _UNSPACED_AVG_TOKEN


def _is_from_the_song(fragment: str, norm_lyrics: str) -> bool:
    """True when a guard trigger is the SONG'S words rather than the reader's.

    The guard asks the reader to quote the line, then reads that same text for a
    verdict, and the two demands collide head-on: this rubric names its tiers
    after colours, and songs are full of colours. `the line "I have been feeling
    blue since the flood" is a figure` is a reader doing exactly what the form
    asked, and the first version bounced it for naming a tier. Same for a quoted
    "raise a glass" or "lower me down easy" (read as directional demands), and
    for any quoted line opening "you are" (read as prompt injection).

    The rule that resolves it: a trigger appearing VERBATIM IN THE SONG is
    quotation, not instruction. It cannot be turned into a tier request, because
    the re-pasted lyrics have to fingerprint-match the held reading -- a reader
    cannot conjure a song that happens to contain "should be elevated".
    """
    frag = _normalise(fragment)
    if not frag or not norm_lyrics:
        return False
    return f" {frag} " in f" {norm_lyrics} "


def _points_at_lyric(note: str, lyrics: str, min_run: int = 4) -> bool:
    """True when the note quotes the song.

    Two ways to satisfy it. An explicit quotation (single or double quotes) whose
    contents appear in the lyrics, or -- because most people do not bother with
    quote marks -- any run of `min_run` consecutive words from the note appearing
    verbatim in the normalised lyrics.

    Four words is the threshold because three catches ordinary English by
    accident ("and then the" appears in half the songs ever written) while four
    consecutive words matching a specific song is effectively always a quotation.
    """
    norm_lyrics = _normalise(lyrics)
    if not norm_lyrics:
        return False

    for quoted in re.findall(_QUOTE_RE, note or ""):
        q = _normalise(quoted)
        if q and q in norm_lyrics:
            return True

    # Unspaced scripts: characters stand in for words. Without this a Japanese
    # or Chinese lyric is a single token, no run of four words can ever exist,
    # and the only way through is quote marks the reader may not have used.
    if _looks_unspaced(norm_lyrics):
        packed_note = _normalise(note).replace(" ", "")
        packed_lyrics = norm_lyrics.replace(" ", "")
        return any(
            packed_note[i:i + _UNSPACED_MIN_RUN] in packed_lyrics
            for i in range(len(packed_note) - _UNSPACED_MIN_RUN + 1)
        )

    words = _normalise(note).split()
    for i in range(len(words) - min_run + 1):
        if " ".join(words[i:i + min_run]) in norm_lyrics:
            return True
    return False


def check_contest(axis: str, note: str, lyrics: str) -> str | None:
    """Validate one contest. Returns an error message for the reader, or None
    when the contest is well formed.

    Every message says what to send INSTEAD. A reader whose contest bounces with
    "invalid" learns to give up; one who is told "quote the line" learns to file
    the contest that works, which is the only kind worth spending a call on.
    """
    if axis not in CONTEST_AXES:
        return "Pick one of the listed ways the reading went wrong."

    note = (note or "").strip()
    if len(note) < NOTE_MIN:
        return ("Say what the reading missed, and quote the line it missed it on. "
                "A few words is enough.")
    if len(note) > NOTE_MAX:
        return f"Keep it under {NOTE_MAX} characters. Quote the line and say what it means."

    lowered = note.lower()
    norm_lyrics = _normalise(lyrics)

    # Every scan below exempts a trigger THE SONG ITSELF says (see
    # `_is_from_the_song`). The reader was told to quote the line; bouncing them
    # for reproducing the words they were asked to reproduce is the guard eating
    # its own instruction. `finditer` rather than `search`, because one pattern
    # can match in two places and only one of them may be quotation.
    def _fires(pattern: str) -> bool:
        return any(
            not _is_from_the_song(m.group(0), norm_lyrics)
            for m in re.finditer(pattern, lowered)
        )

    # Tier language. Word-boundary matched so "degrading" in a sentence about
    # what the song DEPICTS does not trip the guard that exists to catch
    # "should be less degraded".
    for word in _TIER_WORDS:
        if _fires(rf"\b{re.escape(word)}\b"):
            return ("Leave the tier out of it. Tell us what the lyric says and "
                    "quote the line; the rubric decides where that lands.")

    for pattern in _DIRECTIONAL:
        if _fires(pattern):
            return ("Don't tell us which way to move it. Tell us what the "
                    "reading got wrong about the words, and quote the line.")

    # No exemption for this one: the brackets that make it dangerous do not
    # survive normalisation, so a song containing the word "system" would
    # otherwise license a literal "</system>".
    if re.search(_INJECTION_TAGS, lowered):
        return "Write about the song, not about the reader."

    for pattern in _INJECTION:
        if _fires(pattern):
            return "Write about the song, not about the reader."

    if not _points_at_lyric(note, lyrics):
        return ("Quote the line you mean. The re-read has to look at something "
                "specific in the lyrics, not at a general impression.")

    return None
