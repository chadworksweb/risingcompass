"""Turso → DigitalOcean Spaces daily backup.

Pipeline:
  1. Open a fresh libsql embedded replica, sync from Turso → produces a
     complete local SQLite file of the current database state.
  2. gzip the file.
  3. Upload to s3://{bucket}/{prefix}/{YYYY-MM-DD}.db.gz.
  4. Verify: download the object back, open it, count compass_songs.
  5. Prune objects under the prefix older than BACKUP_RETENTION_DAYS.
"""

from __future__ import annotations

import gzip
import logging
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class BackupResult:
    key: str
    bytes: int
    verified: bool
    pruned: int

    @property
    def name(self) -> str:
        return self.key.rsplit("/", 1)[-1]


def _spaces_client():
    endpoint = f"https://{settings.do_spaces_region}.digitaloceanspaces.com"
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=settings.do_spaces_region,
        aws_access_key_id=settings.do_spaces_key,
        aws_secret_access_key=settings.do_spaces_secret,
        config=BotoConfig(signature_version="s3v4", retries={"max_attempts": 3}),
    )


def _dump_turso_to_file(dest: Path) -> None:
    """Sync Turso → a fresh local SQLite file at `dest`.

    Uses libsql's embedded-replica mode: opening a connection with
    sync_url + auth_token pulls the full primary state into `dest`.
    """
    import libsql

    url = settings.database_url
    token = settings.turso_auth_token
    if not (url.startswith("libsql://") or url.startswith("https://")):
        raise RuntimeError(f"backup requires a libsql:// DATABASE_URL, got {url!r}")
    if not token:
        raise RuntimeError("TURSO_AUTH_TOKEN is unset")

    if dest.exists():
        dest.unlink()

    conn = libsql.connect(database=str(dest), sync_url=url, auth_token=token)
    try:
        conn.sync()
    finally:
        conn.close()


def _verify_sqlite(path: Path) -> bool:
    conn = None
    try:
        conn = sqlite3.connect(str(path))
        row = conn.execute("SELECT count(*) FROM compass_songs").fetchone()
        return bool(row and row[0] >= 0)
    except Exception:
        logger.exception("Backup verification failed for %s", path)
        return False
    finally:
        if conn is not None:
            conn.close()


def _prune_old_objects(s3, bucket: str, prefix: str, retention_days: int) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    removed = 0
    paginator = s3.get_paginator("list_objects_v2")
    to_delete: list[dict] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix.rstrip("/") + "/"):
        for obj in page.get("Contents", []) or []:
            if obj["LastModified"] < cutoff:
                to_delete.append({"Key": obj["Key"]})
    # S3 DeleteObjects max 1000 per call
    for i in range(0, len(to_delete), 1000):
        chunk = to_delete[i : i + 1000]
        s3.delete_objects(Bucket=bucket, Delete={"Objects": chunk, "Quiet": True})
        removed += len(chunk)
        for obj in chunk:
            logger.info("Pruned old backup: %s", obj["Key"])
    return removed


def run_backup() -> BackupResult | None:
    """Dump Turso, upload to DO Spaces, verify, prune. Returns None on failure."""
    if not (
        settings.do_spaces_key
        and settings.do_spaces_secret
        and settings.do_spaces_bucket
    ):
        logger.error("Backup skipped: DO_SPACES_* env vars are not configured")
        return None

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"{settings.do_spaces_prefix.strip('/')}/{stamp}.db.gz"
    bucket = settings.do_spaces_bucket

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        tmp_dir = Path(tmp)
        db_file = tmp_dir / f"rc-{stamp}.db"
        gz_file = tmp_dir / f"rc-{stamp}.db.gz"

        try:
            _dump_turso_to_file(db_file)
        except Exception:
            logger.exception("Turso dump failed")
            return None

        if not _verify_sqlite(db_file):
            logger.error("Pre-upload verify failed on %s", db_file)
            return None

        with open(db_file, "rb") as src, gzip.open(gz_file, "wb", compresslevel=6) as dst:
            shutil.copyfileobj(src, dst)

        s3 = _spaces_client()
        try:
            s3.upload_file(
                str(gz_file),
                bucket,
                key,
                ExtraArgs={"ContentType": "application/gzip", "ACL": "private"},
            )
        except ClientError:
            logger.exception("Upload to s3://%s/%s failed", bucket, key)
            return None

        # Round-trip verification
        verify_file = tmp_dir / "verify.db"
        try:
            gz_dl = tmp_dir / "verify.db.gz"
            s3.download_file(bucket, key, str(gz_dl))
            with gzip.open(gz_dl, "rb") as src, open(verify_file, "wb") as dst:
                shutil.copyfileobj(src, dst)
            verified = _verify_sqlite(verify_file)
        except Exception:
            logger.exception("Post-upload verify failed for s3://%s/%s", bucket, key)
            verified = False

        if not verified:
            # Delete the bad object rather than leave an unverified backup in place
            try:
                s3.delete_object(Bucket=bucket, Key=key)
            except ClientError:
                logger.exception("Failed to clean up unverified object s3://%s/%s", bucket, key)
            return None

        size_bytes = gz_file.stat().st_size

    try:
        pruned = _prune_old_objects(
            s3, bucket, settings.do_spaces_prefix, settings.backup_retention_days
        )
    except ClientError:
        logger.exception("Prune failed (backup itself succeeded)")
        pruned = 0

    logger.info(
        "Backup ok: s3://%s/%s (%d bytes, pruned %d old)", bucket, key, size_bytes, pruned
    )
    return BackupResult(key=key, bytes=size_bytes, verified=True, pruned=pruned)


def list_backups(limit: int = 30) -> list[dict]:
    """List recent backup objects under the configured prefix."""
    if not (
        settings.do_spaces_key
        and settings.do_spaces_secret
        and settings.do_spaces_bucket
    ):
        return []
    s3 = _spaces_client()
    prefix = settings.do_spaces_prefix.strip("/") + "/"
    paginator = s3.get_paginator("list_objects_v2")
    items: list[dict] = []
    for page in paginator.paginate(Bucket=settings.do_spaces_bucket, Prefix=prefix):
        for obj in page.get("Contents", []) or []:
            items.append(
                {
                    "key": obj["Key"],
                    "bytes": obj["Size"],
                    "last_modified": obj["LastModified"].isoformat(),
                }
            )
    items.sort(key=lambda x: x["last_modified"], reverse=True)
    return items[:limit]
