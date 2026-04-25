"""Admin authentication endpoints.

The login page lives at an obscured URL (/api/rc-admin-{token}/login)
gated by ADMIN_LOGIN_URL_TOKEN — anything else returns 404, so port
scanners don't find a form to brute force. Once authed, the rc_admin_session
cookie (HttpOnly + Secure + SameSite=Strict, scoped to /api/admin)
authorizes every admin API call. /api/admin/auth/me + /api/admin/auth/logout
work off the cookie too.
"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import AdminUser
from app.services import admin_auth as auth_svc

router = APIRouter(tags=["admin-auth"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def _login_token() -> str:
    return settings.admin_login_url_token


def _login_path() -> str:
    return f"/rc-admin-{_login_token()}"


def _is_https(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    return request.headers.get("x-forwarded-proto", "").lower() == "https"


def _client_meta(request: Request) -> tuple[str, str]:
    ip = request.client.host if request.client else ""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        ip = fwd.split(",")[0].strip() or ip
    ua = request.headers.get("user-agent", "")
    return ip, ua


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


@router.get("/rc-admin-{token}", response_class=HTMLResponse)
def login_page(token: str, request: Request):
    """Render the login form. Wrong token → 404."""
    if token != _login_token():
        raise HTTPException(404)
    return templates.TemplateResponse(
        request=request,
        name="admin/login.html",
        context={"login_path": _login_path()},
    )


@router.post("/rc-admin-{token}")
def login_submit(
    token: str,
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    if token != _login_token():
        raise HTTPException(404)

    ip, ua = _client_meta(request)

    if auth_svc.too_many_recent_failures(db, username=body.username, ip=ip):
        auth_svc.record_attempt(
            db, username=body.username, ip=ip, user_agent=ua,
            success=False, reason="rate_limited",
        )
        raise HTTPException(status_code=429, detail="Too many attempts. Try again later.")

    user = db.query(AdminUser).filter(AdminUser.username == body.username).first()

    # Always run a hash verify even when the user is unknown, so the
    # response time doesn't reveal whether the username exists.
    if user is None:
        auth_svc.waste_time_against_unknown_user(body.password)
        auth_svc.record_attempt(
            db, username=body.username, ip=ip, user_agent=ua,
            success=False, reason="unknown_user",
        )
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not user.is_active:
        auth_svc.waste_time_against_unknown_user(body.password)
        auth_svc.record_attempt(
            db, username=body.username, ip=ip, user_agent=ua,
            success=False, reason="inactive",
        )
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if user.locked_until and user.locked_until > datetime.utcnow():
        auth_svc.waste_time_against_unknown_user(body.password)
        auth_svc.record_attempt(
            db, username=body.username, ip=ip, user_agent=ua,
            success=False, reason="locked",
        )
        raise HTTPException(status_code=429, detail="Account temporarily locked.")

    if not auth_svc.verify_password(user.password_hash, body.password):
        user.failed_login_count = (user.failed_login_count or 0) + 1
        if user.failed_login_count >= settings.admin_lockout_threshold:
            user.locked_until = datetime.utcnow() + timedelta(
                seconds=settings.admin_lockout_duration_seconds
            )
        db.commit()
        auth_svc.record_attempt(
            db, username=body.username, ip=ip, user_agent=ua,
            success=False, reason="bad_password",
        )
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Success — reset counters, mint session, set cookie.
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = datetime.utcnow()
    user.last_login_ip = ip
    db.commit()

    if auth_svc.needs_rehash(user.password_hash):
        user.password_hash = auth_svc.hash_password(body.password)
        db.commit()

    raw = auth_svc.create_session(db, user, ip=ip, user_agent=ua)

    response.set_cookie(
        key=auth_svc.SESSION_COOKIE_NAME,
        value=raw,
        max_age=settings.admin_session_idle_seconds,
        httponly=True,
        secure=_is_https(request),
        samesite="strict",
        path="/api/admin",
    )
    auth_svc.record_attempt(
        db, username=user.username, ip=ip, user_agent=ua,
        success=True,
    )
    return {
        "username": user.username,
        "role": user.role,
        "redirect": "/api/admin/dashboard",
    }


@router.post("/api/admin/auth/logout")
def logout(
    response: Response,
    rc_admin_session: Optional[str] = Cookie(default=None),
    db: Session = Depends(get_db),
):
    if rc_admin_session:
        result = auth_svc.lookup_session(db, rc_admin_session)
        if result:
            auth_svc.revoke_session(db, result[0])
    response.delete_cookie(key=auth_svc.SESSION_COOKIE_NAME, path="/api/admin")
    return {"status": "ok"}


@router.get("/api/admin/auth/me")
def whoami(
    rc_admin_session: Optional[str] = Cookie(default=None),
    db: Session = Depends(get_db),
):
    if not rc_admin_session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    result = auth_svc.lookup_session(db, rc_admin_session)
    if not result:
        raise HTTPException(status_code=401, detail="Session invalid or expired")
    sess, user = result
    auth_svc.touch_session(db, sess)
    return {
        "username": user.username,
        "role": user.role,
        "session_expires_at": sess.expires_at.isoformat() + "Z",
        "session_absolute_expires_at": sess.absolute_expires_at.isoformat() + "Z",
    }
