"""Reading-digest sender (Hockey Stick Build 2b, Phase 2).

The recurring value that earns the subscription: a short note built from RC's
latest daily reading (editorial summary + charge), sent to confirmed subscribers
via Resend. Cadence-agnostic -- the caller's cron decides daily vs weekly; dedup
is by the reading's own date key, so a re-run never double-sends.

Fail-soft per recipient: one bad address never aborts the run; the recipient's
last_digest_key is stamped only after a successful send, so a partial run is
safely resumable.
"""
import logging
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import settings
from app.constants import COLOR_BG, COLOR_HEX, COLOR_LABELS
from app.models import DailyReading, RcSubscriber
from app.services.charge_calc import degree_to_score_display

logger = logging.getLogger(__name__)

_MAX_PER_RUN = 2000  # backstop; the list is small pre-launch


def _site() -> str:
    return (settings.site_url or "https://risingcompass.net").rstrip("/")


def latest_reading(db: Session) -> Optional[DailyReading]:
    return db.query(DailyReading).order_by(DailyReading.date.desc()).first()


def digest_key(reading: DailyReading) -> str:
    return reading.date.isoformat()


def _song_rows(reading: DailyReading) -> str:
    """Public version of the admin email's song table: position, title/artist,
    tier badge, one-line summary. Calibration fields live on the linked unified
    Song (reading_songs only stores title/artist/position), resolved via rs.song.
    A song with no resolved row (song_id SET NULL) is skipped."""
    rows = ""
    for rs in sorted(reading.songs, key=lambda x: x.position):
        song = getattr(rs, "song", None)
        color_key = getattr(song, "rubric_color", None) if song else None
        if not color_key:
            continue  # uncalibrated / unresolved -> leave out of the public digest
        label = COLOR_LABELS.get(color_key, color_key)
        color = COLOR_HEX.get(color_key, "#999")
        bg = COLOR_BG.get(color_key, "#f5f5f5")
        summary = (getattr(song, "charge_summary", None) or "")
        rows += f"""
        <tr>
          <td style="padding:14px 4px 14px 0;color:#aaa;font-size:13px;vertical-align:top;width:18px;">{rs.position}</td>
          <td style="padding:14px 6px;vertical-align:top;">
            <div style="font-weight:600;color:#1a1a2e;font-size:14px;">{rs.title}</div>
            <div style="color:#777;font-size:13px;margin-top:2px;">{rs.artist}</div>
          </td>
          <td style="padding:14px 6px;vertical-align:top;white-space:nowrap;">
            <span style="display:inline-block;background:{bg};color:{color};font-weight:600;font-size:12px;padding:3px 8px;border-radius:4px;">{label}</span>
          </td>
          <td style="padding:14px 0 14px 6px;color:#555;font-size:13px;line-height:1.45;vertical-align:top;">{summary}</td>
        </tr>"""
    return rows


def _render_html(reading: DailyReading, sub: RcSubscriber, ad_image_url: str | None = None) -> str:
    """Public mirror of the daily admin reading email (email_notifier): the same
    charge metrics + editorial + song list, minus every admin affordance
    (approve link, needs-lyrics callout, incomplete badges, admin link).

    The Lyrical Charger ad uses the real intro-splash banner image
    (lc-splash-ad.png). ad_image_url overrides the hosted URL (the test send
    passes a cid: reference and attaches the image inline)."""
    site = _site()
    ad_img = ad_image_url or f"{site}/lyrical-charger/lc-splash-ad.png"
    unsub = f"{site}/api/unsubscribe?token={sub.unsubscribe_token}"
    charge_color = COLOR_HEX.get(reading.charge_level, "#999")
    charge_label = COLOR_LABELS.get(reading.charge_level, reading.charge_level)
    score = degree_to_score_display(reading.compass_degree) if reading.compass_degree is not None else ""
    editorial = (reading.editorial_summary or "").strip()
    editorial_html = (
        f'<div style="font-size:15px;line-height:1.55;color:#1a1a2e;font-style:italic;">{editorial}</div>'
        if editorial else ""
    )
    rows = _song_rows(reading)
    songs_html = f"""
      <table style="width:100%;border-collapse:collapse;margin-top:6px;">
        <tr>
          <th style="text-align:left;padding:8px 4px 8px 0;border-bottom:2px solid #1a1a2e;font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:#999;">#</th>
          <th style="text-align:left;padding:8px 6px;border-bottom:2px solid #1a1a2e;font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:#999;">Song</th>
          <th style="text-align:left;padding:8px 6px;border-bottom:2px solid #1a1a2e;font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:#999;">Tier</th>
          <th style="text-align:left;padding:8px 0 8px 6px;border-bottom:2px solid #1a1a2e;font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:#999;">What it is about</th>
        </tr>
        {rows}
      </table>""" if rows else ""

    return f"""
    <div style="background:#ffffff;font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:600px;margin:0 auto;color:#1a1a2e;">
      <div style="background:#0a0a14;padding:22px 26px;border-radius:8px 8px 0 0;">
        <div style="font-size:11px;letter-spacing:.3em;text-transform:uppercase;color:#00d4aa;margin-bottom:6px;">The Rising Compass</div>
        <div style="font-size:21px;font-weight:300;color:#eeeef4;">Daily reading &middot; <span style="font-weight:600;">{reading.date.isoformat()}</span></div>
      </div>
      <div style="background:#f8f8fa;padding:18px 26px;border-bottom:1px solid #eee;">
        <table style="border-collapse:collapse;"><tr>
          <td style="padding-right:28px;">
            <div style="font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:#999;margin-bottom:4px;">Charge</div>
            <div style="font-size:20px;font-weight:700;color:{charge_color};">{score}</div>
          </td>
          <td style="padding-right:28px;">
            <div style="font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:#999;margin-bottom:4px;">Tier</div>
            <div style="display:inline-block;background:{COLOR_BG.get(reading.charge_level, '#f5f5f5')};color:{charge_color};font-weight:700;font-size:14px;padding:4px 12px;border-radius:4px;">{charge_label}</div>
          </td>
        </tr></table>
      </div>
      <div style="padding:20px 26px;border-bottom:1px solid #eee;">{editorial_html}</div>
      <div style="padding:14px 14px 22px;">{songs_html}</div>
      <div style="padding:18px 26px;border-top:1px solid #eee;text-align:center;">
        <a href="{site}/" style="display:inline-block;background:#0a0a14;color:#00d4aa;padding:12px 28px;text-decoration:none;font-weight:600;font-size:14px;border-radius:6px;">See the full reading</a>
      </div>
      <!-- Lyrical Charger ad (real intro-splash banner + CRT-terminal panel) -->
      <div style="padding:6px 26px 18px;">
        <table width="100%" cellpadding="0" cellspacing="0" role="presentation"
               style="border-collapse:separate;border:1px solid #2a2a3e;border-radius:12px;background:#0a0a14;">
          <tr><td align="center" style="padding:0;line-height:0;font-size:0;">
            <a href="{site}/lyrical-charger/" style="display:inline-block;line-height:0;">
              <img src="{ad_img}" alt="Lyrical Charger" width="350" height="79"
                   style="display:block;width:350px;height:79px;border:0;">
            </a>
          </td></tr>
          <tr><td style="padding:2px 26px 26px;text-align:center;font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
            <div style="font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:19px;font-weight:700;color:#eeeef4;line-height:1.3;margin-bottom:10px;">Don't see your favorite song?</div>
            <div style="font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:15px;color:#c8c8d8;line-height:1.55;margin-bottom:20px;">Paste any lyrics and find out what frequency they carry, scored by the same 58-tenet rubric behind the daily reading.</div>
            <a href="{site}/lyrical-charger/" style="display:inline-block;background:#ffc400;color:#0a0a14;padding:9px 18px;text-decoration:none;font-weight:700;font-size:14px;border-radius:6px;font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">Charge a song &rarr;</a>
          </td></tr>
        </table>
      </div>
      <!-- chadlewine attribution (shaded + boxed) -->
      <div style="padding:0 26px 20px;">
        <table width="100%" cellpadding="0" cellspacing="0" role="presentation"
               style="border-collapse:separate;border:1px solid #e3e3e8;border-radius:8px;background:#f6f6f9;">
          <tr><td style="padding:14px 18px;text-align:center;font-size:13px;color:#555;">
            Rising Compass was created and is managed by
            <a href="https://chadlewine.com" style="color:#3388ff;font-weight:600;text-decoration:none;">Chad Lewine</a>.
          </td></tr>
        </table>
      </div>
      <div style="padding:14px 26px;border-top:1px solid #eee;text-align:center;">
        <span style="font-size:11px;color:#999;">You are subscribed to The Rising Compass readings. <a href="{unsub}" style="color:#999;">Unsubscribe</a>.</span>
      </div>
    </div>
    """


def send_digest(db: Session, dry_run: bool = False) -> dict:
    """Send the latest reading digest to eligible confirmed subscribers.

    Returns counts. dry_run reports who WOULD receive it without sending or
    stamping. Eligible = status 'confirmed' and last_digest_key != this reading's
    key (so unsubscribed/pending are excluded and nobody is sent twice)."""
    reading = latest_reading(db)
    if reading is None:
        return {"status": "no_reading", "sent": 0, "failed": 0, "eligible": 0}

    key = digest_key(reading)
    eligible = (
        db.query(RcSubscriber)
        .filter(
            RcSubscriber.status == "confirmed",
            or_(RcSubscriber.last_digest_key.is_(None), RcSubscriber.last_digest_key != key),
        )
        .order_by(RcSubscriber.id.asc())
        .limit(_MAX_PER_RUN)
        .all()
    )

    if dry_run:
        return {"status": "dry_run", "digest_key": key, "eligible": len(eligible),
                "sent": 0, "failed": 0}

    from app.services.alerts import _send_resend  # local import avoids a cycle
    subject = f"The Rising Compass reading - {reading.date.isoformat()}"
    sent = failed = 0
    for sub in eligible:
        try:
            ok = _send_resend(sub.email, subject, _render_html(reading, sub))
        except Exception:
            logger.exception("digest send raised for subscriber id=%s", sub.id)
            ok = False
        if ok:
            sub.last_digest_key = key
            db.commit()  # stamp only on success -> resumable, no double-send
            sent += 1
        else:
            failed += 1

    logger.info("subscriber digest %s: sent=%d failed=%d eligible=%d", key, sent, failed, len(eligible))
    return {"status": "ok", "digest_key": key, "eligible": len(eligible),
            "sent": sent, "failed": failed}
