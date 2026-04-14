"""Resend API email sender for draft notifications."""

import logging
from typing import Optional

import httpx

from app.config import Settings
from app.constants import COLOR_LABELS, COLOR_HEX, COLOR_BG
from app.services.charge_calc import degree_to_score_display

logger = logging.getLogger(__name__)


def send_draft_email(draft, songs: list, config: Settings, db=None) -> bool:
    """Send an HTML email with draft summary for approval.

    Returns True if sent successfully, False otherwise.
    """
    if not config.resend_api_key or not config.approval_email:
        logger.warning("Resend not configured — skipping email notification")
        return False

    # Look up which songs are not fully calibrated (missing color, score, or summary)
    incomplete_titles = set()
    if db:
        from sqlalchemy import func
        from app.models import CompassSong
        for s in songs:
            existing = (
                db.query(CompassSong)
                .filter(func.lower(CompassSong.title) == s.title.lower())
                .filter(CompassSong.rubric_color.isnot(None))
                .filter(CompassSong.charge_value.isnot(None))
                .filter(CompassSong.charge_summary.isnot(None))
                .first()
            )
            if not existing:
                incomplete_titles.add(s.title.lower())

    charge_label = COLOR_LABELS.get(draft.charge_level, draft.charge_level)
    needs_lyrics = [s for s in songs if s.rubric_color is None]
    subject = f"Rising Compass Draft — {draft.date} — {charge_label}"
    if needs_lyrics:
        subject += f" ({len(needs_lyrics)} song{'s' if len(needs_lyrics) != 1 else ''} need lyrics)"

    html = _build_html(draft, songs, config, incomplete_titles=incomplete_titles)

    try:
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {config.resend_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": config.email_from,
                "to": [config.approval_email],
                "subject": subject,
                "html": html,
            },
            timeout=15,
        )
        resp.raise_for_status()
        logger.info("Draft email sent for %s (Resend ID: %s)", draft.date, resp.json().get("id"))
        return True
    except Exception:
        logger.exception("Failed to send draft email via Resend")
        return False


def _build_html(draft, songs: list, config: Settings, incomplete_titles: Optional[set] = None) -> str:
    """Build the HTML email body — white background, Rising Compass brand."""
    if incomplete_titles is None:
        incomplete_titles = set()
    from app.auth import create_approval_token

    draft_ref = draft.label or str(draft.id)
    token = create_approval_token(draft_ref)
    approve_url = f"{config.site_url}/api/admin/agent/drafts/{draft_ref}/approve?token={token}"
    admin_url = f"{config.site_url}/api/admin/dashboard"

    charge_color = COLOR_HEX.get(draft.charge_level, "#999")
    charge_label = COLOR_LABELS.get(draft.charge_level, draft.charge_level)
    charge_bg = COLOR_BG.get(draft.charge_level, "#f5f5f5")

    # Build needs-lyrics callout
    needs_lyrics_songs = [s for s in songs if s.rubric_color is None]
    needs_lyrics_section = ""
    if needs_lyrics_songs:
        nl_items = "".join(
            f'<li style="padding:4px 0;font-size:14px;color:#1a1a2e;">'
            f'<strong>{s.title}</strong> — {s.artist} '
            f'<span style="color:#999;font-size:12px;">(#{s.position})</span></li>'
            for s in needs_lyrics_songs
        )
        needs_lyrics_section = f"""
        <div style="padding:16px 28px;border-bottom:1px solid #eee;">
            <div style="background:#fff5e0;border:1px solid #ffe0a0;border-radius:6px;padding:16px 20px;">
                <div style="font-size:13px;font-weight:700;color:#cc7700;margin-bottom:8px;">\u26A0 {len(needs_lyrics_songs)} song{'s' if len(needs_lyrics_songs) != 1 else ''} need lyrics before approval</div>
                <ul style="margin:0;padding:0 0 0 18px;">{nl_items}</ul>
                <div style="font-size:12px;color:#999;margin-top:10px;">Supply lyrics via POST /drafts/{draft_ref}/songs/&lt;id&gt;/lyrics</div>
            </div>
        </div>"""

    # Build song rows
    song_rows = ""
    for s in songs:
        is_needs_lyrics = s.rubric_color is None
        color = "#cc7700" if is_needs_lyrics else COLOR_HEX.get(s.rubric_color, "#999")
        label = "NEEDS LYRICS" if is_needs_lyrics else COLOR_LABELS.get(s.rubric_color, s.rubric_color or "?")
        bg = "#fff5e0" if is_needs_lyrics else COLOR_BG.get(s.rubric_color, "#f5f5f5")
        contam_badge = (
            '<span style="display:inline-block;background:#ffe8e8;color:#ff3333;'
            'font-size:11px;padding:1px 6px;border-radius:3px;margin-left:4px;">'
            'contaminated</span>'
        ) if s.contaminated else ""

        incomplete_badge = (
            '<div style="display:inline-block;background:#ffe8e8;color:#cc0000;'
            'font-size:9px;font-weight:600;padding:2px 6px;border-radius:3px;'
            'margin-top:3px;letter-spacing:0.04em;border:1px solid #ffb0b0;">INCOMPLETE</div>'
        ) if s.title.lower() in incomplete_titles else ""

        # Charge value display
        cv = getattr(s, 'charge_value', None)
        cv_str = f'<span style="font-family:\'JetBrains Mono\',monospace;font-size:12px;color:{color};font-weight:700;">{("+" if cv and cv > 0 else "")}{cv}</span>' if cv is not None else ''


        song_rows += f"""
        <tr>
            <td style="padding:30px 8px 10px;color:#999;font-family:'JetBrains Mono',monospace;font-size:13px;width:30px;vertical-align:top;">{s.position}</td>
            <td style="padding:30px 8px 10px;vertical-align:top;">
                <div style="font-weight:600;color:#1a1a2e;font-size:14px;">{s.title}</div>
                <div style="color:#666;font-size:13px;margin-top:2px;">{s.artist}</div>
                {incomplete_badge}
            </td>
            <td style="padding:30px 8px 10px;vertical-align:top;white-space:nowrap;">
                <span style="display:inline-block;background:{bg};color:{color};font-weight:600;font-size:12px;padding:3px 10px;border-radius:4px;">{label}</span>
                {cv_str}
                {contam_badge}
            </td>
            <td style="padding:30px 8px 10px;color:#555;font-size:13px;line-height:1.4;vertical-align:top;">
                {s.charge_summary or ''}
            </td>
        </tr>
        <tr>
            <td colspan="4" style="padding:20px 8px;color:#e0e0e0;font-size:10px;">&#x25CB; &#x25CB; &#x25CB;</td>
        </tr>"""

    return f"""
    <div style="background:#ffffff;font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:720px;margin:0 auto;">
        <!-- Header -->
        <div style="background:#0a0a14;padding:24px 28px;border-radius:8px 8px 0 0;">
            <div style="font-size:11px;letter-spacing:0.3em;text-transform:uppercase;color:#00d4aa;margin-bottom:6px;">The Rising Compass</div>
            <div style="font-size:22px;font-weight:300;color:#eeeef4;">Agent Draft — <span style="font-weight:600;">{draft.date}</span></div>
        </div>

        <!-- Metrics bar -->
        <div style="background:#f8f8fa;padding:20px 28px;border-bottom:1px solid #eee;display:flex;">
            <table style="width:100%;border-collapse:collapse;">
                <tr>
                    <td style="padding:0 16px 0 0;">
                        <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.08em;color:#999;margin-bottom:4px;">Charge</div>
                        <div style="font-family:'JetBrains Mono',monospace;font-size:20px;font-weight:700;color:{charge_color};">{degree_to_score_display(draft.compass_degree)}</div>
                    </td>
                    <td style="padding:0 16px;">
                        <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.08em;color:#999;margin-bottom:4px;">Charge</div>
                        <div style="display:inline-block;background:{charge_bg};color:{charge_color};font-weight:700;font-size:14px;padding:4px 12px;border-radius:4px;">{charge_label}</div>
                    </td>
                    <td style="padding:0 16px;">
                        <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.08em;color:#999;margin-bottom:4px;">Contamination</div>
                        <div style="font-family:'JetBrains Mono',monospace;font-size:20px;font-weight:700;color:{'#ff3333' if draft.contamination_count > 0 else '#33cc55'};">{draft.contamination_count}</div>
                    </td>
                </tr>
            </table>
        </div>

        <!-- Editorial -->
        <div style="padding:20px 28px;border-bottom:1px solid #eee;">
            <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.08em;color:#999;margin-bottom:6px;">Editorial</div>
            <div style="font-size:15px;line-height:1.5;color:#1a1a2e;font-style:italic;">{draft.editorial_summary or 'No editorial generated.'}</div>
        </div>

        {needs_lyrics_section}

        <!-- Songs table -->
        <div style="padding:20px 28px;">
            <table style="width:100%;border-collapse:collapse;">
                <tr>
                    <th style="text-align:left;padding:8px;border-bottom:2px solid #1a1a2e;font-size:11px;text-transform:uppercase;letter-spacing:0.08em;color:#999;">#</th>
                    <th style="text-align:left;padding:8px;border-bottom:2px solid #1a1a2e;font-size:11px;text-transform:uppercase;letter-spacing:0.08em;color:#999;">Song</th>
                    <th style="text-align:left;padding:8px;border-bottom:2px solid #1a1a2e;font-size:11px;text-transform:uppercase;letter-spacing:0.08em;color:#999;">Tier</th>
                    <th style="text-align:left;padding:8px;border-bottom:2px solid #1a1a2e;font-size:11px;text-transform:uppercase;letter-spacing:0.08em;color:#999;">Summary</th>
                </tr>
                {song_rows}
            </table>
        </div>

        <!-- Actions -->
        <div style="padding:20px 28px;border-top:1px solid #eee;text-align:center;">
            <a href="{approve_url}" style="display:inline-block;background:#0a0a14;color:#00d4aa;padding:14px 32px;text-decoration:none;font-weight:600;font-size:14px;border-radius:6px;margin-right:12px;">Approve &amp; Publish</a>
            <a href="{admin_url}" style="display:inline-block;background:#f0f0f4;color:#1a1a2e;padding:14px 32px;text-decoration:none;font-weight:600;font-size:14px;border-radius:6px;">View in Admin</a>
        </div>

        <!-- Footer -->
        <div style="padding:16px 28px;border-top:1px solid #eee;text-align:center;">
            <span style="font-size:11px;color:#999;">Agent model: {draft.agent_model or 'unknown'} &middot; The Rising Compass &middot; A LEAM Product</span>
        </div>
    </div>
    """
