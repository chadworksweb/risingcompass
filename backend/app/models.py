from sqlalchemy import (
    Column, Integer, String, Text, Float, Boolean, Date, ForeignKey, UniqueConstraint
)
from sqlalchemy.orm import relationship
from app.database import Base


class Song(Base):
    __tablename__ = "songs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(Text, nullable=False)
    artist = Column(Text, nullable=False)
    year = Column(Integer, nullable=False)
    decade = Column(Text, nullable=False)
    chart_position = Column(Integer, nullable=False)
    rubric_color = Column(Text, nullable=False)
    contaminated = Column(Boolean, default=False)
    contamination_note = Column(Text)
    charge_summary = Column(Text)
    why_classification = Column(Text)
    message_analysis = Column(Text)
    expression_analysis = Column(Text)
    intention_analysis = Column(Text)
    chart_source = Column(Text, default="billboard_hot_100")


class DailyReading(Base):
    __tablename__ = "daily_readings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, unique=True, nullable=False)
    compass_degree = Column(Float, nullable=False)
    charge_level = Column(Text, nullable=False)
    contamination_count = Column(Integer, nullable=False, default=0)
    editorial_summary = Column(Text)

    songs = relationship("ReadingSong", back_populates="reading", cascade="all, delete-orphan")


class ReadingSong(Base):
    __tablename__ = "reading_songs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    reading_id = Column(Integer, ForeignKey("daily_readings.id"), nullable=False)
    title = Column(Text, nullable=False)
    artist = Column(Text, nullable=False)
    position = Column(Integer, nullable=False)
    rubric_color = Column(Text, nullable=False)
    contaminated = Column(Boolean, default=False)
    contamination_note = Column(Text)
    charge_summary = Column(Text)
    chart_source = Column(Text, default="spotify")

    reading = relationship("DailyReading", back_populates="songs")


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


class AlbumTrack(Base):
    __tablename__ = "album_tracks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    album_id = Column(Integer, ForeignKey("album_deep_dives.id"), nullable=False)
    track_number = Column(Integer, nullable=False)
    name = Column(Text, nullable=False)
    charge_color = Column(Text)
    assessment = Column(Text)

    album = relationship("AlbumDeepDive", back_populates="tracks")
