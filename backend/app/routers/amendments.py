from fastapi import APIRouter

from app.services.amendments import load_amendment_log

router = APIRouter(prefix="/api/amendments", tags=["amendments"])


@router.get("")
def get_amendments() -> dict:
    """Return the public amendment log — proposed, deliberating, ratified,
    already-covered, and rejected entries. The deliberation itself lives in
    the off-site venue; this endpoint is the public record of what occurred."""
    return load_amendment_log()
