"""Gmail SMTP email sender for draft notifications."""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import Settings

logger = logging.getLogger(__name__)

# Rising Compass tier system
COLOR_LABELS = {
    "bright_green": "Ascended",
    "green": "Elevated",
    "yellow": "Decent",
    "orange": "Degraded",
    "red": "Corrupted",
}

COLOR_HEX = {
    "bright_green": "#9933ff",
    "green": "#3388ff",
    "yellow": "#33cc55",
    "orange": "#ffbb33",
    "red": "#ff3333",
}

# Slightly muted versions for backgrounds on white
COLOR_BG = {
    "bright_green": "#f3e8ff",
    "green": "#e8f0ff",
    "yellow": "#e8fae8",
    "orange": "#fff5e0",
    "red": "#ffe8e8",
}


def _degree_to_score(degree: float) -> str:
    """Convert internal degree to display score (+100 to -100)."""
    score = round((90 - degree) * 100 / 90)
    return f"{'+' if score > 0 else ''}{score}"


def send_draft_email(draft, songs: list, config: Settings) -> bool:
    """Send an HTML email with draft summary for approval.

    Returns True if sent successfully, False otherwise.
    """
    if not config.smtp_user or not config.smtp_password or not config.approval_email:
        logger.warning("SMTP not configured — skipping email notification")
        return False

    charge_label = COLOR_LABELS.get(draft.charge_level, draft.charge_level)
    subject = f"Rising Compass Draft — {draft.date} — {charge_label}"

    html = _build_html(draft, songs, config)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = config.smtp_user
    msg["To"] = config.approval_email
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(config.smtp_host, config.smtp_port) as server:
            server.starttls()
            server.login(config.smtp_user, config.smtp_password)
            server.sendmail(config.smtp_user, config.approval_email, msg.as_string())
        logger.info("Draft email sent for %s", draft.date)
        return True
    except Exception:
        logger.exception("Failed to send draft email")
        return False


def _build_html(draft, songs: list, config: Settings) -> str:
    """Build the HTML email body — white background, Rising Compass brand."""
    approve_url = f"{config.site_url}/api/admin/agent/drafts/{draft.id}/approve"
    admin_url = f"{config.site_url}/api/admin/dashboard"

    charge_color = COLOR_HEX.get(draft.charge_level, "#999")
    charge_label = COLOR_LABELS.get(draft.charge_level, draft.charge_level)
    charge_bg = COLOR_BG.get(draft.charge_level, "#f5f5f5")

    # Build song rows
    song_rows = ""
    for s in songs:
        color = COLOR_HEX.get(s.rubric_color, "#999")
        label = COLOR_LABELS.get(s.rubric_color, s.rubric_color or "?")
        bg = COLOR_BG.get(s.rubric_color, "#f5f5f5")
        contam_badge = (
            '<span style="display:inline-block;background:#ffe8e8;color:#ff3333;'
            'font-size:11px;padding:1px 6px;border-radius:3px;margin-left:4px;">'
            'contaminated</span>'
        ) if s.contaminated else ""

        # Charge value display
        cv = getattr(s, 'charge_value', None)
        cv_str = f'<span style="font-family:\'JetBrains Mono\',monospace;font-size:12px;color:{color};font-weight:700;">{("+" if cv and cv > 0 else "")}{cv}</span>' if cv is not None else ''

        # M/E/I breakdown — 3-column table
        msg = getattr(s, 'message_analysis', None) or ''
        exp = getattr(s, 'expression_analysis', None) or ''
        intent = getattr(s, 'intention_analysis', None) or ''
        mei_html = ''
        if msg or exp or intent:
            mei_cell = 'style="padding:6px 8px;font-size:11px;line-height:1.4;color:#777;vertical-align:top;width:33%;"'
            mei_header = 'style="padding:4px 8px 2px;font-size:10px;text-transform:uppercase;letter-spacing:0.06em;color:#555;font-weight:700;vertical-align:top;width:33%;"'
            mei_html = f'''<table style="width:100%;border-collapse:collapse;margin-top:8px;border-top:1px solid #f0f0f0;">
                <tr><td {mei_header}>Message</td><td {mei_header}>Expression</td><td {mei_header}>Intention</td></tr>
                <tr><td {mei_cell}>{msg}</td><td {mei_cell}>{exp}</td><td {mei_cell}>{intent}</td></tr>
            </table>'''

        song_rows += f"""
        <tr>
            <td style="padding:30px 8px 10px;color:#999;font-family:'JetBrains Mono',monospace;font-size:13px;width:30px;vertical-align:top;">{s.position}</td>
            <td style="padding:30px 8px 10px;vertical-align:top;">
                <div style="font-weight:600;color:#1a1a2e;font-size:14px;">{s.title}</div>
                <div style="color:#666;font-size:13px;margin-top:2px;">{s.artist}</div>
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
            <td colspan="4" style="padding:0 8px 8px;border-bottom:1px solid #eee;">
                {mei_html}
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
                        <div style="font-family:'JetBrains Mono',monospace;font-size:20px;font-weight:700;color:{charge_color};">{_degree_to_score(draft.compass_degree)}</div>
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
