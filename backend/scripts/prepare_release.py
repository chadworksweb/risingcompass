"""Prepare a release for the rc-album lens: resolve, bind, and print the rows.

The album lane is two phases and no third thing. Phase one is ordinary song
calibration, one track at a time, with the album out of scope. Phase two is the
release read. This script is the whole seam between them.

It does four things:
  1. resolves every title in the tracklist to a finished `songs` row,
  2. REFUSES if any track has no reading, naming all of them at once,
  3. creates the Release + ReleaseSong links in one transaction, aggregates NULL,
  4. prints the rows in running order -- the exact text the lens reads.

The read itself stays manual. Claude Code is the model in terminal, so this
prepares the read and the operator performs it, then `write_album_reading.py`
composes and writes it.

Three refusals are deliberate and load-bearing:
  * NO MEAN, anywhere. Not as a sanity check, not in a summary line. The
    aggregate of the track charges is neither the album's charge nor its
    starting position, and a mean seen before the read anchors it on a
    conclusion it never earned.
  * NO AUTO-CALIBRATION. A release with an uncalibrated track is not ready.
    The answer is to run that song, never to read around the gap.
  * NO INFERENCE of release type or tracklist. Type is not derivable from the
    track count and has been assumed wrong before, and a streaming tracklist
    silently accumulates bonus tracks, deluxe additions and later singles.
    Both are operator confirmations; this script only ever reports them back.

Usage:
    prepare_release.py --title T --artist A --type album|ep|single \
                       --date YYYY-MM-DD --tracks tracks.txt
    prepare_release.py --release-id N          # reprint an existing release
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import Release, ReleaseSong  # noqa: E402

RELEASE_TYPES = ("album", "ep", "single")


def read_tracklist(path: str) -> list[str]:
    """One title per line, in running order. Blanks and # comments skipped.

    Running order IS the text: two orderings of the same tracks are two
    different releases, and a reorder is a re-read rather than a metadata edit.
    """
    titles: list[str] = []
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            titles.append(line)
    return titles


def resolve_artist(db, name: str) -> int:
    row = db.execute(
        text("select id from artists where lower(name) = lower(:n)"), {"n": name}
    ).first()
    if not row:
        sys.exit(
            f"REFUSED: no artist row for {name!r}. An artist with calibrated songs "
            "already has one, so check the spelling against the songs table."
        )
    return row[0]


def resolve_tracks(db, artist: str, titles: list[str]) -> list[tuple[int, str, int]]:
    """(position, title, song_id) for every track, or exit naming every problem.

    Reports the WHOLE list of failures rather than the first one, so a run is
    never a sequence of one-at-a-time discoveries.
    """
    resolved: list[tuple[int, str, int]] = []
    missing: list[str] = []
    unread: list[str] = []
    ambiguous: list[tuple[str, list[int]]] = []

    for pos, title in enumerate(titles, start=1):
        rows = db.execute(
            text(
                "select id, rubric_color, calibration_failed from songs "
                "where lower(artist) = lower(:a) and lower(title) = lower(:t) "
                "order by id"
            ),
            {"a": artist, "t": title},
        ).fetchall()
        if not rows:
            missing.append(title)
            continue
        if len(rows) > 1:
            ambiguous.append((title, [r[0] for r in rows]))
            continue
        song_id, color, failed = rows[0]
        if color is None or failed:
            unread.append(title)
            continue
        resolved.append((pos, title, song_id))

    if missing or unread or ambiguous:
        print("REFUSED: the tracks come first, and they are approved data.\n")
        if missing:
            print("  No song row at all (calibrate these as ordinary songs first):")
            for t in missing:
                print(f"    - {t}")
        if unread:
            print("  Row exists but carries no finished reading:")
            for t in unread:
                print(f"    - {t}")
        if ambiguous:
            print("  Resolves to more than one row (disambiguate by id):")
            for t, ids in ambiguous:
                print(f"    - {t}: {ids}")
        print(
            "\nNothing was created. A release with an uncalibrated track is not "
            "ready to read."
        )
        sys.exit(1)

    return resolved


def find_existing(db, artist_id: int, title: str) -> int | None:
    row = db.execute(
        text(
            "select id from releases where artist_id = :a and lower(title) = lower(:t)"
        ),
        {"a": artist_id, "t": title},
    ).first()
    return row[0] if row else None


def create_release(db, *, artist_id, title, rtype, date, tracks) -> int:
    """Build the row and its links in one transaction.

    `rubric_color` and `charge_value` stay NULL until the read is written. A
    composed mean sitting in the row is a conclusion the read has not earned,
    and the aggregate must not exist anywhere the reader might see it first.

    `source='manual_terminal'` is what PROTECTS that emptiness, and it has to be
    a durable stamp rather than something inferred. `releases_admin` refuses to
    write its placeholder mean onto a release awaiting a read, and it identifies
    one by this value. An earlier version inferred it from "no musicbrainz_id and
    no spotify_id", which holds only until the nightly cover-art sweep attaches
    an MBID -- and attaching MBIDs to releases that lack them is that sweep's
    entire job, so the guard would have quietly stopped guarding. Nothing in the
    codebase writes `releases.source` after creation, so this survives.

    track_count states the length of the sequence the lens READ, which keeps the
    stored release and the read release the same object.
    """
    rel = Release(
        artist_id=artist_id,
        title=title,
        release_type=rtype,
        release_date=date,
        release_year=date.year if date else None,
        track_count=len(tracks),
        calibrated_count=len(tracks),
        rubric_color=None,
        charge_value=None,
        source="manual_terminal",
    )
    db.add(rel)
    db.flush()
    for pos, _title, song_id in tracks:
        db.add(ReleaseSong(release_id=rel.id, song_id=song_id, track_number=pos))
    db.commit()
    return rel.id


def print_rows(db, release_id: int) -> None:
    """Print the release's song rows in running order. This IS the lens's text.

    Deliberately carries no aggregate, no average, and no total. The lens starts
    at zero and builds its case from these rows.
    """
    head = db.execute(
        text(
            "select r.title, a.name, r.release_type, r.release_date, r.track_count "
            "from releases r join artists a on a.id = r.artist_id where r.id = :i"
        ),
        {"i": release_id},
    ).first()
    if not head:
        sys.exit(f"REFUSED: no release {release_id}")
    title, artist, rtype, rdate, count = head

    print(f"\nRELEASE {release_id}: {title!r} / {artist}")
    print(f"  {rtype}, {rdate}, {count} tracks in the running order")
    print(
        "\n  CONFIRM BEFORE READING, neither is inferable from the data:\n"
        f"    1. the type is {rtype!r}, not something else\n"
        "    2. every position below belongs to the release AS ORIGINALLY ISSUED\n"
        "       (a streaming tracklist carries bonus and deluxe additions, and a\n"
        "       TRAILING one is the worst case, since the final position is the\n"
        "       album's stated thesis)"
    )
    print("\n" + "=" * 72)
    print("THE ROWS, IN RUNNING ORDER. This is the whole of the evidence.")
    print("=" * 72)

    rows = db.execute(
        text(
            "select rs.track_number, s.title, s.rubric_color, s.charge_value, "
            "s.charge_summary, s.deadpan_line, s.topics, s.contaminated, "
            "s.contamination_note, s.dogma_referenced, s.dogma_note, "
            "s.listener_effects_prose, s.societal_effects_prose "
            "from release_songs rs join songs s on s.id = rs.song_id "
            "where rs.release_id = :i order by rs.track_number"
        ),
        {"i": release_id},
    ).mappings()

    for r in rows:
        topics = json.loads(r["topics"]) if r["topics"] else []
        print(f"\n--- {r['track_number']}. {r['title']}")
        print(f"    {r['rubric_color']} {r['charge_value']:+d}")
        print(f"    SUMMARY:  {r['charge_summary']}")
        print(f"    DEADPAN:  {r['deadpan_line']}")
        print(f"    TOPICS:   {', '.join(topics)}")
        if r["contaminated"]:
            print(f"    CONTAMINATION: {r['contamination_note']}")
        if r["dogma_referenced"]:
            print(f"    DOGMA: {r['dogma_note']}")
        if r["listener_effects_prose"]:
            print(f"    LISTENER: {r['listener_effects_prose']}")
        if r["societal_effects_prose"]:
            print(f"    SOCIETAL: {r['societal_effects_prose']}")

    print("\n" + "=" * 72)
    print(
        "Read these top to bottom, in order, starting at zero. Then write the\n"
        "lens JSON and the argument, and run:\n"
        f"  write_album_reading.py --release-id {release_id} "
        "--reading-file R.json --reasoning-file A.txt --dry-run"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--release-id", type=int, help="reprint an existing release")
    ap.add_argument("--title")
    ap.add_argument("--artist")
    ap.add_argument("--type", dest="rtype", choices=RELEASE_TYPES)
    ap.add_argument("--date", help="YYYY-MM-DD")
    ap.add_argument("--tracks", help="file, one title per line, in running order")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        if args.release_id:
            print_rows(db, args.release_id)
            return

        for flag in ("title", "artist", "rtype", "tracks"):
            if not getattr(args, flag):
                ap.error("--title, --artist, --type and --tracks are all required")

        rdate = datetime.date.fromisoformat(args.date) if args.date else None
        titles = read_tracklist(args.tracks)
        if not titles:
            sys.exit(f"REFUSED: no titles in {args.tracks}")

        artist_id = resolve_artist(db, args.artist)

        existing = find_existing(db, artist_id, args.title)
        if existing:
            print(
                f"Release {existing} already exists for this artist and title; "
                "reprinting its rows rather than creating a second one."
            )
            print_rows(db, existing)
            return

        tracks = resolve_tracks(db, args.artist, titles)
        release_id = create_release(
            db,
            artist_id=artist_id,
            title=args.title,
            rtype=args.rtype,
            date=rdate,
            tracks=tracks,
        )
        print(f"Created release {release_id} with {len(tracks)} tracks, reading NULL.")
        print_rows(db, release_id)
    finally:
        db.close()


if __name__ == "__main__":
    main()
