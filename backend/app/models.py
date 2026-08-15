from sqlalchemy import (
    CheckConstraint, Column, Integer, BigInteger, String, Text, Float, Boolean, Date, DateTime, ForeignKey, Index, JSON, LargeBinary, UniqueConstraint, text
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
    chart_source = Column(Text, default="spotify_top50_usa")

    reading = relationship("DailyReading", back_populates="songs")
    song = relationship("Song", foreign_keys=[song_id])  # unified renovation


class ChartSnapshot(Base):
    """Per-chart daily top-N snapshot. Independent of daily_readings.

    Each (date, chart_source) is a self-contained list. Per-song charge values
    are looked up live against the unified songs table at render time — the row
    stores only the chart's own (title, artist, position). The aggregate
    compass_degree / charge_level for the whole snapshot ARE stored (computed +
    persisted at approval), so the chart-agnostic Calendar can paint each day
    its spectrum color without recomputing the aggregate on every read.
    """
    __tablename__ = "chart_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False)
    chart_source = Column(Text, nullable=False)
    position = Column(Integer, nullable=False)
    title = Column(Text, nullable=False)
    artist = Column(Text, nullable=False)
    # Aggregate for the whole (date, chart) snapshot, stamped on every row of the
    # snapshot at approval (denormalised, equal across the day's rows). Nullable:
    # fetch-time/unpublished rows have none until the chart draft is approved.
    compass_degree = Column(Float, nullable=True)
    charge_level = Column(Text, nullable=True)
    # Per-chart editorial summary, denormalised onto every row of the snapshot at
    # approval (equal across the day's rows). Reuses the editorial the compass
    # agent already generated onto the chart's AgentDraft. Nullable: unpublished
    # rows and pre-editorial snapshots carry none.
    editorial = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    # Approval gate: rows are written unpublished by the scraper/refresh, then
    # flipped to True when the chart draft is approved (agent.approve_draft).
    # The public endpoint only serves published rows, so an unapproved or
    # half-calibrated chart never leaks. Mirrors the daily reading's approve-
    # before-public flow.
    published = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    # Charting on pre-order: no songs row / reading yet. Rendered as "Pre-order"
    # on the panel, excluded from the snapshot aggregate.
    preorder = Column(Boolean, nullable=False, default=False, server_default=text("false"))

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
    # Temporary null disposition: charting on pre-order with no lyrics yet. Exempt
    # from the approval gate, excluded from aggregates, NOT a cache hit -- re-lists
    # until real lyrics drop. Sibling to instrumental, but lifecycle differs.
    preorder = Column(Boolean, default=False)
    # Permanent null disposition: a RELEASED song whose lyrics are genuinely
    # unobtainable (not published anywhere). Exempt from the approval gate and
    # excluded from aggregates like preorder, but -- unlike preorder -- it IS a
    # cache hit (a persistent songs row carries the flag), so the feeder stops
    # re-listing it daily. Cleared if real lyrics later surface and supersede it.
    lyrics_unavailable = Column(Boolean, default=False)
    # Permanent null disposition: a track with NO LYRICS TO READ. A placeholder --
    # no charge, no color, renders grey, excluded from every aggregate. Like
    # lyrics_unavailable it is a permanent cache hit, so the feeder stops
    # re-listing it. Distinct claim: instrumental asserts there is nothing to
    # read, lyrics_unavailable asserts the lyrics exist but cannot be obtained.
    instrumental = Column(Boolean, default=False)
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

    song_id points at the canonical unified `songs` row the submission reconciled
    against (post song-entity renovation, schema_version 88 -- the old polymorphic
    song_source + four-table model is gone), matching the slug link. The snapshot
    fields (rubric_color, charge_value, charge_summary) are denormalized so the
    account list renders without joining the song table.
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

    Not editorial. This is the badge-serving layer.
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
    listener_effects_prose = Column(Text)  # what the album does to a listener (mig 090)
    societal_effects_prose = Column(Text)  # what running this album at scale does to a society
    # Album-level Ether Art Chart entry (mig 090): the album as a first-class
    # ether subject, parallel to a song's deadpan_line/topics/topic_audit.
    deadpan_line = Column(Text)
    topics = Column(Text)       # JSON-encoded list of taxonomy slugs
    topic_audit = Column(Text)  # JSON-encoded audit dict, or NULL when topics present
    source = Column(String(30))  # 'album_charger' for user-charged albums; else NULL
    submitted_at = Column(DateTime)  # when charged via the Album Charger
    # --- the rest of the v3 release reading (migration 148) ---
    # The rc-album lens emits a full v3 reading; migrations 069/090 only had room
    # for the prose. This is the SONG column set (see Song below), so a release
    # answers the same questions a song does. The v3 COMPONENTS (visceral,
    # coherence, harm/transcendence, center, vernier) are deliberately absent --
    # like a song's, they live per-run on calibration_runs (migration 149).
    contaminated = Column(Boolean, default=False)
    contamination_note = Column(Text)
    dogma_referenced = Column(Boolean, default=False)
    dogma_note = Column(Text)
    confidence = Column(Float)
    # JSON-encoded Text, same convention as topics: the prescription for taking
    # in the WHOLE album, composed from the release's own finished reading.
    psyche_facts = Column(Text)
    effects_pl = Column(Text)
    calibration_failed = Column(Boolean, default=False)
    # Prose seal + one-step-back archive, matching the song provenance contract.
    # prior_arc_prose has no song counterpart -- arc_prose is release-only and
    # shipped in 069 with no archive slot, and all three lanes archive or none
    # of them is a contract.
    societal_prose_generated_at = Column(DateTime)
    societal_prose_model = Column(Text)
    prior_listener_effects_prose = Column(Text)
    prior_societal_effects_prose = Column(Text)
    prior_arc_prose = Column(Text)
    prior_societal_prose_generated_at = Column(DateTime)
    prior_societal_prose_model = Column(Text)
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


class DraftSongEdit(Base):
    """Audit log for hand-corrections to a draft song's title or artist credit.

    Written in the same transaction as the edit, so the log can never disagree
    with reality (same discipline as ArtistAdminEvent below).

    Why this exists at the DRAFT layer: the feeders credit whatever the platform
    hands them, which can be an upload channel rather than a performer, and the
    title + artist pair is what mints the `songs` row and its canonical key. A
    bad credit corrected AFTER calibration is a merge; corrected BEFORE, it is
    just a string. So the correction happens on the draft, and this records it.

    Deliberately separate from ArtistAdminEvent: that table is shaped around an
    Artist entity and requires a name + slug before, while the string being
    corrected here was typically never an artist at all.
    """
    __tablename__ = "draft_song_edits"

    id = Column(Integer, primary_key=True, autoincrement=True)
    occurred_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    actor = Column(String(64))

    # Draft coordinates. Kept as loose values, not FKs: chart drafts are DELETED
    # at approval, and the audit has to outlive them.
    draft_song_id = Column(Integer)
    draft_label = Column(Text)
    position = Column(Integer)
    song_id = Column(Integer)

    # Only the changed side is populated; an untouched field stays NULL on both
    # halves, so "what actually changed" reads straight off the row.
    title_before = Column(Text)
    title_after = Column(Text)
    artist_before = Column(Text)
    artist_after = Column(Text)

    reason = Column(Text)
    environment = Column(String(16), nullable=False, default="prod")


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


class ReleaseSuppression(Base):
    """A curated "this is not that artist's catalogue" exclusion (migration 147).

    The codified filter cannot reach a release MusicBrainz files as official, of
    a valid type, credited to the artist -- the Beatles' Tony Sheridan sessions
    and fan-club Christmas discs are the canonical example. Deleting those by
    hand does not stick, because rebuild-releases re-fetches from MusicBrainz and
    re-creates them. The resolve consults this table and skips a match.

    Keyed on the NORMALISED title (see suppressed_titles), not the MBID, so the
    exclusion survives MusicBrainz re-filing the group under a new id.
    """
    __tablename__ = "release_suppressions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    artist_id = Column(Integer, ForeignKey("artists.id", ondelete="CASCADE"), nullable=False)
    title_norm = Column(Text, nullable=False)
    title_snapshot = Column(Text, nullable=False)
    reason = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class MbCoverArt(Base):
    """Cover Art Archive lookup cache, keyed by the MusicBrainz release-group
    MBID -- NOT by releases.id, which churns whenever an artist's releases are
    rebuilt. Keying on the stable MBID means the cache survives rebuilds and
    each release-group is fetched from CAA exactly once, ever.

    Display URLs are derived from the MBID (see services/coverart.coverart_urls);
    only existence + freshness are stored. has_art=False is a recorded
    "checked, CAA had none" -- the read path renders the tier dot in that case
    and never re-queries.
    """
    __tablename__ = "mb_cover_art"

    musicbrainz_id = Column(Text, primary_key=True)  # release-group MBID
    has_art = Column(Boolean, nullable=False, default=False)
    checked_at = Column(DateTime, default=datetime.utcnow)


class SongCoverArtReport(Base):
    """A reader's "this is the wrong cover" report on a song page (migration 152).

    The two automated checks catch a wrong ARTIST (the artist-credit check in
    musicbrainz._pick_release_group) and a release issued years after the song
    charted (scripts/audit_song_cover_art.py). Neither can catch a right artist
    on a contemporaneous but wrong release, and nothing in the data can -- a
    person looking at the page is the only check left, so this is how what they
    saw gets back.

    No account required: wrong art is a factual claim anyone can see. A report is
    safe to leave open because it CHANGES NOTHING on its own -- an admin resolves
    every one.

    `reported_mbid` is the pick that was serving art AT FILING TIME, not the
    song's current one, so a re-resolve in between can't turn the report into a
    complaint about a picture nobody ever saw. `mbid_source` says whether that
    came from the song's own resolved group or from its linked Release, because
    the two are fixed in different places.

    The `confirmed` rows ARE the rejection list the backfill excludes from a
    --recheck-misses re-resolve; deriving it rather than storing it twice keeps
    the two from drifting. See the migration for the full reasoning.
    """
    __tablename__ = "song_cover_art_reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    song_id = Column(Integer, ForeignKey("songs.id", ondelete="CASCADE"), nullable=False)
    reported_mbid = Column(Text)
    mbid_source = Column(String(10))                     # song | release
    note = Column(Text)
    device_id = Column(Text)
    ip_address = Column(Text)
    status = Column(String(12), nullable=False, default="open")   # open | confirmed | dismissed
    environment = Column(String(10), nullable=False, default="prod")
    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime)
    resolved_by = Column(String(120))                    # admin username
    resolution_note = Column(Text)

    __table_args__ = (
        Index("ix_cover_art_reports_queue", "environment", "status", "created_at"),
        Index("ix_cover_art_reports_song", "song_id"),
        # One report per device per song per pick -- scoped to the MBID, so that
        # art re-resolved to something else can be reported again by the same
        # person. It is a new claim about a new picture.
        Index("uq_cover_art_report_device", "song_id", "device_id", "reported_mbid",
              unique=True,
              postgresql_where=text("device_id IS NOT NULL"),
              sqlite_where=text("device_id IS NOT NULL")),
    )


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
    row drifts toward the MEDIAN of live runs as they accumulate) and the
    training corpus for future agent improvements.

    Polymorphic song pointer is nullable so a run can exist before or without
    a persisted canonical song row. title + artist snapshot regardless.
    Lyrics themselves are never stored — only a SHA-256 hash for dedupe /
    variance awareness.

    Also logs RELEASE readings (migration 149): the rc-album lens emits the same
    v3 component shape, so an album run is this table with `release_id` set and
    `song_id` NULL. The album lane is lyric-free by construction — it reads
    approved song ROWS, never lyrics — so its runs carry no hash and no
    fingerprint, and `coherence` carries the axis `route` carries for a song.
    """
    __tablename__ = "calibration_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    song_id = Column(Integer, ForeignKey("songs.id", ondelete="SET NULL"))  # unified renovation (5c-2): the atomic songs.id
    # A run keys to a song OR a release (migration 149). SET NULL because a
    # catalogue rebuild churns releases.id and the ledger has to outlive it.
    release_id = Column(Integer, ForeignKey("releases.id", ondelete="SET NULL"))
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
    # Calibrator v3 components + incoherence signals (migration 116). The model
    # emits components; the server composes the charge and derives the tier.
    # Stored per run so the composition is auditable and the escalation gate
    # has signals. Internal-only: never surfaced outside admin.
    visceral_charge = Column(Integer)  # System-1 first-impression placement
    route = Column(String(40))  # internal_work | collective_stance | encouragement | witness_critique | doctrinal | static_portrait | negative_payload
    # The album lane's structural axis (migration 149): coherent | anthology --
    # do the tracks answer each other, or sit side by side. Lens-specific and
    # sharing this table exactly as `route` does; NULL on every song run.
    coherence = Column(String(20))
    harm_value = Column(Integer)  # harm axis read, 0..-100
    harm_pervasive = Column(Boolean, default=False, nullable=False)  # R8 pervasiveness; forces harm governance
    transcendence_value = Column(Integer)  # transcendence axis read, 0..+100
    governing_axis = Column(String(15))  # server's governance decision: harm | transcendence | neutral
    center = Column(Integer)  # precedent-placed center before the vernier shift
    vernier = Column(Text)  # JSON {sat, res, reg, reach}, each -2..+2, pole-agnostic
    precedent_refs = Column(Text)  # JSON array of precedent-table entry ids anchoring the placement
    gut_divergence = Column(Integer)  # abs(charge - visceral_charge); the headline incoherence signal
    guard_trips = Column(Integer, default=0, nullable=False)  # output-guard trips this run
    parse_retries = Column(Integer, default=0, nullable=False)  # JSON parse retries this run
    escalation_flags = Column(Text)  # JSON: trigger slugs fired (+ first-pass snapshot on re-pass); NULL = clean
    escalated = Column(Boolean, default=False, nullable=False)  # an escalation re-pass actually ran
    translated = Column(Boolean, default=False, nullable=False)  # this run read a translation (original unavailable)


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


class ArtistOutreach(Base):
    """Manual outreach-touch ledger for the Artist CRM (Hockey Stick Build 8).

    One row per outbound touch Chad logs by hand: which artist, which song's
    charge was sent, on what channel, and WHEN. Single-song outreach only for
    now. Hangs off the artist (not the verification row), so a touch can be
    logged before/without a funnel record. song_title is a display snapshot so
    the history survives a later song deletion (song_id then goes NULL).
    """
    __tablename__ = "artist_outreach"

    id = Column(Integer, primary_key=True, autoincrement=True)
    artist_id = Column(Integer, ForeignKey("artists.id", ondelete="CASCADE"), nullable=False)
    song_id = Column(Integer, ForeignKey("songs.id", ondelete="SET NULL"))
    song_title = Column(Text)
    channel = Column(String(20), nullable=False, default="email")  # email | dm | other
    contact_used = Column(Text)  # the address/handle it actually went to
    sent_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    notes = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    artist = relationship("Artist")


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
    # Display timezone for the admin area (IANA name). Times are stored in UTC;
    # this is purely the per-admin render zone. Default America/New_York (ET).
    timezone = Column(String(64), nullable=False, default="America/New_York")
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


class EtherTheme(Base):
    """A top-level Ether theme -- the shelf a topic is filed under. Phase 1 of
    the admin taxonomy editor makes the DB the source of truth for the theme
    LIST, LABELS, and ORDER (presentation-authoritative). The live song tagger
    is unchanged: its topic slug set + scope/examples stay code-driven in
    services/ether_taxonomy.py. Seeded once from the ETHER_THEMES code constant;
    the resolver falls back to the code constants when this table is empty or
    unreachable, so the public surface never breaks. See
    RISING-COMPASS-TAXONOMY-EDITOR-SCOPE.md.
    """
    __tablename__ = "ether_themes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    slug = Column(Text, nullable=False, unique=True)
    label = Column(Text, nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class EtherTopic(Base):
    """A topic and its placement in the theme hierarchy. Phase 1 stores the
    PRESENTATION facts only: display label, the single primary theme (the strict
    tree every rollup sums on), secondary theme facets (never summed), and order.
    NO scope/examples here in Phase 1 -- those stay in ETHER_TAXONOMY (code) for
    the tagger. A topic row whose slug is not in ETHER_TAXONOMY is "DB-only" (the
    tagger cannot classify it yet); the admin surfaces that as a badge.

    Topic-slug renames are disabled in Phase 1 because songs.topics JSON stores
    slugs -- a rename would orphan history (a Phase 2 alias-migration concern).
    Theme-slug renames ARE safe (only these rows reference a theme; updated in
    the same transaction).
    """
    __tablename__ = "ether_topics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    slug = Column(Text, nullable=False, unique=True)
    label = Column(Text, nullable=False)
    primary_theme_slug = Column(Text, nullable=False)
    # JSON array of theme slugs; optional facets, never summed.
    secondary_themes = Column(JSON, nullable=False, default=list)
    sort_order = Column(Integer, nullable=False, default=0)
    # Phase 2: the tagger DEFINITION. `scope` is the one-line meaning the
    # classifier reads; `examples` is a JSON list of {artist, title, why}. NULL
    # scope = a topic the tagger cannot use yet (no definition). When the
    # `taxonomy_db_driven.enabled` flag is on, these drive the live tagger prompt
    # + the valid-slug set; otherwise the code constants (ETHER_TAXONOMY) do.
    scope = Column(Text, nullable=True)
    examples = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class LyricalChargerSubscriber(Base):
    __tablename__ = "lyrical_charger_subscribers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(Text, nullable=False, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    notified_at = Column(DateTime, nullable=True)


class RcSubscriber(Base):
    """On-site email-subscriber layer -- the top of RC's own subscriber funnel
    (Hockey Stick Build 2b). Distinct from LyricalChargerSubscriber, which is
    the LC-outage notice list (a different consent purpose).

    Double opt-in: a row is created `pending` with a confirm_token, then flips
    to `confirmed` when the tokenized link is clicked. unsubscribe_token gives a
    stable one-click opt-out.

    No-duplicate / promote-to-Clerk: email_hash (sha256 of the normalized email)
    is the match key against users.email_hash. When the subscriber becomes a
    Clerk account (or an account holder subscribes later), user_id + promoted_at
    link the two -- no duplicate identity. Plaintext email lives only here and in
    Clerk, never on users.
    """
    __tablename__ = "rc_subscribers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(Text, nullable=False, unique=True)
    email_hash = Column(String(64), nullable=False, index=True)
    status = Column(String(16), nullable=False, default="pending")  # pending | confirmed | unsubscribed
    source = Column(String(40), nullable=True)
    source_detail = Column(Text, nullable=True)
    confirm_token = Column(String(64), nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    unsubscribe_token = Column(String(64), nullable=False)
    unsubscribed_at = Column(DateTime, nullable=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    promoted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    # Date key (YYYY-MM-DD) of the last reading digest sent -- per-recipient
    # dedup so a re-run only targets those who have not yet received it.
    last_digest_key = Column(String(10), nullable=True)
    # Notification-category toggles (opt-out model: all default true). A subscriber
    # turns any off from the tokenized preference center. See services/subscribers
    # NOTIFY_CATEGORIES; daily_reading gates the digest, the other two gate the
    # admin-composed broadcasts (moments of notice, updates/releases).
    pref_daily_reading = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    pref_moments_of_notice = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    pref_config_updates = Column(Boolean, nullable=False, default=True, server_default=text("true"))


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


class ShopProduct(Base):
    """A Printify product synced into RC's storefront (/shop/).

    Mirrors chadlewine's `merch` row for a `printify_curated` product, trimmed
    to what the RC shop needs. Products are pulled from the Printify custom-
    integration shop by services/shop_sync.py -- Printify is the source of
    truth for title/description/images/variants/price; RC keeps a stable slug +
    display order + status here so the grid + detail pages read from Postgres,
    not a live Printify call per request.

    `variants` is a JSON-encoded Text list of the ENABLED variants only:
      [{"id": <printify_variant_id>, "title", "size", "color", "price_cents"}]
    (RC convention: per-row JSON bundles are Text, json.dumps'd -- same as
    songs.topics / psyche_facts.) `image_urls` is a JSON-encoded Text list of
    gallery image src strings (default image first).
    """

    __tablename__ = "shop_products"

    id = Column(Integer, primary_key=True, autoincrement=True)
    printify_product_id = Column(Text, nullable=False, unique=True)
    slug = Column(Text, nullable=False, unique=True)
    title = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    image_url = Column(Text, nullable=True)          # hero / default image
    image_urls = Column(Text, nullable=True)         # JSON list of gallery srcs
    price = Column(Float, nullable=True)             # lowest enabled-variant price, dollars
    variants = Column(Text, nullable=True)           # JSON list (enabled variants)
    status = Column(Text, nullable=False, default="active")  # active | inactive
    display_order = Column(Integer, nullable=False, default=0)
    last_synced_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ShopOrder(Base):
    """A completed shop purchase. Written by the Stripe cart webhook on
    checkout.session.completed, then pushed to Printify as an order (auto sent
    to production) and updated by the Printify order-status webhook.

    `line_items` is a JSON-encoded Text list:
      [{"printify_product_id", "variant_id", "quantity", "title",
        "variant_label", "price_cents"}]
    Money is stored in cents (Stripe's native unit). `user_id` is set when a
    signed-in Clerk user checked out; anonymous orders leave it NULL and are
    keyed by buyer_email.
    """

    __tablename__ = "shop_orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_number = Column(Text, nullable=False, unique=True)
    stripe_session_id = Column(Text, nullable=False, unique=True)
    stripe_payment_intent_id = Column(Text, nullable=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    buyer_email = Column(Text, nullable=True)
    buyer_name = Column(Text, nullable=True)
    phone = Column(Text, nullable=True)

    subtotal_cents = Column(Integer, nullable=False, default=0)
    shipping_cents = Column(Integer, nullable=False, default=0)
    total_cents = Column(Integer, nullable=False, default=0)
    currency = Column(Text, nullable=False, default="usd")

    ship_line1 = Column(Text, nullable=True)
    ship_line2 = Column(Text, nullable=True)
    ship_city = Column(Text, nullable=True)
    ship_state = Column(Text, nullable=True)
    ship_zip = Column(Text, nullable=True)
    ship_country = Column(Text, nullable=True)

    line_items = Column(Text, nullable=True)  # JSON list (see docstring)

    # Fulfillment lifecycle: paid -> in_production -> shipped -> delivered,
    # plus cancelled / error. printify_order_id links back to Printify.
    status = Column(Text, nullable=False, default="paid")
    printify_order_id = Column(Text, nullable=True)
    printify_error = Column(Text, nullable=True)
    carrier = Column(Text, nullable=True)
    tracking_number = Column(Text, nullable=True)
    tracking_url = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    pushed_to_printify_at = Column(DateTime, nullable=True)
    shipped_at = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)


class ShopSubscriber(Base):
    """Coming-soon / "notify me" list for the shop while it launches dark.
    Mirrors LyricalChargerSubscriber (simple single-step capture, no double
    opt-in): store an email, notify when the shop opens."""

    __tablename__ = "shop_subscribers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(Text, nullable=False, unique=True)
    notified_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


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
    # sha256 of the normalized Clerk email. The ONLY email-derived value on this
    # row (the table is otherwise pseudonymous, no plaintext email) -- it is the
    # link key for the rc_subscribers layer, populated fail-soft at provision.
    email_hash = Column(String(64))
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


class CalibrateJob(Base):
    """Async job for a single-song (public Lyrical Charger) calibration.

    A public charge is several sequential Opus calls (identity -> calibrate ->
    listener -> ether -> societal -> save), long enough that holding the HTTP
    connection open read as a hang and the bar could only fake progress. The
    /calibrate-lyrics/start endpoint validates synchronously, creates one of
    these (status 'queued'), launches a background task, and returns the token;
    the frontend polls the status endpoint and drives a REAL bar from `phase`.
    The worker writes the final LyricsCalibrateOut payload into result_json --
    every terminal status (scored, run_capped, not_commercial_warning,
    lyrics_mismatch, lyrics_diverge_from_prior, saved_view_on_page, error) is
    delivered through it. Mirrors AlbumChargeJob; migration 126.
    """
    __tablename__ = "calibrate_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_token = Column(String(64), nullable=False, unique=True)
    status = Column(String(20), nullable=False, default="queued")  # queued|running|done|error
    # queued|identity|calibrating|listener|ether|societal|saving|done
    phase = Column(String(30))
    result_json = Column(Text)           # final LyricsCalibrateOut payload (JSON)
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
    # Phase-1 identity resolution: canonical_key computed AFTER the closed feeder-
    # cruft cleaning pass (app.services.feeder_clean). Indexed, NOT unique -- a
    # collision is a duplicate to surface, not an error. NULL until backfilled
    # (migration 122) on legacy rows; stamped on every write going forward. See
    # RISING-COMPASS-SONG-IDENTITY-RESOLUTION.md.
    canonical_key_clean = Column(Text)
    # --- canonical calibration (identical column set across the 4 legacy tables) ---
    rubric_color = Column(Text)            # nullable: the Library includes uncalibrated songs
    charge_value = Column(Integer)         # -100 to +100
    charge_summary = Column(Text)
    contaminated = Column(Boolean, default=False)
    contamination_note = Column(Text)
    dogma_referenced = Column(Boolean, default=False)
    dogma_note = Column(Text)
    instrumental = Column(Boolean, default=False)
    # Released song with genuinely unobtainable lyrics. A permanent NULL-tier
    # cache hit (sibling to instrumental, but no rubric_color and a distinct
    # public label): resolves the feeder so the song stops re-listing as
    # awaiting-lyrics, carries no charge, and is excluded from every aggregate.
    lyrics_unavailable = Column(Boolean, default=False)
    translated = Column(Boolean, default=False)  # calibrated off a translation of non-English lyrics
    medley = Column(Boolean, default=False)  # calibration reads a curated multi-song medley as one arc
    preorder = Column(Boolean, default=False)  # charting on pre-order; no lyrics yet, awaiting release
    confidence = Column(Float)
    listener_effects_prose = Column(Text)
    societal_effects_prose = Column(Text)
    societal_prose_generated_at = Column(DateTime)
    societal_prose_model = Column(Text)
    prior_listener_effects_prose = Column(Text)
    prior_societal_effects_prose = Column(Text)
    prior_societal_prose_generated_at = Column(DateTime)
    prior_societal_prose_model = Column(Text)
    deadpan_line = Column(Text)
    topics = Column(Text)
    topic_audit = Column(Text)
    activations = Column(Text)
    # Psyche Facts family (migration 138): the "Drug Facts" prescription bundle,
    # JSON-encoded Text (same convention as topics/topic_audit). Sibling keys:
    # purpose, indicated_for[], do_not_use_if, directions, onset, duration,
    # warning. The psyche_effects tag axis joins this family once its vocab is
    # re-derived. Terminal-supplied via calibrate_song.py --psyche-facts-file.
    psyche_facts = Column(Text)
    # Per-listen effects (migration 140): the psyche_effects tag axis. A
    # JSON-encoded list of slugs from the closed, RC-owned vocabulary in
    # services/effects_pl_vocab.py, stored like topics. RC is the source; the
    # badge resolves the display labels. Terminal-supplied + validated.
    effects_pl = Column(Text)
    calibration_failed = Column(Boolean, default=False)
    message_analysis = Column(Text)
    expression_analysis = Column(Text)
    intention_analysis = Column(Text)
    # --- library linkage (from library_songs); album_id is now a plain
    # nullable int -- the album_deep_dives editorial table was dropped (mig 089).
    album_id = Column(Integer, nullable=True)
    track_number = Column(Integer)
    # method that owns the current canonical calibration -- gates overwrite rules
    # (authoritative chart_reading/catalog_backfill/terminal beats crowd lyrical_charger/stream)
    canonical_calibration_method = Column(Text)
    # The chart a song FIRST surfaced on (the chart_source of its earliest
    # chart_reading ingestion); stamped once, immutable, NULL for non-chart
    # births (lyrical_charger / terminal / catalog_backfill). Build 7 -- the
    # gutter-vs-mainstream origin signal (degraded music tends to surface via
    # the social-discovery charts: Shazam, YouTube Trending).
    origin_chart = Column(Text)
    # --- cover art (migration 146) ---
    # The MusicBrainz release-GROUP MBID this song's art comes from, resolved by
    # the offline backfill (scripts/backfill_song_cover_art.py), never on the
    # request path -- MusicBrainz is 1 req/sec and would stall the page. Art
    # itself is NOT stored: mb_cover_art says whether CAA has any, and the URL is
    # derived from this MBID (services/coverart.coverart_urls). Songs already
    # linked to a Release read that release's MBID instead and never need this.
    release_group_mbid = Column(Text)
    # When the MBID resolve last RAN, independent of whether it found anything.
    # Set + release_group_mbid NULL means "searched MusicBrainz, no confident
    # match" -- a recorded miss, so the backfill skips the song instead of
    # re-searching it every pass. NULL means never searched.
    release_group_checked_at = Column(DateTime)
    # MB's first-release-date for the picked release group (migration 151), as MB
    # reports it -- "1972", "1972-02", "1972-02-01". TEXT, not DATE: MB dates are
    # variable-precision and compare correctly as strings. Kept so a bad pick is
    # detectable in bulk: the artist-credit check stops a wrong ARTIST, but a right
    # artist on an archival set or reissue is only visible against the calendar
    # (charted 1972, release group dated 2022). NULL on rows resolved before 151.
    release_group_date = Column(Text)
    # server_default so the raw-SQL insert in song_sync.upsert_unified_song (the
    # unified write chokepoint) stamps a time -- a client-side `default=` only
    # fires on ORM inserts, which this table never uses. See migration 116.
    created_at = Column(DateTime, default=datetime.utcnow, server_default=text("(now() at time zone 'utc')"))


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
    # chart_reading | lyrical_charger | api_client | terminal | catalog_backfill | stream
    method = Column(Text, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    api_client_id = Column(Integer)
    ip_address = Column(String(45))  # new home for submitted_songs.ip_address PII
    detail = Column(Text)            # JSON: stream source_url/platform, submitted source, etc.
    created_at = Column(DateTime, default=datetime.utcnow)


class SongIdentityAlias(Base):
    """Human-confirmed identity bridge -- rung 1b of resolve_song_identity.

    One row = "this incoming feeder string IS that Library song", asserted by a
    human. It exists for the strings the deterministic rungs cannot reach and
    SHOULD NOT be widened to reach: a feeder that carries a version marker the
    cleaner deliberately preserves ("MORNING DEW" vs the stored "MORNING DEW
    (DONK)"), or one crediting a channel where the Library row carries the real
    performer ("DisneyMusic" vs the stored "Descendants Cast"). Widening a rung to
    catch those would false-merge genuine remixes and same-title works; a
    human-confirmed row cannot.

    Written automatically by scripts/server_only/relink_draft_song.py, so the
    relink that fixes today's draft is the LAST relink for that string.

    alias_key is compute_canonical_key_clean() of the string the feeder sent (the
    clean normalizer, so diacritics fold). UNIQUE: one incoming identity resolves
    to exactly one song. Merges repoint aliases onto the survivor (song_merge).
    """
    __tablename__ = "song_identity_aliases"
    __table_args__ = (
        UniqueConstraint("alias_key", name="uq_song_identity_aliases_key"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    alias_key = Column(Text, nullable=False)
    song_id = Column(Integer, ForeignKey("songs.id", ondelete="CASCADE"), nullable=False)
    # The raw incoming strings, kept for humans auditing the table (the key is
    # normalized past recognition).
    alias_title = Column(Text)
    alias_artist = Column(Text)
    source = Column(Text, nullable=False, default="relink")  # relink | admin
    notes = Column(Text)
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


class ClutterAudit(Base):
    """LEIT clutter-control audit queue -- the single human-review surface for
    songs that shouldn't be in the Library/corpus as a commercially released
    track. Two feeders, one queue:

      - source='lc_push'     -- a Lyrical Charger submitter was warned the paste
                                didn't look like a released song and pushed it
                                through anyway (confirm_commercial=true).
      - source='daily_sweep' -- the daily LEIT sweep agent flagged a song that
                                already slipped in (gibberish, unknown non-artist,
                                or content that belongs on Creative/Curio Charger).

    Flag-only: a row here never changes the live site. An admin reviews and
    resolves it (keep / remove / dismiss). Tagged `environment` like Faultline
    because local dev shares the prod DB via the tunnel -- the admin queue MUST
    filter by env so local test rows don't pollute the prod worklist.
    """
    __tablename__ = "clutter_audits"

    id = Column(Integer, primary_key=True, autoincrement=True)
    song_id = Column(Integer, ForeignKey("songs.id", ondelete="SET NULL"))
    source = Column(String(16), nullable=False)          # lc_push | daily_sweep
    category = Column(String(24), nullable=False)        # non_commercial | gibberish | unknown_person | wrong_charger
    suggested_action = Column(String(40))                # e.g. route_to_creative | route_to_curio | delete | review
    reason = Column(Text)                                # the verdict/finding rationale
    confidence = Column(Float)                            # nullable; sweep findings carry one
    status = Column(String(16), nullable=False, default="open")  # open | kept | removed | dismissed
    environment = Column(String(10), nullable=False, default="local")  # local | prod
    payload_json = Column(Text)                          # title/artist/ip snapshot at detection
    detected_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    reviewed_at = Column(DateTime)
    reviewed_by = Column(String(120))                    # admin handle
    review_notes = Column(Text)

    __table_args__ = (
        Index("idx_clutter_env_status", "environment", "status"),
        Index("idx_clutter_song", "song_id"),
        # One OPEN finding per song -- a re-sweep or repeat push won't stack
        # duplicate open rows; resolved rows (kept/removed/dismissed) are exempt
        # so history accumulates.
        Index("uq_clutter_open_song", "song_id", unique=True,
              postgresql_where=text("status = 'open' AND song_id IS NOT NULL"),
              sqlite_where=text("status = 'open' AND song_id IS NOT NULL")),
    )


class SentinelAuditor(Base):
    """Sentinel Auditor Team -- enrollment funnel (ships DARK).

    One row per user who applies to the bug-bounty-style red-team program. The
    program invites outsiders to hunt for holes in RC's results/algorithm instead
    of defending against them. Apply + admin approve: a signed-in Tier-1 user
    submits an application (motivation + focus area); an admin moves status
    pending -> approved | rejected, or later revoked. Only `approved` auditors
    may file findings. Mirrors the artist_verifications review funnel; apply-once
    is enforced by UNIQUE(user_id).
    """
    __tablename__ = "sentinel_auditors"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, unique=True)
    status = Column(String(16), nullable=False, default="pending")  # pending|approved|rejected|revoked
    motivation = Column(Text, nullable=False)
    focus_area = Column(String(24), nullable=False)      # algorithm|methodology|data|ux|other
    handle_snapshot = Column(String(120))                # denorm of the handle for the admin queue
    review_notes = Column(Text)
    reviewed_by = Column(String(120))                    # admin username
    reviewed_at = Column(DateTime)
    applied_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    findings = relationship("SentinelFinding", back_populates="auditor",
                            cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_sentinel_auditors_status", "status"),
    )


class SentinelFinding(Base):
    """Sentinel Auditor Team -- a single submitted finding + its triage state.

    scope='song' attaches to a specific song (song_id, ON DELETE SET NULL -- the
    `scope` column survives the song's deletion); scope='general' carries no song
    and uses the category enum (algorithm|methodology|data|ux|other). The auditor
    proposes a severity; the admin can override it (accepted_severity). The status
    lifecycle mirrors faultline_triage:

      new -> triaged -> investigating -> confirmed -> fixed -> accepted   (valid)
      new -> rejected | duplicate | wont_fix                              (dismissals)

    Reputation is DERIVED, not a running counter: `points_awarded` is a
    point-in-time snapshot stamped (from accepted_severity, falling back to
    proposed_severity) when a finding ENTERS `accepted`, and zeroed if it is later
    reopened. The leaderboard sums points_awarded over accepted findings. Tagged
    `environment` like clutter_audits / faultline so the admin queue keeps local
    test rows (shared tunnel DB) out of the prod worklist.
    """
    __tablename__ = "sentinel_findings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    auditor_id = Column(Integer, ForeignKey("sentinel_auditors.id", ondelete="CASCADE"),
                        nullable=False)
    song_id = Column(Integer, ForeignKey("songs.id", ondelete="SET NULL"))
    scope = Column(String(8), nullable=False)            # song | general
    category = Column(String(16), nullable=False)        # algorithm|methodology|data|ux|other
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=False)
    evidence_url = Column(Text)
    proposed_severity = Column(String(10), nullable=False)  # low|medium|high|critical
    accepted_severity = Column(String(10))                  # admin override; reputation keys off this
    status = Column(String(16), nullable=False, default="new")
    disposition = Column(Text)                            # admin response shown back to the auditor
    points_awarded = Column(Integer, nullable=False, default=0)  # snapshot at acceptance
    environment = Column(String(10), nullable=False, default="local")  # local | prod
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    reviewed_by = Column(String(120))                    # admin username
    reviewed_at = Column(DateTime)

    auditor = relationship("SentinelAuditor", back_populates="findings")
    song = relationship("Song")

    __table_args__ = (
        Index("ix_sentinel_findings_auditor", "auditor_id"),
        Index("ix_sentinel_findings_status", "status"),
        Index("ix_sentinel_findings_env_status", "environment", "status"),
        Index("ix_sentinel_findings_song", "song_id"),
    )


class SentinelWaitlist(Base):
    """Sentinel Auditor Team -- notify-me list captured on the landing while
    applications are closed. Single-step capture (no double opt-in); mirrors
    LyricalChargerSubscriber. A manual admin dispatch emails everyone unnotified
    when intake opens and stamps notified_at. Isolated from rc_subscribers."""
    __tablename__ = "sentinel_waitlist"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(Text, nullable=False, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    notified_at = Column(DateTime, nullable=True)


class SongMergeCandidate(Base):
    """Song identity-resolution Phase 2: the human-audit queue for likely
    DUPLICATE song rows (the same song minted twice under different formatting).
    Mirrors `clutter_audits`: one OPEN row per unordered pair, env-tagged (local
    dev shares the prod DB via the tunnel), resolved merge | keep_separate |
    dismiss by an admin. NEVER auto-merges -- the songs-not-artists rule means a
    cover/remix/live version that shares a title must be confirmed, not collapsed.

    Pair stored canonically (source_song_id < target_song_id) so the partial
    unique dedups it; merge DIRECTION (which row survives) is the admin's choice
    at resolve time. Feeders: migration-122 clean-key collisions
    (reason='clean_collision'), the resolve ladder's trgm gray band
    (reason='trgm'), and manual admin entry (reason='manual')."""
    __tablename__ = "song_merge_candidates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_song_id = Column(Integer, ForeignKey("songs.id", ondelete="SET NULL"))
    target_song_id = Column(Integer, ForeignKey("songs.id", ondelete="SET NULL"))
    reason = Column(String(24), nullable=False)          # clean_collision | trgm | manual
    confidence = Column(Float)                            # trgm similarity; 1.0 for exact clean collision
    detected_by = Column(String(24), nullable=False, default="backfill")  # backfill | resolve_ladder | admin
    status = Column(String(16), nullable=False, default="open")  # open | merged | kept_separate | dismissed
    environment = Column(String(10), nullable=False, default="local")  # local | prod
    payload_json = Column(Text)                          # optional snapshot at detection
    # server_default so the RAW-SQL insert in song_sync (the resolve-ladder trgm
    # path) populates it; a client-side default fires only on ORM inserts.
    detected_at = Column(DateTime, nullable=False, default=datetime.utcnow,
                         server_default=text("CURRENT_TIMESTAMP"))
    reviewed_at = Column(DateTime)
    reviewed_by = Column(String(120))                    # admin handle
    review_notes = Column(Text)

    __table_args__ = (
        Index("idx_merge_cand_env_status", "environment", "status"),
        Index("idx_merge_cand_source", "source_song_id"),
        Index("idx_merge_cand_target", "target_song_id"),
        Index("uq_merge_cand_open_pair", "source_song_id", "target_song_id", unique=True,
              postgresql_where=text("status = 'open'"),
              sqlite_where=text("status = 'open'")),
    )


class SongMergeEvent(Base):
    """Permanent audit log of applied song merges (mirrors artist_admin_events).
    One row per merge: who, the source/target snapshot, and the rewrites
    breakdown. Survives the source song's deletion (no FK -- ids are recorded as
    plain values)."""
    __tablename__ = "song_merge_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # server_default so the merge service's raw-SQL INSERT (which omits this) is
    # safe on a fresh create_all DB too, matching the migration's DDL default.
    occurred_at = Column(DateTime, nullable=False, default=datetime.utcnow,
                         server_default=text("CURRENT_TIMESTAMP"))
    actor = Column(String(120))                          # admin handle | 'system'
    source_song_id = Column(Integer)                     # deleted row's id (no FK)
    source_title = Column(Text)
    source_artist = Column(Text)
    target_song_id = Column(Integer)
    target_title = Column(Text)
    target_artist = Column(Text)
    rewrites_json = Column(Text)                          # per-table repoint counts
    notes = Column(Text)
    environment = Column(String(10), nullable=False, default="local")


class AgentRun(Base):
    """One row per run of an in-house Rising Compass agent -- the operational
    ledger behind the admin "Agents" mini-warehouse (Dusty the clutter sweep is
    the first resident). This is the agent's OWN activity log: when it ran, by
    what trigger, whether it succeeded, and how much it found -- kept separate
    from what it FOUND (`clutter_audits` holds the findings). Generic + agent_id-
    keyed so future RC agents share the same home.

    Health on the admin page is derived from these rows (last run recency +
    status), since these agents are cron-triggered, not long-running daemons.
    Cost is derived separately from `claude_api_usage` by call_site. Env-tagged
    like the other LEIT tables (local dev shares the prod DB via the tunnel)."""
    __tablename__ = "agent_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(String(40), nullable=False)         # e.g. 'custodian-001'
    trigger = Column(String(16), nullable=False, default="cron")  # cron | admin
    status = Column(String(16), nullable=False, default="running")  # running | ok | error
    scanned = Column(Integer, nullable=False, default=0)
    flagged = Column(Integer, nullable=False, default=0)
    error = Column(Text)                                  # set on status='error'
    environment = Column(String(10), nullable=False, default="local")
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    finished_at = Column(DateTime)
    duration_ms = Column(Integer)

    __table_args__ = (
        Index("idx_agent_runs_agent_started", "agent_id", "started_at"),
        Index("idx_agent_runs_env", "environment"),
    )


class AlltimeStreamSong(Base):
    """The Most-Streamed Songs of All Time chart (Spotify, GLOBAL lifetime
    streams). One row per chart slot (top 100), refreshed MONTHLY by scraping
    kworb.net (the only public source of lifetime Spotify stream totals -- a
    US-only all-time total does not exist anywhere). This is a current-state
    table (the chart IS these 100 rows, upserted in place), NOT a dated snapshot,
    so it is deliberately separate from `chart_snapshots` / the daily-reading
    pipeline.

    Stream rank + counts are the real chart data and always render. The
    calibration fields (tier/charge/deadpan/topics) are denormalized off the
    unified `songs` row at refresh time via `lookup_calibrated`, so the public
    page reads one table with no join. They are populated only on a cache HIT --
    the monthly cron makes no Anthropic calls; misses are flagged for manual
    `calibrate_song.py` and render as an "untagged" ether row until calibrated."""
    __tablename__ = "alltime_stream_songs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rank = Column(Integer, nullable=False)                 # 1..100, kworb row order
    title = Column(Text, nullable=False)
    artist = Column(Text, nullable=False)
    total_streams = Column(BigInteger)                     # lifetime (exceeds INT4)
    daily_streams = Column(Integer)
    song_id = Column(Integer, ForeignKey("songs.id", ondelete="SET NULL"))
    non_music = Column(Boolean, default=False)             # white-noise/sleep/ASMR -> nulled + tagged
    # Denormalized calibration snapshot (cache-hit fill from lookup_calibrated):
    rubric_color = Column(String(20))
    charge_value = Column(Integer)
    deadpan_line = Column(Text)
    topics = Column(Text)                                  # JSON-encoded list
    song_slug = Column(Text)
    artist_slug = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_alltime_stream_songs_rank", "rank"),
    )


class AlltimeAlbum(Base):
    """The Best-Selling Albums of All Time chart (US / RIAA certified units).
    One row per chart slot (top 100), maintained by a MANUAL annual sweep (this
    list changes once every few years) -- no cron. An admin editor owns the row;
    `last_reviewed_at` drives a data-driven staleness banner.

    Self-contained: the display fields (charge + album-level deadpan + topics)
    are copied onto the row from album synthesis output
    (`services/album_synthesis.py::generate_album_synthesis`, which already emits
    a whole-album `deadpan_line` + `topics`). `release_id` links the calibrated
    Release for provenance; the chart row is the source of truth for what renders."""
    __tablename__ = "alltime_albums"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rank = Column(Integer, nullable=False)                 # 1..100
    album_title = Column(Text, nullable=False)
    artist = Column(Text, nullable=False)
    certified_units = Column(Text)                         # display, e.g. "38x Platinum"
    units_millions = Column(Float)                          # sortable numeric (US units, millions)
    release_year = Column(Integer)
    release_id = Column(Integer, ForeignKey("releases.id", ondelete="SET NULL"))
    non_music = Column(Boolean, default=False)             # parallel to instrumental: nulled + tagged
    # Denormalized calibration snapshot (from album synthesis):
    rubric_color = Column(String(20))
    charge_value = Column(Integer)
    charge_summary = Column(Text)
    deadpan_line = Column(Text)
    topics = Column(Text)                                  # JSON-encoded list
    artist_slug = Column(Text)
    release_slug = Column(Text)
    last_reviewed_at = Column(DateTime)                    # drives the staleness banner
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_alltime_albums_rank", "rank"),
    )


class AlltimeStreamAlbum(Base):
    """The Most-Streamed Albums of All Time chart (Spotify, GLOBAL lifetime
    streams). The streaming-era twin of `AlltimeAlbum` (RIAA physical sales):
    surfaces the modern albums the sales list misses. Current-state table (top
    100), refreshed MONTHLY by scraping kworb.net. Stream rank + counts are the
    real data; the calibration columns are denormalized off a matching charged
    Release at refresh time (auto-link by title+artist), parallel to how the
    songs board fills off `lookup_calibrated`."""
    __tablename__ = "alltime_stream_albums"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rank = Column(Integer, nullable=False)
    album_title = Column(Text, nullable=False)
    artist = Column(Text, nullable=False)
    total_streams = Column(BigInteger)
    daily_streams = Column(Integer)
    release_id = Column(Integer, ForeignKey("releases.id", ondelete="SET NULL"))
    # Denormalized calibration snapshot (auto-linked Release at refresh):
    rubric_color = Column(String(20))
    charge_value = Column(Integer)
    charge_summary = Column(Text)
    deadpan_line = Column(Text)
    topics = Column(Text)                                  # JSON-encoded list
    artist_slug = Column(Text)
    release_slug = Column(Text)
    non_music = Column(Boolean, default=False)             # nulled + tagged, parallel to instrumental
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_alltime_stream_albums_rank", "rank"),
    )


# --- Build 6: social broadcaster (own-account automated broadcast) ----------
# RISING-COMPASS-HOCKEY-STICK-PLAN.md Build 6. The cron renders + queues the
# day's trending verdicts + the daily-aggregate reading to RC's Buffer queue and
# records each here so nothing is broadcast twice. Migration 121.

class SocialCard(Base):
    """The rendered 1080x1080 PNG for one broadcast item. One card is shared by
    every platform the item fans out to. Served at /api/social/card/{token}.png
    so Buffer can fetch the media by URL (token = unguessable public handle)."""
    __tablename__ = "social_cards"

    id = Column(Integer, primary_key=True, autoincrement=True)
    token = Column(Text, nullable=False, unique=True)
    scope = Column(Text, nullable=False)        # 'song' | 'reading'
    ref = Column(Text, nullable=False)          # song_id (str) | reading date ISO
    png = Column(LargeBinary, nullable=False)   # BYTEA on Postgres
    content_type = Column(Text, nullable=False, default="image/png")
    created_at = Column(DateTime, default=datetime.utcnow, server_default=text("(now() at time zone 'utc')"))


class SocialPost(Base):
    """One broadcast of one item to one platform. dedup_key (UNIQUE) is both the
    idempotency guard and the selection-dedup key, so a re-run never double-posts
    and the next day's selection can exclude already-broadcast songs/readings."""
    __tablename__ = "social_posts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    scope = Column(Text, nullable=False)        # 'song' | 'reading'
    song_id = Column(Integer, ForeignKey("songs.id", ondelete="SET NULL"), nullable=True)
    reading_date = Column(Date, nullable=True)
    platform = Column(Text, nullable=False)
    # 'song:{song_id}:{platform}' | 'reading:{date}:{platform}'
    dedup_key = Column(Text, nullable=False, unique=True)
    post_text = Column(Text, nullable=False)
    card_token = Column(Text, nullable=True)    # -> social_cards.token
    charge_value = Column(Integer, nullable=True)   # audit snapshot
    tier = Column(Text, nullable=True)              # audit snapshot
    trending_source = Column(Text, nullable=True)   # 'shazam' | 'youtube' (song scope)
    post_external_id = Column(Text, nullable=True)  # Buffer update id
    # prepared | dark | queued | posted | skipped | error
    status = Column(Text, nullable=False, default="prepared")
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=text("(now() at time zone 'utc')"))
    posted_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_social_posts_song", "song_id"),
        Index("ix_social_posts_reading_date", "reading_date"),
        Index("ix_social_posts_created_at", "created_at"),
    )


class Resonance(Base):
    """Audience Resonance -- one person's testimony about one already-scored song,
    sliced into a PROPORTIONAL verdict (True / Camouflage / Adjacent, summing to
    100). RC's fourth instrument (its own section, not Audience Vibe); never
    overrides the rubric. Isolated table, FK to the unified songs(id). The
    "did we misread your story" review rides on flag_state (distinct from the
    song-charge satire flag). is_synthetic fences build/demo seed from real
    aggregates. See migration 130."""

    __tablename__ = "resonances"

    id = Column(Integer, primary_key=True, autoincrement=True)
    song_id = Column(Integer, ForeignKey("songs.id", ondelete="CASCADE"), nullable=False)
    # Signed-in attribution; mirrors user_calibrations / audience_vibe_pushes (no FK).
    user_id = Column(Integer, nullable=True)
    username = Column(String(120), nullable=False)
    story_text = Column(Text, nullable=False)
    # Proportional verdict; the three sum to 100.
    prop_true = Column(Integer, nullable=False, default=0)
    prop_camouflage = Column(Integer, nullable=False, default=0)
    prop_adjacent = Column(Integer, nullable=False, default=0)
    # JSON-encoded line-by-line "shows its work" (TEXT-JSON, like songs.topics).
    slice_attribution = Column(Text, nullable=True)
    # publish | private (delete is a hard row removal, not a status).
    consent_tier = Column(String(20), nullable=False, default="private")
    # none | flagged | in_review | upheld | corrected.
    flag_state = Column(String(20), nullable=False, default="none")
    # The contesting note ("did we misread your story") -- the training signal
    # for the resonance rubric. Set at submit (preemptive flag) or via /flag.
    flag_reason = Column(Text, nullable=True)
    # Coherence-check result (fabrication signal): JSON {coherent, score, reasons,
    # layer}. A non-coherent result routes the row to flag_state='in_review'.
    coherence_json = Column(Text, nullable=True)
    # Hand-authored build/demo seed; excluded from every real aggregate.
    is_synthetic = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=text("(now() at time zone 'utc')"))
    updated_at = Column(DateTime, default=datetime.utcnow, server_default=text("(now() at time zone 'utc')"), onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_resonances_song", "song_id"),
        Index("ix_resonances_consent", "consent_tier"),
        Index("ix_resonances_flag", "flag_state"),
    )


class ResonanceSliceJob(Base):
    """Durable async slice job for Audience Resonance (start -> token -> poll ->
    reveal). Backs the slicer across worker restarts and multiple uvicorn workers
    so /submit can resolve the server-computed verdict by token. slice_json holds
    the computed slice dict (prop_*, slice_attribution, status). See migration 132."""

    __tablename__ = "resonance_slice_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_token = Column(String(64), nullable=False, unique=True)
    status = Column(String(20), nullable=False, default="queued")  # queued|running|done|error
    slice_json = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=text("(now() at time zone 'utc')"))
    updated_at = Column(DateTime, default=datetime.utcnow, server_default=text("(now() at time zone 'utc')"), onupdate=datetime.utcnow)


class SongProseVersion(Base):
    """Append-only history of a song's generated prose, one row per lane per write.

    The calibration side already has `calibration_runs`; this is the same posture
    for the generated text. `songs.prior_*` remains the one-step-back read that
    the badge and the admin page use, and this table is the depth behind it.

    Lanes: listener | societal | psyche_facts (the last stored as its JSON string,
    since it carries the same overwrite exposure and has no archive column).
    `topics` and `effects_pl` are deliberately out of scope: they are tags, cheap
    to re-derive, and the calibration audit already records the read they were
    chosen under.

    Song pointer is nullable and NOT a FK so the history outlives a merged or
    deleted song. See migration 145.
    """

    __tablename__ = "song_prose_versions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    written_at = Column(DateTime, default=datetime.utcnow,
                        server_default=text("(now() at time zone 'utc')"), nullable=False)

    song_id = Column(Integer)
    title = Column(Text)
    artist = Column(Text)

    lane = Column(String(20), nullable=False)  # listener | societal | psyche_facts
    prose = Column(Text, nullable=False)

    model = Column(String(80))
    generated_at = Column(DateTime)

    trigger = Column(String(40))

    # The read this version was written for, so a stale-prose mismatch is a
    # column comparison rather than a reading of the text.
    rubric_color = Column(String(20))
    charge_value = Column(Integer)

    environment = Column(String(16), nullable=False, default="prod")

    __table_args__ = (
        Index("ix_song_prose_versions_song", "song_id", "id"),
        Index("ix_song_prose_versions_written", "written_at"),
    )


class ReleaseProseVersion(Base):
    """Append-only history of a release's generated prose. The album twin of
    SongProseVersion, deliberately identical so the two read as one system.

    A release carries THREE prose lanes (arc, listener, societal) plus the
    psyche facts bundle, and migration 148 gives each a single `prior_*` slot --
    one regen deep. Re-composing release prose is not a re-read of one lyric
    sheet but a re-read of every approved row in the running order, so what a
    shallow archive drops here is expensive.

    Release pointer is nullable and NOT a FK because a catalogue rebuild churns
    `releases.id` by design, so routine maintenance would orphan the history.
    See migration 150.
    """

    __tablename__ = "release_prose_versions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    written_at = Column(DateTime, default=datetime.utcnow,
                        server_default=text("(now() at time zone 'utc')"), nullable=False)

    release_id = Column(Integer)
    title = Column(Text)
    artist = Column(Text)

    lane = Column(String(20), nullable=False)  # arc | listener | societal | psyche_facts
    prose = Column(Text, nullable=False)

    model = Column(String(80))
    generated_at = Column(DateTime)

    trigger = Column(String(40))

    # The read this version was written for.
    rubric_color = Column(String(20))
    charge_value = Column(Integer)

    environment = Column(String(16), nullable=False, default="prod")

    __table_args__ = (
        Index("ix_release_prose_versions_release", "release_id", "id"),
        Index("ix_release_prose_versions_written", "written_at"),
    )
