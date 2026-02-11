from pydantic import BaseModel
from typing import List, Optional
import datetime


# --- Songs ---
class SongOut(BaseModel):
    id: int
    title: str
    artist: str
    year: int
    decade: str
    chart_position: int
    rubric_color: str
    contaminated: bool
    contamination_note: Optional[str] = None
    charge_summary: Optional[str] = None
    why_classification: Optional[str] = None

    model_config = {"from_attributes": True}


# --- Reading Songs ---
class ReadingSongOut(BaseModel):
    id: int
    title: str
    artist: str
    position: int
    rubric_color: str
    contaminated: bool
    contamination_note: Optional[str] = None
    charge_summary: Optional[str] = None
    chart_source: Optional[str] = None

    model_config = {"from_attributes": True}


class ReadingSongIn(BaseModel):
    title: str
    artist: str
    position: int
    rubric_color: str
    contaminated: bool = False
    contamination_note: Optional[str] = None
    charge_summary: Optional[str] = None
    chart_source: str = "spotify"


# --- Daily Readings ---
class DailyReadingOut(BaseModel):
    id: int
    date: datetime.date
    compass_degree: float
    charge_level: str
    contamination_count: int
    editorial_summary: Optional[str] = None
    songs: List[ReadingSongOut] = []

    model_config = {"from_attributes": True}


class DailyReadingSummary(BaseModel):
    id: int
    date: datetime.date
    compass_degree: float
    charge_level: str
    contamination_count: int
    editorial_summary: Optional[str] = None

    model_config = {"from_attributes": True}


class ReadingCreate(BaseModel):
    date: datetime.date
    editorial_summary: Optional[str] = None
    songs: List[ReadingSongIn]


class ReadingUpdate(BaseModel):
    editorial_summary: Optional[str] = None
    songs: Optional[List[ReadingSongIn]] = None


# --- Weekly Album Readings ---
class WeeklyAlbumEntryOut(BaseModel):
    id: int
    title: str
    artist: str
    position: int
    rubric_color: str
    contaminated: bool
    contamination_note: Optional[str] = None
    charge_summary: Optional[str] = None
    chart_source: Optional[str] = None

    model_config = {"from_attributes": True}


class WeeklyAlbumEntryIn(BaseModel):
    title: str
    artist: str
    position: int
    rubric_color: str
    contaminated: bool = False
    contamination_note: Optional[str] = None
    charge_summary: Optional[str] = None
    chart_source: str = "billboard_200"


class WeeklyAlbumReadingOut(BaseModel):
    id: int
    week_date: datetime.date
    compass_degree: float
    charge_level: str
    contamination_count: int
    editorial_summary: Optional[str] = None
    albums: List[WeeklyAlbumEntryOut] = []

    model_config = {"from_attributes": True}


class WeeklyAlbumReadingSummary(BaseModel):
    id: int
    week_date: datetime.date
    compass_degree: float
    charge_level: str
    contamination_count: int
    editorial_summary: Optional[str] = None

    model_config = {"from_attributes": True}


class WeeklyAlbumReadingCreate(BaseModel):
    week_date: datetime.date
    editorial_summary: Optional[str] = None
    albums: List[WeeklyAlbumEntryIn]


class WeeklyAlbumReadingUpdate(BaseModel):
    editorial_summary: Optional[str] = None
    albums: Optional[List[WeeklyAlbumEntryIn]] = None


# --- Compass Current ---
class CompassCurrent(BaseModel):
    has_reading: bool
    date: Optional[datetime.date] = None
    compass_degree: float
    charge_level: str
    contamination_count: int
    editorial_summary: Optional[str] = None
    songs: List[ReadingSongOut] = []
    historical_degree: float
    historical_charge: str
    # Weekly album reading (if any)
    has_album_reading: bool = False
    album_week_date: Optional[datetime.date] = None
    album_compass_degree: Optional[float] = None
    album_charge_level: Optional[str] = None
    album_contamination_count: int = 0
    album_editorial_summary: Optional[str] = None
    album_entries: List[WeeklyAlbumEntryOut] = []


# --- Drift (decade aggregates) ---
class DecadeAggregate(BaseModel):
    decade: str
    compass_degree: float
    charge_level: str
    song_count: int
    contamination_count: int
    color_counts: dict[str, int] = {}


class YearAggregate(BaseModel):
    year: int
    compass_degree: float
    charge_level: str
    song_count: int


# --- Albums ---
class AlbumTrackOut(BaseModel):
    track_number: int
    name: str
    charge_color: Optional[str] = None
    assessment: Optional[str] = None

    model_config = {"from_attributes": True}


class AlbumOut(BaseModel):
    id: int
    title: str
    artist: str
    slug: str
    release_year: Optional[int] = None
    overall_color: Optional[str] = None
    summary: Optional[str] = None
    tracks: List[AlbumTrackOut] = []

    model_config = {"from_attributes": True}


class AlbumSummary(BaseModel):
    id: int
    title: str
    artist: str
    slug: str
    release_year: Optional[int] = None
    overall_color: Optional[str] = None

    model_config = {"from_attributes": True}


# --- Paginated ---
class PaginatedReadings(BaseModel):
    items: List[DailyReadingSummary]
    total: int
    page: int
    pages: int


class PaginatedWeeklyAlbumReadings(BaseModel):
    items: List[WeeklyAlbumReadingSummary]
    total: int
    page: int
    pages: int
