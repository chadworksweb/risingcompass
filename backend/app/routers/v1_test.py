"""V1-frozen control — admin-only endpoint that scores lyrics against
the v1 rubric pinned at commit a839df9.

Rows land in v1_tests, isolated from every other calibration store.
Powers the "V1 Test" admin tab.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import V1Test
from app.routers.admin import verify_admin_key
from app.v1_frozen import RUBRIC_COMMIT
from app.v1_frozen.calibrator import calibrate_song as v1_calibrate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/v1-test", tags=["v1-test"])


class V1TestRunIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    artist: str = Field(..., min_length=1, max_length=300)
    lyrics: str = Field(..., min_length=1)


class V1TestOut(BaseModel):
    id: int
    title: str
    artist: str
    rubric_color: str | None
    charge_value: int | None
    contaminated: bool
    contamination_note: str | None
    charge_summary: str | None
    confidence: float | None
    rubric_commit: str
    error: str | None
    created_at: str

    @classmethod
    def from_row(cls, row: V1Test) -> "V1TestOut":
        return cls(
            id=row.id,
            title=row.title,
            artist=row.artist,
            rubric_color=row.rubric_color,
            charge_value=row.charge_value,
            contaminated=bool(row.contaminated) if row.contaminated is not None else False,
            contamination_note=row.contamination_note,
            charge_summary=row.charge_summary,
            confidence=row.confidence,
            rubric_commit=row.rubric_commit,
            error=row.error,
            created_at=row.created_at.isoformat() if row.created_at else "",
        )


@router.post("/run", response_model=V1TestOut, dependencies=[Depends(verify_admin_key)])
def run_v1_test(body: V1TestRunIn, db: Session = Depends(get_db)):
    """Calibrate the supplied lyrics against the v1-frozen rubric and store
    the result in v1_tests. No reads from or writes to any other table.
    """
    title = body.title.strip()
    artist = body.artist.strip()

    result: dict = {}
    error: str | None = None
    try:
        # Pass the live DB so v1's build_few_shot_examples can prime the
        # prompt with tier examples from CompassSong — the mechanism v1
        # used in production. skip_cache=True prevents lookup_calibrated
        # from short-circuiting and returning a v2-calibrated value.
        result = v1_calibrate(title, artist, lyrics=body.lyrics, db=db, skip_cache=True)
    except Exception as e:
        logger.exception("v1 calibration failed for '%s' by %s", title, artist)
        error = f"{type(e).__name__}: {e}"

    if not error and result.get("rubric_color") is None:
        error = result.get("charge_summary") or "v1 calibrator returned no color"

    row = V1Test(
        title=title,
        artist=artist,
        rubric_color=result.get("rubric_color"),
        charge_value=result.get("charge_value"),
        contaminated=bool(result.get("contaminated", False)),
        contamination_note=result.get("contamination_note"),
        charge_summary=result.get("charge_summary"),
        confidence=result.get("confidence"),
        rubric_commit=RUBRIC_COMMIT,
        error=error,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return V1TestOut.from_row(row)


@router.get("", response_model=list[V1TestOut], dependencies=[Depends(verify_admin_key)])
def list_v1_tests(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Recent v1 control runs, newest first."""
    rows = db.query(V1Test).order_by(V1Test.id.desc()).limit(limit).all()
    return [V1TestOut.from_row(r) for r in rows]
