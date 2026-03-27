"""Database backup — file copy for SQLite, no-op for Turso (managed backups)."""

import logging
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

# Resolve paths relative to the backend/data/ directory
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DB_PATH = DATA_DIR / "rising_compass.db"
BACKUP_DIR = DATA_DIR / "backups"
MAX_BACKUPS = 30  # Keep last 30 days


def run_backup() -> Path | None:
    """Copy the database file to backups/ with a date-stamped name.

    Returns the backup path on success, None on failure.
    Skips when running on Turso (Turso handles its own backups).
    """
    if settings.is_turso:
        logger.info("Turso mode — skipping file-based backup (managed by Turso)")
        return None

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

    # Verify backup is valid SQLite
    if not _verify_backup(backup_path):
        logger.error("Backup verification FAILED: %s", backup_path)
        return None

    logger.info("Backup verified: %s", backup_path)
    _prune_old_backups()
    return backup_path


def _verify_backup(backup_path: Path) -> bool:
    """Verify a backup file is valid SQLite with expected tables."""
    try:
        with sqlite3.connect(str(backup_path)) as conn:
            conn.execute("SELECT count(*) FROM compass_songs")
        return True
    except Exception:
        return False


def _prune_old_backups() -> None:
    """Remove oldest backups beyond MAX_BACKUPS."""
    backups = sorted(BACKUP_DIR.glob("rising_compass_*.db"))
    if len(backups) <= MAX_BACKUPS:
        return

    to_remove = backups[: len(backups) - MAX_BACKUPS]
    for old in to_remove:
        old.unlink()
        logger.info("Pruned old backup: %s", old.name)
