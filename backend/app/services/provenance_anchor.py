"""External tamper-evident anchoring for societal-prose provenance.

The DB columns from migration 075 (societal_prose_generated_at + model) are
editable by whoever controls the DB. This module is the bridge to a trust root
that the author cannot rewrite:

  GitHub  -- a public, append-only, hash-only log (the prose text never leaves
             the DB; only sha256(table:id | generated_at | model | prose) is
             published, so a sold prose value is provable later by revealing it
             and re-hashing, without publishing it now).
  Bitcoin -- each committed batch file is OpenTimestamped, anchoring its hash to
             a block. The proof shows the hash existed no later than that block.

Design notes:
  - Wholly OFF the calibration hot path. A sweep reads already-sealed rows and
    creates anchors; a failed/missing anchor never blocks a calibration.
  - Fail-soft everywhere. Disabled or misconfigured -> no-op with a status, not
    an exception. A git/ots failure is logged and retried on the next run.
  - Idempotent. One anchor per (table, row, prose version); re-running only
    picks up prose that has no anchor for its current hash.

The hash recipe is intentionally simple and frozen so an independent verifier
can reproduce it:

    sha256( f"{song_table}:{song_id}\\n{generated_at_iso}\\n{model}\\n{prose}" )

where generated_at_iso is exactly the "generated_at" string published in the
log (the row's societal_prose_generated_at, ISO 8601).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
from datetime import datetime, timedelta

import httpx
from sqlalchemy import func

from app.config import settings
from app.models import (
    CompassSong, LibrarySong, SubmittedSong, StreamSong, ProseProvenanceAnchor,
)

logger = logging.getLogger(__name__)

# (published table name, model). AgentDraftSong carries no societal prose.
_PROSE_MODELS = [
    ("compass_songs", CompassSong),
    ("library_songs", LibrarySong),
    ("submitted_songs", SubmittedSong),
    ("stream_songs", StreamSong),
]

_BITCOIN_ATTEST_RE = re.compile(r"BitcoinBlockHeaderAttestation\((\d+)\)")
# `ots verify` output classification. CONSERVATIVE on purpose: only an
# unambiguous content-mismatch is treated as tampering (-> alarm). A
# not-yet-confirmed / calendar-unreachable / missing-CLI run is 'inconclusive'
# and never alarms -- it just gets retried next round. Exact strings are the
# opentimestamps-client phrasings and should be confirmed against real CLI
# output during first-run validation (see RISING-COMPASS-PROSE-PROVENANCE.md).
_OTS_VERIFY_SUCCESS_RE = re.compile(r"\bSuccess!", re.I)
_OTS_VERIFY_MISMATCH_RE = re.compile(r"does not match|hash mismatch|failed to verify", re.I)

# Health-check breach thresholds (see evaluate_breaches). Tunable; chosen so a
# single calibration landing between a sweep and the daily health check does not
# trip the alarm, while a genuinely stalled pipeline does.
UNANCHORED_ALERT = 25       # sealed prose rows still awaiting an anchor
STALE_SWEEP_HOURS = 26      # backlog present but no anchor commit this long
STUCK_UNCONFIRMED_HOURS = 72  # OTS proof submitted but not on-chain this long


def canonical_hash(song_table: str, song_id: int, generated_at_iso: str,
                   model: str, prose: str) -> str:
    """Frozen provenance hash recipe. See module docstring -- an independent
    verifier reproduces this from the published fields + the revealed prose."""
    canonical = f"{song_table}:{song_id}\n{generated_at_iso}\n{model}\n{prose}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def status_counts(db) -> dict:
    rows = (
        db.query(ProseProvenanceAnchor.ots_status, func.count())
        .group_by(ProseProvenanceAnchor.ots_status)
        .all()
    )
    return {"counts": {status: count for status, count in rows}}


def _pending_hash(db, table: str, row) -> str | None:
    """The canonical hash for a sealed-prose row that has NO anchor yet, or None
    if the row carries no sealed prose or its current version is already
    anchored. Shared by the sweep (what to anchor) and the health check (how far
    behind we are) so both agree on 'needs an anchor'."""
    prose = row.societal_effects_prose
    gen_at = row.societal_prose_generated_at
    if not prose or gen_at is None:
        return None
    model = row.societal_prose_model or "unknown"
    h = canonical_hash(table, row.id, gen_at.isoformat(), model, prose)
    exists = db.query(ProseProvenanceAnchor.id).filter_by(
        song_table=table, song_id=row.id, prose_sha256=h).first()
    return None if exists else h


def _sealed_rows(db, Model):
    return (
        db.query(Model)
        .filter(Model.societal_effects_prose.isnot(None))
        .filter(Model.societal_effects_prose != "")
        .filter(Model.societal_prose_generated_at.isnot(None))
        .all()
    )


def count_unanchored(db) -> int:
    """Sealed prose rows (across all four tables) whose current version has no
    anchor -- the backlog between sealing and anchoring. Near 0 right after a
    sweep; a growing number means the sweep is not running or is failing."""
    n = 0
    for table, Model in _PROSE_MODELS:
        for r in _sealed_rows(db, Model):
            if _pending_hash(db, table, r):
                n += 1
    return n


def _git_ahead(repo: str) -> int | None:
    """Local commits not yet pushed to the upstream. None when there is no
    upstream / git is unavailable (fail-soft -- absence of signal, not zero)."""
    try:
        proc = _run(["git", "rev-list", "--count", "@{u}..HEAD"], cwd=repo)
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    try:
        return int((proc.stdout or "").strip() or 0)
    except ValueError:
        return None


def _health_raw(db) -> dict:
    """Health snapshot with native datetimes/ints (for breach evaluation)."""
    counts = status_counts(db)["counts"]
    oldest_unconfirmed = (
        db.query(func.min(ProseProvenanceAnchor.sealed_at))
        .filter(ProseProvenanceAnchor.ots_status.in_(("pending", "submitted")))
        .scalar()
    )
    last_commit = db.query(func.max(ProseProvenanceAnchor.github_committed_at)).scalar()
    last_anchor = db.query(func.max(ProseProvenanceAnchor.sealed_at)).scalar()
    integrity_mismatches = (
        db.query(func.count())
        .select_from(ProseProvenanceAnchor)
        .filter(ProseProvenanceAnchor.ots_verify_status == "mismatch")
        .scalar()
    ) or 0
    last_reverify = db.query(func.max(ProseProvenanceAnchor.ots_last_verified_at)).scalar()
    repo = settings.provenance_repo_path
    repo_ok = _repo_ready(repo)
    return {
        "enabled": settings.provenance_enabled,
        "repo_ok": repo_ok,
        "git_ahead": _git_ahead(repo) if repo_ok else None,
        "counts": counts,
        "unanchored_sealed": count_unanchored(db),
        "oldest_unconfirmed_at": oldest_unconfirmed,
        "last_commit_at": last_commit,
        "last_anchor_at": last_anchor,
        "integrity_mismatches": integrity_mismatches,
        "last_reverify_at": last_reverify,
    }


def health(db) -> dict:
    """JSON-serializable health payload for the admin status endpoint/page."""
    h = _health_raw(db)
    for k in ("oldest_unconfirmed_at", "last_commit_at", "last_anchor_at", "last_reverify_at"):
        h[k] = h[k].isoformat() if h[k] else None
    return h


def evaluate_breaches(db) -> list[str]:
    """Return human-readable breach reasons, or [] when healthy. Disabled is not
    a breach -- the subsystem ships dark on purpose until provisioned."""
    h = _health_raw(db)
    if not h["enabled"]:
        return []
    now = datetime.utcnow()
    breaches: list[str] = []
    if h["integrity_mismatches"]:
        breaches.append(
            f"{h['integrity_mismatches']} anchor(s) FAILED ots re-verification "
            "(published hash no longer matches its on-chain proof -- possible "
            "corruption or tampering)")
    if not h["repo_ok"]:
        breaches.append("enabled but the anchor repo is unavailable")
    if h["git_ahead"]:
        breaches.append(f"{h['git_ahead']} local commit(s) not pushed to the public repo")
    if h["unanchored_sealed"] > UNANCHORED_ALERT:
        breaches.append(f"{h['unanchored_sealed']} sealed prose rows awaiting an anchor")
    if (h["unanchored_sealed"] > 0 and h["last_commit_at"]
            and now - h["last_commit_at"] > timedelta(hours=STALE_SWEEP_HOURS)):
        breaches.append(f"backlog present but no anchor commit in {STALE_SWEEP_HOURS}h (sweep stalled?)")
    if (h["oldest_unconfirmed_at"]
            and now - h["oldest_unconfirmed_at"] > timedelta(hours=STUCK_UNCONFIRMED_HOURS)):
        age_h = int((now - h["oldest_unconfirmed_at"]).total_seconds() // 3600)
        breaches.append(f"oldest unconfirmed OTS proof is {age_h}h old (> {STUCK_UNCONFIRMED_HOURS}h)")
    return breaches


def _git_author() -> tuple[str, str]:
    """Parse 'Name <email>' from settings; fall back to a sane default."""
    raw = settings.provenance_git_author or "rc-provenance <provenance@risingcompass.net>"
    m = re.match(r"^(.*?)\s*<([^>]+)>\s*$", raw)
    if m:
        return m.group(1).strip() or "rc-provenance", m.group(2).strip()
    return "rc-provenance", "provenance@risingcompass.net"


def _run(cmd: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def _git(repo: str, *args: str) -> str:
    """Run a git subcommand in `repo`, returning stdout. Raises on failure.
    Stderr is intentionally NOT echoed wholesale by callers that push, since a
    token-in-URL remote could surface there."""
    name, email = _git_author()
    proc = _run(["git", "-c", f"user.name={name}", "-c", f"user.email={email}",
                 *args], cwd=repo)
    if proc.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed rc={proc.returncode}")
    return proc.stdout


def _repo_ready(repo: str) -> bool:
    return bool(repo) and os.path.isdir(os.path.join(repo, ".git"))


def _ots_stamp(abs_path: str) -> bool:
    """OpenTimestamp a file -> writes <abs_path>.ots (pending). True on success.
    Fail-soft: a missing `ots` CLI or any error returns False, never raises."""
    try:
        proc = _run([settings.provenance_ots_bin, "stamp", abs_path], cwd=os.path.dirname(abs_path))
    except OSError:
        logger.warning("ots stamp: CLI not runnable (%r)", settings.provenance_ots_bin)
        return False
    ok = proc.returncode == 0 and os.path.isfile(abs_path + ".ots")
    if not ok:
        logger.warning("ots stamp failed rc=%s for %s", proc.returncode, os.path.basename(abs_path))
    return ok


def _ots_block(abs_proof: str) -> int | None:
    """Run `ots upgrade` then parse `ots info` for a confirmed Bitcoin block.
    Returns the block height once anchored on-chain, else None (still pending).
    Fail-soft: a missing `ots` CLI or any error returns None, never raises."""
    try:
        up = _run([settings.provenance_ots_bin, "upgrade", abs_proof], cwd=os.path.dirname(abs_proof))
    except OSError:
        logger.warning("ots upgrade: CLI not runnable (%r)", settings.provenance_ots_bin)
        return None
    # `ots upgrade` may drop a .bak alongside the proof; don't let it linger.
    bak = abs_proof + ".bak"
    if os.path.isfile(bak):
        try:
            os.remove(bak)
        except OSError:
            pass
    if up.returncode != 0:
        logger.warning("ots upgrade failed rc=%s for %s", up.returncode, os.path.basename(abs_proof))
        return None
    try:
        info = _run([settings.provenance_ots_bin, "info", abs_proof], cwd=os.path.dirname(abs_proof))
    except OSError:
        return None
    m = _BITCOIN_ATTEST_RE.search(info.stdout or "")
    return int(m.group(1)) if m else None


def _ots_verify(abs_proof: str) -> str:
    """Re-verify a confirmed proof against its file + Bitcoin. Returns:
      'ok'           -- the file still matches the on-chain timestamp.
      'mismatch'     -- the file no longer matches (corruption/tampering). ALARM.
      'inconclusive' -- not yet confirmable, calendar/network error, missing
                        original file, or the `ots` CLI is unavailable. NO alarm.
    Fail-soft: any error returns 'inconclusive', never raises. `ots verify` needs
    the original batch file present alongside the .ots, which the sweep commits."""
    try:
        proc = _run([settings.provenance_ots_bin, "verify", abs_proof],
                    cwd=os.path.dirname(abs_proof))
    except OSError:
        logger.warning("ots verify: CLI not runnable (%r)", settings.provenance_ots_bin)
        return "inconclusive"
    out = f"{proc.stdout or ''}\n{proc.stderr or ''}"
    if _OTS_VERIFY_MISMATCH_RE.search(out):
        logger.error("ots verify MISMATCH for %s", os.path.basename(abs_proof))
        return "mismatch"
    if proc.returncode == 0 and _OTS_VERIFY_SUCCESS_RE.search(out):
        return "ok"
    return "inconclusive"


def heartbeat_ping(*, ok: bool = True) -> bool:
    """Dead-man's-switch: ping the external uptime monitor so a silently-dead
    sweep cron pages without any RC code. Pings the configured URL on a healthy
    run, and {url}/fail on a failed one (healthchecks.io convention; harmless
    elsewhere). No-op (returns False) when no URL is configured. Fail-soft."""
    url = settings.provenance_heartbeat_url
    if not url:
        return False
    target = url if ok else url.rstrip("/") + "/fail"
    try:
        resp = httpx.get(target, timeout=10.0)
    except httpx.HTTPError:
        logger.warning("provenance heartbeat ping failed (ok=%s)", ok)
        return False
    return 200 <= resp.status_code < 300


def sweep(db, *, limit: int = 2000) -> dict:
    """Anchor every sealed prose row that has no anchor for its current hash:
    create the anchor rows, append hash-only records to the repo's batch +
    cumulative logs, OpenTimestamp the batch, then commit + push. Fail-soft."""
    if not settings.provenance_enabled:
        return {"status": "disabled"}
    repo = settings.provenance_repo_path
    if not _repo_ready(repo):
        logger.warning("provenance sweep: %r is not a git working clone", repo)
        return {"status": "misconfigured", "detail": "provenance_repo_path is not a git clone"}

    new_records: list[dict] = []
    new_anchor_ids: list[int] = []
    for table, Model in _PROSE_MODELS:
        for r in _sealed_rows(db, Model):
            h = _pending_hash(db, table, r)
            if not h:
                continue
            gen_at = r.societal_prose_generated_at
            model = r.societal_prose_model or "unknown"
            anchor = ProseProvenanceAnchor(
                song_table=table, song_id=r.id, prose_sha256=h,
                generated_at=gen_at, model=model, ots_status="pending",
            )
            db.add(anchor)
            db.flush()
            new_anchor_ids.append(anchor.id)
            new_records.append({
                "v": 1, "anchor_id": anchor.id, "table": table, "id": r.id,
                "generated_at": gen_at.isoformat(), "model": model, "sha256": h,
            })
            if len(new_records) >= limit:
                break
        if len(new_records) >= limit:
            break

    if not new_records:
        return {"status": "nothing_new", **status_counts(db)}

    # Append hash-only records to an immutable per-batch file (what OTS anchors)
    # and to the cumulative human-readable log.
    lines = [json.dumps(rec, separators=(",", ":"), sort_keys=True) for rec in new_records]
    batch_rel = os.path.join("batches",
                             f"batch-{new_anchor_ids[0]:08d}-{new_anchor_ids[-1]:08d}.jsonl")
    batch_abs = os.path.join(repo, batch_rel)
    try:
        os.makedirs(os.path.dirname(batch_abs), exist_ok=True)
        with open(batch_abs, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        with open(os.path.join(repo, settings.provenance_jsonl_name), "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        logger.exception("provenance sweep: failed writing log files")
        db.rollback()
        return {"status": "error", "detail": "log write failed"}

    ots_ok = _ots_stamp(batch_abs)

    add_paths = [settings.provenance_jsonl_name, batch_rel]
    if ots_ok:
        add_paths.append(batch_rel + ".ots")

    commit_sha = None
    committed_at = None
    try:
        _git(repo, "add", *add_paths)
        _git(repo, "commit", "-m",
             f"Anchor {len(new_records)} societal-prose hashes "
             f"(anchors {new_anchor_ids[0]}-{new_anchor_ids[-1]})")
        commit_sha = _git(repo, "rev-parse", "HEAD").strip()
        committed_at = datetime.utcnow()
    except Exception:
        logger.exception("provenance sweep: git commit failed")

    pushed = False
    if commit_sha:
        try:
            _git(repo, "push")
            pushed = True
        except Exception:
            logger.warning("provenance sweep: git push failed; will retry next run")

    for aid in new_anchor_ids:
        a = db.get(ProseProvenanceAnchor, aid)
        if a is None:
            continue
        if commit_sha:
            a.github_commit_sha = commit_sha
            a.github_committed_at = committed_at
        if ots_ok:
            a.ots_status = "submitted"
            a.ots_proof_path = batch_rel + ".ots"
    db.commit()

    return {
        "status": "swept", "anchored": len(new_records), "commit": commit_sha,
        "pushed": pushed, "ots_submitted": ots_ok, **status_counts(db),
    }


def upgrade(db) -> dict:
    """Confirm submitted OTS proofs on-chain: `ots upgrade` each pending batch
    proof, and on a Bitcoin attestation mark its anchors complete + record the
    block. Commits the upgraded proofs back to the repo. Fail-soft."""
    if not settings.provenance_enabled:
        return {"status": "disabled"}
    repo = settings.provenance_repo_path
    if not _repo_ready(repo):
        return {"status": "misconfigured", "detail": "provenance_repo_path is not a git clone"}

    pending = (
        db.query(ProseProvenanceAnchor)
        .filter(ProseProvenanceAnchor.ots_status == "submitted")
        .filter(ProseProvenanceAnchor.ots_proof_path.isnot(None))
        .all()
    )
    by_proof: dict[str, list] = {}
    for a in pending:
        by_proof.setdefault(a.ots_proof_path, []).append(a)

    confirmed_paths: list[str] = []
    for proof_rel, anchors in by_proof.items():
        proof_abs = os.path.join(repo, proof_rel)
        if not os.path.isfile(proof_abs):
            continue
        block = _ots_block(proof_abs)
        if block is None:
            continue
        for a in anchors:
            a.ots_status = "complete"
            a.ots_bitcoin_block = block
        confirmed_paths.append(proof_rel)

    if confirmed_paths:
        try:
            _git(repo, "add", *confirmed_paths)
            _git(repo, "commit", "-m",
                 f"Confirm {len(confirmed_paths)} OTS proof(s) on Bitcoin")
            try:
                _git(repo, "push")
            except Exception:
                logger.warning("provenance upgrade: git push failed; will retry next run")
        except Exception:
            logger.exception("provenance upgrade: git commit failed")

    db.commit()
    return {"status": "upgraded", "confirmed_batches": len(confirmed_paths),
            **status_counts(db)}


def reverify(db, *, sample: int | None = None) -> dict:
    """Re-prove a rolling sample of `complete` proofs against Bitcoin to catch a
    proof that has since been corrupted/tampered in the public repo. Round-robins
    the least-recently-verified proofs first (NULL = never verified sorts first),
    runs `ots verify` per distinct batch proof, and records the result on every
    anchor sharing that proof. A 'mismatch' is the tampering signal -- surfaced
    in the return value (caller emits the provenance_integrity alert) and in the
    health breach list. Fail-soft and dark-safe."""
    if not settings.provenance_enabled:
        return {"status": "disabled"}
    repo = settings.provenance_repo_path
    if not _repo_ready(repo):
        return {"status": "misconfigured", "detail": "provenance_repo_path is not a git clone"}

    n = sample if sample is not None else settings.provenance_reverify_sample
    # Distinct complete proofs, least-recently-verified (NULLs) first.
    proof_paths = [
        row[0] for row in (
            db.query(ProseProvenanceAnchor.ots_proof_path)
            .filter(ProseProvenanceAnchor.ots_status == "complete")
            .filter(ProseProvenanceAnchor.ots_proof_path.isnot(None))
            .group_by(ProseProvenanceAnchor.ots_proof_path)
            .order_by(func.min(ProseProvenanceAnchor.ots_last_verified_at).asc().nullsfirst())
            .limit(n)
            .all()
        )
    ]
    if not proof_paths:
        return {"status": "nothing_to_verify", **status_counts(db)}

    now = datetime.utcnow()
    checked = 0
    mismatches: list[str] = []
    inconclusive = 0
    for proof_rel in proof_paths:
        proof_abs = os.path.join(repo, proof_rel)
        result = "inconclusive" if not os.path.isfile(proof_abs) else _ots_verify(proof_abs)
        anchors = (
            db.query(ProseProvenanceAnchor)
            .filter(ProseProvenanceAnchor.ots_proof_path == proof_rel)
            .filter(ProseProvenanceAnchor.ots_status == "complete")
            .all()
        )
        for a in anchors:
            a.ots_last_verified_at = now
            a.ots_verify_status = result
        checked += 1
        if result == "mismatch":
            mismatches.append(proof_rel)
        elif result == "inconclusive":
            inconclusive += 1
    db.commit()

    return {
        "status": "reverified", "proofs_checked": checked,
        "mismatches": mismatches, "mismatch_count": len(mismatches),
        "inconclusive": inconclusive, **status_counts(db),
    }
