"""The resonance rubric -- Audience Resonance's OWN classification standard.

This is NOT the song moral rubric (LEC / the compass). The song rubric scores
lyrics; the resonance rubric classifies TESTIMONY about songs into a proportional
verdict across three buckets that sum to 100:

  True       -- the song itself did real work on the listener. Not only the
                bright forms (lifted, elevated, changed) but the quiet ones too:
                it held them, steadied them, sat with them, kept them alive. The
                mark is that the work is real and the SONG is its source, whatever
                the intensity (the gold; the thing this feature exists to catch,
                even when the compass scored the song low).
  Camouflage -- it rang, but the note was degraded. Counterfeit lift: resentment
                or despair dressed as catharsis. Felt like ascension, was not.
  Adjacent   -- the song did not do the work; the listener's own life did. The
                song was the bookmark on a meaning the memory already carried.

The slicer answers two questions in sequence:
  1. Did the song *cause* the resonance?  No  -> Adjacent.
  2. If yes, was the note clean or degraded?  Clean -> True.  Degraded -> Camouflage.

Bucket names are intentionally asymmetric: one virtue word (True) and two
mechanism words (Camouflage, Adjacent). We never tell a person their experience
was a lie -- only that the song was not the true source.

SURPRISABILITY IS STRUCTURAL. The slicer is given the story and the song's
identity (title / artist) but is NEVER told the compass charge. It cannot anchor
to "what the house already decided," so a low-charge song earning a True verdict
emerges only when we compare the slice against the charge AFTERWARD. That rare
ruling-against-the-house entry is the most valuable data the feature produces.

This module is pure content + parsing: the rubric text, the prompt builder, and
the proportion math. It performs NO model call (see services/resonance_slicer.py
for the gated invocation seam). ASCII only.
"""

import json
import logging

logger = logging.getLogger(__name__)

# Bump when the rubric text or the output contract changes. Stored alongside a
# slice so a re-slice under a newer rubric is distinguishable from the original.
RESONANCE_RUBRIC_VERSION = "1.1.0"

# Bucket keys are the storage contract (resonances.prop_true / _camouflage /
# _adjacent). Order is the decision order, not a ranking.
BUCKETS = ("true", "camouflage", "adjacent")

# Cap the testimony we hand the model. Stories are <=8000 chars at submit; this
# is a defensive ceiling on the prompt.
_MAX_STORY_CHARS = 8000

_SYSTEM_PROMPT = f"""You classify one listener's TESTIMONY about a single song.

You are not scoring the song. You are reading what the person wrote and naming
which KIND of resonance their words describe. Your output is a proportional
verdict across three buckets that sum to exactly 100.

THE THREE BUCKETS
- True: the song ITSELF did real work on this listener, and the note was clean.
  This is NOT only euphoric lift. The quiet forms count fully: a song that held
  them, steadied them, sat with them through it, or kept them alive did real
  work. "It did not lift me so much as sit with me, but that kept me alive" is
  True -- the song was the source of the holding. Intensity does not matter; what
  matters is that the work was real and the SONG did it.
- Camouflage: the song rang, but the note was degraded. Counterfeit lift --
  resentment, despair, or self-pity dressed up as catharsis. It felt like
  ascension and was not.
- Adjacent: the song did not do the work; the listener's own life did. The song
  was a bookmark on a meaning a memory already carried. The lift is real but its
  source is the person, not the song.

DECIDE IN THIS ORDER
1. Did the song CAUSE the resonance, or was it the bookmark on the listener's own
   experience? Weight toward Adjacent for the part that is the listener's life.
2. For the part the song did cause: is the note clean (True) or degraded
   (Camouflage)?
A single testimony usually blends buckets. Read the proportions from the text.
The True/Adjacent line is about SOURCE, not intensity: if the song's presence did
the holding (sat with them, kept them company through it), that is True; it is
only Adjacent to the extent the listener's own life did the work and the song was
merely the bookmark. Do not downgrade a quiet, low-key testimony to Adjacent just
because nothing dramatic happened -- ask who did the work.

HARD RULES
- Read the TEXT, not the person. Phrase your work as "these words read as ..."
  never "you are ..." Nothing you write may diagnose, sentence, or label the
  human being. The verdict is scoped to the story, present tense, never the person.
- You are NOT told the song's compass charge and must NOT guess it. A song you
  might assume is degraded can still have done genuine, clean work on this
  listener -- if the words say so, the verdict is True. Do not sort by reputation.
- Show your work. Quote the EXACT words from the testimony that drove each part of
  the verdict, and say what each quote reads as.
- Never imply the listener's experience was a lie. Camouflage and Adjacent name
  the MECHANISM (where the lift came from), not the validity of the feeling.

OUTPUT
Return ONLY a JSON object, no prose around it:
{{
  "true": <int 0-100>,
  "camouflage": <int 0-100>,
  "adjacent": <int 0-100>,
  "attribution": [
    {{"quote": "<exact words from the testimony>",
      "reads_as": "true" | "camouflage" | "adjacent",
      "note": "<one short clause: why these words read this way>"}}
  ]
}}
The three integers MUST sum to 100. attribution holds 1-6 entries, each quoting
verbatim from the testimony.
RUBRIC_VERSION: {RESONANCE_RUBRIC_VERSION}
"""


def build_slice_messages(story: str, title: str, artist: str):
    """Build (system, messages) for the slice call.

    Deliberately passes ONLY the song's identity -- never its charge / tier --
    so the slicer cannot anchor to the compass's prior reading (surprisability).
    """
    snippet = (story or "")[:_MAX_STORY_CHARS].strip()
    user = (
        f"Song: {title or 'Unknown'}\n"
        f"Artist: {artist or 'Unknown'}\n\n"
        f"Listener testimony:\n---\n{snippet}\n---\n\n"
        f"Return the JSON verdict."
    )
    return _SYSTEM_PROMPT, [{"role": "user", "content": user}]


def neutral_slice(reason: str) -> dict:
    """A not-yet-sliced placeholder: zero proportions (does NOT sum to 100, by
    design -- the frontend reads status='pending' to show 'verdict pending'
    rather than a false 0/0/0 reading). Used when the slicer is disabled / the
    call failed."""
    return {
        "prop_true": 0,
        "prop_camouflage": 0,
        "prop_adjacent": 0,
        "slice_attribution": [],
        "status": "pending",
        "reason": reason,
        "rubric_version": RESONANCE_RUBRIC_VERSION,
    }


def _largest_remainder(values: dict) -> dict:
    """Round three floats to integers that sum to exactly 100 (largest-remainder
    method). Negatives clamped to 0 first."""
    clamped = {k: max(0.0, float(values.get(k, 0))) for k in BUCKETS}
    total = sum(clamped.values())
    if total <= 0:
        # No signal -> even split keeps the sum-100 contract.
        return {"true": 34, "camouflage": 33, "adjacent": 33}
    scaled = {k: clamped[k] / total * 100 for k in BUCKETS}
    floors = {k: int(scaled[k]) for k in BUCKETS}
    remainder = 100 - sum(floors.values())
    # Hand the leftover units to the largest fractional parts.
    order = sorted(BUCKETS, key=lambda k: scaled[k] - floors[k], reverse=True)
    for i in range(remainder):
        floors[order[i % len(order)]] += 1
    return floors


def parse_slice(raw: str) -> dict:
    """Parse the model's JSON into the stored slice shape. Fail-soft: any parse
    problem returns a neutral slice (status='pending') so a transient bad
    response never persists a fake reading."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    brace = text.find("{")
    if brace > 0:
        text = text[brace:]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("resonance slicer could not parse response: %s", (raw or "")[:300])
        return neutral_slice("parse_failed")

    props = _largest_remainder({k: parsed.get(k, 0) for k in BUCKETS})

    attribution = []
    for entry in (parsed.get("attribution") or [])[:6]:
        if not isinstance(entry, dict):
            continue
        reads_as = str(entry.get("reads_as", "")).lower().strip()
        if reads_as not in BUCKETS:
            reads_as = ""
        attribution.append({
            "quote": str(entry.get("quote", "")).strip()[:600],
            "reads_as": reads_as,
            "note": str(entry.get("note", "")).strip()[:300],
        })

    return {
        "prop_true": props["true"],
        "prop_camouflage": props["camouflage"],
        "prop_adjacent": props["adjacent"],
        "slice_attribution": attribution,
        "status": "done",
        "rubric_version": RESONANCE_RUBRIC_VERSION,
    }
