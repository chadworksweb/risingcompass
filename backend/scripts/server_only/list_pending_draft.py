"""List a pending chart-feeder draft's songs (awaiting-lyrics + already-calibrated).

Runs INSIDE the prod backend container (server_only), which is a trusted DB
source -- no local SSH tunnel required:

    ssh deploy@<droplet> "docker exec -i rc-backend python -" \
        < scripts/server_only/list_pending_draft.py [draft_type]

draft_type defaults to youtube_trending_usa. Other feeders:
itunes_download_usa, shazam_top200_usa, top50 (Spotify, legacy), or the daily
reading (draft_type is the date-less reading type -- see AgentDraft rows).

Emits JSON: the draft label/date, every needs-lyrics song (id, pos, title,
artist) for calibrate_song.py, and the cache-hit songs already calibrated.
"""
import json
import sys

from app.database import SessionLocal
from app.models import AgentDraft

draft_type = sys.argv[1] if len(sys.argv) > 1 else "youtube_trending_usa"

db = SessionLocal()
try:
    d = (
        db.query(AgentDraft)
        .filter(AgentDraft.draft_type == draft_type)
        .filter(AgentDraft.status == "pending")
        .order_by(AgentDraft.id.desc())
        .first()
    )
    if not d:
        print(json.dumps({"draft": None, "draft_type": draft_type}))
    else:
        songs = sorted(d.songs, key=lambda s: s.position)
        print(json.dumps({
            "label": d.label,
            "date": str(d.date),
            "status": d.status,
            "total": len(songs),
            "needs_lyrics": [
                {"id": s.id, "pos": s.position, "title": s.title, "artist": s.artist}
                for s in songs if s.rubric_color is None
            ],
            "calibrated": [
                {"pos": s.position, "title": s.title, "artist": s.artist,
                 "color": s.rubric_color, "charge": s.charge_value}
                for s in songs if s.rubric_color is not None
            ],
        }, indent=2))
finally:
    db.close()
