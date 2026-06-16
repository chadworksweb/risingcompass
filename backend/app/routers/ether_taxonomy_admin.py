"""Admin Taxonomy Editor API (Phase 1) -- edit the Ether theme/topic hierarchy
from RC admin without a redeploy.

Presentation-authoritative: the DB owns the theme list, labels, each topic's
primary theme, secondary facets, and ordering. The live song tagger is NOT
touched here -- its topic slug set + scope/examples stay code-driven in
services/ether_taxonomy.py. Every write busts the resolver cache so edits
propagate within seconds.

Server-side validation mirrors the import-time asserts in ether_taxonomy.py:
  - slugs are kebab-case and unique
  - every topic has exactly ONE primary theme, and it must exist
  - secondary themes must exist and must not equal the primary
  - a theme cannot be deleted while any topic uses it as primary
  - topic-slug rename is DISABLED (label-only) -- songs.topics JSON stores
    slugs, so a rename orphans history (a Phase 2 alias-migration concern).
    Theme-slug rename IS allowed (referencing topic rows are updated in the
    same transaction).

Cookie auth + site-admin only (kept OUT of API_ADMIN_SECTIONS). See
RISING-COMPASS-TAXONOMY-EDITOR-SCOPE.md.
"""

import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import EtherTheme, EtherTopic
from app.routers.admin import verify_admin_key
from app.services.ether_taxonomy import bust_taxonomy_cache
from app.services.feature_flags import is_taxonomy_db_driven, set_taxonomy_db_driven

router = APIRouter(prefix="/api/admin/taxonomy", tags=["taxonomy-admin"])

_KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


# --- Schemas ---------------------------------------------------------------

class ThemeIn(BaseModel):
    slug: str = Field(..., min_length=1, max_length=64)
    label: str = Field(..., min_length=1, max_length=120)
    sort_order: Optional[int] = None


class ThemePatch(BaseModel):
    slug: Optional[str] = Field(None, min_length=1, max_length=64)  # rename allowed
    label: Optional[str] = Field(None, min_length=1, max_length=120)
    sort_order: Optional[int] = None


class ExampleIn(BaseModel):
    artist: str = Field("", max_length=200)
    title: str = Field("", max_length=300)
    why: str = Field("", max_length=400)


class TopicIn(BaseModel):
    slug: str = Field(..., min_length=1, max_length=64)
    label: str = Field(..., min_length=1, max_length=120)
    primary_theme: str = Field(..., min_length=1, max_length=64)
    secondary: list[str] = Field(default_factory=list)
    sort_order: Optional[int] = None
    # Phase 2a: the tagger definition. Optional -- a topic with no scope is
    # "DB-only" and the tagger cannot use it.
    scope: Optional[str] = Field(None, max_length=2000)
    examples: Optional[list[ExampleIn]] = None


class TopicPatch(BaseModel):
    # NB: no slug field -- topic-slug rename is disabled in Phase 1.
    label: Optional[str] = Field(None, min_length=1, max_length=120)
    primary_theme: Optional[str] = Field(None, min_length=1, max_length=64)
    secondary: Optional[list[str]] = None
    sort_order: Optional[int] = None
    scope: Optional[str] = Field(None, max_length=2000)
    examples: Optional[list[ExampleIn]] = None


class ReorderIn(BaseModel):
    kind: str = Field(..., description="themes | topics")
    order: list[str] = Field(..., min_length=1, description="slugs in the desired order")


class TaggerSourceIn(BaseModel):
    enabled: bool = Field(..., description="true = tagger reads the DB definitions; false = code")


# --- Helpers ---------------------------------------------------------------

def _kebab_or_400(slug: str) -> str:
    slug = (slug or "").strip()
    if not _KEBAB_RE.match(slug):
        raise HTTPException(400, f"Slug must be kebab-case (a-z, 0-9, hyphens): {slug!r}")
    return slug


def _theme_slugs(db: Session) -> set[str]:
    return {t.slug for t in db.query(EtherTheme.slug).all()}


def _validate_secondary(secondary: list[str], primary: str, theme_slugs: set[str]) -> list[str]:
    out: list[str] = []
    for s in secondary or []:
        s = (s or "").strip()
        if not s:
            continue
        if s not in theme_slugs:
            raise HTTPException(400, f"Secondary theme '{s}' does not exist.")
        if s == primary:
            raise HTTPException(400, f"Secondary theme '{s}' duplicates the primary theme.")
        if s not in out:
            out.append(s)
    return out


def _theme_dict(t: EtherTheme) -> dict:
    return {"slug": t.slug, "label": t.label, "sort_order": t.sort_order}


def _clean_examples(examples) -> list[dict]:
    """Normalize a list of ExampleIn (or dicts) to stored {artist,title,why}
    dicts, dropping fully-empty rows."""
    out: list[dict] = []
    for ex in examples or []:
        if hasattr(ex, "model_dump"):
            ex = ex.model_dump()
        a = (ex.get("artist") or "").strip()
        t = (ex.get("title") or "").strip()
        w = (ex.get("why") or "").strip()
        if a or t or w:
            out.append({"artist": a, "title": t, "why": w})
    return out


def _topic_dict(t: EtherTopic) -> dict:
    scope = (t.scope or "").strip()
    return {
        "slug": t.slug,
        "label": t.label,
        "primary_theme": t.primary_theme_slug,
        "secondary": list(t.secondary_themes or []),
        "sort_order": t.sort_order,
        "scope": t.scope or "",
        "examples": list(t.examples or []),
        # A topic is taggable only once it carries a definition (scope).
        "has_definition": bool(scope),
    }


def _next_sort(db: Session, model) -> int:
    rows = db.query(model.sort_order).all()
    return (max((r[0] for r in rows), default=-1)) + 1


# --- Read ------------------------------------------------------------------

@router.get("", dependencies=[Depends(verify_admin_key)])
def get_taxonomy(db: Session = Depends(get_db)):
    """Editor payload: ordered themes + ordered topics (with primary theme,
    secondary facets, order, and a has_definition flag), plus topics grouped
    under their primary theme for the grouped view."""
    themes = (
        db.query(EtherTheme)
        .order_by(EtherTheme.sort_order.asc(), EtherTheme.id.asc())
        .all()
    )
    topics = (
        db.query(EtherTopic)
        .order_by(EtherTopic.sort_order.asc(), EtherTopic.id.asc())
        .all()
    )
    theme_dicts = [_theme_dict(t) for t in themes]
    topic_dicts = [_topic_dict(t) for t in topics]

    groups = []
    for th in theme_dicts:
        groups.append({
            "theme": th,
            "topics": [tp for tp in topic_dicts if tp["primary_theme"] == th["slug"]],
        })
    # Orphans: topics whose primary theme is missing (should not happen, but
    # surface rather than hide).
    known = {th["slug"] for th in theme_dicts}
    orphans = [tp for tp in topic_dicts if tp["primary_theme"] not in known]

    return {
        "themes": theme_dicts,
        "topics": topic_dicts,
        "groups": groups,
        "orphans": orphans,
        # Phase 2a: is the live tagger reading these DB definitions, or code?
        "tagger_db_driven": is_taxonomy_db_driven(db),
    }


@router.post("/tagger-source", dependencies=[Depends(verify_admin_key)])
def set_tagger_source(data: TaggerSourceIn, db: Session = Depends(get_db)):
    """Flip whether the live ether tagger reads the DB definitions (scope/examples)
    or the code constants. Fail-closed default is code. Busts the resolver cache
    so the change propagates within seconds."""
    set_taxonomy_db_driven(db, data.enabled)
    bust_taxonomy_cache()
    return {"tagger_db_driven": is_taxonomy_db_driven(db)}


# --- Theme CRUD ------------------------------------------------------------

@router.post("/themes", dependencies=[Depends(verify_admin_key)])
def create_theme(data: ThemeIn, db: Session = Depends(get_db)):
    slug = _kebab_or_400(data.slug)
    if db.query(EtherTheme).filter(EtherTheme.slug == slug).first():
        raise HTTPException(409, f"Theme '{slug}' already exists.")
    theme = EtherTheme(
        slug=slug,
        label=data.label.strip(),
        sort_order=data.sort_order if data.sort_order is not None else _next_sort(db, EtherTheme),
    )
    db.add(theme)
    db.commit()
    bust_taxonomy_cache()
    return _theme_dict(theme)


@router.patch("/themes/{slug}", dependencies=[Depends(verify_admin_key)])
def update_theme(slug: str, data: ThemePatch, db: Session = Depends(get_db)):
    theme = db.query(EtherTheme).filter(EtherTheme.slug == slug).first()
    if not theme:
        raise HTTPException(404, "Theme not found")

    # Theme-slug rename is safe: only the topic rows we control reference it.
    # Update every referencing topic (primary + secondary) in the same txn.
    if data.slug is not None and data.slug.strip() != theme.slug:
        new_slug = _kebab_or_400(data.slug)
        if db.query(EtherTheme).filter(EtherTheme.slug == new_slug).first():
            raise HTTPException(409, f"Theme '{new_slug}' already exists.")
        old_slug = theme.slug
        theme.slug = new_slug
        for tp in db.query(EtherTopic).all():
            changed = False
            if tp.primary_theme_slug == old_slug:
                tp.primary_theme_slug = new_slug
                changed = True
            sec = list(tp.secondary_themes or [])
            if old_slug in sec:
                tp.secondary_themes = [new_slug if s == old_slug else s for s in sec]
                changed = True
            if changed:
                # Re-assign so SQLAlchemy flags the JSON column as dirty.
                tp.secondary_themes = list(tp.secondary_themes or [])

    if data.label is not None:
        theme.label = data.label.strip()
    if data.sort_order is not None:
        theme.sort_order = data.sort_order

    db.commit()
    bust_taxonomy_cache()
    return _theme_dict(theme)


@router.delete("/themes/{slug}", dependencies=[Depends(verify_admin_key)])
def delete_theme(slug: str, db: Session = Depends(get_db)):
    theme = db.query(EtherTheme).filter(EtherTheme.slug == slug).first()
    if not theme:
        raise HTTPException(404, "Theme not found")
    users = (
        db.query(EtherTopic)
        .filter(EtherTopic.primary_theme_slug == slug)
        .all()
    )
    if users:
        names = ", ".join(t.slug for t in users[:8])
        raise HTTPException(
            409,
            f"Cannot delete theme '{slug}': {len(users)} topic(s) use it as primary "
            f"({names}). Reassign them first.",
        )
    # Strip it from any secondary facet lists too, so no dangling reference.
    for tp in db.query(EtherTopic).all():
        sec = list(tp.secondary_themes or [])
        if slug in sec:
            tp.secondary_themes = [s for s in sec if s != slug]
    db.delete(theme)
    db.commit()
    bust_taxonomy_cache()
    return {"deleted": slug}


# --- Topic CRUD ------------------------------------------------------------

@router.post("/topics", dependencies=[Depends(verify_admin_key)])
def create_topic(data: TopicIn, db: Session = Depends(get_db)):
    slug = _kebab_or_400(data.slug)
    if db.query(EtherTopic).filter(EtherTopic.slug == slug).first():
        raise HTTPException(409, f"Topic '{slug}' already exists.")
    theme_slugs = _theme_slugs(db)
    primary = data.primary_theme.strip()
    if primary not in theme_slugs:
        raise HTTPException(400, f"Primary theme '{primary}' does not exist.")
    secondary = _validate_secondary(data.secondary, primary, theme_slugs)
    scope = data.scope.strip() if data.scope is not None else None
    topic = EtherTopic(
        slug=slug,
        label=data.label.strip(),
        primary_theme_slug=primary,
        secondary_themes=secondary,
        sort_order=data.sort_order if data.sort_order is not None else _next_sort(db, EtherTopic),
        scope=scope or None,
        examples=_clean_examples(data.examples) if data.examples is not None else [],
    )
    db.add(topic)
    db.commit()
    bust_taxonomy_cache()
    return _topic_dict(topic)


@router.patch("/topics/{slug}", dependencies=[Depends(verify_admin_key)])
def update_topic(slug: str, data: TopicPatch, db: Session = Depends(get_db)):
    """Edit a topic's label, primary theme, secondary facets, and order. The
    topic SLUG is immutable in Phase 1 (songs.topics JSON stores it)."""
    topic = db.query(EtherTopic).filter(EtherTopic.slug == slug).first()
    if not topic:
        raise HTTPException(404, "Topic not found")
    theme_slugs = _theme_slugs(db)

    # Resolve the primary first so secondary validation can compare against it.
    primary = topic.primary_theme_slug
    if data.primary_theme is not None:
        primary = data.primary_theme.strip()
        if primary not in theme_slugs:
            raise HTTPException(400, f"Primary theme '{primary}' does not exist.")
        topic.primary_theme_slug = primary

    if data.secondary is not None:
        topic.secondary_themes = _validate_secondary(data.secondary, primary, theme_slugs)
    elif data.primary_theme is not None:
        # Primary changed but secondary not supplied -- drop any now-conflicting
        # secondary that equals the new primary.
        topic.secondary_themes = [s for s in (topic.secondary_themes or []) if s != primary]

    if data.label is not None:
        topic.label = data.label.strip()
    if data.sort_order is not None:
        topic.sort_order = data.sort_order
    if data.scope is not None:
        topic.scope = data.scope.strip() or None
    if data.examples is not None:
        topic.examples = _clean_examples(data.examples)

    db.commit()
    bust_taxonomy_cache()
    return _topic_dict(topic)


@router.delete("/topics/{slug}", dependencies=[Depends(verify_admin_key)])
def delete_topic(slug: str, db: Session = Depends(get_db)):
    topic = db.query(EtherTopic).filter(EtherTopic.slug == slug).first()
    if not topic:
        raise HTTPException(404, "Topic not found")
    db.delete(topic)
    db.commit()
    bust_taxonomy_cache()
    return {"deleted": slug}


# --- Reorder ---------------------------------------------------------------

@router.post("/reorder", dependencies=[Depends(verify_admin_key)])
def reorder(data: ReorderIn, db: Session = Depends(get_db)):
    """Bulk sort_order for themes or topics. `order` is the full list of slugs
    in the desired order; each row's sort_order is set to its index."""
    if data.kind == "themes":
        model = EtherTheme
    elif data.kind == "topics":
        model = EtherTopic
    else:
        raise HTTPException(400, "kind must be 'themes' or 'topics'")

    by_slug = {r.slug: r for r in db.query(model).all()}
    for i, slug in enumerate(data.order):
        row = by_slug.get(slug)
        if row is not None:
            row.sort_order = i
    db.commit()
    bust_taxonomy_cache()
    return {"reordered": data.kind, "count": len(data.order)}
