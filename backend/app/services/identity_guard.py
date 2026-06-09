"""Layer 1 of the Lyrical Charger paste-path guards.

Asks Opus whether the submitted lyrics plausibly belong to the claimed
(title, artist). The point is to reject obvious mismatches at the door —
e.g. someone pastes The Cranberries' "Zombie" under Yungblud's "Zombie" —
before the calibration step runs and a wrong-lyrics row pollutes the
corpus.

Design notes:
- Opus only. Per project standard, no Haiku/Sonnet for this kind of check;
  the cost of a false-positive ("yes" on wrong lyrics) is corrupted
  consensus, not a small UX nit.
- Three-way verdict: `yes` / `no` / `unsure`. The prompt biases toward
  `unsure` for songs the model doesn't recognize — false-rejecting niche
  artists is worse than letting one through (Layer 2 catches divergence on
  any subsequent submission of the right lyrics anyway).
- No lyric text is persisted anywhere by this service. The lyrics travel in
  the Anthropic request body; the call log only stores title + artist +
  verdict in context_json (see claude_meter._build_row trimming).
"""

import json
import logging

from anthropic import AsyncAnthropic

from app.config import settings
from app.services.claude_meter import tracked_create_async

logger = logging.getLogger(__name__)

# Hard cap so a runaway paste doesn't blow up the API call. Real lyrics
# fit comfortably under this; longer inputs are usually prose or pasted
# transcripts that should fail validation upstream anyway.
_MAX_LYRICS_CHARS = 6000

_VALID_VERDICTS = {"yes", "no", "unsure"}

_SYSTEM_PROMPT = """You verify whether submitted song lyrics plausibly belong to a specified song, AND whether the song looks like a commercially released recording.

You will receive a song title, artist, and a block of lyrics. Return two judgments.

JUDGMENT 1 -- identity ("verdict"): do these lyrics belong to this exact song by this exact artist?

- "yes": the lyrics are clearly from this exact song by this exact artist.
- "no": the lyrics are clearly from a different song or a different artist
  (different recognizable lyrics, wrong language, wrong era, demonstrably
  belongs to a different known track, etc).
- "unsure": you don't recognize the song, the artist is obscure to you, or
  you can't confidently rule the lyrics in or out. When in doubt, choose
  "unsure" — it is much better to be unsure than to guess "yes".

Two important rules for JUDGMENT 1:

1. Do not reject lyrics just because the artist is obscure to you. Niche or
   independent artists are common submissions; "I don't know this artist"
   is "unsure", not "no".
2. Reject "no" only when you are confident the lyrics belong to a different
   identifiable song. The classic "no" case is two unrelated songs that
   happen to share a title: the user types one artist's name but pastes
   the other artist's lyrics. Same title, different recognizable lyrics,
   different known track.

JUDGMENT 2 -- commercial release ("commercial"): is this plausibly a commercially released musical recording (single/album track/EP), as opposed to non-song text or a private/amateur upload?

- "yes": clearly a real released song -- you recognize it, OR the title/artist
  and the lyric structure (verses, chorus/hook, song shape) read as a genuine
  released track even if you don't know it.
- "no": clearly NOT a commercially released song. Use "no" ONLY when confident:
  the text is gibberish or word-salad; it's an essay, message, email, note, or
  other prose pasted as if it were lyrics; the "artist" is plainly not a
  recording act (a personal name with no musical context, "me", "test",
  "anonymous", random handles); or it reads as raw personal writing / a draft
  rather than a finished released song.
- "unsure": you don't recognize it but it has a plausible song shape, or the
  artist is just obscure to you. STRONGLY prefer "unsure" over "no" for niche,
  independent, or self-released artists -- they are legitimate submissions.
  Being unsure is much better than wrongly calling a real indie song "no".

Return JSON only, no prose around it:

{"verdict": "yes" | "no" | "unsure", "reason": "<one short sentence>", "commercial": "yes" | "no" | "unsure", "commercial_reason": "<one short sentence>"}
"""


async def check_lyrics_identity(
    *, title: str, artist: str, lyrics: str
) -> dict:
    """Ask Opus whether `lyrics` belong to `title` by `artist`, and whether the
    song looks commercially released.

    Returns {"verdict": "yes"|"no"|"unsure", "reason": "...",
             "commercial": "yes"|"no"|"unsure", "commercial_reason": "..."}.
    On any error (parse failure, API exception), defaults BOTH judgments to
    "unsure" with the error captured in the reasons. Failing soft is the right
    move — Layer 2 (divergence guard) catches identity misses, the commercial
    verdict only ever WARNS (never hard-blocks), and we never want a transient
    API blip to gate legitimate submissions.
    """
    snippet = (lyrics or "")[:_MAX_LYRICS_CHARS]
    if not snippet.strip():
        return {"verdict": "unsure", "reason": "empty lyrics",
                "commercial": "unsure", "commercial_reason": "empty lyrics"}

    user_prompt = (
        f"Title: {title}\n"
        f"Artist: {artist}\n\n"
        f"Lyrics:\n---\n{snippet}\n---\n\n"
        f"Return JSON: verdict + one-sentence reason."
    )

    try:
        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        response = await tracked_create_async(
            client,
            call_site="identity_guard",
            context={"title": title, "artist": artist},
            model=settings.agent_model,
            max_tokens=200,
            temperature=0,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = response.content[0].text.strip()
    except Exception as exc:
        logger.exception("identity_guard API call failed")
        return {"verdict": "unsure", "reason": f"api_error: {type(exc).__name__}",
                "commercial": "unsure", "commercial_reason": f"api_error: {type(exc).__name__}"}

    # Parse — strip code fences and any leading prose, then take the first {...}
    json_str = raw
    if json_str.startswith("```"):
        json_str = json_str.split("\n", 1)[-1]
        if json_str.rstrip().endswith("```"):
            json_str = json_str.rstrip()[:-3]
    brace = json_str.find("{")
    if brace > 0:
        json_str = json_str[brace:]
    try:
        parsed = json.loads(json_str)
    except json.JSONDecodeError:
        logger.warning("identity_guard could not parse response: %s", raw[:300])
        return {"verdict": "unsure", "reason": "parse_failed",
                "commercial": "unsure", "commercial_reason": "parse_failed"}

    verdict = str(parsed.get("verdict", "unsure")).lower().strip()
    if verdict not in _VALID_VERDICTS:
        verdict = "unsure"
    reason = str(parsed.get("reason", "")).strip()[:300]

    commercial = str(parsed.get("commercial", "unsure")).lower().strip()
    if commercial not in _VALID_VERDICTS:
        commercial = "unsure"
    commercial_reason = str(parsed.get("commercial_reason", "")).strip()[:300]

    logger.info("identity_guard: %s/%s -> verdict=%s commercial=%s",
                title, artist, verdict, commercial)
    return {"verdict": verdict, "reason": reason,
            "commercial": commercial, "commercial_reason": commercial_reason}
