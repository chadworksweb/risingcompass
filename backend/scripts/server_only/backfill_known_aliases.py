"""One-shot backfill of the identity aliases for the relinks that predate rung 1b.

These three were relinked BY HAND every day (Wonderwall and MORNING DEW on
2026-07-10 / 07-13 / 07-15, Dancing with the Enemy on 07-13 / 07-15) because
relink_draft_song.py discarded the mapping on exit. The script now writes an
alias itself, so this file is only needed for the backlog that predates it; new
relinks are self-extinguishing and never land here.

Each entry is a HUMAN-CONFIRMED assertion (Chad relinked it, repeatedly), which
is the only thing that licenses an alias row.

  'Wonderwall - Remastered' / 'Oasis'      -> 3994  (title: version marker)
  'MORNING DEW' / 'Beyonce'                -> 3790  (title: stored carries (DONK))
  'Dancing with the Enemy' / 'DisneyMusic' -> 4008  (artist: channel credit)

Run INSIDE the prod backend container, AFTER migration 141 has applied:

    ssh deploy@<droplet> "docker exec -i rc-backend python -" \
        < scripts/server_only/backfill_known_aliases.py

Idempotent (ON CONFLICT), and it refuses any entry whose target song is missing
or whose raw string already resolves on the exact key (rung 1 would win, so the
alias would be dead weight).
"""
from sqlalchemy import text

from app.database import SessionLocal
from app.services.song_identity import (
    compute_canonical_key,
    compute_canonical_key_clean,
    resolve_song_identity,
)

# (raw feeder title, raw feeder artist, canonical song_id, why)
ALIASES = [
    ("Wonderwall - Remastered", "Oasis", 3994, "title: version marker not stripped"),
    ("MORNING DEW", "Beyoncé", 3790, "title: stored row carries iTunes '(DONK)'"),
    ("Dancing with the Enemy", "DisneyMusic", 4008, "artist: channel credit vs cast"),
]

db = SessionLocal()
try:
    for title, artist, song_id, why in ALIASES:
        song = db.execute(
            text("SELECT id, title, artist FROM songs WHERE id = :i"), {"i": song_id}
        ).first()
        if not song:
            print(f"SKIP  {title!r} / {artist!r}: song {song_id} not found")
            continue
        if db.execute(
            text("SELECT 1 FROM songs WHERE canonical_key = :k LIMIT 1"),
            {"k": compute_canonical_key(title, artist)},
        ).first():
            print(f"SKIP  {title!r} / {artist!r}: exact key already resolves")
            continue

        db.execute(text(
            "INSERT INTO song_identity_aliases "
            "  (alias_key, song_id, alias_title, alias_artist, source, notes) "
            "VALUES (:k, :sid, :t, :a, 'relink', :n) "
            "ON CONFLICT (alias_key) DO UPDATE SET "
            "  song_id = EXCLUDED.song_id, notes = EXCLUDED.notes"
        ), {
            "k": compute_canonical_key_clean(title, artist), "sid": song_id,
            "t": title, "a": artist, "n": f"backfill 2026-07-15; {why}",
        })
        print(f"ALIAS {title!r} / {artist!r} -> {song_id} {song[1]!r} ({why})")

    db.commit()

    # Prove the rung now resolves each one, through the real ladder.
    print("\nverifying through resolve_song_identity:")
    ok = True
    for title, artist, song_id, _ in ALIASES:
        r = resolve_song_identity(db, title, artist)
        hit = (r.song_id == song_id)
        ok = ok and hit
        print(f"  {'OK  ' if hit else 'FAIL'} {title!r} / {artist!r} -> "
              f"song_id={r.song_id} via={r.via}")
    print("\nall three resolve" if ok else "\nSOMETHING DID NOT RESOLVE")
finally:
    db.close()
