"""Daily SQLite database backup — copies DB file with date stamp, prunes old backups."""

import logging
import shutil
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Resolve paths relative to the backend/data/ directory
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DB_PATH = DATA_DIR / "rising_compass.db"
BACKUP_DIR = DATA_DIR / "backups"
MAX_BACKUPS = 30  # Keep last 30 days


def run_backup() -> Path | None:
    """Copy the database file to backups/ with a date-stamped name.

    Returns the backup path on success, None on failure.
    """
    if not DB_PATH.exists():
        logger.error("Database not found at %s", DB_PATH)
        return None

    BACKUP_DIR.mkdir(exist_ok=True)

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup_path = BACKUP_DIR / f"rising_compass_{stamp}.db"

    try:
        shutil.copy2(str(DB_PATH), str(backup_path))
        logger.info("Database backed up to %s", backup_path)
    except Exception:
        logger.exception("Failed to back up database")
        return None

    _prune_old_backups()
    return backup_path


def _prune_old_backups() -> None:
    """Remove oldest backups beyond MAX_BACKUPS."""
    backups = sorted(BACKUP_DIR.glob("rising_compass_*.db"))
    if len(backups) <= MAX_BACKUPS:
        return

    to_remove = backups[: len(backups) - MAX_BACKUPS]
    for old in to_remove:
        old.unlink()
        logger.info("Pruned old backup: %s", old.name)
