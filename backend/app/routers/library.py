from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Collection, Recommendation, CompassSong, AlbumDeepDive
from app.schemas import (
    CollectionCreate, CollectionUpdate, CollectionOut,
    RecommendationCreate, RecommendationUpdate, RecommendationOut,
    LibraryItemOut, LibraryCollectionOut,
)
from app.routers.admin import verify_admin_key

router = APIRouter(tags=["library"])


# --- Public endpoints ---

@router.get("/api/library", response_model=list[LibraryCollectionOut])
def get_library(db: Session = Depends(get_db)):
    """All active collections with merged items (manual + auto-aggregated)."""
    collections = (
        db.query(Collection)
        .filter(Collection.is_active == True)
        .order_by(Collection.sort_order, Collection.id)
        .all()
    )

    result = []
    for col in collections:
        items = _build_collection_items(col, db)
        result.append(LibraryCollectionOut(
            id=col.id,
            name=col.name,
            slug=col.slug,
            description=col.description,
            items=items,
        ))
    return result


@router.get("/api/library/{slug}", response_model=LibraryCollectionOut)
def get_library_collection(slug: str, db: Session = Depends(get_db)):
    """Single collection detail with merged items."""
    col = db.query(Collection).filter(Collection.slug == slug, Collection.is_active == True).first()
    if not col:
        raise HTTPException(status_code=404, detail="Collection not found")

    items = _build_collection_items(col, db)
    return LibraryCollectionOut(
        id=col.id,
        name=col.name,
        slug=col.slug,
        description=col.description,
        items=items,
    )


def _build_collection_items(col: Collection, db: Session) -> list[LibraryItemOut]:
    """Merge manual recommendations + auto-aggregated items for a collection."""
    items: list[LibraryItemOut] = []

    # 1. Manual recommendations first (by sort_order)
    recs = sorted(col.recommendations, key=lambda r: r.sort_order)
    for rec in recs:
        items.append(LibraryItemOut(
            title=rec.title,
            artist=rec.artist,
            rubric_color=rec.rubric_color,
            source="recommendation",
            item_type=rec.item_type or "song",
            editorial_note=rec.editorial_note,
            external_url=rec.external_url,
            cover_art_url=rec.cover_art_url,
        ))

    # 2. Auto-aggregated items from charge_colors
    if not col.charge_colors:
        return items

    colors = [c.strip() for c in col.charge_colors.split(",") if c.strip()]
    if not colors:
        return items

    # Collect existing manual titles for dedup
    existing = {(item.title.lower(), item.artist.lower()) for item in items}

    # Songs — deduplicated by title+artist (historical Billboard #1 hits)
    songs = (
        db.query(CompassSong)
        .filter(CompassSong.rubric_color.in_(colors))
        .order_by(CompassSong.title)
        .all()
    )
    for s in songs:
        key = (s.title.lower(), s.artist.lower())
        if key not in existing:
            existing.add(key)
            items.append(LibraryItemOut(
                title=s.title,
                artist=s.artist,
                rubric_color=s.rubric_color,
                source="song",
                item_type="song",
            ))

    # AlbumDeepDives — by overall_color
    albums = (
        db.query(AlbumDeepDive)
        .filter(AlbumDeepDive.overall_color.in_(colors))
        .order_by(AlbumDeepDive.title)
        .all()
    )
    for a in albums:
        key = (a.title.lower(), a.artist.lower())
        if key not in existing:
            existing.add(key)
            items.append(LibraryItemOut(
                title=a.title,
                artist=a.artist,
                rubric_color=a.overall_color,
                source="album_deep_dive",
                item_type="album",
            ))

    return items


# --- Admin endpoints ---

@router.get("/api/admin/collections", response_model=list[CollectionOut], dependencies=[Depends(verify_admin_key)])
def list_collections(db: Session = Depends(get_db)):
    """All collections (including inactive) for admin management."""
    return db.query(Collection).order_by(Collection.sort_order, Collection.id).all()


@router.get("/api/admin/collections/{collection_id}/recommendations", response_model=list[RecommendationOut], dependencies=[Depends(verify_admin_key)])
def list_recommendations(collection_id: int, db: Session = Depends(get_db)):
    """All recommendations for a given collection."""
    col = db.query(Collection).filter(Collection.id == collection_id).first()
    if not col:
        raise HTTPException(status_code=404, detail="Collection not found")
    return (
        db.query(Recommendation)
        .filter(Recommendation.collection_id == collection_id)
        .order_by(Recommendation.sort_order, Recommendation.id)
        .all()
    )


@router.post("/api/admin/collections", response_model=CollectionOut, dependencies=[Depends(verify_admin_key)])
def create_collection(data: CollectionCreate, db: Session = Depends(get_db)):
    existing = db.query(Collection).filter(Collection.slug == data.slug).first()
    if existing:
        raise HTTPException(status_code=409, detail="Collection with this slug already exists")
    col = Collection(**data.model_dump())
    db.add(col)
    db.commit()
    db.refresh(col)
    return col


@router.put("/api/admin/collections/{collection_id}", response_model=CollectionOut, dependencies=[Depends(verify_admin_key)])
def update_collection(collection_id: int, data: CollectionUpdate, db: Session = Depends(get_db)):
    col = db.query(Collection).filter(Collection.id == collection_id).first()
    if not col:
        raise HTTPException(status_code=404, detail="Collection not found")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(col, field, value)
    db.commit()
    db.refresh(col)
    return col


@router.delete("/api/admin/collections/{collection_id}", dependencies=[Depends(verify_admin_key)])
def delete_collection(collection_id: int, db: Session = Depends(get_db)):
    col = db.query(Collection).filter(Collection.id == collection_id).first()
    if not col:
        raise HTTPException(status_code=404, detail="Collection not found")
    db.delete(col)
    db.commit()
    return {"detail": "Deleted"}


@router.post("/api/admin/recommendations", response_model=RecommendationOut, dependencies=[Depends(verify_admin_key)])
def create_recommendation(data: RecommendationCreate, db: Session = Depends(get_db)):
    col = db.query(Collection).filter(Collection.id == data.collection_id).first()
    if not col:
        raise HTTPException(status_code=404, detail="Collection not found")
    rec = Recommendation(**data.model_dump())
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return rec


@router.put("/api/admin/recommendations/{rec_id}", response_model=RecommendationOut, dependencies=[Depends(verify_admin_key)])
def update_recommendation(rec_id: int, data: RecommendationUpdate, db: Session = Depends(get_db)):
    rec = db.query(Recommendation).filter(Recommendation.id == rec_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(rec, field, value)
    db.commit()
    db.refresh(rec)
    return rec


@router.delete("/api/admin/recommendations/{rec_id}", dependencies=[Depends(verify_admin_key)])
def delete_recommendation(rec_id: int, db: Session = Depends(get_db)):
    rec = db.query(Recommendation).filter(Recommendation.id == rec_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    db.delete(rec)
    db.commit()
    return {"detail": "Deleted"}
