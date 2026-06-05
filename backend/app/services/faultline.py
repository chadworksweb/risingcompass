"""Faultline -- capture core.

The inbound seam of RC's internal error ledger. A single logging.Handler on the
root logger turns every `logger.exception(...)` / ERROR record anywhere in the
app into a deduplicated, durable fault row -- with ZERO business-code coupling
(faults arrive via Python's standard logging contract, nothing imports this from
app logic).

Hard guarantees (capture must NEVER break the app):
  * Non-blocking: emit() does cheap work then hands off to a bounded queue
    drained by a daemon writer thread with its own short-lived DB session. The
    request path never waits on a DB write.
  * Drop-on-overflow: a full queue drops the occurrence rather than blocking.
  * Never raises: every path is wrapped; on failure it falls back to the
    file log (independent) and swallows.
  * Recursion-safe: a contextvar guard + logger-name exclusions stop an error
    raised while recording an error from re-entering the handler.

See RISING-COMPASS-FAULTLINE-SCOPE.md.
"""

import contextvars
import hashlib
import json
import logging
import os
import queue
import threading
import traceback as _tb
from datetime import datetime, timedelta

# Module logger -- EXCLUDED from capture (see _EXCLUDED_PREFIXES) so a write
# failure logged here can never feed back into the handler.
logger = logging.getLogger(__name__)

# Loggers we never capture from, to prevent feedback loops:
#  - our own module (write failures log here)
#  - SQLAlchemy (the writer's own SQL/errors)
#  - the logging bootstrap
_EXCLUDED_PREFIXES = ("app.services.faultline", "sqlalchemy", "app.logging_config")

# Guards against re-entrancy if fingerprinting/formatting itself logs an error.
_in_emit: contextvars.ContextVar[bool] = contextvars.ContextVar("faultline_in_emit", default=False)

# Innermost frames used to fingerprint a fault. Enough to distinguish call
# sites; few enough that the same bug from different callers still collapses.
_FRAME_DEPTH = 6

_QUEUE_MAXSIZE = 1000
_FLAG_TTL_SECONDS = 30


def _environment() -> str:
    return (os.environ.get("ENVIRONMENT") or "local").strip().lower()[:10] or "local"


def _frame_signature(tb) -> list[str]:
    """Innermost N frames as 'basename:func:lineno' -- no variable data, so the
    same code fault hashes identically across requests."""
    frames = _tb.extract_tb(tb)
    out = []
    for fr in frames[-_FRAME_DEPTH:]:
        out.append(f"{os.path.basename(fr.filename)}:{fr.name}:{fr.lineno}")
    return out


def compute_fingerprint(exc_type_name: str, tb, fallback: str) -> str:
    """Stable hash for dedup. Uses exc type + innermost frames when a traceback
    is present; otherwise a fallback (logger+location+message) so non-exception
    ERROR records still collapse sanely."""
    if tb is not None:
        basis = exc_type_name + "|" + "|".join(_frame_signature(tb))
    else:
        basis = "noexc|" + fallback
    return hashlib.sha256(basis.encode("utf-8", "replace")).hexdigest()[:64]


def _build_payload(record: logging.LogRecord) -> dict | None:
    """Translate a LogRecord into a fault payload. Returns None if the record
    should be skipped. Never raises."""
    try:
        exc_info = record.exc_info
        exc_type_name = ""
        tb = None
        tb_text = None
        if exc_info and exc_info[0] is not None:
            exc_type_name = getattr(exc_info[0], "__name__", str(exc_info[0]))
            tb = exc_info[2]
            tb_text = "".join(_tb.format_exception(*exc_info))[:20000]

        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)

        location = f"{record.name}:{os.path.basename(record.pathname)}:{record.lineno}"
        fallback = f"{location}|{record.msg}"
        fingerprint = compute_fingerprint(exc_type_name, tb, fallback)

        first_line = (message or "").strip().splitlines()[0] if message else ""
        if exc_type_name:
            title = f"{exc_type_name}: {first_line}"[:300] or exc_type_name
        else:
            title = f"{record.name}: {first_line}"[:300] or record.name

        context = {
            "logger": record.name,
            "level": record.levelname,
            "func": record.funcName,
            "module": record.module,
            "pathname": record.pathname,
            "lineno": record.lineno,
            "message": message[:2000] if message else None,
        }

        return {
            "fingerprint": fingerprint,
            "exc_type": exc_type_name[:120] or None,
            "title": title,
            "component": record.name[:160],
            "traceback": tb_text,
            "context": context,
            "environment": _environment(),
        }
    except Exception:
        return None


# --- async write pipeline ---------------------------------------------------

_q: "queue.Queue[dict]" = queue.Queue(maxsize=_QUEUE_MAXSIZE)
_writer_started = False
_writer_lock = threading.Lock()

# Cached enable-flag so the hot path never reads the DB. Refreshed by the
# writer thread on a TTL.
_enabled_cache = {"value": True, "checked_at": datetime.min}


def _refresh_enabled() -> bool:
    now = datetime.utcnow()
    if (now - _enabled_cache["checked_at"]) < timedelta(seconds=_FLAG_TTL_SECONDS):
        return _enabled_cache["value"]
    try:
        from app.database import SessionLocal
        from app.services.feature_flags import is_faultline_enabled
        db = SessionLocal()
        try:
            val = is_faultline_enabled(db)
        finally:
            db.close()
        _enabled_cache["value"] = val
    except Exception:
        # If we can't read the flag (e.g. tables not migrated yet), default ON
        # but don't hammer -- the TTL throttles retries.
        _enabled_cache["value"] = True
    _enabled_cache["checked_at"] = now
    return _enabled_cache["value"]


def _persist(payload: dict) -> None:
    """Upsert the signature, append the occurrence. Own short-lived session.
    Runs only on the writer thread."""
    from app.database import SessionLocal
    from app.models import ErrorSignature, ErrorOccurrence

    db = SessionLocal()
    regressed = False
    try:
        now = datetime.utcnow()
        ctx_json = json.dumps(payload["context"], default=str)
        sig = (
            db.query(ErrorSignature)
            .filter(ErrorSignature.fingerprint == payload["fingerprint"])
            .first()
        )
        if sig is None:
            sig = ErrorSignature(
                fingerprint=payload["fingerprint"],
                exc_type=payload["exc_type"],
                title=payload["title"],
                component=payload["component"],
                severity="medium",
                status="new",
                environment=payload["environment"],
                first_seen_at=now,
                last_seen_at=now,
                occurrence_count=1,
                last_traceback=payload["traceback"],
                last_context=ctx_json,
            )
            db.add(sig)
            db.flush()  # need sig.id for the occurrence
        else:
            sig.occurrence_count = (sig.occurrence_count or 0) + 1
            sig.last_seen_at = now
            sig.last_traceback = payload["traceback"]
            sig.last_context = ctx_json
            # A new hit on a resolved fault is a regression -- reopen it.
            if sig.status == "resolved":
                sig.status = "regressed"
                regressed = True

        db.add(ErrorOccurrence(
            signature_id=sig.id,
            occurred_at=now,
            traceback=payload["traceback"],
            context=ctx_json,
            environment=payload["environment"],
        ))
        db.commit()

        # Alert seam (after commit so it reflects persisted state). Fail-safe --
        # the alert must never break capture; send_alert is fire-and-forget.
        if regressed:
            try:
                from app.services.alerts import emit_faultline_regression
                emit_faultline_regression(
                    sig_id=sig.id, title=sig.title, component=sig.component,
                    environment=sig.environment, occurrence_count=sig.occurrence_count,
                )
            except Exception:
                logger.warning("faultline_regression alert failed (non-fatal)", exc_info=True)
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        # Excluded logger -> cannot feed back into the handler.
        logger.warning("Faultline persist failed (fault dropped to file log)", exc_info=True)
    finally:
        db.close()


def _writer_loop() -> None:
    while True:
        payload = _q.get()
        try:
            if _refresh_enabled():
                _persist(payload)
        except Exception:
            logger.warning("Faultline writer loop error", exc_info=True)
        finally:
            _q.task_done()


def _ensure_writer() -> None:
    global _writer_started
    if _writer_started:
        return
    with _writer_lock:
        if _writer_started:
            return
        t = threading.Thread(target=_writer_loop, name="faultline-writer", daemon=True)
        t.start()
        _writer_started = True


# --- the handler ------------------------------------------------------------

class ErrorLedgerHandler(logging.Handler):
    """Root-logger handler that enqueues qualifying records for durable capture.
    Cheap + fail-safe on the calling thread; all DB work is offloaded."""

    def __init__(self, level=logging.ERROR):
        super().__init__(level=level)
        _ensure_writer()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            name = record.name or ""
            if any(name == p or name.startswith(p + ".") or name.startswith(p) for p in _EXCLUDED_PREFIXES):
                return
            if _in_emit.get():
                return
            token = _in_emit.set(True)
            try:
                payload = _build_payload(record)
            finally:
                _in_emit.reset(token)
            if payload is None:
                return
            try:
                _q.put_nowait(payload)
            except queue.Full:
                # Drop rather than block. The file handler still has it.
                pass
        except Exception:
            # Absolute backstop: capture must never raise into the caller.
            pass


_handler_singleton: ErrorLedgerHandler | None = None


def get_handler() -> ErrorLedgerHandler:
    """Singleton handler, leveled from FAULTLINE_CAPTURE_LEVEL (default ERROR)."""
    global _handler_singleton
    if _handler_singleton is None:
        level_name = (os.environ.get("FAULTLINE_CAPTURE_LEVEL") or "ERROR").upper()
        level = getattr(logging, level_name, logging.ERROR)
        _handler_singleton = ErrorLedgerHandler(level=level)
    return _handler_singleton


# --- retention --------------------------------------------------------------

def prune_occurrences(db, keep_per_sig: int | None = None, older_than_days: int = 30) -> int:
    """Trim occurrence history: keep the most recent `keep_per_sig` rows per
    signature, but never delete anything younger than `older_than_days` (so a
    burst stays fully visible for a while). Signatures themselves are never
    deleted -- only their occurrence tail. Returns rows pruned. PG-specific
    (window function + interval). Safe to run repeatedly (idempotent)."""
    from sqlalchemy import text
    if keep_per_sig is None:
        try:
            from app.config import settings
            keep_per_sig = settings.faultline_occurrence_retention
        except Exception:
            keep_per_sig = 50
    keep = max(1, int(keep_per_sig))
    days = max(0, int(older_than_days))
    res = db.execute(text("""
        DELETE FROM error_occurrences
        WHERE id IN (
            SELECT id FROM (
                SELECT id, occurred_at,
                       ROW_NUMBER() OVER (
                           PARTITION BY signature_id ORDER BY occurred_at DESC
                       ) AS rn
                FROM error_occurrences
            ) ranked
            WHERE ranked.rn > :keep
              AND ranked.occurred_at < (now() - make_interval(days => :days))
        )
    """), {"keep": keep, "days": days})
    db.commit()
    pruned = res.rowcount or 0
    if pruned:
        logger.info("Faultline retention pruned %d occurrence row(s)", pruned)
    return pruned
