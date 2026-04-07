from sqlalchemy import (
    CheckConstraint, Column, Integer, String, Text, Float, Boolean, Date, DateTime, ForeignKey, UniqueConstraint
)
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base


class CompassSong(Base):
    __tablename__ = "compass_songs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(Text, nullable=False)
    artist = Column(Text, nullable=False)
    year = Column(Integer, nullable=False)
    decade = Column(Text, nullable=False)
    chart_position = Column(Integer, nullable=False)
    rubric_color = Column(Text, nullable=False)
    charge_value = Column(Integer)  # -100 to +100 per-song charge
    contaminated = Column(Boolean, default=False)
    contamination_note = Column(Text)
    charge_summary = Column(Text)
    why_classification = Column(Text)
    chart_source = Column(Text, default="billboard_hot_100")
    instrumental = Column(Boolean, default=False)


class DailyReading(Base):
    __tablename__ = "daily_readings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, unique=True, nullable=False)
    label = Column(Text)
    compass_degree = Column(Float, nullable=False)
    charge_level = Column(Text, nullable=False)
    contamination_count = Column(Integer, nullable=False, default=0)
    editorial_summary = Column(Text)

    songs = relationship("ReadingSong", back_populates="reading", cascade="all, delete-orphan")


class ReadingSong(Base):
    __tablename__ = "reading_songs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    reading_id = Column(Integer, ForeignKey("daily_readings.id"), nullable=False)
    compass_song_id = Column(Integer, ForeignKey("compass_songs.id", ondelete="SET NULL"), nullable=True)
    title = Column(Text, nullable=False)
    artist = Column(Text, nullable=False)
    position = Column(Integer, nullable=False)
    chart_source = Column(Text, default="spotify")

    reading = relationship("DailyReading", back_populates="songs")
    compass_song = relationship("CompassSong")


class WeeklyAlbumReading(Base):
    __tablename__ = "weekly_album_readings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    week_date = Column(Date, unique=True, nullable=False)  # Monday of the chart week
    compass_degree = Column(Float, nullable=False)
    charge_level = Column(Text, nullable=False)
    contamination_count = Column(Integer, nullable=False, default=0)
    editorial_summary = Column(Text)

    albums = relationship("WeeklyAlbumEntry", back_populates="reading", cascade="all, delete-orphan")


class WeeklyAlbumEntry(Base):
    __tablename__ = "weekly_album_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    reading_id = Column(Integer, ForeignKey("weekly_album_readings.id"), nullable=False)
    title = Column(Text, nullable=False)
    artist = Column(Text, nullable=False)
    position = Column(Integer, nullable=False)
    rubric_color = Column(Text, nullable=False)
    contaminated = Column(Boolean, default=False)
    contamination_note = Column(Text)
    charge_summary = Column(Text)
    chart_source = Column(Text, default="billboard_200")

    reading = relationship("WeeklyAlbumReading", back_populates="albums")


class AlbumDeepDive(Base):
    __tablename__ = "album_deep_dives"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(Text, nullable=False)
    artist = Column(Text, nullable=False)
    slug = Column(String(200), unique=True, nullable=False)
    release_year = Column(Integer)
    overall_color = Column(Text)
    summary = Column(Text)

    tracks = relationship("AlbumTrack", back_populates="album", cascade="all, delete-orphan")
    library_songs = relationship("LibrarySong", back_populates="album", cascade="all, delete-orphan")


class AlbumTrack(Base):
    __tablename__ = "album_tracks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    album_id = Column(Integer, ForeignKey("album_deep_dives.id"), nullable=False)
    track_number = Column(Integer, nullable=False)
    name = Column(Text, nullable=False)
    charge_color = Column(Text)
    assessment = Column(Text)

    album = relationship("AlbumDeepDive", back_populates="tracks")


class LibrarySong(Base):
    """Non-chart songs: manual entries, agent-scanned case studies, album deep dive tracks.

    Completely separate from the compass_songs table (which feeds compass/drift).
    The library frontend can read from both tables, but songs only gets fed by the agent pipeline.
    """
    __tablename__ = "library_songs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(Text, nullable=False)
    artist = Column(Text, nullable=False)
    rubric_color = Column(Text, nullable=False)
    charge_value = Column(Integer)  # -100 to +100
    contaminated = Column(Boolean, default=False)
    contamination_note = Column(Text)
    charge_summary = Column(Text)
    album_id = Column(Integer, ForeignKey("album_deep_dives.id"), nullable=True)
    track_number = Column(Integer, nullable=True)  # position within album
    source = Column(String(20), default="manual")  # manual / agent
    created_at = Column(DateTime, default=datetime.utcnow)

    album = relationship("AlbumDeepDive", back_populates="library_songs")


class Collection(Base):
    __tablename__ = "collections"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(Text, nullable=False)
    slug = Column(String(200), unique=True, nullable=False)
    description = Column(Text)
    charge_colors = Column(Text)  # Comma-separated: "violet,blue"
    is_active = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)

    recommendations = relationship("Recommendation", back_populates="collection", cascade="all, delete-orphan")


class Recommendation(Base):
    __tablename__ = "recommendations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    collection_id = Column(Integer, ForeignKey("collections.id"), nullable=False)
    title = Column(Text, nullable=False)
    artist = Column(Text, nullable=False)
    item_type = Column(String(20), default="song")  # "song" or "album"
    rubric_color = Column(Text, nullable=False)
    editorial_note = Column(Text)
    external_url = Column(Text)
    cover_art_url = Column(Text)
    sort_order = Column(Integer, default=0)

    collection = relationship("Collection", back_populates="recommendations")


class AgentDraft(Base):
    __tablename__ = "agent_drafts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="valid_draft_status",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    label = Column(Text, unique=True, nullable=True)  # e.g. compass_song_2026-02-22_draft
    draft_type = Column(Text, default="daily")  # daily / manual
    created_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String(20), default="pending")  # pending / approved / rejected
    date = Column(Date, nullable=False)
    compass_degree = Column(Float)
    charge_level = Column(Text)
    contamination_count = Column(Integer, default=0)
    editorial_summary = Column(Text)
    agent_model = Column(Text)
    agent_notes = Column(Text)
    agent_warnings = Column(Text)  # JSON-encoded list of warning strings

    songs = relationship("AgentDraftSong", back_populates="draft", cascade="all, delete-orphan")


class AgentDraftSong(Base):
    __tablename__ = "agent_draft_songs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    draft_id = Column(Integer, ForeignKey("agent_drafts.id"), nullable=False)
    compass_song_id = Column(Integer, ForeignKey("compass_songs.id", ondelete="SET NULL"), nullable=True)
    title = Column(Text, nullable=False)
    artist = Column(Text, nullable=False)
    position = Column(Integer, nullable=False)
    rubric_color = Column(Text)
    charge_value = Column(Integer)  # -100 to +100 per-song charge
    contaminated = Column(Boolean, default=False)
    contamination_note = Column(Text)
    charge_summary = Column(Text)
    chart_source = Column(Text, default="spotify")
    confidence = Column(Float)
    lyrics_available = Column(Boolean, default=False)

    draft = relationship("AgentDraft", back_populates="songs")
    compass_song = relationship("CompassSong")


class SubmittedSong(Base):
    """Crowd-submitted song classifications from Lyrical Charger.

    Separate from compass_songs (chart data) and library_songs (editorial).
    This is the public contribution layer — building the world's lyrical charge database.
    """
    __tablename__ = "submitted_songs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(Text, nullable=True)  # optional — paste-lyrics may not have metadata
    artist = Column(Text, nullable=True)
    rubric_color = Column(Text, nullable=False)
    charge_value = Column(Integer)
    contaminated = Column(Boolean, default=False)
    contamination_note = Column(Text)
    charge_summary = Column(Text)
    confidence = Column(Float)
    source = Column(String(20), default="paste_lyrics")  # paste_lyrics | search
    ip_address = Column(String(45), nullable=True)  # IPv4 or IPv6, for abuse detection
    submitted_at = Column(DateTime, default=datetime.utcnow)


class MisreadSubmission(Base):
    __tablename__ = "misread_submissions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    song_title = Column(Text, nullable=False)
    song_artist = Column(Text, nullable=False)
    song_color = Column(Text, nullable=False)
    song_position = Column(Integer)
    first_name = Column(Text, nullable=False)
    last_name = Column(Text, nullable=False)
    email = Column(Text, nullable=False)
    message = Column(Text, nullable=False)
    device_id = Column(Text)
    ip_address = Column(Text)
    status = Column(String(20), default="pending")  # pending / reviewed / accepted / rejected / flagged


class MisreadBan(Base):
    __tablename__ = "misread_bans"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    device_id = Column(Text, nullable=True)
    ip_address = Column(Text, nullable=True)
    reason = Column(Text)
