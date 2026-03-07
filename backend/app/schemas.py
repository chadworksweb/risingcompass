import json
from typing import Optional

from pydantic import BaseModel, Field, computed_field
import datetime

from app.services.charge_calc import degree_to_score as _degree_to_score


# --- Songs ---
class CompassSongOut(BaseModel):
    id: int
    title: str
    artist: str
    year: int
    decade: str
    chart_position: int
    rubric_color: str
    charge_value: Optional[int] = None
    contaminated: bool
    contamination_note: Optional[str] = None
    charge_summary: Optional[str] = None
    instrumental: bool = False

    model_config = {"from_attributes": True}


# --- Reading Songs ---
class ReadingSongOut(BaseModel):
    id: int
    title: str
    artist: str
    position: int
    rubric_color: Optional[str] = None
    charge_value: Optional[int] = None
    contaminated: bool = False
    contamination_note: Optional[str] = None
    charge_summary: Optional[str] = None
    chart_source: Optional[str] = None
    instrumental: bool = False

    model_config = {"from_attributes": True}


class ReadingSongIn(BaseModel):
    title: str = Field(..., max_length=300)
    artist: str = Field(..., max_length=300)
    position: int
    rubric_color: str = Field(..., max_length=20)
    charge_value: Optional[int] = None
    contaminated: bool = False
    contamination_note: Optional[str] = Field(None, max_length=1000)
    charge_summary: Optional[str] = Field(None, max_length=2000)
    chart_source: str = Field("spotify", max_length=50)


# --- Daily Readings ---
class DailyReadingOut(BaseModel):
    id: int
    date: datetime.date
    compass_degree: float
    charge_level: str
    contamination_count: int
    editorial_summary: Optional[str] = None
    songs: list[ReadingSongOut] = []

    @computed_field
    @property
    def charge_score(self) -> int:
        return _degree_to_score(self.compass_degree)

    model_config = {"from_attributes": True}


class DailyReadingSummary(BaseModel):
    id: int
    date: datetime.date
    compass_degree: float
    charge_level: str
    contamination_count: int
    editorial_summary: Optional[str] = None

    @computed_field
    @property
    def charge_score(self) -> int:
        return _degree_to_score(self.compass_degree)

    model_config = {"from_attributes": True}


class ReadingCreate(BaseModel):
    date: datetime.date
    editorial_summary: Optional[str] = None
    songs: list[ReadingSongIn]


class ReadingUpdate(BaseModel):
    editorial_summary: Optional[str] = None
    songs: Optional[list[ReadingSongIn]] = None


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
    albums: list[WeeklyAlbumEntryOut] = []

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
    albums: list[WeeklyAlbumEntryIn]


class WeeklyAlbumReadingUpdate(BaseModel):
    editorial_summary: Optional[str] = None
    albums: Optional[list[WeeklyAlbumEntryIn]] = None


# --- Compass Current ---
class CompassCurrent(BaseModel):
    has_reading: bool
    date: Optional[datetime.date] = None
    compass_degree: float
    charge_level: str
    contamination_count: int
    editorial_summary: Optional[str] = None
    songs: list[ReadingSongOut] = []
    historical_degree: float
    historical_charge: str
    # Weekly album reading (if any)
    has_album_reading: bool = False
    album_week_date: Optional[datetime.date] = None
    album_compass_degree: Optional[float] = None
    album_charge_level: Optional[str] = None
    album_contamination_count: int = 0
    album_editorial_summary: Optional[str] = None
    album_entries: list[WeeklyAlbumEntryOut] = []

    @computed_field
    @property
    def charge_score(self) -> int:
        return _degree_to_score(self.compass_degree)

    @computed_field
    @property
    def historical_score(self) -> int:
        return _degree_to_score(self.historical_degree)

    @computed_field
    @property
    def album_charge_score(self) -> Optional[int]:
        if self.album_compass_degree is None:
            return None
        return _degree_to_score(self.album_compass_degree)


# --- Drift (decade aggregates) ---
class DecadeAggregate(BaseModel):
    decade: str
    compass_degree: float
    charge_level: str
    chart_song_count: int
    total_song_count: int
    contamination_count: int
    color_counts: dict[str, int] = {}

    @computed_field
    @property
    def charge_score(self) -> int:
        return _degree_to_score(self.compass_degree)


class YearAggregate(BaseModel):
    year: int
    compass_degree: float
    charge_level: str
    chart_song_count: int
    total_song_count: int

    @computed_field
    @property
    def charge_score(self) -> int:
        return _degree_to_score(self.compass_degree)


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
    tracks: list[AlbumTrackOut] = []

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
    items: list[DailyReadingSummary]
    total: int
    page: int
    pages: int


class PaginatedWeeklyAlbumReadings(BaseModel):
    items: list[WeeklyAlbumReadingSummary]
    total: int
    page: int
    pages: int


# --- Library (Collections + Recommendations) ---
class CollectionCreate(BaseModel):
    name: str
    slug: str
    description: Optional[str] = None
    charge_colors: Optional[str] = None
    is_active: bool = True
    sort_order: int = 0


class CollectionUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    description: Optional[str] = None
    charge_colors: Optional[str] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None


class CollectionOut(BaseModel):
    id: int
    name: str
    slug: str
    description: Optional[str] = None
    charge_colors: Optional[str] = None
    is_active: bool
    sort_order: int

    model_config = {"from_attributes": True}


class RecommendationCreate(BaseModel):
    collection_id: int
    title: str
    artist: str
    item_type: str = "song"
    rubric_color: str
    editorial_note: Optional[str] = None
    external_url: Optional[str] = None
    cover_art_url: Optional[str] = None
    sort_order: int = 0


class RecommendationUpdate(BaseModel):
    collection_id: Optional[int] = None
    title: Optional[str] = None
    artist: Optional[str] = None
    item_type: Optional[str] = None
    rubric_color: Optional[str] = None
    editorial_note: Optional[str] = None
    external_url: Optional[str] = None
    cover_art_url: Optional[str] = None
    sort_order: Optional[int] = None


class RecommendationOut(BaseModel):
    id: int
    collection_id: int
    title: str
    artist: str
    item_type: str
    rubric_color: str
    editorial_note: Optional[str] = None
    external_url: Optional[str] = None
    cover_art_url: Optional[str] = None
    sort_order: int

    model_config = {"from_attributes": True}


class LibraryItemOut(BaseModel):
    title: str
    artist: str
    rubric_color: str
    source: str  # "recommendation", "song", "album_deep_dive"
    item_type: str = "song"
    editorial_note: Optional[str] = None
    external_url: Optional[str] = None
    cover_art_url: Optional[str] = None


class LibraryCollectionOut(BaseModel):
    id: int
    name: str
    slug: str
    description: Optional[str] = None
    items: list[LibraryItemOut] = []


# --- Library Songs (non-chart archive) ---
class LibrarySongCreate(BaseModel):
    title: str
    artist: str
    rubric_color: str
    charge_value: Optional[int] = None
    contaminated: bool = False
    contamination_note: Optional[str] = None
    charge_summary: Optional[str] = None
    album_id: Optional[int] = None
    track_number: Optional[int] = None
    source: str = "manual"


class LibrarySongUpdate(BaseModel):
    title: Optional[str] = None
    artist: Optional[str] = None
    rubric_color: Optional[str] = None
    charge_value: Optional[int] = None
    contaminated: Optional[bool] = None
    contamination_note: Optional[str] = None
    charge_summary: Optional[str] = None
    album_id: Optional[int] = None
    track_number: Optional[int] = None


class LibrarySongOut(BaseModel):
    id: int
    title: str
    artist: str
    rubric_color: str
    charge_value: Optional[int] = None
    contaminated: bool
    contamination_note: Optional[str] = None
    charge_summary: Optional[str] = None
    album_id: Optional[int] = None
    track_number: Optional[int] = None
    source: str
    created_at: Optional[datetime.datetime] = None

    model_config = {"from_attributes": True}


# --- Manual Song Feed ---
class CompassSongFeedIn(BaseModel):
    title: str = Field(..., max_length=300)
    artist: str = Field(..., max_length=300)
    rubric_color: str = Field(..., max_length=20)
    charge_value: int  # -100 to +100
    contaminated: bool = False
    contamination_note: Optional[str] = Field(None, max_length=1000)
    charge_summary: Optional[str] = Field(None, max_length=2000)
    year: Optional[int] = None
    chart_source: str = Field("manual", max_length=50)


# --- Agent Drafts ---
class DraftSongOut(BaseModel):
    id: int
    compass_song_id: Optional[int] = None
    title: str
    artist: str
    position: int
    rubric_color: Optional[str] = None
    charge_value: Optional[int] = None
    contaminated: bool = False
    contamination_note: Optional[str] = None
    charge_summary: Optional[str] = None
    chart_source: Optional[str] = None
    confidence: Optional[float] = None
    lyrics_available: bool = False

    model_config = {"from_attributes": True}


class DraftOut(BaseModel):
    id: int
    label: Optional[str] = None
    draft_type: Optional[str] = "daily"
    created_at: datetime.datetime
    status: str
    date: datetime.date
    compass_degree: Optional[float] = None
    charge_level: Optional[str] = None
    contamination_count: int = 0
    editorial_summary: Optional[str] = None
    agent_model: Optional[str] = None
    agent_notes: Optional[str] = None
    agent_warnings: Optional[str] = None
    songs: list[DraftSongOut] = []

    @computed_field
    @property
    def charge_score(self) -> Optional[int]:
        if self.compass_degree is None:
            return None
        return _degree_to_score(self.compass_degree)

    @computed_field
    @property
    def warnings(self) -> list[str]:
        if not self.agent_warnings:
            return []
        try:
            return json.loads(self.agent_warnings)
        except (json.JSONDecodeError, TypeError):
            return []

    model_config = {"from_attributes": True}


class DraftSummary(BaseModel):
    id: int
    label: Optional[str] = None
    draft_type: Optional[str] = "daily"
    created_at: datetime.datetime
    status: str
    date: datetime.date
    compass_degree: Optional[float] = None
    charge_level: Optional[str] = None
    contamination_count: int = 0
    editorial_summary: Optional[str] = None

    @computed_field
    @property
    def charge_score(self) -> Optional[int]:
        if self.compass_degree is None:
            return None
        return _degree_to_score(self.compass_degree)

    model_config = {"from_attributes": True}


class SupplyLyricsIn(BaseModel):
    lyrics: str = Field(..., min_length=50)


class DraftTriggerSongIn(BaseModel):
    title: str = Field(..., max_length=300)
    artist: str = Field(..., max_length=300)
    position: int
    chart_source: str = Field("spotify", max_length=50)
    lyrics: Optional[str] = None  # Manual lyrics — skips fetch when provided


class DraftTriggerIn(BaseModel):
    songs: list[DraftTriggerSongIn]
    date: Optional[datetime.date] = None
    draft_only: bool = False  # True = case study mode, skip compass_songs table


class DraftSongUpdate(BaseModel):
    rubric_color: Optional[str] = None
    charge_value: Optional[int] = None
    contaminated: Optional[bool] = None
    contamination_note: Optional[str] = None
    charge_summary: Optional[str] = None


class DraftUpdate(BaseModel):
    editorial_summary: Optional[str] = None
    songs: Optional[list[DraftSongUpdate]] = None


class PaginatedDrafts(BaseModel):
    items: list[DraftSummary]
    total: int
    page: int
    pages: int


# --- Misread Submissions ---
class MisreadSubmissionCreate(BaseModel):
    song_title: str = Field(..., max_length=300)
    song_artist: str = Field(..., max_length=300)
    song_color: str = Field(..., max_length=20)
    song_position: Optional[int] = None
    first_name: str = Field(..., max_length=100)
    last_name: str = Field(..., max_length=100)
    email: str = Field(..., max_length=254)
    message: str = Field(..., max_length=5000)
    device_id: Optional[str] = Field(None, max_length=200)


class MisreadSubmissionOut(BaseModel):
    id: int
    created_at: datetime.datetime
    song_title: str
    song_artist: str
    song_color: str
    song_position: Optional[int] = None
    first_name: str
    last_name: str
    email: str
    message: str
    device_id: Optional[str] = None
    ip_address: Optional[str] = None
    status: str

    model_config = {"from_attributes": True}


class MisreadStatusUpdate(BaseModel):
    status: str  # reviewed / accepted / rejected / flagged


class DailyChartPoint(BaseModel):
    date: datetime.date
    compass_degree: float
    charge_level: str

    @computed_field
    @property
    def charge_score(self) -> int:
        return _degree_to_score(self.compass_degree)

    model_config = {"from_attributes": True}


class MisreadBanOut(BaseModel):
    id: int
    created_at: datetime.datetime
    device_id: Optional[str] = None
    ip_address: Optional[str] = None
    reason: Optional[str] = None

    model_config = {"from_attributes": True}


# --- Backfill ---
class BackfillSongOut(BaseModel):
    id: int
    title: str
    artist: str
    year: int
    rubric_color: str
    charge_value: Optional[int] = None
    contaminated: bool = False
    contamination_note: Optional[str] = None
    charge_summary: Optional[str] = None
    confidence: Optional[float] = None
    lyrics_available: bool = False
    instrumental: bool = False

    model_config = {"from_attributes": True}


class BackfillResult(BaseModel):
    year: int
    total_songs: int
    reclassified: int
    skipped_calibrated: int
    songs: list[BackfillSongOut]


# --- Calibration ---
class CalibrateSongIn(BaseModel):
    id: int
    rubric_color: str
    charge_value: int  # -100 to +100
    charge_summary: Optional[str] = None
    contaminated: Optional[bool] = None
    contamination_note: Optional[str] = None

class CalibrateRequest(BaseModel):
    songs: list[CalibrateSongIn]

class CalibrateResult(BaseModel):
    calibrated: int
    songs: list[CompassSongOut]


# --- Music Frequency Analyzer ---
class AnalyzerSongIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    artist: str = Field(..., min_length=1, max_length=200)


class AnalyzerSessionCreate(BaseModel):
    songs: list[AnalyzerSongIn] = Field(..., min_length=1, max_length=10)
    weighted: bool = True


class AnalyzerSessionOut(BaseModel):
    session_id: str
    song_count: int
    stream_url: str
    expires_at: datetime.datetime


class AnalyzerSongResult(BaseModel):
    index: int
    title: str
    artist: str
    status: str  # "scored" | "no_lyrics" | "error"
    tier: Optional[str] = None  # color: "violet", "blue", etc.
    tier_label: Optional[str] = None  # "Ascended", "Elevated", etc.
    charge: Optional[int] = None  # -100 to +100
    contaminated: bool = False
    contamination_note: Optional[str] = None
    charge_summary: Optional[str] = None
    confidence: float = 0.0
    lyrics_found: bool = False


class AnalyzerAggregate(BaseModel):
    compass_degree: float
    charge_score: int
    charge_level: str  # color
    charge_label: str  # tier name
    tier_distribution: dict[str, int]
    contamination_count: int
    total_songs: int
    classified_songs: int
    unclassified_songs: int


class AnalyzerSessionStatus(BaseModel):
    session_id: str
    status: str  # "pending" | "processing" | "completed" | "error"
    total_songs: int
    completed_songs: int
    songs: list[AnalyzerSongResult] = []
    aggregate: Optional[AnalyzerAggregate] = None
    narrative: Optional[str] = None


class PlaylistResolveIn(BaseModel):
    spotify_url: str = Field(..., min_length=1)


class PlaylistTrackOut(BaseModel):
    title: str
    artist: str


class PlaylistResolveOut(BaseModel):
    playlist_name: str
    playlist_owner: str
    track_count: int
    tracks: list[PlaylistTrackOut]
