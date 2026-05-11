"""Admin endpoints for API client metering — clients, keys, usage."""

import json
from datetime import datetime, timedelta, date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ApiClient, ApiClientKey, ApiCallLog
from app.routers.admin import verify_admin_key
from app.services.api_clients import issue_key

router = APIRouter(prefix="/api/admin/api-clients", tags=["api-clients-admin"])


class ClientCreateIn(BaseModel):
    slug: str
    name: str
    behavior: str = "service"
    plan_tier: str = "trial"
    contact_email: str | None = None
    notes: str | None = None


class ClientUpdateIn(BaseModel):
    name: str | None = None
    behavior: str | None = None
    plan_tier: str | None = None
    status: str | None = None
    contact_email: str | None = None
    notes: str | None = None


class KeyCreateIn(BaseModel):
    label: str | None = None


def _client_summary(db: Session, c: ApiClient, since: datetime) -> dict:
    total = (
        db.query(func.count(ApiCallLog.id))
        .filter(ApiCallLog.client_id == c.id)
        .filter(ApiCallLog.ts >= since)
        .scalar() or 0
    )
    errors = (
        db.query(func.count(ApiCallLog.id))
        .filter(ApiCallLog.client_id == c.id)
        .filter(ApiCallLog.ts >= since)
        .filter(ApiCallLog.status >= 400)
        .scalar() or 0
    )
    last = (
        db.query(func.max(ApiCallLog.ts))
        .filter(ApiCallLog.client_id == c.id)
        .scalar()
    )
    keys_active = (
        db.query(func.count(ApiClientKey.id))
        .filter(ApiClientKey.client_id == c.id)
        .filter(ApiClientKey.revoked_at.is_(None))
        .scalar() or 0
    )
    return {
        "id": c.id, "slug": c.slug, "name": c.name, "behavior": c.behavior,
        "plan_tier": c.plan_tier, "status": c.status,
        "contact_email": c.contact_email, "notes": c.notes,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "calls_7d": total, "errors_7d": errors,
        "last_activity": last.isoformat() if last else None,
        "active_keys": keys_active,
    }


@router.get("", dependencies=[Depends(verify_admin_key)])
def list_clients(db: Session = Depends(get_db)):
    """All clients with 7-day activity summary."""
    since = datetime.utcnow() - timedelta(days=7)
    clients = db.query(ApiClient).order_by(ApiClient.slug).all()
    return {"clients": [_client_summary(db, c, since) for c in clients]}


@router.post("", dependencies=[Depends(verify_admin_key)], status_code=201)
def create_client(body: ClientCreateIn, db: Session = Depends(get_db)):
    if body.behavior not in ("public", "service"):
        raise HTTPException(422, "behavior must be 'public' or 'service'")
    existing = db.query(ApiClient).filter(ApiClient.slug == body.slug).first()
    if existing:
        raise HTTPException(409, f"Client with slug '{body.slug}' already exists")
    client = ApiClient(
        slug=body.slug, name=body.name, behavior=body.behavior,
        plan_tier=body.plan_tier, contact_email=body.contact_email, notes=body.notes,
    )
    db.add(client)
    db.commit()
    db.refresh(client)
    since = datetime.utcnow() - timedelta(days=7)
    return _client_summary(db, client, since)


@router.patch("/{client_id}", dependencies=[Depends(verify_admin_key)])
def update_client(client_id: int, body: ClientUpdateIn, db: Session = Depends(get_db)):
    client = db.query(ApiClient).filter(ApiClient.id == client_id).first()
    if not client:
        raise HTTPException(404, "Client not found")
    if body.name is not None:
        client.name = body.name
    if body.behavior is not None:
        if body.behavior not in ("public", "service"):
            raise HTTPException(422, "behavior must be 'public' or 'service'")
        client.behavior = body.behavior
    if body.plan_tier is not None:
        client.plan_tier = body.plan_tier
    if body.status is not None:
        if body.status not in ("active", "suspended", "revoked"):
            raise HTTPException(422, "status must be active|suspended|revoked")
        client.status = body.status
    if body.contact_email is not None:
        client.contact_email = body.contact_email
    if body.notes is not None:
        client.notes = body.notes
    client.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(client)
    since = datetime.utcnow() - timedelta(days=7)
    return _client_summary(db, client, since)


@router.get("/{client_id}", dependencies=[Depends(verify_admin_key)])
def client_detail(client_id: int, db: Session = Depends(get_db)):
    client = db.query(ApiClient).filter(ApiClient.id == client_id).first()
    if not client:
        raise HTTPException(404, "Client not found")

    since = datetime.utcnow() - timedelta(days=7)

    # Per-endpoint breakdown (last 7d)
    endpoint_rows = (
        db.query(ApiCallLog.path, func.count(ApiCallLog.id), func.avg(ApiCallLog.duration_ms))
        .filter(ApiCallLog.client_id == client_id)
        .filter(ApiCallLog.ts >= since)
        .group_by(ApiCallLog.path)
        .order_by(desc(func.count(ApiCallLog.id)))
        .limit(20)
        .all()
    )
    endpoints = [
        {"path": r[0], "count": r[1], "avg_ms": round(r[2] or 0, 1)}
        for r in endpoint_rows
    ]

    keys = (
        db.query(ApiClientKey)
        .filter(ApiClientKey.client_id == client_id)
        .order_by(ApiClientKey.created_at.desc())
        .all()
    )
    keys_out = [{
        "id": k.id, "key_prefix": k.key_prefix, "label": k.label,
        "created_at": k.created_at.isoformat() if k.created_at else None,
        "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
        "revoked_at": k.revoked_at.isoformat() if k.revoked_at else None,
    } for k in keys]

    return {
        "client": _client_summary(db, client, since),
        "endpoints_7d": endpoints,
        "keys": keys_out,
    }


@router.get("/{client_id}/calls", dependencies=[Depends(verify_admin_key)])
def client_calls(
    client_id: int,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    total = db.query(func.count(ApiCallLog.id)).filter(ApiCallLog.client_id == client_id).scalar() or 0
    rows = (
        db.query(ApiCallLog)
        .filter(ApiCallLog.client_id == client_id)
        .order_by(desc(ApiCallLog.ts))
        .offset(offset).limit(limit)
        .all()
    )
    calls = []
    for r in rows:
        ctx = None
        if r.context_json:
            try:
                ctx = json.loads(r.context_json)
            except Exception:
                ctx = None
        calls.append({
            "id": r.id, "ts": r.ts.isoformat() if r.ts else None,
            "method": r.method, "path": r.path, "status": r.status,
            "ip": r.ip, "user_agent": r.user_agent, "duration_ms": r.duration_ms,
            "context": ctx,
        })
    return {"total": total, "calls": calls}


@router.post("/{client_id}/keys", dependencies=[Depends(verify_admin_key)], status_code=201)
def create_key(client_id: int, body: KeyCreateIn, db: Session = Depends(get_db)):
    client = db.query(ApiClient).filter(ApiClient.id == client_id).first()
    if not client:
        raise HTTPException(404, "Client not found")
    raw = issue_key(db, client, label=body.label)
    db.commit()
    # Return the raw key ONCE — never shown again.
    return {"raw_key": raw, "key_prefix": raw[:8], "label": body.label}


@router.post("/keys/{key_id}/revoke", dependencies=[Depends(verify_admin_key)])
def revoke_key(key_id: int, db: Session = Depends(get_db)):
    key = db.query(ApiClientKey).filter(ApiClientKey.id == key_id).first()
    if not key:
        raise HTTPException(404, "Key not found")
    if key.revoked_at:
        return {"revoked": True, "already": True}
    key.revoked_at = datetime.utcnow()
    db.commit()
    return {"revoked": True, "already": False}


@router.post("/{client_id}/keys/rotate", dependencies=[Depends(verify_admin_key)], status_code=201)
def rotate_key(client_id: int, body: KeyCreateIn, db: Session = Depends(get_db)):
    """Issue a fresh key and revoke every currently-active key for this client.
    Returns the new raw key once. Use this when the prior key is lost or
    suspected leaked.
    """
    client = db.query(ApiClient).filter(ApiClient.id == client_id).first()
    if not client:
        raise HTTPException(404, "Client not found")

    now = datetime.utcnow()
    active_keys = (
        db.query(ApiClientKey)
        .filter(ApiClientKey.client_id == client_id)
        .filter(ApiClientKey.revoked_at.is_(None))
        .all()
    )
    revoked_count = 0
    for k in active_keys:
        k.revoked_at = now
        revoked_count += 1

    raw = issue_key(db, client, label=body.label or "rotated")
    db.commit()
    return {
        "raw_key": raw,
        "key_prefix": raw[:8],
        "label": body.label or "rotated",
        "revoked_count": revoked_count,
    }
