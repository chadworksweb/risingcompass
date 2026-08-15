"""Public topic pages API.

Read-only, derived entirely from what the tagger already wrote onto
`songs.topics` and from the taxonomy tables. No calibration, no tagging, and no
Anthropic call anywhere in this path.

Mounted under the public read dependency with the other bulk-content routers,
so it carries key auth and the scrape shield exactly like /api/songs.
"""

import logging

from fastapi import APIRouter, HTTPException, Query

from app.database import SessionLocal
from app.services import topic_pages

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/topics", tags=["topics"])
# Themes are the parent of topics, so they get their own prefix rather than
# hanging off /api/topics as a sub-resource.
themes_router = APIRouter(prefix="/api/themes", tags=["topics"])


@themes_router.get("/{slug}")
def theme_detail(slug: str):
    """One theme: its topics, the topics filed elsewhere that touch it, and a
    reading over the DISTINCT songs beneath it."""
    db = SessionLocal()
    try:
        data = topic_pages.theme_detail(db, slug)
        if data is None:
            raise HTTPException(404, "Theme not found")
        return data
    finally:
        db.close()


@router.get("")
@router.get("/")
def topics_index():
    """Every theme with its topics and their counts."""
    db = SessionLocal()
    try:
        return topic_pages.index(db)
    finally:
        db.close()


@router.get("/{slug}")
def topic_detail(
    slug: str,
    offset: int = Query(0, ge=0),
    dominant_only: bool = False,
):
    """One topic: its definition, its reading, its neighbours, and its songs.

    404s on a slug the taxonomy does not know, rather than rendering an empty
    page for a typo -- a topic that does not exist should not look like a
    subject nobody has written about.
    """
    db = SessionLocal()
    try:
        data = topic_pages.detail(db, slug, offset=offset, dominant_only=dominant_only)
        if data is None:
            raise HTTPException(404, "Topic not found")
        return data
    finally:
        db.close()
