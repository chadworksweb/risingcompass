"""The Audience Resonance slicer -- turns one listener testimony into a
proportional verdict (True / Camouflage / Adjacent) under the resonance rubric.

SHIPS DARK. The live classification is gated behind the fail-closed flag
`resonance_slicer.enabled` (feature_flags.is_resonance_slicer_enabled). With the
flag OFF -- the default and current state -- slice_story() returns a NEUTRAL
slice (status='pending') and makes NO model call. This matches the build
constraint that this feature introduces no Anthropic call from terminal or
server until the approach + credits are settled, and the SCOPE note that "until
then /submit stores a neutral verdict."

When the feature is turned on, the ONE wiring point is _invoke_slicer_model()
below. It is deliberately left as an explicit seam rather than a live Anthropic
call, so flipping the flag without wiring the model cannot silently start billing
Anthropic. Wiring it is a ~15-line function that mirrors services/identity_guard.py
(AsyncAnthropic + claude_meter.tracked_create_async). See the docstring there.

slice_story() is fail-soft end to end: any error returns a neutral slice, never
an exception into the caller, so the submission path can always complete.
"""

import logging

from sqlalchemy.orm import Session

from app.services import resonance_rubric
from app.services.feature_flags import is_resonance_slicer_enabled

logger = logging.getLogger(__name__)


async def _invoke_slicer_model(system: str, messages: list) -> str:
    """The single model-invocation seam. NOT WIRED -- introduces zero Anthropic.

    To go live (after the flag is enabled and credits exist), implement this by
    mirroring services/identity_guard.py:

        from anthropic import AsyncAnthropic
        from app.config import settings
        from app.services.claude_meter import tracked_create_async
        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        resp = await tracked_create_async(
            client, call_site="resonance_slicer", context={...},
            model=settings.agent_model, max_tokens=600, temperature=0,
            system=system, messages=messages,
        )
        return resp.content[0].text

    Return the raw model text; resonance_rubric.parse_slice() handles the rest.
    """
    raise NotImplementedError(
        "resonance slicer model call is not wired -- see _invoke_slicer_model"
    )


async def slice_story(*, story: str, title: str, artist: str, db: Session) -> dict:
    """Classify one testimony into the stored slice shape:
        {prop_true, prop_camouflage, prop_adjacent, slice_attribution,
         status, rubric_version}

    Returns a NEUTRAL slice (status='pending') when the slicer is disabled or any
    step fails. Never raises.
    """
    try:
        if not is_resonance_slicer_enabled(db):
            return resonance_rubric.neutral_slice("slicer_disabled")
    except Exception:
        logger.exception("resonance slicer flag read failed; returning neutral")
        return resonance_rubric.neutral_slice("flag_read_failed")

    if not (story or "").strip():
        return resonance_rubric.neutral_slice("empty_story")

    try:
        system, messages = resonance_rubric.build_slice_messages(story, title, artist)
        raw = await _invoke_slicer_model(system, messages)
    except NotImplementedError:
        # Flag on but model not wired: behave as dark (neutral), don't crash.
        logger.warning("resonance slicer enabled but model not wired; returning neutral")
        return resonance_rubric.neutral_slice("model_not_wired")
    except Exception as exc:
        logger.exception("resonance slicer model call failed")
        return resonance_rubric.neutral_slice(f"api_error:{type(exc).__name__}")

    return resonance_rubric.parse_slice(raw)
