from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.amendments import load_amendment_log

router = APIRouter(prefix="/api/amendments", tags=["amendments"])


@router.get("")
def get_amendments(db: Session = Depends(get_db)) -> dict:
    """Public amendment log -- proposed, deliberating, ratified, covered,
    rejected. The static framing (venue, thresholds, statuses) is read from
    log.json; the amendments array itself comes from the motions table
    (Phase 3.2). The Chamber (Phase 4) will add discussion_url and
    co-sponsor data; for now those fields are placeholders."""
    return load_amendment_log(db)
