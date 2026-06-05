"""Durable application logging.

The app previously relied on uvicorn's default console handler only, so any
`logger.exception(...)` -- including swallowed "non-fatal" failures in the
calibrate/reconcile path -- vanished the moment the console scrolled. This
module attaches a rotating file handler to the root logger so every WARNING
and above survives to disk, independent of how the process is launched.

Call configure_logging() once, as early as possible (it is idempotent).

Env:
  RC_LOG_DIR   -- directory for log files (default: <backend>/logs)
  RC_LOG_LEVEL -- root level for the file handler (default: INFO)
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

_CONFIGURED = False
_HANDLER_TAG = "rc_durable_file"

# 5 MB per file, 5 rotations = ~25 MB ceiling.
_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 5

_FORMAT = "%(asctime)s %(levelname)-8s %(name)s %(message)s"


def _default_log_dir() -> Path:
    # app/logging_config.py -> app -> backend
    return Path(__file__).resolve().parent.parent / "logs"


def configure_logging() -> Path | None:
    """Attach a rotating file handler to the root logger. Idempotent.

    Returns the active log file path, or None if file logging could not be
    set up (in which case console logging is unaffected -- this never raises).
    """
    global _CONFIGURED
    if _CONFIGURED:
        return None

    level_name = (os.environ.get("RC_LOG_LEVEL") or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    log_dir = Path(os.environ.get("RC_LOG_DIR") or _default_log_dir())
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "backend.log"

        root = logging.getLogger()
        # The root level gates what reaches handlers; keep it at the lower of
        # its current level and ours so we never silence existing handlers.
        if root.level == logging.NOTSET or root.level > level:
            root.setLevel(level)

        # Don't double-attach on reload/re-import.
        for h in root.handlers:
            if getattr(h, "_rc_tag", None) == _HANDLER_TAG:
                _CONFIGURED = True
                return log_path

        handler = RotatingFileHandler(
            log_path, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.setLevel(level)
        handler.setFormatter(logging.Formatter(_FORMAT))
        handler._rc_tag = _HANDLER_TAG  # type: ignore[attr-defined]
        root.addHandler(handler)

        # uvicorn sets propagate=False on its own loggers, so root handlers
        # never see request errors. Re-enable propagation for the access/error
        # loggers and the SQLAlchemy logger so their records reach the file.
        for name in ("uvicorn", "uvicorn.error", "sqlalchemy.engine"):
            logging.getLogger(name).propagate = True

        # Faultline capture (the inbound seam of the error ledger). Attached
        # here so it rides the same root-logger pipeline as the file handler,
        # with zero coupling to app logic. Fail-safe: if it can't load, file
        # logging is unaffected.
        try:
            from app.services.faultline import get_handler
            fault_handler = get_handler()
            for h in root.handlers:
                if h is fault_handler:
                    break
            else:
                root.addHandler(fault_handler)
        except Exception:
            logging.getLogger(__name__).exception(
                "Could not attach Faultline capture handler; file logging unaffected"
            )

        _CONFIGURED = True
        logging.getLogger(__name__).info(
            "Durable file logging active: %s (level=%s)", log_path, level_name
        )
        return log_path
    except Exception:
        # File logging must never break startup; fall back to console only.
        logging.getLogger(__name__).exception(
            "Could not set up durable file logging; console only"
        )
        return None
