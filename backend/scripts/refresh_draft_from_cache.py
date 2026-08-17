"""Refresh a pending draft's per-song calibration snapshots from the
compass_songs cache.

Why this exists: agent_draft_songs are snapshots taken at draft-creation
time. If a song's cache calibration is updated after the draft was created
(e.g. via supply_lyrics on a different draft, or post-rubric-update
recalibration), the draft snapshot stays frozen with the old values. This
script reconciles each draft song against the current cache, no new
Anthropic calls (except an optional editorial regen at the end if the
draft becomes fully calibrated).

Usage:
    python refresh_draft_from_cache.py <draft_ref>

Refresh logic for each draft_song:
- Look up compass_songs by case-insensitive (title, artist), most recent first.
- If a fully-calibrated cache row exists AND any field differs from the
  draft snapshot, copy the cache values onto the draft_song.
- Songs already aligned with cache are left alone.
- Songs without any cache row stay at needs-lyrics.

If all songs are calibrated post-refresh, recompute compass_degree /
charge_level / contamination_count and regenerate the editorial summary.
"""
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from app.database import SessionLocal
from app.models import AgentDraft, Song
from app.services.charge_calc import degree_to_charge
from app.services.compass_calc import compute_degree
from app.services.contamination import count_contaminated
from app.services.song_store import find_song_by_title_artist


def _cache_lookup(db, title: str, artist: str) -> Song | None:
    # Unified renovation: resolve against the one atomic `songs` table via the
    # canonical-key identity ladder (CompassSong + the 4-table model were dropped
    # in Phase 5). find_song_by_title_artist handles the case-insensitive +
    # feeder-clean resolution.
    return find_song_by_title_artist(db, title, artist)


def _is_complete(cs: Song) -> bool:
    return bool(cs.rubric_color) and cs.charge_value is not None and bool(cs.charge_summary)


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: refresh_draft_from_cache.py <draft_ref>", file=sys.stderr)
        return 2
    draft_ref = sys.argv[1]

    refreshed = []
    skipped_no_cache = []
    editorial_input = None

    write_db = SessionLocal()
    try:
        draft = (
            write_db.query(AgentDraft)
            .filter(AgentDraft.label == draft_ref)
            .first()
        )
        if not draft:
            print(f"Draft {draft_ref!r} not found", file=sys.stderr)
            return 1
        if draft.status != "pending":
            print(
                f"Draft is not pending (status={draft.status!r}); refresh "
                "only applies to pending drafts.",
                file=sys.stderr,
            )
            return 1

        for ds in sorted(draft.songs, key=lambda x: x.position):
            cs = _cache_lookup(write_db, ds.title, ds.artist)
            if not cs or not _is_complete(cs):
                if ds.rubric_color is None:
                    skipped_no_cache.append((ds.position, ds.title, ds.artist))
                continue

            cs_dogma_ref = bool(getattr(cs, "dogma_referenced", None) or False)
            cs_dogma_note = getattr(cs, "dogma_note", None)
            cs_contam = bool(cs.contaminated or False)

            differs = (
                ds.rubric_color != cs.rubric_color
                or ds.charge_value != cs.charge_value
                or bool(ds.contaminated or False) != cs_contam
                or (ds.contamination_note or None) != (cs.contamination_note or None)
                or bool(ds.dogma_referenced or False) != cs_dogma_ref
                or (ds.dogma_note or None) != (cs_dogma_note or None)
                or (ds.charge_summary or None) != (cs.charge_summary or None)
                or ds.song_id != cs.id
            )
            if not differs:
                continue

            old_state = (
                f"{ds.rubric_color or 'none'} {ds.charge_value if ds.charge_value is not None else 'none'}"
                f"{' contam' if ds.contaminated else ''}"
            )
            ds.song_id = cs.id
            ds.rubric_color = cs.rubric_color
            ds.charge_value = cs.charge_value
            ds.contaminated = cs_contam
            ds.contamination_note = cs.contamination_note
            ds.dogma_referenced = cs_dogma_ref
            ds.dogma_note = cs_dogma_note
            ds.charge_summary = cs.charge_summary
            ds.confidence = ds.confidence if ds.confidence is not None else 1.0
            ds.lyrics_available = True
            new_state = (
                f"{cs.rubric_color} {cs.charge_value:+d}"
                f"{' contam' if cs_contam else ''}"
            )
            refreshed.append((ds.position, ds.title, ds.artist, old_state, new_state))

        all_calibrated = all(s.rubric_color is not None for s in draft.songs)
        if all_calibrated:
            song_dicts = [
                {
                    "rubric_color": s.rubric_color,
                    "charge_value": s.charge_value,
                    "position": s.position,
                }
                for s in draft.songs
            ]
            draft.compass_degree = compute_degree(song_dicts)
            draft.charge_level = degree_to_charge(draft.compass_degree)
            draft.contamination_count = count_contaminated(
                [{"contaminated": s.contaminated} for s in draft.songs]
            )
            editorial_input = [
                {
                    "title": s.title, "artist": s.artist, "position": s.position,
                    "rubric_color": s.rubric_color, "charge_value": s.charge_value,
                    "contaminated": s.contaminated,
                    "contamination_note": s.contamination_note,
                    "charge_summary": s.charge_summary,
                    "confidence": s.confidence,
                    "lyrics_available": s.lyrics_available,
                    "chart_source": s.chart_source,
                }
                for s in draft.songs
            ]

        write_db.commit()
    finally:
        write_db.close()

    print(f"=== Refresh of {draft_ref} ===")
    print(f"Refreshed: {len(refreshed)}")
    for pos, title, artist, old, new in refreshed:
        print(f"  pos {pos:>2}  {title!r} by {artist!r}: {old}  ->  {new}")
    if skipped_no_cache:
        print(f"\nStill needs lyrics ({len(skipped_no_cache)}):")
        for pos, title, artist in skipped_no_cache:
            print(f"  pos {pos:>2}  {title!r} by {artist!r}")

    if editorial_input:
        print("\nAll songs calibrated. Regenerating editorial...")
        try:
            from app.services.agents.compass_agent import _generate_editorial
            editorial = _generate_editorial(editorial_input)
        except Exception as exc:
            print(f"Editorial regen failed: {exc}", file=sys.stderr)
            editorial = None
        if editorial:
            ed_db = SessionLocal()
            try:
                d = ed_db.query(AgentDraft).filter(AgentDraft.label == draft_ref).first()
                d.editorial_summary = editorial
                ed_db.commit()
                print("Editorial updated.")
            finally:
                ed_db.close()
        else:
            print("Editorial regen returned no content; previous editorial preserved.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
