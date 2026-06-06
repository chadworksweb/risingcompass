"""Admin alert delivery + preference lookup.

Two-tier categorization mirrors the Alerts UI:

  category='activity'   -- general site heartbeat. Lower-priority but
                           higher-volume. Subject prefixed [RC-ACTIVITY].
  category='moderation' -- moderation events the admin needs to triage.
                           Subject prefixed [RC-MOD].

Both currently land in the same inbox (settings.admin_alert_email). The
category drives the subject prefix so Gmail filters can split them.

send_alert is fire-and-forget on a background thread -- comment writes
must not block on SMTP, and a Resend outage shouldn't break the feature
that triggered the alert.

Pref lookup is in-process cached for 60s so a comment-storm doesn't hammer
the DB for the same flag. The TTL is short enough that flipping a toggle
in the admin UI takes effect within a minute without a restart.
"""

import logging
import threading
import time
from html import escape
from typing import Optional

import httpx
from sqlalchemy import text

from app.config import settings
from app.database import SessionLocal

logger = logging.getLogger(__name__)

_CACHE_TTL = 60.0  # seconds
_cache: dict[tuple[str, str], tuple[bool, float]] = {}
_cache_lock = threading.Lock()


def get_pref(alert_key: str, channel: str = "email") -> bool:
    """Read enabled flag for (alert_key, channel) with a 60s cache. Returns
    False if the row doesn't exist -- alerts must be opted into, never
    accidentally on after a fresh deploy."""
    now = time.time()
    with _cache_lock:
        hit = _cache.get((alert_key, channel))
        if hit and (now - hit[1]) < _CACHE_TTL:
            return hit[0]
    db = SessionLocal()
    try:
        row = db.execute(text(
            "SELECT enabled FROM admin_alert_prefs WHERE alert_key = :k AND channel = :c"
        ), {"k": alert_key, "c": channel}).fetchone()
        enabled = bool(row[0]) if row else False
    finally:
        db.close()
    with _cache_lock:
        _cache[(alert_key, channel)] = (enabled, now)
    return enabled


def set_pref(alert_key: str, enabled: bool, channel: str = "email") -> None:
    """Upsert the pref. Bypasses the cache by invalidating on write so the
    admin UI sees the new state immediately."""
    db = SessionLocal()
    try:
        db.execute(text(
            "INSERT INTO admin_alert_prefs (alert_key, channel, enabled, updated_at) "
            "VALUES (:k, :c, :e, now()) "
            "ON CONFLICT (alert_key, channel) DO UPDATE SET "
            "  enabled = excluded.enabled, updated_at = now()"
        ), {"k": alert_key, "c": channel, "e": enabled})
        db.commit()
    finally:
        db.close()
    with _cache_lock:
        _cache.pop((alert_key, channel), None)


def ensure_pref_default(alert_key: str, enabled: bool, channel: str = "email") -> None:
    """Insert a default pref row only if one doesn't already exist. Never
    overrides an admin who later toggled it. Used at startup to ship a specific
    alert on-by-default while keeping it visible/toggleable in the Alerts UI."""
    db = SessionLocal()
    try:
        db.execute(text(
            "INSERT INTO admin_alert_prefs (alert_key, channel, enabled, updated_at) "
            "VALUES (:k, :c, :e, now()) "
            "ON CONFLICT (alert_key, channel) DO NOTHING"
        ), {"k": alert_key, "c": channel, "e": enabled})
        db.commit()
    finally:
        db.close()
    with _cache_lock:
        _cache.pop((alert_key, channel), None)


def list_prefs() -> list[dict]:
    """All pref rows for the admin UI. Used by GET /api/admin/alerts."""
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT alert_key, channel, enabled, updated_at FROM admin_alert_prefs ORDER BY alert_key, channel"
        )).fetchall()
        return [
            {"alert_key": r[0], "channel": r[1], "enabled": bool(r[2]), "updated_at": r[3]}
            for r in rows
        ]
    finally:
        db.close()


def _category_subject_prefix(category: str) -> str:
    if category == "moderation":
        return "[RC-MOD]"
    return "[RC-ACTIVITY]"


def _send_resend(to_addr: str, subject: str, html: str) -> bool:
    if not settings.resend_api_key:
        logger.warning("Resend not configured; admin alert skipped (subject=%s)", subject)
        return False
    try:
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {settings.resend_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": settings.email_from,
                "to": [to_addr],
                "subject": subject,
                "html": html,
            },
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        logger.exception("Resend POST failed: %s", exc)
        return False
    if 200 <= resp.status_code < 300:
        return True
    logger.error("Resend returned %s: %s", resp.status_code, resp.text[:300])
    return False


def send_alert(category: str, alert_key: str, subject: str, html_body: str) -> None:
    """Fire-and-forget alert delivery. Spawned on a daemon thread so the
    caller is never blocked by SMTP latency."""
    if not settings.admin_alert_email:
        return
    if not get_pref(alert_key):
        return
    full_subject = f"{_category_subject_prefix(category)} {subject}"
    target = settings.admin_alert_email

    def _run():
        try:
            _send_resend(target, full_subject, html_body)
        except Exception:
            logger.exception("Admin alert delivery crashed (key=%s)", alert_key)

    threading.Thread(target=_run, daemon=True, name=f"alert-{alert_key}").start()


# ---------- canned alert formatters ----------

def emit_comment_created(*, handle: str, target_type: str, target_source: Optional[str],
                         target_id: int, content: str, comment_id: int) -> None:
    """Activity heartbeat -- a single comment was just posted. Body keeps
    the content short and links back to the admin queue + the public
    target page."""
    target_label = (
        f"{target_type}/{target_source}#{target_id}" if target_source
        else f"{target_type}#{target_id}"
    )
    snippet = content if len(content) <= 400 else content[:400] + "..."
    site = settings.site_url.rstrip("/")
    html = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 600px;">
      <p style="margin:0 0 12px;font-size:14px;color:#555;">
        New comment by <strong>@{escape(handle)}</strong> on <code>{escape(target_label)}</code>
      </p>
      <blockquote style="margin:0 0 16px;padding:10px 14px;background:#f7f7f9;border-left:3px solid #008f72;border-radius:0 4px 4px 0;font-size:14px;color:#333;white-space:pre-wrap;">{escape(snippet)}</blockquote>
      <p style="margin:0;font-size:13px;color:#555;">
        Comment #{comment_id} &middot;
        <a href="{site}/api/admin/dashboard/lobby-mod" style="color:#008f72;">Lobby Mod queue</a>
      </p>
    </div>
    """
    send_alert(
        category="activity",
        alert_key="comment_created",
        subject=f"New comment by @{handle}",
        html_body=html,
    )


def emit_album_charged(*, album_title: str, artist: str, artist_slug: Optional[str],
                       release_type: str, charge: Optional[int], tier_label: Optional[str],
                       track_count: int, calibrated_count: int,
                       contamination_count: int) -> None:
    """Activity heartbeat -- someone just charged a full album through the
    Album Charger. Links to the artist page so the admin can see it land."""
    site = settings.site_url.rstrip("/")
    type_label = {"album": "Album", "ep": "EP", "single": "Single"}.get(release_type, "Album")
    charge_str = f"{charge:+d}" if charge is not None else "n/a"
    tier = tier_label or "n/a"
    artist_link = (
        f'<a href="{site}/artists/{escape(artist_slug)}" style="color:#008f72;">{escape(artist)}</a>'
        if artist_slug else escape(artist)
    )
    html = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 600px;">
      <p style="margin:0 0 12px;font-size:14px;color:#333;">
        New album charged: <strong>{escape(album_title)}</strong> by {artist_link}
      </p>
      <table style="border-collapse:collapse;font-size:14px;color:#333;margin:0 0 16px;">
        <tr><td style="padding:2px 12px 2px 0;color:#555;">Format</td><td><strong>{type_label}</strong></td></tr>
        <tr><td style="padding:2px 12px 2px 0;color:#555;">Album charge</td><td><strong>{charge_str}</strong> ({escape(tier)})</td></tr>
        <tr><td style="padding:2px 12px 2px 0;color:#555;">Tracks charged</td><td><strong>{calibrated_count}</strong> of {track_count}</td></tr>
        <tr><td style="padding:2px 12px 2px 0;color:#555;">Contaminated</td><td><strong>{contamination_count}</strong></td></tr>
      </table>
      <p style="margin:0;font-size:13px;color:#555;">
        <a href="{site}/api/admin/dashboard/lc-activity" style="color:#008f72;">LC Activity</a>
        {(' &middot; ' + artist_link) if artist_slug else ''}
      </p>
    </div>
    """
    send_alert(
        category="activity",
        alert_key="album_charged",
        subject=f"Album charged: {album_title} by {artist}",
        html_body=html,
    )


def emit_album_mb_match(*, album_title: str, artist: str, artist_slug: Optional[str],
                        release_slug: Optional[str], musicbrainz_id: str,
                        auto: bool, alternatives: Optional[list] = None) -> None:
    """Verify-me alert -- an album was matched to a MusicBrainz release-group
    (auto when confident, or user-confirmed when ambiguous). The admin should
    eyeball the match, since a wrong release-group attaches the wrong cover art.
    """
    site = settings.site_url.rstrip("/")
    how = "auto-matched (high confidence)" if auto else "user-confirmed (was ambiguous)"
    mb_link = f"https://musicbrainz.org/release-group/{escape(musicbrainz_id)}"
    rel_link = (
        f'<a href="{site}/artists/{escape(artist_slug)}/{escape(release_slug)}" style="color:#008f72;">{escape(album_title)}</a>'
        if artist_slug and release_slug else escape(album_title)
    )
    alts_html = ""
    if alternatives:
        rows = "".join(
            f'<li>{escape(a.get("title", ""))} '
            f'<span style="color:#888;">({escape(str(a.get("primary_type") or ""))}, '
            f'score {escape(str(a.get("score", "")))})</span> '
            f'&middot; <a href="https://musicbrainz.org/release-group/{escape(a.get("mbid") or a.get("musicbrainz_id") or "")}" '
            f'style="color:#008f72;">MB</a></li>'
            for a in alternatives[:5]
        )
        alts_html = f'<p style="margin:12px 0 4px;font-size:13px;color:#555;">Other candidates:</p><ul style="margin:0;font-size:13px;color:#555;">{rows}</ul>'
    html = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 600px;">
      <p style="margin:0 0 12px;font-size:14px;color:#333;">
        Cover-art match to <strong>verify</strong>: {rel_link} by {escape(artist)}
      </p>
      <table style="border-collapse:collapse;font-size:14px;color:#333;margin:0 0 8px;">
        <tr><td style="padding:2px 12px 2px 0;color:#555;">How</td><td><strong>{how}</strong></td></tr>
        <tr><td style="padding:2px 12px 2px 0;color:#555;">Release-group</td><td><a href="{mb_link}" style="color:#008f72;">{escape(musicbrainz_id)}</a></td></tr>
      </table>
      {alts_html}
      <p style="margin:12px 0 0;font-size:13px;color:#555;">
        If wrong, clear/replace <code>releases.musicbrainz_id</code> for this release.
      </p>
    </div>
    """
    send_alert(
        category="moderation",
        alert_key="album_mb_match",
        subject=f"Verify cover-art match: {album_title} by {artist}",
        html_body=html,
    )


def emit_general_inquiry(*, inquiry_id: int, name: Optional[str], email: Optional[str],
                         topic: str, subject: Optional[str], message: str,
                         source: Optional[str]) -> None:
    """Moderation alert -- a general inquiry / contact form was submitted."""
    site = settings.site_url.rstrip("/")
    snippet = message if len(message) <= 600 else message[:600] + "..."
    who = escape(name) if name else "(no name)"
    contact = f" &lt;{escape(email)}&gt;" if email else ""
    subj_line = escape(subject) if subject else "(no subject)"
    src = f" &middot; from <code>{escape(source)}</code>" if source else ""
    html = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 600px;">
      <p style="margin:0 0 6px;font-size:14px;color:#555;">
        New inquiry &middot; <strong>{escape(topic)}</strong>{src}
      </p>
      <p style="margin:0 0 4px;font-size:14px;color:#333;"><strong>{subj_line}</strong></p>
      <p style="margin:0 0 12px;font-size:13px;color:#555;">From {who}{contact}</p>
      <blockquote style="margin:0 0 16px;padding:10px 14px;background:#f7f7f9;border-left:3px solid #008f72;border-radius:0 4px 4px 0;font-size:14px;color:#333;white-space:pre-wrap;">{escape(snippet)}</blockquote>
      <p style="margin:0;font-size:13px;color:#555;">
        Inquiry #{inquiry_id} &middot;
        <a href="{site}/api/admin/dashboard/inquiries" style="color:#008f72;">Inquiries queue</a>
      </p>
    </div>
    """
    send_alert(
        category="moderation",
        alert_key="general_inquiry",
        subject=f"New inquiry ({topic}): {subject or '(no subject)'}",
        html_body=html,
    )


def emit_provenance_health(*, breaches: list[str], health: dict) -> None:
    """Activity alert -- the provenance health check found one or more breaches
    (backlog, stalled sweep, unpushed commits, or OTS proofs stuck off-chain).
    The caller invokes this ONLY on a breach, so it is silent when healthy."""
    site = settings.site_url.rstrip("/")
    items = "".join(f"<li style=\"margin:0 0 4px;\">{escape(b)}</li>" for b in breaches)
    counts = health.get("counts", {}) or {}
    counts_str = ", ".join(f"{escape(str(k))}: {v}" for k, v in sorted(counts.items())) or "none"
    git_ahead = health.get("git_ahead")
    git_ahead_str = "unknown" if git_ahead is None else str(git_ahead)
    html = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 600px;">
      <p style="margin:0 0 12px;font-size:14px;color:#333;">
        Prose provenance anchoring needs attention:
      </p>
      <ul style="margin:0 0 16px;padding-left:20px;font-size:14px;color:#cc3333;">{items}</ul>
      <table style="border-collapse:collapse;font-size:14px;color:#333;margin:0 0 16px;">
        <tr><td style="padding:2px 12px 2px 0;color:#555;">Unanchored sealed</td><td><strong>{health.get('unanchored_sealed', 'n/a')}</strong></td></tr>
        <tr><td style="padding:2px 12px 2px 0;color:#555;">By OTS status</td><td><strong>{escape(counts_str)}</strong></td></tr>
        <tr><td style="padding:2px 12px 2px 0;color:#555;">Commits not pushed</td><td><strong>{escape(git_ahead_str)}</strong></td></tr>
        <tr><td style="padding:2px 12px 2px 0;color:#555;">Last anchor commit</td><td><strong>{escape(str(health.get('last_commit_at') or 'never'))}</strong></td></tr>
        <tr><td style="padding:2px 12px 2px 0;color:#555;">Oldest unconfirmed</td><td><strong>{escape(str(health.get('oldest_unconfirmed_at') or 'none'))}</strong></td></tr>
      </table>
      <p style="margin:0;font-size:13px;color:#555;">
        <a href="{site}/api/admin/dashboard/provenance" style="color:#008f72;">Provenance dashboard</a>
      </p>
    </div>
    """
    send_alert(
        category="activity",
        alert_key="provenance_health",
        subject=f"Provenance health: {len(breaches)} issue(s)",
        html_body=html,
    )


def emit_provenance_integrity(*, mismatches: list[str], health: dict) -> None:
    """Moderation alert -- the integrity re-verify cron found one or more
    `complete` proofs whose published hash NO LONGER matches its on-chain
    OpenTimestamp. That means the public batch file was corrupted or tampered:
    the highest-severity provenance event. Caller invokes this ONLY on a
    mismatch, so it is silent otherwise."""
    site = settings.site_url.rstrip("/")
    items = "".join(f"<li style=\"margin:0 0 4px;\"><code>{escape(p)}</code></li>" for p in mismatches)
    html = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 600px;">
      <p style="margin:0 0 12px;font-size:14px;color:#cc3333;">
        <strong>Provenance integrity failure.</strong> {len(mismatches)} batch
        proof(s) no longer match their on-chain OpenTimestamp -- the public log
        was corrupted or tampered. This breaks the tamper-evidence chain for the
        prose anchored in these batches and needs investigation now.
      </p>
      <ul style="margin:0 0 16px;padding-left:20px;font-size:13px;color:#333;">{items}</ul>
      <p style="margin:0 0 8px;font-size:13px;color:#555;">
        Mismatches by OTS status: {escape(", ".join(f"{k}: {v}" for k, v in sorted((health.get('counts') or {}).items())) or 'none')}.
      </p>
      <p style="margin:0;font-size:13px;color:#555;">
        <a href="{site}/api/admin/dashboard/provenance" style="color:#008f72;">Provenance dashboard</a>
      </p>
    </div>
    """
    send_alert(
        category="moderation",
        alert_key="provenance_integrity",
        subject=f"Provenance INTEGRITY FAILURE: {len(mismatches)} proof(s)",
        html_body=html,
    )


def emit_prompt_cache_warranted(*, stats: dict, window_days: int) -> None:
    """One-time nudge: calibrator API traffic is now dense enough that turning
    on prompt caching would save money. See app/services/cache_advisor.py for
    the detection logic. Fires once (deduped by a system_flags row), so it's
    a 'go flip this switch' message, not a recurring heartbeat."""
    site = settings.site_url.rstrip("/")
    hit_pct = f"{stats['hit_rate'] * 100:.0f}%"
    monthly = f"${stats['monthly_savings_usd']:.0f}"
    html = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 600px;">
      <p style="margin:0 0 12px;font-size:14px;color:#333;">
        Lyrical Charger calibrator traffic has crossed the density where
        <strong>prompt caching is now worth turning on</strong>.
      </p>
      <table style="border-collapse:collapse;font-size:14px;color:#333;margin:0 0 16px;">
        <tr><td style="padding:2px 12px 2px 0;color:#555;">Window</td><td><strong>last {window_days} days</strong></td></tr>
        <tr><td style="padding:2px 12px 2px 0;color:#555;">Calibrator calls</td><td><strong>{stats['total']}</strong> ({stats['warm']} would hit cache, {stats['cold']} cold)</td></tr>
        <tr><td style="padding:2px 12px 2px 0;color:#555;">Projected cache hit rate</td><td><strong>{hit_pct}</strong></td></tr>
        <tr><td style="padding:2px 12px 2px 0;color:#555;">Projected savings</td><td><strong>~{monthly}/mo</strong> (system-prefix input only)</td></tr>
      </table>
      <p style="margin:0 0 8px;font-size:13px;color:#555;">
        To turn it on, add a cache breakpoint to the calibrator system prompt in
        <code>app/services/agents/calibrator.py</code> (the <code>tracked_create_async</code> call):
      </p>
      <pre style="margin:0 0 16px;padding:10px 14px;background:#f7f7f9;border-left:3px solid #008f72;border-radius:0 4px 4px 0;font-size:13px;color:#333;white-space:pre-wrap;">system=[{{"type": "text", "text": system_prompt,
         "cache_control": {{"type": "ephemeral"}}}}],</pre>
      <p style="margin:0 0 12px;font-size:13px;color:#555;">
        This is invisible to output -- the model reads the identical prompt and
        returns the identical reading; only the billing/latency changes. The
        Claude usage tab will show the real hit rate climbing once it's live.
      </p>
      <p style="margin:0;font-size:13px;color:#555;">
        <a href="{site}/api/admin/dashboard" style="color:#008f72;">Admin dashboard</a>
        &middot; this nudge fires once and won't repeat.
      </p>
    </div>
    """
    send_alert(
        category="activity",
        alert_key="prompt_cache_warranted",
        subject="Prompt caching is now worth turning on",
        html_body=html,
    )


def _faultline_alert_html(intro: str, *, sig_id: int, title: str, component: Optional[str],
                          environment: str, occurrence_count: int) -> str:
    site = settings.site_url.rstrip("/")
    return f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 600px;">
      <p style="margin:0 0 12px;font-size:14px;color:#333;">{intro}</p>
      <table style="border-collapse:collapse;font-size:14px;color:#333;margin:0 0 16px;">
        <tr><td style="padding:2px 12px 2px 0;color:#555;">Fault</td><td><strong>{escape(title)}</strong></td></tr>
        <tr><td style="padding:2px 12px 2px 0;color:#555;">Component</td><td>{escape(component or 'unknown')}</td></tr>
        <tr><td style="padding:2px 12px 2px 0;color:#555;">Environment</td><td><strong>{escape(environment)}</strong></td></tr>
        <tr><td style="padding:2px 12px 2px 0;color:#555;">Occurrences</td><td><strong>{occurrence_count}</strong></td></tr>
        <tr><td style="padding:2px 12px 2px 0;color:#555;">Signature</td><td>#{sig_id}</td></tr>
      </table>
      <p style="margin:0;font-size:13px;color:#555;">
        <a href="{site}/api/admin/dashboard/faultline" style="color:#008f72;">Open Faultline</a>
      </p>
    </div>
    """


def emit_faultline_new_critical(*, sig_id: int, title: str, component: Optional[str],
                                environment: str, occurrence_count: int) -> None:
    """A fault was triaged to CRITICAL severity. Default-on so a critical never
    sits unseen."""
    html = _faultline_alert_html(
        "A fault was marked <strong>critical</strong>.",
        sig_id=sig_id, title=title, component=component,
        environment=environment, occurrence_count=occurrence_count,
    )
    send_alert(category="activity", alert_key="faultline_new_critical",
               subject=f"Critical fault: {title}", html_body=html)


def emit_faultline_regression(*, sig_id: int, title: str, component: Optional[str],
                              environment: str, occurrence_count: int) -> None:
    """A resolved fault recurred (status flipped to regressed). Default-on -- a
    fix that didn't hold is worth knowing about immediately."""
    html = _faultline_alert_html(
        "A <strong>resolved</strong> fault just recurred (regression).",
        sig_id=sig_id, title=title, component=component,
        environment=environment, occurrence_count=occurrence_count,
    )
    send_alert(category="activity", alert_key="faultline_regression",
               subject=f"Fault regressed: {title}", html_body=html)


def emit_faultline_new_signature(*, sig_id: int, title: str, component: Optional[str],
                                 environment: str, occurrence_count: int) -> None:
    """A brand-new fault signature was just captured. Default-on, deduped per
    fingerprint (the caller fires this ONLY when a new error_signatures row is
    created, so a burst collapses to one email). This closes the gap where a new
    prod-down fault stayed silent until manually triaged to critical."""
    html = _faultline_alert_html(
        "A <strong>new</strong> fault was just captured.",
        sig_id=sig_id, title=title, component=component,
        environment=environment, occurrence_count=occurrence_count,
    )
    send_alert(category="activity", alert_key="faultline_new_signature",
               subject=f"New fault: {title}", html_body=html)
