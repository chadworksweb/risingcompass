"""Build 6 social broadcaster -- cron entry point + the public card image route.

POST /api/admin/agent/cron/social-broadcast runs one broadcast pass (select the
day's top trending verdicts + the daily-aggregate reading, render each card, push
to RC's Buffer queue, ledger the result). Auth reuses the daily-reading cron lane
(X-Reading-Cron-Key) so no new server secret is needed -- same pattern as
leit-sweep. Ships dark: with Buffer unconfigured it ledgers status='dark' and
pushes nothing.

GET /api/social/card/{token}.png serves a rendered card PNG from social_cards.
PUBLIC + unauthed (mounted like geo/subscribe) so Buffer can fetch the media by
URL when it publishes the post. Unknown token -> 404.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.auth import verify_reading_cron_key
from app.database import get_db
from app.models import SocialCard
from app.services.social.broadcaster import run_social_broadcast

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/agent", tags=["social-broadcast"])
public_router = APIRouter(prefix="/api/social", tags=["social-card"])


@router.post("/cron/social-broadcast", dependencies=[Depends(verify_reading_cron_key)])
async def cron_social_broadcast():
    """Service endpoint for the daily social broadcast cron (X-Reading-Cron-Key)."""
    return await run_social_broadcast(trigger="cron")


@public_router.get("/card/{token}.png")
def get_social_card(token: str, db: Session = Depends(get_db)):
    card = db.query(SocialCard).filter(SocialCard.token == token).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    return Response(
        content=card.png,
        media_type=card.content_type or "image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )
