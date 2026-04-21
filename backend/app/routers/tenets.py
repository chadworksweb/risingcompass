from fastapi import APIRouter

from app.services.agents.rubric_builder import load_tenets

router = APIRouter(prefix="/api/tenets", tags=["tenets"])


@router.get("")
def get_tenets() -> dict:
    """Return the canonical tenets JSON — the public constitution of the Compass."""
    return load_tenets()
