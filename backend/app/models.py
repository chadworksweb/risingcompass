from sqlalchemy import (
    CheckConstraint, Column, Integer, String, Text, Float, Boolean, Date, DateTime, ForeignKey, Index, LargeBinary, UniqueConstraint, text
)
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base


# Phase 5d (2026-06-05): the four legacy song tables -- compass_songs,
# library_songs, submitted_songs, cl_stream_songs -- and their model classes
# (CompassSong / LibrarySong / SubmittedSong / StreamSong) were removed. The
# unified `songs` table (class Song, below) is the sole song entity; all data
# was folded into it in Phase 2. Removing the classes is required so
# create_all() does not recreate the dropped tables. Migration 088 drops them.


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
    # Unified renovation: the atomic songs.id (legacy compass_song_id dropped in 5d).
    song_id = Column(Integer, ForeignKey("songs.id", ondelete="SET NULL"), nullable=True)
    title = Column(Text, nullable=False)
    artist = Column(Text, nullable=False)
    position = Column(Integer, nullable=False)
    chart_source = Column(Text, default="spotify")

    reading = relationship("DailyReading", back_populates="songs")
    song = relationship("Song", foreign_keys=[song_id])  # unified renovation


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


class AlbumTrack(Base):
    __tablename__ = "album_tracks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    album_id = Column(Integer, ForeignKey("album_deep_dives.id"), nullable=False)
    track_number = Column(Integer, nullable=False)
    name = Column(Text, nullable=False)
    charge_color = Column(Text)
    assessment = Column(Text)

    album = relationship("AlbumDeepDive", back_populates="tracks")


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
    # Unified renovation: the atomic songs.id (legacy compass_song_id dropped in 5d).
    song_id = Column(Integer, ForeignKey("songs.id", ondelete="SET NULL"), nullable=True)
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
    # Migration-added calibration fields (kept in sync with the live schema 2026-05-24)
    activations = Column(Text)
    calibration_failed = Column(Boolean, default=False)
    message_analysis = Column(Text)
    expression_analysis = Column(Text)
    intention_analysis = Column(Text)

    draft = relationship("AgentDraft", back_populates="songs")


class UserCalibration(Base):
    """A song a signed-in user ran through Lyrical Charger.

    One row per (user, canonical song). Re-running the same song updates the
    snapshot + calibrated_at instead of stacking duplicates -- the account
    page shows distinct songs, not a raw event log. Anonymous LC runs never
    land here (no user_id to attribute).

    user_id is a plain Integer with no FK -- mirrors AudienceVibePush. The
    row is written from a SessionLocal() write session distinct from the
    Clerk get_db session that lazily provisions the user, so a hard FK would
    risk a cross-transaction visibility race. The account read filters by
    user_id, which is enough.

    song_source / song_id point at the canonical row the submission reconciled
    against (compass / library / submitted), matching the slug link. The
    snapshot fields (rubric_color, charge_value, charge_summary) are denormalized
    so the account list renders without joining four song tables.
    """
    __tablename__ = "user_calibrations"
    __table_args__ = (
        UniqueConstraint("song_id", "user_id", name="uq_user_calibration_song_uid"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    song_id = Column(Integer, ForeignKey("songs.id", ondelete="SET NULL"))  # unified renovation (5c-2): the atomic songs.id
    song_slug = Column(Text)  # denormalized for the account-page link
    title = Column(Text)
    artist = Column(Text)
    rubric_color = Column(Text)
    charge_value = Column(Integer)
    charge_summary = Column(Text)
    calibrated_at = Column(DateTime, default=datetime.utcnow)  # last run time
    created_at = Column(DateTime, default=datetime.utcnow)     # first run time


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
    # Unified renovation: optional link to the atomic songs.id (legacy
    # submission_id FK to submitted_songs dropped in 5d).
    song_id = Column(Integer, ForeignKey("songs.id", ondelete="SET NULL"))


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
    # Atomic song ref (unified renovation 5c-2), nullable, set by the slug
    # matcher when the submitted (title, artist) resolves cleanly.
    song_id = Column(Integer, ForeignKey("songs.id", ondelete="SET NULL"))


class ProseProvenanceAnchor(Base):
    """External tamper-evident anchor for one sealed societal-prose version.

    Bridges the in-DB sealed provenance (societal_prose_generated_at + model,
    migration 075) to a public append-only GitHub log + an OpenTimestamps
    Bitcoin anchor. Only the hash leaves the DB -- never the prose text. One row
    per (table, row, prose version); see migration 076. Off the calibration hot
    path -- populated by the provenance sweep/upgrade crons, fail-soft.
    """
    __tablename__ = "prose_provenance_anchors"
    __table_args__ = (
        UniqueConstraint("song_table", "song_id", "prose_sha256",
                         name="uq_prose_anchor_version"),
        Index("idx_prose_anchor_ots_status", "ots_status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    song_table = Column(Text, nullable=False)  # compass_songs|library_songs|submitted_songs|stream_songs
    song_id = Column(Integer, nullable=False)
    prose_sha256 = Column(Text, nullable=False)  # sha256(table:id | generated_at | model | prose)
    generated_at = Column(DateTime, nullable=False)  # copy of the sealed generated_at
    model = Column(Text, nullable=False)  # copy of the sealed model ('legacy_unknown' for proxy rows)
    sealed_at = Column(DateTime, default=datetime.utcnow)  # when this anchor row was created
    github_commit_sha = Column(Text)  # commit that recorded this hash in the public log
    github_committed_at = Column(DateTime)
    ots_status = Column(Text, nullable=False, default="pending")  # pending|submitted|complete|failed
    ots_proof_path = Column(Text)  # path to the batch .ots proof in the provenance repo
    ots_bitcoin_block = Column(Integer)  # Bitcoin block height once confirmed
    ots_block_time = Column(DateTime)  # block timestamp once confirmed (optional)
    ots_last_verified_at = Column(DateTime)  # when the integrity cron last ran `ots verify` (migration 077)
    ots_verify_status = Column(Text)  # last re-verify result: ok|mismatch|inconclusive (migration 077)


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
    # Album Charger: album-level synthesized reading (migration 069). NULL on
    # MusicBrainz/Spotify-derived releases that were never run through the
    # Album Charger.
    charge_summary = Column(Text)  # one-paragraph album-level summary
    arc_prose = Column(Text)  # how the album moves across its tracks
    societal_prose = Column(Text)  # what running this album at scale does to a society
    source = Column(String(30))  # 'album_charger' for user-charged albums; else NULL
    submitted_at = Column(DateTime)  # when charged via the Album Charger
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

    Keyed on the atomic songs.id (unified renovation 5c-2). Role enum stays
    small on purpose: primary | featured. Position is display-order within the
    credit string.
    """
    __tablename__ = "song_artists"

    id = Column(Integer, primary_key=True, autoincrement=True)
    song_id = Column(Integer, ForeignKey("songs.id", ondelete="SET NULL"))
    artist_id = Column(Integer, ForeignKey("artists.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(20), nullable=False, default="primary")
    position = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("song_id", "artist_id", name="uq_song_artists_uid_artist"),
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
    # Unified renovation (5c-2): the atomic songs.id.
    song_id = Column(Integer, ForeignKey("songs.id", ondelete="SET NULL"))
    track_number = Column(Integer)

    release = relationship("Release", back_populates="songs")


class SongSlug(Base):
    """Lookup table mapping URL slugs to songs across the three song tables."""
    __tablename__ = "song_slugs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    slug = Column(String(300), unique=True, nullable=False)
    title = Column(Text, nullable=False)
    artist = Column(Text, nullable=False)
    # Unified renovation (5c-2): the atomic songs.id.
    song_id = Column(Integer, ForeignKey("songs.id", ondelete="SET NULL"))
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
    plan_tier = Column(String(32), default="free")
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
    song_id = Column(Integer, ForeignKey("songs.id", ondelete="SET NULL"))  # unified renovation (5c-2): the atomic songs.id
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
    # Migration-added calibration fields (kept in sync with the live schema 2026-05-24)
    activations = Column(Text)
    calibration_failed = Column(Boolean, default=False)


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
    song_id = Column(Integer, ForeignKey("songs.id", ondelete="SET NULL"))  # unified renovation (5c-2): the atomic songs.id
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
    """Audit row for an admin override of an agent-calibrated draft song,
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
    song_id = Column(Integer, ForeignKey("songs.id", ondelete="SET NULL"))  # unified renovation (5c-2): the atomic songs.id
    current_value = Column(Integer, nullable=False, default=0)  # -100..+100
    pushes_up_total = Column(Integer, nullable=False, default=0)
    pushes_down_total = Column(Integer, nullable=False, default=0)
    pushes_agree_total = Column(Integer, nullable=False, default=0)
    last_push_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("song_id", name="uq_vibe_needles_uid"),
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
    song_id = Column(Integer, ForeignKey("songs.id", ondelete="SET NULL"))  # unified renovation (5c-2): the atomic songs.id
    direction = Column(Integer, nullable=False)  # +1 (higher), 0 (agree), -1 (lower)
    user_id = Column(Integer)  # nullable — placeholder for future auth
    device_id = Column(Text)
    ip_address = Column(Text)
    push_year = Column(Integer, nullable=False)
    pushed_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "song_id", "device_id", "push_year",
            name="uq_vibe_pushes_uid_device_year",
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
    song_id = Column(Integer, ForeignKey("songs.id", ondelete="SET NULL"))  # unified renovation (5c-2): the atomic songs.id
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
    song_id = Column(Integer, ForeignKey("songs.id", ondelete="SET NULL"))  # unified renovation (5c-2): the atomic songs.id
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
    # Migration-added calibration fields (kept in sync with the live schema 2026-05-24)
    activations = Column(Text)
    calibration_failed = Column(Boolean, default=False)
    reasoning = Column(Text)


class SongReset(Base):
    """Append-only audit log of resets: calibrations returned to the null state.

    Keyed on the atomic songs.id (unified renovation 5c-2). Carries a full
    before-snapshot so the public history timeline can render what was wiped.
    Distinct from SongRecalibration, which always writes a new charge (satire /
    public-interest re-reads). Resets go the other direction -- back to
    uncalibrated.
    """
    __tablename__ = "song_resets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    song_id = Column(Integer, ForeignKey("songs.id", ondelete="SET NULL"))  # unified renovation (5c-2): the atomic songs.id
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
    song_id = Column(Integer, ForeignKey("songs.id", ondelete="SET NULL"))  # unified renovation (5c-2): the atomic songs.id
    block_text = Column(Text)
    video_url = Column(Text)
    audio_url = Column(Text)
    published = Column(Boolean, nullable=False, default=False)
    published_at = Column(DateTime)
    internal_notes = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("artist_id", "song_id", name="uq_av_blocks_artist_uid"),
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
    song_id = Column(Integer, ForeignKey("songs.id", ondelete="SET NULL"))  # unified renovation (5c-2): the atomic songs.id
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
    result_song_source = Column(String(20))  # 'compass' | 'library' (legacy; drops in 5d)
    result_song_id = Column(Integer)  # legacy; drops in 5d
    song_id = Column(Integer, ForeignKey("songs.id", ondelete="SET NULL"))  # unified renovation (5c-2): the atomic songs.id
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
    song_id = Column(Integer, ForeignKey("songs.id", ondelete="SET NULL"))  # unified renovation (5c-2; target_type='song' only)
    # Self-referential FKs are DEFERRABLE INITIALLY DEFERRED: a top-level
    # comment sets thread_root_id to its own (not-yet-assigned) id, so the
    # check must happen at commit, not at the INSERT flush. (On SQLite this
    # worked only because FK enforcement was off; PG enforces immediately.)
    parent_id = Column(
        Integer, ForeignKey("comments.id", deferrable=True, initially="DEFERRED")
    )
    thread_root_id = Column(
        Integer,
        ForeignKey("comments.id", deferrable=True, initially="DEFERRED"),
        nullable=False,
    )
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


class CommentNotification(Base):
    """A reply-to-you or @mention notification for a Lobby comment.

    type: 'reply' (someone replied to the user's comment) | 'mention'
    (someone @mentioned the user's handle). comment_id points at the
    triggering comment; actor_id is its author. read_at NULL = unread.
    """
    __tablename__ = "comment_notifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    type = Column(Text, nullable=False)
    comment_id = Column(Integer, ForeignKey("comments.id"), nullable=False)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    read_at = Column(DateTime)
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


class MotionArgument(Base):
    """Public Participation Phase 4 -- Deliberation Chamber post.

    One row per structured argument on a motion in_deliberation. Five
    post types: argument_for, argument_against, rebuttal, citation,
    clarification. Rebuttals reference a parent post via parent_id;
    every other type has parent_id NULL. Shape rules and lifecycle
    checks (motion.status must be in_deliberation to POST) live in
    app/services/chamber.py, not here.

    Tier 2 (id_verified) is required to write. Anonymous + Tier 1 read.
    Threads remain public after the motion resolves; the room becomes
    read-only at that point.
    """
    __tablename__ = "motion_arguments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    motion_id = Column(Integer, ForeignKey("motions.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    post_type = Column(Text, nullable=False)
    parent_id = Column(Integer, ForeignKey("motion_arguments.id"))
    summary = Column(Text, nullable=False)  # 1-280 chars
    body = Column(Text, nullable=False)  # 50-5000 chars
    citations = Column(Text)  # JSON array of URL strings
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


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
    # Verified legal name (Stripe Identity) + the consent timestamp for
    # public display in the Deliberation Chamber. Shown only when BOTH are
    # set -- a name captured without consent is never surfaced.
    legal_name = Column(Text)
    legal_name_public_consent_at = Column(DateTime)
    banned_at = Column(DateTime)
    banned_reason = Column(Text)
    suspended_until = Column(DateTime)
    # Billing / metering (migration 072). Two-bucket credit model + Stripe
    # linkage. credit_ledger is the source of truth; these are the
    # denormalised fast-read counts.
    stripe_customer_id = Column(Text)
    stripe_subscription_id = Column(Text)
    subscription_tier = Column(String(30), nullable=False, default="free")
    subscription_status = Column(String(20))
    subscription_period_end = Column(DateTime)
    allowance_credits = Column(Integer, nullable=False, default=0)
    purchased_credits = Column(Integer, nullable=False, default=0)
    # Admin-granted unlimited Lyrical Charger comp (migration 083). Orthogonal
    # to subscription_tier: a comped user keeps tier='free' / no Stripe sub.
    # When true, billing treats every Charger run as zero-cost and the
    # calibrate rate-limiter lifts the per-user daily backstop. Charger-only;
    # Library entitlement is unaffected.
    comp_unlimited = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class CreditLedger(Base):
    """Source of truth for every credit grant and spend (migration 073).

    delta is signed (negative=spend, positive=grant/refund). bucket is one
    of 'allowance', 'purchased', 'rejected' (preflight 402), 'settlement'
    (no-delta settle marker), or 'daily_free' (no-delta marker for a free-tier
    daily-charge pass -- counted per UTC day, never debits a balance bucket).
    Partial UNIQUE(reason, ref_id, bucket) on
    ref_id IS NOT NULL gates Stripe webhook replays; bucket lives in the
    index so a single charge can split across both buckets under the same
    reason+ref_id without colliding.
    """
    __tablename__ = "credit_ledger"
    __table_args__ = (
        # Grant idempotency: a replayed Stripe event (same reason+ref_id+
        # bucket) collides here and no-ops. Declared on the model -- and
        # mirroring migration 073 exactly -- so create_all / pg_baseline build
        # a self-sufficient fresh DB (the numbered migration only runs against
        # already-migrated databases).
        Index(
            "uq_credit_ledger_reason_ref_bucket",
            "reason", "ref_id", "bucket",
            unique=True,
            postgresql_where=text("ref_id IS NOT NULL"),
        ),
        Index("idx_credit_ledger_user_created", "user_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    delta = Column(Integer, nullable=False)
    bucket = Column(String(20), nullable=False)
    reason = Column(String(40), nullable=False)
    ref_type = Column(String(40))
    ref_id = Column(Text)
    context_json = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class GeneralInquiry(Base):
    """General-purpose public inquiry / contact form.

    Reusable across surfaces -- the first caller is the Album Charger's
    "need more than 15 tracks?" link, but the form is generic. `topic`
    classifies the inquiry, `source` records where it was opened from
    (e.g. 'album_charger'). Bot-protected (honeypot + Turnstile) at the
    endpoint; lyrics/PII beyond name+email are not collected.
    """
    __tablename__ = "general_inquiries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(Text)
    email = Column(Text)
    topic = Column(String(40))         # general | album_charger | bug | partnership | data | other
    subject = Column(Text)
    message = Column(Text, nullable=False)
    source = Column(String(40))        # surface the inquiry came from
    page_url = Column(Text)            # referring page path, if supplied
    ip_address = Column(String(45))
    status = Column(String(20), default="new")  # new | read | closed
    created_at = Column(DateTime, default=datetime.utcnow)
    handled_at = Column(DateTime)


class ChartAnomaly(Base):
    """Admin-authored annotation explaining a spike/dip on the daily chart.

    Anchored to a reading date. `anomaly_type` classifies the cause (the first
    and only type so far is 'album_release', where `artist` + `album` name the
    release that flooded the chart). `note` is an optional freeform line used as
    the display fallback for types that carry no artist/album. `active` hides a
    row from the public chart without deleting it. Multiple rows may share a
    date; the chart renders one marker per date and lists them together.
    """
    __tablename__ = "chart_anomalies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, index=True)   # reading date the marker sits on
    anomaly_type = Column(String(40), nullable=False, default="album_release")
    artist = Column(Text)
    album = Column(Text)
    note = Column(Text)                               # freeform fallback / extra context
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AlbumChargeJob(Base):
    """Async job for an Album Charger run.

    Album charging is minutes of sequential Opus work, too long to hold an HTTP
    connection open for. The submit endpoint creates one of these (status
    'queued'), kicks off a background task, and returns the token immediately;
    the frontend polls the status endpoint. The worker updates progress
    (phase + calibrated_tracks) and writes the final AlbumCalibrateOut payload
    into result_json. The work (and the Release write) completes server-side
    even if the client disconnects.
    """
    __tablename__ = "album_charge_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_token = Column(String(64), nullable=False, unique=True)
    status = Column(String(20), nullable=False, default="queued")  # queued|running|done|error
    phase = Column(String(30))           # validating|calibrating|synthesizing|writing|done
    total_tracks = Column(Integer, default=0)      # tracks being calibrated (progress denominator)
    calibrated_tracks = Column(Integer, default=0)
    result_json = Column(Text)           # final AlbumCalibrateOut payload (JSON)
    error_message = Column(Text)
    ip_address = Column(String(45))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ===========================================================================
# Unified song-entity renovation (migrations 081/082). `songs` is the atomic
# unit AND the entire Library; "charting" is a derived role via chart
# appearances; ingestion is a logged dimension. The four legacy song tables
# (compass_songs, library_songs, submitted_songs, cl_stream_songs) fold into
# `songs` in Phase 2 and are dropped in Phase 5. See
# RISING-COMPASS-SONG-ENTITY-RENOVATION.md.
# ===========================================================================


class Song(Base):
    """The atomic song entity = the entire Library. One row per canonical
    (title, artist); holds the single canonical calibration."""
    __tablename__ = "songs"
    __table_args__ = (
        UniqueConstraint("canonical_key", name="uq_songs_canonical_key"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(Text, nullable=False)   # display, original casing
    artist = Column(Text, nullable=False)  # display, original casing
    # normalize_for_search(title) + '\x1f' + normalize_for_search(normalize_artist_name(artist))
    canonical_key = Column(Text, nullable=False)
    # --- canonical calibration (identical column set across the 4 legacy tables) ---
    rubric_color = Column(Text)            # nullable: the Library includes uncalibrated songs
    charge_value = Column(Integer)         # -100 to +100
    charge_summary = Column(Text)
    contaminated = Column(Boolean, default=False)
    contamination_note = Column(Text)
    dogma_referenced = Column(Boolean, default=False)
    dogma_note = Column(Text)
    instrumental = Column(Boolean, default=False)
    confidence = Column(Float)
    effects_prose = Column(Text)
    societal_effects_prose = Column(Text)
    societal_prose_generated_at = Column(DateTime)
    societal_prose_model = Column(Text)
    prior_effects_prose = Column(Text)
    prior_societal_effects_prose = Column(Text)
    prior_societal_prose_generated_at = Column(DateTime)
    prior_societal_prose_model = Column(Text)
    deadpan_line = Column(Text)
    topics = Column(Text)
    topic_audit = Column(Text)
    activations = Column(Text)
    calibration_failed = Column(Boolean, default=False)
    message_analysis = Column(Text)
    expression_analysis = Column(Text)
    intention_analysis = Column(Text)
    # --- library linkage (from library_songs) ---
    album_id = Column(Integer, ForeignKey("album_deep_dives.id"), nullable=True)
    track_number = Column(Integer)
    # method that owns the current canonical calibration -- gates overwrite rules
    # (authoritative chart_reading/editorial/terminal beats crowd lyrical_charger/stream)
    canonical_calibration_method = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class Chart(Base):
    """A chart definition (Billboard Year-End Hot 100, Spotify Top 50, ...)."""
    __tablename__ = "charts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    slug = Column(Text, nullable=False, unique=True)
    label = Column(Text, nullable=False)
    # server_default so create_all-built tables carry the DB default the
    # migration-081 seed INSERT relies on (the NOT NULL column is omitted there).
    cadence = Column(Text, nullable=False, default="annual", server_default="annual")  # annual|daily|weekly
    provider = Column(Text)
    active = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    created_at = Column(DateTime, default=datetime.utcnow)


class ChartAppearance(Base):
    """A song's placement on a chart at a time. Charting = EXISTS an appearance."""
    __tablename__ = "chart_appearances"
    __table_args__ = (
        UniqueConstraint(
            "song_id", "chart_id", "year", "position", "position_letter",
            name="uq_chart_appearance",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    song_id = Column(Integer, ForeignKey("songs.id", ondelete="CASCADE"), nullable=False)
    chart_id = Column(Integer, ForeignKey("charts.id"), nullable=False)
    year = Column(Integer)
    period = Column(Date)  # specific date for daily/weekly charts; NULL for annual
    position = Column(Integer)
    position_letter = Column(Text, nullable=False, server_default="", default="")
    created_at = Column(DateTime, default=datetime.utcnow)


class SongIngestion(Base):
    """How a song entered the corpus (logged dimension; one+ per song)."""
    __tablename__ = "song_ingestions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    song_id = Column(Integer, ForeignKey("songs.id", ondelete="CASCADE"), nullable=False)
    # chart_reading | lyrical_charger | api_client | terminal | editorial | stream
    method = Column(Text, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    api_client_id = Column(Integer)
    ip_address = Column(String(45))  # new home for submitted_songs.ip_address PII
    detail = Column(Text)            # JSON: stream source_url/platform, submitted source, etc.
    created_at = Column(DateTime, default=datetime.utcnow)


class SongIdMap(Base):
    """Migration-only old (source, id) -> new songs.id mapping. Permanent through
    Phase 5 as the reverse-mapping rollback net."""
    __tablename__ = "song_id_map"

    old_source = Column(Text, primary_key=True)  # compass|library|submitted|stream
    old_id = Column(Integer, primary_key=True)
    new_song_id = Column(Integer, ForeignKey("songs.id", ondelete="CASCADE"), nullable=False)
    canonical_key = Column(Text, nullable=False)


class DevLedgerItem(Base):
    """Dev Ledger -- the "dev side, exposed". One pipeline drives changelog,
    roadmap, feature requests, and bug reports; CalVer is the spine.

    item_type: 'feature' (request) | 'bug' (report) | 'change' (admin-authored
    roadmap/changelog work item).

    Walled from Motion Desk / Misread Reports / Deliberation Chamber: those are
    the tenet/framework layer; this is the product/engineering layer. See
    RISING-COMPASS-DEV-LEDGER-SCOPE.md.
    """
    __tablename__ = "dev_ledger_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    item_type = Column(String(20), nullable=False)  # feature | bug | change
    title = Column(Text, nullable=False)
    body = Column(Text, nullable=False)  # markdown, public-safe prose
    # feature/bug: submitted->triaging->accepted->in_progress->shipped | declined | duplicate
    # change: planned->in_progress->shipped
    status = Column(String(20), nullable=False, default="submitted")
    stage = Column(String(20))  # roadmap bucket: now | next | later
    version = Column(String(20))  # CalVer release id, set when shipped
    severity = Column(String(10))  # bugs only: low | med | high | critical
    area = Column(String(40))  # charts | calibration | billing | account | site ...
    submitted_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    vote_count = Column(Integer, nullable=False, default=0)  # denormalized rollup of dev_ledger_votes
    is_public = Column(Boolean, nullable=False, default=False)  # gate: hidden until admin publishes
    admin_note = Column(Text)  # internal triage note, never served publicly
    resolution = Column(Text)  # public "why closed / how shipped"
    duplicate_of_id = Column(Integer, ForeignKey("dev_ledger_items.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    published_at = Column(DateTime)  # when is_public first set true
    shipped_at = Column(DateTime)  # when status -> shipped
    resolved_by_admin_id = Column(Integer)

    submitted_by = relationship("User", foreign_keys=[submitted_by_user_id])


class DevLedgerVote(Base):
    """One vote per Clerk user per Dev Ledger item (feature/bug). The parent
    item's vote_count is the denormalized rollup, kept in sync in the service
    layer."""
    __tablename__ = "dev_ledger_votes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    item_id = Column(Integer, ForeignKey("dev_ledger_items.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("item_id", "user_id", name="uq_dev_ledger_vote_item_user"),)


# ---------------------------------------------------------------------------
# Faultline -- internal error ledger + agent-driven triage.
#
# Self-contained reliability subsystem. NO foreign keys INTO business tables;
# the single out-reference is the nullable dev_ledger_item_id (the one-way
# "promote a confirmed fault to a public Dev Ledger bug" seam). Capture is
# decoupled from all app logic -- faults arrive via a logging.Handler on the
# root logger, not via imports. See RISING-COMPASS-FAULTLINE-SCOPE.md.
# JSON-bearing columns are Text holding a JSON string (house style, matches
# lc_events.payload_json) -- not jsonb.
# ---------------------------------------------------------------------------

class ErrorSignature(Base):
    """One row per DISTINCT fault, keyed by `fingerprint` (a normalized hash of
    exception type + innermost stack frames). Repeats of the same code fault
    collapse here and bump occurrence_count rather than spawning new rows --
    this is what turns an error flood into a finite worklist."""
    __tablename__ = "error_signatures"

    id = Column(Integer, primary_key=True, autoincrement=True)
    fingerprint = Column(String(64), nullable=False, unique=True)
    exc_type = Column(String(120))                      # e.g. ResponseValidationError
    title = Column(Text, nullable=False)                # normalized one-liner
    component = Column(String(160))                     # logger name, e.g. app.routers.analyzer
    route = Column(Text)                                # best-effort request route
    severity = Column(String(10), nullable=False, default="medium")  # low|medium|high|critical
    area = Column(String(40))                           # set on triage: calibration|billing|...
    status = Column(String(20), nullable=False, default="new")       # lifecycle (see scope S5)
    environment = Column(String(10), nullable=False, default="local")  # local|prod
    first_seen_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_seen_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    occurrence_count = Column(Integer, nullable=False, default=0)
    last_traceback = Column(Text)                       # freshest full traceback
    last_context = Column(Text)                         # JSON: path, method, actor, ids...
    assigned_to = Column(String(120))                   # agent id or admin handle
    claimed_by = Column(String(120))                    # lease holder (agent worker id)
    claim_expires_at = Column(DateTime)                 # lease TTL
    dev_ledger_item_id = Column(Integer, ForeignKey("dev_ledger_items.id", ondelete="SET NULL"), nullable=True)
    resolution = Column(Text)
    resolved_at = Column(DateTime)
    resolved_by = Column(String(120))
    muted = Column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("idx_error_sig_status_sev_seen", "status", "severity", "last_seen_at"),
        Index("idx_error_sig_env_status", "environment", "status"),
    )


class ErrorOccurrence(Base):
    """One row per individual hit of a signature. Retention-pruned (a later
    phase) to the most recent N per signature. Carries the per-event context an
    agent needs to reproduce."""
    __tablename__ = "error_occurrences"

    id = Column(Integer, primary_key=True, autoincrement=True)
    signature_id = Column(Integer, ForeignKey("error_signatures.id", ondelete="CASCADE"), nullable=False)
    occurred_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    traceback = Column(Text)
    context = Column(Text)                               # JSON blob
    environment = Column(String(10), nullable=False, default="local")

    __table_args__ = (
        Index("idx_error_occ_sig_time", "signature_id", "occurred_at"),
    )


class ErrorAction(Base):
    """The phase-management timeline: every triage/status/diagnosis/fix move on
    a signature, by an agent or a human. This table IS the audit trail an agent
    builds as it works a fault."""
    __tablename__ = "error_actions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    signature_id = Column(Integer, ForeignKey("error_signatures.id", ondelete="CASCADE"), nullable=False)
    action_type = Column(String(24), nullable=False)    # claim|release|triage|status_change|diagnosis|proposed_fix|applied_fix|verification|comment|promote|resolve|reopen
    actor_type = Column(String(10), nullable=False)     # agent|admin|system
    actor_ref = Column(String(120))                     # agent_model or admin handle
    from_status = Column(String(20))
    to_status = Column(String(20))
    note = Column(Text)
    payload = Column(Text)                               # JSON blob
    idempotency_key = Column(String(80))                # dedupe re-fired agent calls
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_error_action_sig_time", "signature_id", "created_at"),
        UniqueConstraint("signature_id", "idempotency_key", name="uq_error_action_idem"),
    )
