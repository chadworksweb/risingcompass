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
    why_calibration = Column(Text)
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
    """Crowd-submitted song calibrations from Lyrical Charger.

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


class LcEvent(Base):
    """Every Lyrical Charger interaction — page views, searches, submissions, failures.

    Built for visibility into who's using the tool, where they came from, and what
    succeeded vs failed. Backs the "LC Activity" admin tab.
    """
    __tablename__ = "lc_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    occurred_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    event_type = Column(String(40), nullable=False)
    ip_address = Column(String(64))
    user_agent = Column(String(512))
    referrer = Column(String(512))
    payload_json = Column(Text)
    submission_id = Column(Integer, ForeignKey("submitted_songs.id", ondelete="SET NULL"))


class AlbumCalibration(Base):
    """Computed album-level calibration — mean of constituent song charges.

    Not editorial (that's album_deep_dives). This is the badge-serving layer.
    """
    __tablename__ = "album_calibrations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(Text, nullable=False)
    artist = Column(Text, nullable=False)
    rubric_color = Column(Text, nullable=False)
    charge_value = Column(Integer, nullable=False)  # mean of track charges
    charge_summary = Column(Text)
    track_count = Column(Integer, nullable=False, default=0)
    contamination_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "title", "artist",
            name="uq_album_calibrations_title_artist",
        ),
    )


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
    report_type = Column(String(20), nullable=False, default="misread")  # misread / satirical
    proof_context = Column(Text)  # required when report_type='satirical'
    # Polymorphic ref into the three song tables; nullable, set by the slug
    # matcher when the submitted (title, artist) resolves cleanly.
    song_source = Column(String(20))  # compass / library / submitted
    song_id = Column(Integer)


class StreamSong(Base):
    """CL Stream — personal feed of songs encountered in the wild.

    The point is the *why* — why this song caught your ear, why it matters.
    Songs land here first, get auto-calibrated, then can be promoted to
    library_songs (official non-chart archive).
    """
    __tablename__ = "stream_songs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(Text, nullable=False)
    artist = Column(Text, nullable=False)
    note = Column(Text)  # The why — optional
    source_url = Column(Text)  # Tidal/Spotify/YouTube link
    source_platform = Column(String(30))  # tidal / spotify / manual
    rubric_color = Column(Text)
    charge_value = Column(Integer)
    contaminated = Column(Boolean, default=False)
    contamination_note = Column(Text)
    charge_summary = Column(Text)
    confidence = Column(Float)
    status = Column(String(20), default="calibrated")  # calibrated / promoted / failed
    promoted_to = Column(String(20))  # library / compass — set on promotion
    created_at = Column(DateTime, default=datetime.utcnow)


class Artist(Base):
    """First-class artist entity for trajectory tracking."""
    __tablename__ = "artists"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(Text, nullable=False)
    slug = Column(String(250), unique=True, nullable=False)
    musicbrainz_id = Column(Text)
    spotify_id = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    releases = relationship("Release", back_populates="artist", cascade="all, delete-orphan")


class Release(Base):
    """A release (single, EP, or album) linked to an artist."""
    __tablename__ = "releases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    artist_id = Column(Integer, ForeignKey("artists.id"), nullable=False)
    title = Column(Text, nullable=False)
    release_type = Column(String(20), nullable=False)  # single / ep / album
    release_date = Column(Date)
    release_year = Column(Integer)
    rubric_color = Column(Text)
    charge_value = Column(Integer)  # mean of constituent song charges
    track_count = Column(Integer, default=0)
    calibrated_count = Column(Integer, default=0)
    contamination_count = Column(Integer, default=0)
    musicbrainz_id = Column(Text)
    spotify_id = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("artist_id", "title", name="uq_releases_artist_title"),
    )

    artist = relationship("Artist", back_populates="releases")
    songs = relationship("ReleaseSong", back_populates="release", cascade="all, delete-orphan")


class SongArtist(Base):
    """Song → artist attribution (N:M). Makes multi-artist songs representable
    across collabs (primary & primary) and features (primary + featured).

    Polymorphic via (song_source, song_id). Role enum stays small on purpose:
    primary | featured. Position is display-order within the credit string.
    """
    __tablename__ = "song_artists"

    id = Column(Integer, primary_key=True, autoincrement=True)
    song_source = Column(String(20), nullable=False)
    song_id = Column(Integer, nullable=False)
    artist_id = Column(Integer, ForeignKey("artists.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(20), nullable=False, default="primary")
    position = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("song_source", "song_id", "artist_id", name="uq_song_artists_song_artist"),
    )

    artist = relationship("Artist")


class ReleaseSong(Base):
    """Links a release to a calibrated song across the three song tables."""
    __tablename__ = "release_songs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    release_id = Column(Integer, ForeignKey("releases.id"), nullable=False)
    song_source = Column(String(20), nullable=False)  # compass / library / submitted
    song_id = Column(Integer, nullable=False)
    track_number = Column(Integer)

    release = relationship("Release", back_populates="songs")


class SongSlug(Base):
    """Lookup table mapping URL slugs to songs across the three song tables."""
    __tablename__ = "song_slugs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    slug = Column(String(300), unique=True, nullable=False)
    title = Column(Text, nullable=False)
    artist = Column(Text, nullable=False)
    song_source = Column(String(20))  # compass / library / submitted
    song_id = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)


class MisreadBan(Base):
    __tablename__ = "misread_bans"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    device_id = Column(Text, nullable=True)
    ip_address = Column(Text, nullable=True)
    reason = Column(Text)


class ApiClient(Base):
    """An organization (or internal system) that consumes the RC API.

    behavior determines how the calibrate endpoints treat this client:
      - "public"  → bot protection + lc_events logging, source forced to "lyrical_charger"
      - "service" → bot protection skipped, source taken from body, no lc_events
    """
    __tablename__ = "api_clients"

    id = Column(Integer, primary_key=True, autoincrement=True)
    slug = Column(String(64), unique=True, nullable=False)
    name = Column(Text, nullable=False)
    contact_email = Column(Text)
    plan_tier = Column(String(32), default="trial")
    status = Column(String(16), default="active")  # active | suspended | revoked
    behavior = Column(String(16), default="service", nullable=False)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    keys = relationship("ApiClientKey", back_populates="client", cascade="all, delete-orphan")


class ApiClientKey(Base):
    """Hashed API key — one client can have many. Raw key is shown only at creation."""
    __tablename__ = "api_client_keys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, ForeignKey("api_clients.id", ondelete="CASCADE"), nullable=False)
    key_hash = Column(String(64), unique=True, nullable=False)
    key_prefix = Column(String(12), nullable=False)
    label = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_used_at = Column(DateTime)
    revoked_at = Column(DateTime)

    client = relationship("ApiClient", back_populates="keys")


class SongRecalibrationProposal(Base):
    """A pending or resolved AI recalibration proposal.

    Created when an admin invokes a recalibration (currently only the satire
    type is implemented). Lives between "AI ran" and "admin acted." On accept,
    a SongRecalibration row is written and this proposal's status flips to
    'accepted'. On reject, only the proposal is updated — nothing applied.
    """
    __tablename__ = "song_recalibration_proposals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    recalibration_type = Column(String(20), nullable=False)  # satire | public_interest
    song_source = Column(String(20), nullable=False)
    song_id = Column(Integer, nullable=False)
    trigger_source = Column(String(40))  # satirical_flag | vibe_gap | admin_manual
    trigger_ref_id = Column(Integer)  # FK-shaped pointer back to the trigger row
    original_charge = Column(Integer)
    original_color = Column(String(20))
    proposed_charge = Column(Integer)
    proposed_color = Column(String(20))
    proposed_summary = Column(Text)
    ai_rationale = Column(Text)
    ai_model = Column(Text)
    status = Column(String(20), nullable=False, default="pending")  # pending | accepted | rejected
    review_notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime)


class SongRecalibration(Base):
    """Immutable audit log of every applied recalibration.

    Read by the public song-history endpoint and rendered as small print on
    the song detail page. The public_summary is the admin-written story of
    why the calibration changed — the recalibrate suite is honest about its
    history because that honesty IS the proof of objectivity.
    """
    __tablename__ = "song_recalibrations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    recalibration_type = Column(String(20), nullable=False)  # satire | public_interest
    song_source = Column(String(20), nullable=False)
    song_id = Column(Integer, nullable=False)
    proposal_id = Column(Integer, ForeignKey("song_recalibration_proposals.id", ondelete="SET NULL"))
    trigger_source = Column(String(40))
    trigger_ref_id = Column(Integer)
    before_charge = Column(Integer)
    before_color = Column(String(20))
    before_summary = Column(Text)  # snapshot of charge_summary before this recalibration — preserved for safe rollback
    after_charge = Column(Integer, nullable=False)
    after_color = Column(String(20), nullable=False)
    ai_rationale = Column(Text)
    public_summary = Column(Text, nullable=False)  # the public-facing story
    internal_notes = Column(Text)
    flag_count_snapshot = Column(Text)  # JSON: {misread: N, satirical: N} at moment of recalibration
    vibe_snapshot = Column(Text)  # JSON: {value, pushes_up, pushes_down} (future, when vibe ships)
    applied_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class AudienceVibeNeedle(Base):
    """Persistent vibe position for one song. Lives wherever the crowd has
    pushed it. Never auto-resets — yearly reset is per-person eligibility,
    not the needle. Polymorphic via (song_source, song_id) like every other
    cross-table song reference in this codebase.
    """
    __tablename__ = "audience_vibe_needles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    song_source = Column(String(20), nullable=False)
    song_id = Column(Integer, nullable=False)
    current_value = Column(Integer, nullable=False, default=0)  # -100..+100
    pushes_up_total = Column(Integer, nullable=False, default=0)
    pushes_down_total = Column(Integer, nullable=False, default=0)
    last_push_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("song_source", "song_id", name="uq_vibe_needles_song"),
    )


class AudienceVibePush(Base):
    """Append-only timestamped push log. Carries the full trajectory of every
    needle for future Vibe Trends aggregations. user_id is reserved for when
    real-account auth ships; v1 gates on device_id + IP per the roadmap.

    Yearly eligibility is enforced via the unique constraint on
    (song_source, song_id, device_id, push_year): a device can push the same
    song once per calendar year. Year boundary refreshes everyone's eligibility.
    """
    __tablename__ = "audience_vibe_pushes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    song_source = Column(String(20), nullable=False)
    song_id = Column(Integer, nullable=False)
    direction = Column(Integer, nullable=False)  # +1 or -1
    user_id = Column(Integer)  # nullable — placeholder for future auth
    device_id = Column(Text)
    ip_address = Column(Text)
    push_year = Column(Integer, nullable=False)
    pushed_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "song_source", "song_id", "device_id", "push_year",
            name="uq_vibe_pushes_device_year",
        ),
    )


class AudienceVibeReviewCase(Base):
    """Open when the gap between the compass charge and the vibe needle exceeds
    the configured threshold. Admin reviews and decides whether to fire a
    public-interest recalibration. Only one open case per song at a time
    (enforced in service code).
    """
    __tablename__ = "audience_vibe_review_cases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    song_source = Column(String(20), nullable=False)
    song_id = Column(Integer, nullable=False)
    compass_charge = Column(Integer)
    compass_color = Column(String(20))
    vibe_value = Column(Integer, nullable=False)
    gap = Column(Integer, nullable=False)  # abs(compass_charge - vibe_value)
    status = Column(String(20), nullable=False, default="open")  # open | acknowledged | recalibrated | dismissed
    admin_notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    resolved_at = Column(DateTime)


class CalibrationRun(Base):
    """Append-only log of every agent calibration run on a song.

    Every run — LC submit, compass daily, stream, admin library CRUD — writes
    one row here. Backs two features: per-song consensus (the canonical song
    row drifts toward the weighted mean as runs accumulate) and the training
    corpus for future agent improvements.

    Polymorphic song pointer is nullable so a run can exist before or without
    a persisted canonical song row. title + artist snapshot regardless.
    Lyrics themselves are never stored — only a SHA-256 hash for dedupe /
    variance awareness.
    """
    __tablename__ = "calibration_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    song_source = Column(String(20))
    song_id = Column(Integer)
    title = Column(Text)
    artist = Column(Text)
    rubric_color = Column(String(20))
    charge_value = Column(Integer)
    charge_summary = Column(Text)
    contaminated = Column(Boolean, default=False)
    contamination_note = Column(Text)
    confidence = Column(Float)
    agent_model = Column(String(80))
    triggered_by = Column(String(40))
    lyrics_hash = Column(String(64))
    run_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class SongReset(Base):
    """Append-only audit log of resets: calibrations returned to the null state.

    Polymorphic (song_source, song_id). Carries a full before-snapshot so the
    public history timeline can render what was wiped. Distinct from
    SongRecalibration, which always writes a new charge (satire / public-
    interest re-reads). Resets go the other direction — back to uncalibrated.
    """
    __tablename__ = "song_resets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    song_source = Column(String(20), nullable=False)
    song_id = Column(Integer, nullable=False)
    before_charge = Column(Integer)
    before_color = Column(String(20))
    before_summary = Column(Text)
    before_contaminated = Column(Boolean)
    before_contamination_note = Column(Text)
    reason = Column(Text, nullable=False)
    reset_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ApiCallLog(Base):
    """One row per /api/* request (admin + health excluded). Backs the API Monitor tab.

    context_json carries the request-specific args — song title/artist, slug,
    search query, etc. Populated from URL query params automatically, plus any
    endpoint-supplied context via request.state.call_context.
    """
    __tablename__ = "api_call_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, ForeignKey("api_clients.id", ondelete="SET NULL"))
    ts = Column(DateTime, default=datetime.utcnow, nullable=False)
    method = Column(String(8), nullable=False)
    path = Column(String(255), nullable=False)
    status = Column(Integer)
    ip = Column(String(64))
    user_agent = Column(String(255))
    duration_ms = Column(Integer)
    context_json = Column(Text)
