"""LEIT daily clutter sweep -- finds clutter that already slipped into the
Library via the public Lyrical Charger and queues it for human audit.

What it looks for, on songs that were BORN from a public LC run (earliest
`song_ingestions` row is `method='lyrical_charger'` -- chart/terminal songs are
trusted and excluded):
  - gibberish / word-salad that passed the format guards,
  - "unknown random people" -- vanity uploads by names that aren't real
    recording artists,
  - content that belongs on the Creative Charger (personal writing) or Curio
    Charger (messages/articles) rather than the Lyrical Charger.

Hard constraint: LC NEVER stores lyrics. The sweep therefore classifies from
stored metadata only -- title, artist, charge_summary, listener/societal prose,
topics, confidence, rubric_color. Enough to catch "unknown person" and "summary
reads like nonsense"; it cannot re-read the words.

Flag-only: every finding is written to `clutter_audits` (source='daily_sweep')
for an admin to review. The sweep changes nothing on the live site.

Idempotent + bounded: candidates exclude any song that already has a
clutter_audits row (so a dismissed song isn't re-flagged forever), only songs
new since the last sweep are considered (watermark in `system_flags`), and each
run is capped. Opus-only per the project standard.
"""

import json
import logging

from anthropic import AsyncAnthropic
from sqlalchemy import text
from sqlalchemy.orm import aliased

from app.config import settings
from app.database import SessionLocal
from app.models import Song, SongIngestion, SystemFlag
from app.services.claude_meter import tracked_create_async
from app.services.clutter import record_clutter_finding, VALID_CATEGORIES
from app.services.agents.warehouse import start_run, finish_run

logger = logging.getLogger(__name__)

# This sweep IS the agent "Dusty" (Custodian 001) in the admin mini-warehouse.
AGENT_ID = "custodian-001"
LC_METHOD = "lyrical_charger"
LAST_RUN_FLAG = "leit_sweep.last_run_at"

# Bound the per-run cost. A daily sweep over new LC submissions is small; this is
# a backstop against a bootstrap run (no watermark) scanning the whole backlog.
MAX_CANDIDATES = 200
BATCH_SIZE = 20
VALID_ACTIONS = {"delete", "route_to_creative", "route_to_curio", "review"}

_SYSTEM_PROMPT = """You are a librarian auditing a music database for clutter.

Each song below was submitted by the public through a "Lyrical Charger" tool,
which is meant ONLY for commercially released music. Some submissions are
clutter that slipped through. Flag a song ONLY if you are reasonably confident
it is one of:

- "gibberish": the title/summary indicate nonsense, word-salad, or not real song
  content.
- "unknown_person": the "artist" is plainly not a real recording act -- a
  placeholder ("me", "test", "anonymous"), a random personal name with no sign of
  being a musician, or an obvious vanity upload.
- "wrong_charger": it reads as personal writing, a message, an email, an article,
  or other non-song text that belongs on a different tool, not a released song.
- "non_commercial": clearly not a commercially released song for some other
  reason (e.g. an unreleased private draft).

You are given only metadata -- title, artist, a one-line charge summary, topics,
and a calibration tier. You do NOT have the lyrics. Judge conservatively: when a
song could plausibly be a real (even obscure/independent) released track, DO NOT
flag it. Obscurity is not clutter. Only flag clear cases.

Return JSON only -- a list of findings for the FLAGGED songs only (omit clean
ones). Each finding:

{"index": <the song's index number>, "category": "gibberish" | "unknown_person" | "wrong_charger" | "non_commercial", "reason": "<one short sentence>", "suggested_action": "delete" | "route_to_creative" | "route_to_curio" | "review", "confidence": <0.0-1.0>}

If nothing is clutter, return [].
"""


def _get_flag(db, key: str) -> str | None:
    row = db.query(SystemFlag).filter(SystemFlag.key == key).first()
    return row.value if row else None


def _set_flag(db, key: str, value: str) -> None:
    row = db.query(SystemFlag).filter(SystemFlag.key == key).first()
    if row:
        row.value = value
    else:
        db.add(SystemFlag(key=key, value=value))
    db.commit()


def _select_candidates(db, last_run: str | None) -> list[tuple]:
    """LC-born songs newer than the last-sweep watermark, with no existing
    clutter row. Returns (Song, ingestion_created_at) tuples.

    Ordered OLDEST-first and capped at MAX_CANDIDATES: a backlog larger than the
    cap is worked forward across successive runs (the watermark advances to the
    newest SCANNED ingestion each run), so nothing between the cap boundary and
    the newest submission is ever silently skipped. `last_run` is an ISO
    timestamp watermark."""
    other = aliased(SongIngestion)
    earlier_other = (
        db.query(other.id)
        .filter(
            other.song_id == SongIngestion.song_id,
            other.method != LC_METHOD,
            other.created_at < SongIngestion.created_at,
        )
        .exists()
    )
    # NOT EXISTS written into the text clause -- `~text(...)` isn't a valid
    # SQLAlchemy negation (raises AssertionError), so negate in SQL directly.
    not_already_flagged = text(
        "NOT EXISTS (SELECT 1 FROM clutter_audits ca WHERE ca.song_id = songs.id)"
    )
    q = (
        db.query(Song, SongIngestion.created_at)
        .join(SongIngestion, SongIngestion.song_id == Song.id)
        .filter(
            SongIngestion.method == LC_METHOD,
            Song.rubric_color.isnot(None),
            SongIngestion.created_at.isnot(None),
            ~earlier_other,
            not_already_flagged,
        )
    )
    if last_run:
        q = q.filter(SongIngestion.created_at > last_run)
    rows = (
        q.order_by(SongIngestion.created_at.asc())
        .limit(MAX_CANDIDATES)
        .all()
    )
    return [(r[0], r[1]) for r in rows]


def _song_brief(idx: int, song: Song) -> str:
    topics = ""
    if song.topics:
        try:
            t = json.loads(song.topics)
            if isinstance(t, list) and t:
                topics = ", ".join(str(x) for x in t[:3])
        except Exception:
            pass
    summary = (song.charge_summary or "").strip()[:300]
    deadpan = (song.deadpan_line or "").strip()[:200]
    return (
        f"[{idx}] title: {song.title!r} | artist: {song.artist!r} | "
        f"tier: {song.rubric_color} | confidence: {song.confidence} | "
        f"topics: {topics or '(none)'}\n"
        f"     summary: {summary or '(none)'}\n"
        f"     names: {deadpan or '(none)'}"
    )


def _parse_findings(raw: str) -> list[dict]:
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    start = s.find("[")
    if start > 0:
        s = s[start:]
    try:
        parsed = json.loads(s)
    except json.JSONDecodeError:
        # ERROR (not warning) so a chronic unparseable-Opus-response pattern
        # surfaces as a Faultline fault -- otherwise the sweep silently flags
        # nothing and the only hint is a "flagged 0" digest.
        logger.error("leit_sweep could not parse findings: %s", raw[:300])
        return []
    return parsed if isinstance(parsed, list) else []


async def _classify_batch(client, batch: list[Song]) -> list[dict]:
    briefs = "\n".join(_song_brief(i, s) for i, s in enumerate(batch))
    user_prompt = f"Songs to audit:\n\n{briefs}\n\nReturn the JSON findings list."
    try:
        response = await tracked_create_async(
            client,
            call_site="leit_sweep",
            context={"batch_size": len(batch)},
            model=settings.agent_model,
            max_tokens=1500,
            temperature=0,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = response.content[0].text
    except Exception:
        logger.exception("leit_sweep batch classification failed")
        return []
    return _parse_findings(raw)


async def run_leit_sweep(trigger: str = "cron") -> dict:
    """Run one sweep pass. Returns a summary dict for the cron response + digest.

    Records the run in the agent mini-warehouse (Dusty = custodian-001): a row is
    opened at the start and closed with the outcome (ok/error + counts), so the
    admin Agents page shows the agent's health + history. Run tracking is
    fail-soft and a crash is recorded as a failed run AND re-raised (so Faultline
    + the cron failure alert still fire)."""
    run_id = start_run(AGENT_ID, trigger)
    scanned = 0
    flagged_count = 0
    try:
        summary = await _run_leit_sweep_inner()
        scanned = summary.get("scanned", 0)
        flagged_count = summary.get("flagged", 0)
        finish_run(run_id, status="ok", scanned=scanned, flagged=flagged_count)
        return summary
    except Exception as exc:
        finish_run(run_id, status="error", scanned=scanned, flagged=flagged_count,
                   error=f"{type(exc).__name__}: {exc}"[:1000])
        raise


async def _run_leit_sweep_inner() -> dict:
    """The sweep body (no run tracking). See run_leit_sweep."""
    db = SessionLocal()
    try:
        last_run = _get_flag(db, LAST_RUN_FLAG)
        rows = _select_candidates(db, last_run)
        scanned = len(rows)
        if not scanned:
            logger.info("leit_sweep: no new candidates")
            return {"scanned": 0, "flagged": 0, "findings": []}

        candidates = [r[0] for r in rows]
        # Watermark = newest ingestion among the SCANNED batch (oldest-first
        # ordering means this is the last element). Advancing only past what we
        # actually scanned is what lets an over-cap backlog drain forward.
        watermark = max((r[1] for r in rows if r[1] is not None), default=None)

        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        flagged: list[dict] = []
        for start in range(0, scanned, BATCH_SIZE):
            batch = candidates[start:start + BATCH_SIZE]
            findings = await _classify_batch(client, batch)
            for f in findings:
                try:
                    idx = int(f.get("index"))
                except (TypeError, ValueError):
                    continue
                if idx < 0 or idx >= len(batch):
                    continue
                song = batch[idx]
                category = str(f.get("category", "")).strip()
                if category not in VALID_CATEGORIES:
                    category = "non_commercial"
                action = str(f.get("suggested_action", "review")).strip()
                if action not in VALID_ACTIONS:
                    action = "review"
                try:
                    confidence = float(f.get("confidence"))
                except (TypeError, ValueError):
                    confidence = None
                reason = str(f.get("reason", "")).strip()

                # Own-session write (db=None): each finding lands in its own tiny
                # fail-soft transaction, so one collision can't poison the batch.
                new_id = record_clutter_finding(
                    song_id=song.id,
                    source="daily_sweep",
                    category=category,
                    reason=reason,
                    suggested_action=action,
                    confidence=confidence,
                    payload={"title": song.title[:120], "artist": song.artist[:120],
                             "tier": song.rubric_color},
                )
                if new_id is not None:
                    flagged.append({
                        "audit_id": new_id,
                        "song_id": song.id, "title": song.title, "artist": song.artist,
                        "category": category, "reason": reason,
                        "suggested_action": action, "confidence": confidence,
                    })

        # Advance the watermark to the newest ingestion in the scanned batch so
        # the next run continues forward (and an over-cap backlog drains across
        # runs rather than being skipped).
        if watermark is not None:
            _set_flag(db, LAST_RUN_FLAG, str(watermark))

        logger.info("leit_sweep: scanned=%d flagged=%d", scanned, len(flagged))
        return {"scanned": scanned, "flagged": len(flagged), "findings": flagged}
    finally:
        db.close()
