from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pathlib import Path
import os
import shutil
import tempfile

from app.auth import (
    optional_admin_session,
    require_admin_session,
    verify_backup_key,
)
from app.database import get_db
from app.config import settings

router = APIRouter(prefix="/api/admin", tags=["admin"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


# Backwards-compatible alias: every admin router imports verify_admin_key
# from here. It now points at the session-cookie dependency, so the whole
# admin surface migrates without touching every router file.
verify_admin_key = require_admin_session


# --- Host-based admin section gating -------------------------------------
# One backend answers on two front doors: risingcompass.net (root) serves the
# Site Admin section, api.risingcompass.net serves the API Admin section (only
# API Monitor). The obscured admin pages 404 on the wrong host so neither
# section's surface is discoverable from the other's domain. Localhost counts
# as "dev" and is gated for neither, so both sections stay testable on one
# local origin. The shared rc_admin_session cookie (Domain=risingcompass.net)
# authenticates on both hosts.
API_ADMIN_SECTIONS = {"api-monitor"}


def _admin_host_kind(request: Request) -> str:
    host = (request.headers.get("host") or "").split(":")[0].lower()
    if host in ("localhost", "127.0.0.1", "0.0.0.0", ""):
        return "dev"
    return "api" if host.startswith("api.") else "site"


def _gate_admin_section(request: Request, section: str) -> None:
    """404 a site-admin section on the api host, and an api-admin section on
    the root host. No-op on localhost (dev)."""
    kind = _admin_host_kind(request)
    if kind == "dev":
        return
    is_api = section in API_ADMIN_SECTIONS
    if (kind == "api") != is_api:
        raise HTTPException(status_code=404)


@router.get("/dashboard")
def admin_dashboard_root(request: Request, admin=Depends(optional_admin_session)):
    """Redirect to the default landing section, or 404 when unauthed.

    Unauthed callers get a flat 404 — no redirect, no Location header
    pointing at the obscured login URL — so port scanners hitting
    /api/admin/* learn nothing about admin existence. The landing section
    depends on the host: API Monitor on the api host, the DB explorer on root.
    """
    if admin is None:
        raise HTTPException(status_code=404)
    landing = "api-monitor" if _admin_host_kind(request) == "api" else "db"
    return RedirectResponse(url=f"/api/admin/dashboard/{landing}", status_code=307)


_ADMIN_SECTIONS = {
    "db": "admin/db.html",
    "misread": "admin/misread.html",
    "artist-verified": "admin/artist_verified.html",
    "submissions": "admin/submissions.html",
    "lc-activity": "admin/lc_activity.html",
    "api-monitor": "admin/api_monitor.html",
    "claude-usage": "admin/claude_usage.html",
    "v1-test": "admin/v1_test.html",
    "lc-status": "admin/lc_status.html",
    "lobby-mod": "admin/comments.html",
    "alerts": "admin/alerts.html",
    "users": "admin/users.html",
    "motions": "admin/motions.html",
}


@router.get("/dashboard/{section}", response_class=HTMLResponse)
def admin_dashboard_section(
    section: str,
    request: Request,
    admin=Depends(optional_admin_session),
):
    """Serve a specific admin section. Returns 404 to unauthed callers."""
    if admin is None:
        raise HTTPException(status_code=404)
    template_name = _ADMIN_SECTIONS.get(section)
    if not template_name:
        raise HTTPException(status_code=404, detail="Unknown admin section")
    _gate_admin_section(request, section)
    return templates.TemplateResponse(request=request, name=template_name)


@router.get("/recalibrate", response_class=HTMLResponse)
def recalibrate_dashboard(
    request: Request,
    admin=Depends(optional_admin_session),
):
    """Serve the recalibrate suite admin UI. Returns 404 to unauthed callers."""
    if admin is None:
        raise HTTPException(status_code=404)
    _gate_admin_section(request, "recalibrate")
    return templates.TemplateResponse(request=request, name="recalibrate.html")


@router.get("/ether-audits", response_class=HTMLResponse)
def ether_audits_dashboard(
    request: Request,
    admin=Depends(optional_admin_session),
):
    """Serve the Ether Audits triage UI. Returns 404 to unauthed callers."""
    if admin is None:
        raise HTTPException(status_code=404)
    _gate_admin_section(request, "ether-audits")
    from app.services.ether_taxonomy import VALID_SLUGS
    return templates.TemplateResponse(
        request=request,
        name="ether_audits.html",
        context={"ether_slugs": sorted(VALID_SLUGS)},
    )


@router.get("/backfill", response_class=HTMLResponse)
def backfill_console(
    request: Request,
    admin=Depends(optional_admin_session),
):
    """Serve the Backfill Console UI. Returns 404 to unauthed callers."""
    if admin is None:
        raise HTTPException(status_code=404)
    _gate_admin_section(request, "backfill")
    return templates.TemplateResponse(request=request, name="backfill_console.html")


@router.get("/dashboard/user/{anon_id}", response_class=HTMLResponse)
def admin_user_detail(
    anon_id: str,
    request: Request,
    admin=Depends(optional_admin_session),
):
    """Per-user admin detail page (Profile + Comments tabs).
    /dashboard/users (plural) is the list; /dashboard/user/{anon_id}
    (singular, anon_id) is the per-user view. anon_id is the stable
    public-facing identifier; numeric PKs stay server-internal."""
    if admin is None:
        raise HTTPException(status_code=404)
    _gate_admin_section(request, "users")
    return templates.TemplateResponse(
        request=request,
        name="admin/user_detail.html",
        context={"anon_id": anon_id},
    )


# Manual daily-reading and weekly-album CRUD endpoints were removed 2026-05-25:
# readings + albums are produced by the agent/cron pipeline (draft -> approve),
# and any future manual entry will go through a dedicated intake form. The
# DailyReading / WeeklyAlbumReading models, the shared calc services, the public
# read routes, and the agent publish path all remain in place.


# --- Database Backup & Export ---

@router.post("/backup", dependencies=[Depends(verify_backup_key)])
def trigger_backup():
    """Service endpoint — called by cron at 04:45 UTC with X-Backup-Key.

    Distinct from the human admin session so a leaked admin password
    never grants automated backup access (and vice versa). RC_BACKUP_KEY
    falls back to RC_ADMIN_KEY during the transition window.
    """
    from app.services.backup import run_backup

    result = run_backup()
    if not result:
        raise HTTPException(status_code=500, detail="Backup failed")
    return {
        "status": "ok",
        "key": result.key,
        "bytes": result.bytes,
        "verified": result.verified,
        "pruned": result.pruned,
    }


@router.get("/backup/list", dependencies=[Depends(verify_admin_key)])
def list_backups(limit: int = 30):
    """List recent backup objects in DO Spaces under the RC prefix."""
    from app.services.backup import list_backups as _list

    return {"backups": _list(limit=limit)}


@router.get("/db-export", dependencies=[Depends(verify_admin_key)])
def export_database(background_tasks: BackgroundTasks):
    """Download a fresh snapshot of the database as a gzipped pg_dump (.sql.gz).

    Runs pg_dump and streams the resulting plain-SQL dump back. Not a
    substitute for the daily backup (no S3 upload) -- this is the admin ad-hoc
    export for a one-off local working copy. Restore with:
        gunzip -c rising_compass.sql.gz | psql <dsn>

    Authed via the admin session cookie (set by /api/rc-admin-{token}/login).
    Use the dashboard "Export DB" button or run from the browser while signed in.
    """
    from app.services.backup import dump_database_to_gz

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".sql.gz")
    tmp.close()
    tmp_path = Path(tmp.name)

    try:
        dump_database_to_gz(tmp_path)
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"pg_dump failed: {exc}") from exc

    background_tasks.add_task(os.unlink, tmp.name)

    return FileResponse(
        tmp.name,
        media_type="application/gzip",
        filename="rising_compass.sql.gz",
    )


@router.post("/deploy", dependencies=[Depends(verify_admin_key)])
def deploy_frontend():
    """Pull latest code from git. Frontend is volume-mounted so git pull is enough."""
    import subprocess

    result = subprocess.run(
        ["git", "pull", "origin", "master"],
        cwd="/root/rising-compass",
        capture_output=True, text=True, timeout=30,
    )
    return {
        "status": "ok" if result.returncode == 0 else "error",
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }
