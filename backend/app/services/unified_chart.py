"""The Unified Charge Chart composer: many daily charts -> one ranked chart.

Pure computation. No HTTP, no writes, no commit. Phase 3 persists what this
returns; Phase 4 serves it. Keeping the math in one side-effect-free function is
the same discipline compass_calc.py and topic_pages.py already follow, and it is
what makes the guarantees below testable without a database.

WHAT IT DOES

Every daily chart RC reads is a top truncation of the same consumption curve,
seen through one platform's lens. This unions them: a song's UNIFIED WEIGHT is
the sum of its normalized rank weights across every chart it appeared on. The
master charge is the weighted mean of the songs' charges under those weights, and
the master ranking is those same songs ordered by that weight.

    unified_weight(song) = SUM over charts c of:
        source_weight(c) * position_weight(rank) / W(c)

    W(c) = sum of position_weight over chart c's ELIGIBLE slots

WHY THE UNION, NOT AN AVERAGE OF THE FOUR CHART DEGREES

Averaging the stored per-chart degrees is one query and produces a number and
nothing else: no ranked list, no #1, no way to say which songs drove the reading.
It also inherits each chart's stored aggregate as-is. Unioning the songs yields
that same number for free PLUS the chart, computes from song rows rather than
from denormalized aggregates, and makes cross-source corroboration fall out of
the arithmetic: a song on all four charts accumulates roughly four times the
weight of a comparable song on one. No consensus coefficient, no bonus
multiplier, no new knob to defend.

This is drift._aggregate_live_year rotated ninety degrees. That function unions a
song's appearances across many DAYS and accumulates effective_weight as a sum of
position_weight. This one unions across SOURCES on one day. Same mechanism.

WHY EACH CHART NORMALIZES TO 1.0 FIRST

W(c) is the total rank weight a chart hands out: at ZIPF_S = 1.0 over 20 slots
that is the harmonic number H(20) = 3.598. Dividing by it makes every chart hand
out exactly one unit of vote no matter how deep it runs, so DEPTH IS A COVERAGE
DECISION AND NEVER A WEIGHT DECISION. Skip this and taking Shazam to its full Top
200 tomorrow would silently hand it 63% more say than iTunes, on nobody's
decision. That is precisely the failure RISING-COMPASS-CHARGE-WEIGHTING.md
section 1 fixed inside a single chart, arriving again between charts, and it gets
the same fix: make the weight depend on the decision, not on the group size.

ELIGIBILITY, AND WHY IT IS STRICT

A slot counts only when it is IDENTIFIED (song_id present) and CALIBRATED (a
reading, no null disposition, no failed calibration). Unidentified is the
important one: the same song reaches RC spelled differently by every feeder, so
a union that fell back to title+artist matching would count one song twice and
destroy the corroboration the whole design rests on. NULL song_id is therefore
ineligible rather than something to resolve here.

W(c) is computed over the ELIGIBLE slots, so a chart still hands out its full
1.0 after exclusions rather than being quietly docked for them.

Full rationale, the measured divergence, and the open decisions:
RISING-COMPASS-UNIFIED-CHARGE-CHART-SCOPE.md
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date as _date

from sqlalchemy.orm import Session

from app.constants import (
    UNIFIED_CONSTITUENT_SLUGS,
    UNIFIED_COVERAGE_FLOOR,
    chart_weighting,
    has_null_disposition,
)
from app.models import ChartSnapshot, DailyReading, ReadingSong, Song
from app.services.charge_calc import degree_to_charge
from app.services.compass_calc import compute_degree, position_weight

logger = logging.getLogger(__name__)

# The daily reading is not a chart snapshot; it has its own table pair.
_DAILY_READING_SLUG = "spotify_top50_usa"


@dataclass
class Slot:
    """One chart position on one chart, with the song it resolved to."""
    position: int
    song_id: int | None
    song: Song | None

    @property
    def eligible(self) -> bool:
        """Identified AND calibrated. Both halves are load-bearing.

        A NULL song_id is an unresolved feeder string; counting it by name would
        reintroduce the double-count this chart exists to avoid. An uncalibrated
        or null-disposition song carries no reading to average.
        """
        if self.song_id is None or self.song is None:
            return False
        if getattr(self.song, "calibration_failed", False):
            return False
        if has_null_disposition(self.song):
            return False
        return self.song.charge_value is not None or self.song.rubric_color is not None


@dataclass
class ChartContribution:
    """What one constituent chart contributed to the day."""
    slug: str
    source_weight: float
    slots: int
    eligible: int
    coverage: float          # eligible share of the chart's raw rank weight
    total_rank_weight: float  # W(c), over eligible slots

    def as_dict(self) -> dict:
        return {
            "slug": self.slug,
            "weight": round(self.source_weight, 4),
            "slots": self.slots,
            "eligible": self.eligible,
            "coverage": round(self.coverage, 4),
        }


@dataclass
class ComposedSong:
    """One song's place in the union."""
    song_id: int
    title: str
    artist: str
    charge_value: int | None
    rubric_color: str | None
    contaminated: bool
    unified_weight: float
    sources: dict[str, int] = field(default_factory=dict)  # slug -> rank

    @property
    def chart_count(self) -> int:
        return len(self.sources)


@dataclass
class ComposedReading:
    """The composed day. Phase 3 persists this; Phase 4 serves it."""
    date: _date
    compass_degree: float
    charge_level: str
    contamination_count: int
    songs: list[ComposedSong]                     # ranked, heaviest first
    sources_included: list[ChartContribution]
    sources_excluded: list[dict]                 # [{slug, reason}]
    weights: dict[str, float]

    @property
    def song_count(self) -> int:
        return len(self.songs)


def default_weights() -> dict[str, float]:
    """Equal weight per constituent, the pre-registered v1.

    Not because every platform deserves equal say, but because RC cannot yet
    defend any other split with evidence, and a weighting invented to produce a
    preferred number is exactly the attack the whitepaper spec was written to
    avoid.

    This is the FALLBACK. The live vector is stored in `unified_chart_weights`
    and read by `unified_store.load_weights`, so a re-weight is an audited data
    edit rather than a deploy, and every stored reading stamps the vector it was
    computed under. Falling back here means the table is empty or unreachable,
    which should read as "equal weights", never as "no chart".
    """
    return {slug: 1.0 for slug in UNIFIED_CONSTITUENT_SLUGS}


def _gather_daily_reading(db: Session, on_date: _date) -> list[Slot]:
    """Spotify Top 50 slots, from the daily reading tables."""
    rows = (
        db.query(ReadingSong.position, ReadingSong.song_id, Song)
        .join(DailyReading, ReadingSong.reading_id == DailyReading.id)
        .outerjoin(Song, ReadingSong.song_id == Song.id)
        .filter(DailyReading.date == on_date)
        .all()
    )
    return [Slot(position=p, song_id=sid, song=s) for p, sid, s in rows]


def _gather_snapshot(db: Session, on_date: _date, slug: str) -> list[Slot]:
    """Slots for one snapshot chart. PUBLISHED rows only.

    Unpublished rows are fetch-time provisionals that approval replaces, and they
    carry no song_id, so they would be ineligible anyway. Filtering here states
    the intent rather than relying on that.
    """
    rows = (
        db.query(ChartSnapshot.position, ChartSnapshot.song_id, Song)
        .outerjoin(Song, ChartSnapshot.song_id == Song.id)
        .filter(
            ChartSnapshot.date == on_date,
            ChartSnapshot.chart_source == slug,
            ChartSnapshot.published.is_(True),
        )
        .all()
    )
    return [Slot(position=p, song_id=sid, song=s) for p, sid, s in rows]


def gather_slots(db: Session, on_date: _date, slug: str) -> list[Slot]:
    """Slots for any constituent, hiding the daily-reading / snapshot split."""
    if slug == _DAILY_READING_SLUG:
        return _gather_daily_reading(db, on_date)
    return _gather_snapshot(db, on_date, slug)


def _raw_weight(slug: str, position: int) -> float:
    """A slot's weight BEFORE normalization.

    Ranked charts use the Zipf law; a curated equal-push list gives every slot
    the same weight, because position there is editorial ordering rather than a
    popularity gradient. No current constituent is flat (New Music Friday is
    excluded by design), but routing through chart_weighting keeps the single
    source of truth honest if one ever is.
    """
    if chart_weighting(slug) == "flat":
        return 1.0
    return position_weight(position)


def compose(
    db: Session,
    on_date: _date,
    weights: dict[str, float] | None = None,
    coverage_floor: float = UNIFIED_COVERAGE_FLOOR,
) -> ComposedReading | None:
    """Compose one day's Unified Charge Chart. Pure read.

    Gathers each constituent's slots, then delegates the arithmetic to
    compose_from_slots. The split is deliberate: every guarantee this module
    makes lives in the arithmetic, so keeping it free of the ORM means the tests
    assert on the real code path rather than on a mocked database.
    """
    slots_by_slug = {
        slug: gather_slots(db, on_date, slug) for slug in UNIFIED_CONSTITUENT_SLUGS
    }
    return compose_from_slots(on_date, slots_by_slug, weights, coverage_floor)


def compose_from_slots(
    on_date: _date,
    slots_by_slug: dict[str, list[Slot]],
    weights: dict[str, float] | None = None,
    coverage_floor: float = UNIFIED_COVERAGE_FLOOR,
) -> ComposedReading | None:
    """The composition arithmetic. No database, no I/O.

    Returns None when no constituent qualifies, which is a real state (nothing
    approved yet that day) and not an error. A partial day composes from whatever
    qualifies and records the rest in `sources_excluded`, so a reading always says
    what it was built from.
    """
    weights = weights or default_weights()

    included: list[ChartContribution] = []
    excluded: list[dict] = []
    accum: dict[int, ComposedSong] = {}

    for slug in UNIFIED_CONSTITUENT_SLUGS:
        source_weight = float(weights.get(slug, 0.0))
        slots = slots_by_slug.get(slug) or []

        if not slots:
            excluded.append({"slug": slug, "reason": "not_published"})
            continue
        if source_weight <= 0:
            excluded.append({"slug": slug, "reason": "zero_weight"})
            continue

        eligible = [s for s in slots if s.eligible]
        raw_total = sum(_raw_weight(slug, s.position) for s in slots)
        eligible_total = sum(_raw_weight(slug, s.position) for s in eligible)
        coverage = (eligible_total / raw_total) if raw_total else 0.0

        if not eligible or coverage < coverage_floor:
            # Below the floor the chart's surviving slots are not a fair picture
            # of it, so it contributes nothing rather than contributing a biased
            # sample. The day still composes, and says so.
            excluded.append({
                "slug": slug,
                "reason": "below_coverage_floor",
                "coverage": round(coverage, 4),
                "floor": coverage_floor,
            })
            continue

        # Normalize over ELIGIBLE slots so the chart still hands out its full
        # source_weight rather than being docked for its exclusions.
        for slot in eligible:
            share = _raw_weight(slug, slot.position) / eligible_total
            contribution = source_weight * share
            song = slot.song
            entry = accum.get(slot.song_id)
            if entry is None:
                entry = ComposedSong(
                    song_id=slot.song_id,
                    title=song.title,
                    artist=song.artist,
                    charge_value=song.charge_value,
                    rubric_color=song.rubric_color,
                    contaminated=bool(song.contaminated),
                    unified_weight=0.0,
                )
                accum[slot.song_id] = entry
            entry.unified_weight += contribution
            # Keep the BEST rank if a song somehow occupies two slots on one
            # chart; both contributions still accumulate above.
            prior = entry.sources.get(slug)
            if prior is None or slot.position < prior:
                entry.sources[slug] = slot.position

        included.append(ChartContribution(
            slug=slug,
            source_weight=source_weight,
            slots=len(slots),
            eligible=len(eligible),
            coverage=coverage,
            total_rank_weight=eligible_total,
        ))

    if not included:
        return None

    # Rank heaviest first. song_id breaks ties so the order is deterministic
    # across runs, which idempotent recomposition depends on.
    ranked = sorted(accum.values(), key=lambda s: (-s.unified_weight, s.song_id))

    degree = compute_degree(
        [{"charge_value": s.charge_value,
          "rubric_color": s.rubric_color,
          "weight": s.unified_weight} for s in ranked],
        weighting="supplied",
    )

    return ComposedReading(
        date=on_date,
        compass_degree=degree,
        charge_level=degree_to_charge(degree),
        # Distinct SONGS, not slots: a contaminated song on three charts is one
        # contaminated song, and counting slots would inflate it by corroboration.
        contamination_count=sum(1 for s in ranked if s.contaminated),
        songs=ranked,
        sources_included=included,
        sources_excluded=excluded,
        weights=weights,
    )
