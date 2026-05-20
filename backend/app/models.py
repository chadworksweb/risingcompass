from sqlalchemy import (
    CheckConstraint, Column, Integer, String, Text, Float, Boolean, Date, DateTime, ForeignKey, LargeBinary, UniqueConstraint
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
    dogma_referenced = Column(Boolean, default=False)
    dogma_note = Column(Text)
    charge_summary = Column(Text)
    chart_source = Column(Text, default="billboard_hot_100")
    instrumental = Column(Boolean, default=False)
    effects_prose = Column(Text)  # 3-paragraph per-song description of what the song transmits
    societal_effects_prose = Column(Text)  # what running this program at scale would do to a society
    deadpan_line = Column(Text)  # Ether Art Chart: flat literal naming of the song
    topics = Column(Text)  # Ether Art Chart: JSON array of taxonomy slugs, dominant-first
    topic_audit = Column(Text)  # Ether Art Chart: JSON audit payload when no taxonomy match
    created_at = Column(DateTime, default=datetime.utcnow)


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


class ChartSnapshot(Base):
    """Per-chart daily top-N snapshot. Independent of daily_readings.

    Each (date, chart_source) is a self-contained list. Charge values are
    looked up live against compass_songs at render time — this row stores
    only the chart's own (title, artist, position).
    """
    __tablename__ = "chart_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False)
    chart_source = Column(Text, nullable=False)
    position = Column(Integer, nullable=False)
    title = Column(Text, nullable=False)
    artist = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("date", "chart_source", "position", name="uq_chart_snapshots_date_source_pos"),
    )


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
    dogma_referenced = Column(Boolean, default=False)
    dogma_note = Column(Text)
    charge_summary = Column(Text)
    album_id = Column(Integer, ForeignKey("album_deep_dives.id"), nullable=True)
    track_number = Column(Integer, nullable=True)  # position within album
    source = Column(String(20), default="manual")  # manual / agent
    effects_prose = Column(Text)  # 3-paragraph per-song description
    societal_effects_prose = Column(Text)  # what running this program at scale would do to a society
    deadpan_line = Column(Text)  # Ether Art Chart: flat literal naming of the song
    topics = Column(Text)  # Ether Art Chart: JSON array of taxonomy slugs, dominant-first
    topic_audit = Column(Text)  # Ether Art Chart: JSON audit payload when no taxonomy match
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
    dogma_referenced = Column(Boolean, default=False)
    dogma_note = Column(Text)
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
    dogma_referenced = Column(Boolean, default=False)
    dogma_note = Column(Text)
    charge_summary = Column(Text)
    confidence = Column(Float)
    source = Column(String(20), default="paste_lyrics")  # paste_lyrics | search
    ip_address = Column(String(45), nullable=True)  # IPv4 or IPv6, for abuse detection
    effects_prose = Column(Text)  # 3-paragraph per-song description
    societal_effects_prose = Column(Text)  # what running this program at scale would do to a society
    submitted_at = Column(DateTime, default=datetime.utcnow)


class V1Test(Base):
    """Isolated write target for v1-frozen control calibrations.

    Every run of the admin "V1 Test" tab writes one row. Intentionally
    separate from compass_songs / submitted_songs / calibration_runs so
    v1 control runs never mix into the canonical corpus.
    """
    __tablename__ = "v1_tests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(Text, nullable=False)
    artist = Column(Text, nullable=False)
    rubric_color = Column(Text)
    charge_value = Column(Integer)
    contaminated = Column(Boolean, default=False)
    contamination_note = Column(Text)
    charge_summary = Column(Text)
    confidence = Column(Float)
    rubric_commit = Column(Text, nullable=False)
    error = Column(Text)  # populated when the calibrator raised / returned no color
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


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
    # Public Participation Phase 2.1: new rows are account-linked.
    # Legacy rows keep user_id=NULL and rely on first_name/email below.
    user_id = Column(Integer, ForeignKey("users.id"))
    first_name = Column(Text)  # nullable: required only for legacy anonymous flow
    last_name = Column(Text)
    email = Column(Text)
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
    effects_prose = Column(Text)  # 3-paragraph per-song description
    societal_effects_prose = Column(Text)  # what running this program at scale would do to a society
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


class ArtistAdminEvent(Base):
    """Audit log for admin artist operations (merge, rename).

    Written in the same transaction as the mutation so the log can never
    disagree with reality. event_type is 'merge' or 'rename'; rewrites_json
    carries a per-operation breakdown of what got touched.
    """
    __tablename__ = "artist_admin_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    occurred_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    event_type = Column(String(20), nullable=False)  # merge | rename
    actor = Column(String(64))

    # The artist acted upon (for merge, this is the source that got absorbed).
    # artist_id is nullable because the row may have been deleted by the merge.
    artist_id = Column(Integer)
    artist_name_before = Column(Text, nullable=False)
    artist_slug_before = Column(Text, nullable=False)
    artist_name_after = Column(Text)
    artist_slug_after = Column(Text)

    # Merge target — null for rename events.
    target_artist_id = Column(Integer)
    target_artist_name = Column(Text)
    target_artist_slug = Column(Text)

    rewrites_json = Column(Text)
    notes = Column(Text)


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
    lens = Column(String(20), nullable=False)  # standard | satire  (how the agent re-reads)
    song_source = Column(String(20), nullable=False)
    song_id = Column(Integer, nullable=False)
    pipeline = Column(String(40))  # manual | rubric_update | satirical_flag | vibe_gap | consensus_drift
    trigger_ref_id = Column(Integer)  # FK-shaped pointer back to the triggering row (flag id, etc.)
    original_charge = Column(Integer)
    original_color = Column(String(20))
    proposed_charge = Column(Integer)
    proposed_color = Column(String(20))
    proposed_summary = Column(Text)
    dogma_referenced = Column(Boolean, default=False)
    dogma_note = Column(Text)
    ai_rationale = Column(Text)
    ai_model = Column(Text)
    status = Column(String(20), nullable=False, default="pending")  # pending | accepted | rejected
    review_notes = Column(Text)
    rubric_change_slug = Column(String(100))  # pipeline=rubric_update only: stable id grouping affected songs
    rubric_change_note = Column(Text)  # pipeline=rubric_update only: 1-2 sentence description of the rule
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
    lens = Column(String(20), nullable=False)  # standard | satire
    song_source = Column(String(20), nullable=False)
    song_id = Column(Integer, nullable=False)
    proposal_id = Column(Integer, ForeignKey("song_recalibration_proposals.id", ondelete="SET NULL"))
    pipeline = Column(String(40))  # manual | rubric_update | satirical_flag | vibe_gap | consensus_drift
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
    vibe_snapshot = Column(Text)  # JSON: {value, pushes_up, pushes_down} captured from audience_vibe_needles at apply time (wired 2026-05-12)
    rubric_change_slug = Column(String(100))  # groups songs recalibrated by the same rubric change
    rubric_change_note = Column(Text)  # 1-2 sentence description of the rubric rule that triggered this
    applied_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    # Calibration Log promote columns (migration 032). human_rationale is
    # admin prose separate from ai_rationale; promoted_to_feed gates public
    # visibility in the unified feed; tags is freeform groundwork for a
    # future pattern taxonomy.
    human_rationale = Column(Text)
    tags = Column(Text)
    # Auto-promoted: every tenet/algo event lands in the public feed immediately
    # (2026-04-23). Manual promote step retired; column kept for backward
    # compatibility + migration path.
    promoted_to_feed = Column(Boolean, default=True, nullable=False)
    promoted_at = Column(DateTime, default=datetime.utcnow)


class PrePublishCorrection(Base):
    """Audit row for an admin override of an agent-classified draft song,
    written before the draft is approved.

    Captures the before/after diff plus an optional human_rationale. Lands
    with promoted_to_feed=false by default; a separate promote step sets it
    true and stamps promoted_at. See RISING-COMPASS-CALIBRATION-LOG.md.
    """
    __tablename__ = "pre_publish_corrections"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # draft_id + draft_song_id are informational — they point to the draft at
    # correction time but drafts get deleted on approval, so these are nullable
    # and may become dangling shortly after the correction is written.
    draft_id = Column(Integer)
    draft_song_id = Column(Integer)
    compass_song_id = Column(Integer)  # nullable — draft song may not be linked yet
    occurred_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    before_rubric_color = Column(Text)
    before_charge_value = Column(Integer)
    before_contaminated = Column(Boolean)
    before_contamination_note = Column(Text)
    before_summary = Column(Text)
    after_rubric_color = Column(Text)
    after_charge_value = Column(Integer)
    after_contaminated = Column(Boolean)
    after_contamination_note = Column(Text)
    after_summary = Column(Text)
    human_rationale = Column(Text)  # optional prose from admin
    tags = Column(Text)  # freeform; JSON array or CSV
    # Auto-promoted — see SongRecalibration comment above (2026-04-23).
    promoted_to_feed = Column(Boolean, default=True, nullable=False)
    promoted_at = Column(DateTime, default=datetime.utcnow)


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


class Trash(Base):
    """Soft-delete destination for rows removed via the admin DB explorer.

    Stores a JSON snapshot of the original row plus any related junction
    rows we capture at delete time. Nothing is ever truly nuked — the
    trash table is the audit record. Restoring from trash is possible but
    not automated yet.
    """
    __tablename__ = "trash"

    id = Column(Integer, primary_key=True, autoincrement=True)
    original_table = Column(String(40), nullable=False)
    original_id = Column(Integer, nullable=False)
    title = Column(Text)
    artist = Column(Text)
    row_data = Column(Text, nullable=False)  # JSON
    related_data = Column(Text)  # JSON — optional junction snapshot
    reason = Column(Text, nullable=False)
    deleted_at = Column(DateTime, default=datetime.utcnow, nullable=False)


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
    dogma_referenced = Column(Boolean, default=False)
    dogma_note = Column(Text)
    confidence = Column(Float)
    agent_model = Column(String(80))
    triggered_by = Column(String(40))
    lyrics_hash = Column(String(64))
    lyrics_fingerprint = Column(LargeBinary)  # 128-fn MinHash signature for divergence detection — see services/lyrics_fingerprint.py
    run_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    superseded = Column(Boolean, default=False, nullable=False)  # true if a later rubric_update invalidated this run
    superseded_reason = Column(String(100))  # e.g. rubric_change_slug that invalidated this
    superseded_at = Column(DateTime)


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


class ClaudeApiUsage(Base):
    """One row per Anthropic messages.create() call from the backend.

    Distinct from api_call_log (inbound) — this tracks OUTBOUND spend on the
    Claude API. call_site identifies which feature made the call ("calibrator",
    "satire_recalibrator", "ether_tagger", etc); context_json captures the
    feature-specific args (song title/artist, draft id, etc) so the admin tab
    can show exactly what was being processed when the cost was incurred.
    """
    __tablename__ = "claude_api_usage"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ts = Column(DateTime, default=datetime.utcnow, nullable=False)
    call_site = Column(String(64), nullable=False)
    model = Column(String(64), nullable=False)
    input_tokens = Column(Integer, nullable=False, default=0)
    output_tokens = Column(Integer, nullable=False, default=0)
    cache_creation_tokens = Column(Integer, nullable=False, default=0)
    cache_read_tokens = Column(Integer, nullable=False, default=0)
    input_cost_usd = Column(Float, nullable=False, default=0.0)
    output_cost_usd = Column(Float, nullable=False, default=0.0)
    cache_creation_cost_usd = Column(Float, nullable=False, default=0.0)
    cache_read_cost_usd = Column(Float, nullable=False, default=0.0)
    total_cost_usd = Column(Float, nullable=False, default=0.0)
    duration_ms = Column(Integer)
    stop_reason = Column(String(32))
    ok = Column(Integer, nullable=False, default=1)
    error = Column(Text)
    pricing_source = Column(String(32))
    context_json = Column(Text)


class ArtistVerification(Base):
    """Artist-level verification record. One-to-one with Artist.

    Funnel stages: lead -> contacted -> in_conversation -> active. Stage
    'active' is the gate that allows verification blocks to be published.
    When verification_method = 'video_call', all five deepfake checklist
    items must be true before the artist can be moved to 'active'. Other
    methods (in_person, audio_call, prior_relationship) bypass the
    deepfake gate but still require the method to be set.
    """
    __tablename__ = "artist_verifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    artist_id = Column(Integer, ForeignKey("artists.id", ondelete="CASCADE"), unique=True, nullable=False)
    funnel_stage = Column(String(30), nullable=False, default="lead")
    verification_method = Column(String(30))  # in_person | video_call | audio_call | prior_relationship
    contact_email = Column(Text)
    contact_phone = Column(Text)
    contact_handle = Column(Text)
    conversation_log = Column(Text)
    notes = Column(Text)
    # Deepfake checklist (gates active when method=video_call)
    deepfake_live_challenge_passed = Column(Boolean, nullable=False, default=False)
    deepfake_cross_channel_confirmed = Column(Boolean, nullable=False, default=False)
    deepfake_two_sessions_completed = Column(Boolean, nullable=False, default=False)
    deepfake_reference_match_confirmed = Column(Boolean, nullable=False, default=False)
    deepfake_recording_archived = Column(Boolean, nullable=False, default=False)
    deepfake_recording_url = Column(Text)
    contacted_at = Column(DateTime)
    verified_at = Column(DateTime)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    artist = relationship("Artist")


class ArtistVerificationBlock(Base):
    """Curated artist content for one song. Polymorphic via (song_source, song_id).

    Block content is text + optional video_url + optional audio_url. The
    published flag drives whether the Artist Verified badge + block render
    on the public song page. Publishing is gated by the parent
    ArtistVerification reaching funnel_stage='active' with the deepfake
    checklist satisfied (when applicable).
    """
    __tablename__ = "artist_verification_blocks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    artist_id = Column(Integer, ForeignKey("artists.id", ondelete="CASCADE"), nullable=False)
    song_source = Column(String(20), nullable=False)  # compass | library | submitted
    song_id = Column(Integer, nullable=False)
    block_text = Column(Text)
    video_url = Column(Text)
    audio_url = Column(Text)
    published = Column(Boolean, nullable=False, default=False)
    published_at = Column(DateTime)
    internal_notes = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("artist_id", "song_source", "song_id", name="uq_av_blocks_artist_song"),
    )

    artist = relationship("Artist")


class ArtistVerificationInquiry(Base):
    """Inbound 'Are you the artist?' lead from a song page. Same shape as
    misread_submissions: name + email + message + device_id + ip + status.
    Triage in admin: dismiss, mark contacted, or promote to a funnel entry
    (sets artist_id and creates an ArtistVerification row at stage='lead'
    on the chosen Artist record).
    """
    __tablename__ = "artist_verification_inquiries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    song_title = Column(Text, nullable=False)
    song_artist = Column(Text, nullable=False)
    song_source = Column(String(20))
    song_id = Column(Integer)
    song_color = Column(String(20))
    song_position = Column(Integer)
    claimant_name = Column(Text, nullable=False)
    claimant_email = Column(Text, nullable=False)
    claimant_role = Column(String(40))  # artist | manager | label | other
    proof_links = Column(Text)
    message = Column(Text, nullable=False)
    device_id = Column(Text)
    ip_address = Column(Text)
    status = Column(String(20), nullable=False, default="pending")  # pending | contacted | dismissed | promoted
    artist_id = Column(Integer, ForeignKey("artists.id", ondelete="SET NULL"))


class AdminUser(Base):
    """Per-user admin account. Replaces the single shared RC_ADMIN_KEY.

    password_hash is argon2id. is_active=False disables login without
    deleting history. failed_login_count + locked_until enforce the
    temporary lockout applied by the auth service after repeated
    failures within the rate-limit window.
    """
    __tablename__ = "admin_users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(64), unique=True, nullable=False)
    email = Column(Text)
    password_hash = Column(Text, nullable=False)
    role = Column(String(20), nullable=False, default="admin")
    is_active = Column(Boolean, nullable=False, default=True)
    failed_login_count = Column(Integer, nullable=False, default=0)
    locked_until = Column(DateTime)
    last_login_at = Column(DateTime)
    last_login_ip = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class AdminSession(Base):
    """Server-side session for an authenticated admin. The raw cookie value
    is never persisted — only its SHA-256 hash. expires_at is the sliding
    idle deadline; absolute_expires_at is the hard cap. revoked_at is set
    on logout or admin revocation.
    """
    __tablename__ = "admin_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(
        Integer, ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash = Column(String(64), unique=True, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)
    absolute_expires_at = Column(DateTime, nullable=False)
    last_seen_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    ip = Column(Text)
    user_agent = Column(Text)
    revoked_at = Column(DateTime)


class AdminLoginAttempt(Base):
    """Append-only audit + rate-limit source for admin logins.

    Used three ways:
      - rate limit: count failures in last N minutes by ip and by username
      - lockout signal: per-user consecutive failures (counter on AdminUser)
      - forensics: full history of who tried what from where
    """
    __tablename__ = "admin_login_attempts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(Text)
    ip = Column(Text)
    user_agent = Column(Text)
    success = Column(Boolean, nullable=False, default=False)
    reason = Column(Text)
    attempted_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class BackfillJob(Base):
    """Backfill Console job — a batch of songs to push through the
    standard calibrate-then-tag SOP. Created from the admin UI; runs in
    a background asyncio task per job. Resumable across restarts via
    the `status` + `paused_flag` columns.
    """
    __tablename__ = "backfill_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    label = Column(Text, nullable=False)
    target_table = Column(String(20), nullable=False)  # 'compass' | 'library'
    passes = Column(String(20), nullable=False)  # 'calibrate' | 'tag' | 'both'
    status = Column(String(20), nullable=False, default="queued")
    paused_flag = Column(Integer, nullable=False, default=0)
    total_rows = Column(Integer, nullable=False, default=0)
    completed_rows = Column(Integer, nullable=False, default=0)
    failed_rows = Column(Integer, nullable=False, default=0)
    created_by = Column(Integer)  # admin_users.id
    note = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)


class BackfillJobRow(Base):
    """One song in a BackfillJob's input list. State machine driven."""
    __tablename__ = "backfill_job_rows"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, nullable=False)
    position = Column(Integer, nullable=False)
    title = Column(Text, nullable=False)
    artist = Column(Text, nullable=False)
    # Compass-target metadata (NULL when target is library)
    year = Column(Integer)
    chart_position = Column(Integer)
    # Library-target metadata (NULL when target is compass)
    album_id = Column(Integer)
    track_number = Column(Integer)
    # Lyrics: paste-only in v1; Musixmatch fetch lands here too
    lyrics = Column(Text)
    lyrics_source = Column(String(20))  # 'paste' | 'musixmatch'
    # Per-row state machine: queued | needs_lyrics | calibrating
    #                       | tagging | done | failed | skipped
    status = Column(String(20), nullable=False, default="queued")
    error = Column(Text)
    # Result hand-off — which song row was created/updated
    result_song_source = Column(String(20))  # 'compass' | 'library'
    result_song_id = Column(Integer)
    # Cached calibrator + tagger output for quick UI display
    rubric_color = Column(Text)
    charge_value = Column(Integer)
    deadpan_line = Column(Text)
    topics = Column(Text)
    topic_audit = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class SystemFlag(Base):
    __tablename__ = "system_flags"

    key = Column(Text, primary_key=True)
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class LyricalChargerSubscriber(Base):
    __tablename__ = "lyrical_charger_subscribers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(Text, nullable=False, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    notified_at = Column(DateTime, nullable=True)


class Donation(Base):
    __tablename__ = "rc_donations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    stripe_session_id = Column(Text, nullable=False, unique=True)
    amount_cents = Column(Integer, nullable=False)
    currency = Column(Text, nullable=False, default="usd")
    status = Column(Text, nullable=False, default="pending")
    source = Column(Text, nullable=True)
    customer_email = Column(Text, nullable=True)
    payment_intent_id = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)


class Comment(Base):
    """Lobby comment. Polymorphic target by (target_type, target_source, target_id):
      target_type    -- 'song' | 'artist' | 'release'
      target_source  -- 'compass' | 'library' | 'submitted' (songs only)
      target_id      -- integer FK into the matching table

    Threading is 2 levels max -- a top-level comment plus flat replies.
    parent_id chains the reply; thread_root_id points at the top-level
    ancestor (self for top-level rows) so thread fetch is O(1).

    Soft-delete and hide are distinct:
      deleted_at -- author removed their own comment (renders as [deleted])
      hidden_at  -- admin or auto-hide trigger suppressed it (author sees
                    the reason; no shadow-ban)
    """
    __tablename__ = "comments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    author_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    target_type = Column(Text, nullable=False)
    target_source = Column(Text)
    target_id = Column(Integer, nullable=False)
    parent_id = Column(Integer, ForeignKey("comments.id"))
    thread_root_id = Column(Integer, ForeignKey("comments.id"), nullable=False)
    content = Column(Text, nullable=False)
    content_length = Column(Integer, nullable=False)
    edited_at = Column(DateTime)
    # Three distinct removal states with different semantics:
    #   withdrawn_at -- user "take back". Attribution kept; original content
    #                   revealable on press-and-hold.
    #   deleted_at   -- admin moderation delete. No attribution, no reveal.
    #   hidden_at    -- admin suppression pending review. Reversible.
    withdrawn_at = Column(DateTime)
    deleted_at = Column(DateTime)
    hidden_at = Column(DateTime)
    hidden_reason = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class CommentReport(Base):
    """One per (reporter, comment) -- the UNIQUE constraint enforces no
    double-counting. 3 distinct rows in 'pending' status with created_at
    inside the last 24h triggers auto-hide on the target comment."""
    __tablename__ = "comment_reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    comment_id = Column(Integer, ForeignKey("comments.id"), nullable=False)
    reporter_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    reason = Column(Text, nullable=False)
    notes = Column(Text)
    status = Column(Text, nullable=False, default="pending")
    resolved_at = Column(DateTime)
    resolved_by_admin_id = Column(Integer)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class AccountVerification(Base):
    """One row per (user, provider, provider_reference) verification attempt.

    status mirrors Stripe Identity's lifecycle:
      requires_input  -- session created, user hasn't completed flow
      processing      -- Stripe is reviewing the submission
      verified        -- success; webhook flipped users.tier to 'id_verified'
      canceled        -- user abandoned, or admin canceled

    UNIQUE (provider, provider_reference) guards against webhook replays
    creating duplicate rows. The provider_reference is the
    VerificationSession id from Stripe.
    """
    __tablename__ = "account_verifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    provider = Column(Text, nullable=False)
    provider_reference = Column(Text, nullable=False)
    status = Column(Text, nullable=False, default="requires_input")
    verified_at = Column(DateTime)
    failure_reason = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class AdminAlertPref(Base):
    """Toggle row per (alert_key, channel). Composite PK -- one knob per
    delivery surface. Channel is currently always 'email' but the column
    is in place for SMS / Slack / webhook without a migration."""
    __tablename__ = "admin_alert_prefs"

    alert_key = Column(Text, primary_key=True)
    channel = Column(Text, primary_key=True, default="email")
    enabled = Column(Boolean, nullable=False, default=True)
    updated_at = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class ModerationEvent(Base):
    """Append-only audit of every consequential moderation action.

    action values: 'auto_hide' | 'admin_hide' | 'admin_unhide' |
                   'cooldown' | 'ban' | 'unban' | 'dismiss_report'
    target_user_id and/or target_comment_id are populated depending on
    action scope. actor_admin_id is null for auto_hide.
    """
    __tablename__ = "moderation_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    action = Column(Text, nullable=False)
    actor_admin_id = Column(Integer)
    target_user_id = Column(Integer)
    target_comment_id = Column(Integer)
    reason = Column(Text)
    details = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class Motion(Base):
    """Public Participation Phase 3.2 -- Motion Desk filing.

    Motions deliberate the framework, never a single song's output.
    motion_type taxonomy:

      amend_tenet | new_tenet | remove_tenet -- changes to the tenets
        (the five tiers and their numbered criteria).
      amend_rule  | new_rule  | remove_rule  -- changes to the
        procedural rules (R1, R2, ...) in tenets/core.json.
      process    -- proposals about methodology / morality / AI
        framework that do not target a single tenet or rule id.

    target_kind / target_ref are polymorphic:
      target_kind='tenet'    target_ref='violet-01'    (or rule, modifier)
      target_kind='rule'     target_ref='R1'
      target_kind='modifier' target_ref='contamination'
      Both NULL for process motions and new_tenet/new_rule.

    Lifecycle: filed -> in_deliberation -> ratified | rejected | covered.
    'covered' means already addressed by an existing tenet/rule.

    Tier 2 (id_verified) gates filing. Anonymous + Tier 1 read.

    Songs are NEVER targets. Songs can be cited inside the reasoning
    text as evidence ("songs A and B both read like X under this tenet,
    which is why I'm filing this"). Per-song "the agent got this wrong"
    lives in MisreadSubmission, not here.
    """
    __tablename__ = "motions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    motion_type = Column(Text, nullable=False)
    target_kind = Column(Text)  # tenet | rule | modifier | NULL (process / new_*)
    target_ref = Column(Text)  # id within target_kind, or NULL
    claim = Column(Text, nullable=False)  # one-line summary
    reasoning = Column(Text, nullable=False)  # full argument
    citations = Column(Text)  # JSON array of URL strings
    filed_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(Text, nullable=False, default="filed")
    resolution_summary = Column(Text)
    resolved_at = Column(DateTime)
    resolved_by_admin_id = Column(Integer)
    filed_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class User(Base):
    """Public Participation Tier 1 user (Clerk-backed email + phone account).

    Row is lazily created on the first authenticated API call after Clerk
    JWT verification (require_clerk_user in app/auth.py). handle is NULL
    until the user calls POST /api/users/me/setup -- frontend treats
    handle IS NULL as "onboarding incomplete" and forces the picker.

    tier transitions: pending -> handled (handle claimed) -> id_verified
    (Persona/Stripe Identity, Phase 3). status drives moderation: active
    is normal; suspended sets suspended_until; banned closes the account.
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    clerk_user_id = Column(Text, nullable=False, unique=True)
    handle = Column(Text, unique=True)
    avatar_url = Column(Text)
    tier = Column(Text, nullable=False, default="pending")
    anon_id = Column(Text, nullable=False, unique=True)
    status = Column(Text, nullable=False, default="active")
    banned_at = Column(DateTime)
    banned_reason = Column(Text)
    suspended_until = Column(DateTime)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )
