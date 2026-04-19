"""One-shot: ensure every calibrated song has a row in song_slugs.

Runs against the Turso primary directly so writes don't round-trip through
the embedded replica. Idempotent — re-running is a no-op.
"""
import os
import re
import sys
import unicodedata

import libsql


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9\s-]", "", text).lower().strip()
    text = re.sub(r"[\s_-]+", "-", text).strip("-")
    return text[:250] or "song"


def main() -> int:
    url = os.environ["DATABASE_URL"]
    token = os.environ["TURSO_AUTH_TOKEN"]
    conn = libsql.connect(database=url, auth_token=token)

    total = 0
    for src, table in [
        ("compass", "compass_songs"),
        ("library", "library_songs"),
        ("submitted", "submitted_songs"),
    ]:
        rows = conn.execute(
            f"SELECT s.id, s.title, COALESCE(s.artist, '') "
            f"FROM {table} s "
            f"WHERE s.charge_value IS NOT NULL AND s.title IS NOT NULL "
            f"AND NOT EXISTS ("
            f"  SELECT 1 FROM song_slugs ss "
            f"  WHERE ss.song_source = ? AND ss.song_id = s.id"
            f")",
            (src,),
        ).fetchall()

        created = 0
        for sid, title, artist in rows:
            base = slugify(f"{title} {artist}")
            slug = base
            k = 2
            while conn.execute(
                "SELECT 1 FROM song_slugs WHERE slug = ?", (slug,)
            ).fetchone():
                slug = f"{base}-{k}"
                k += 1
            conn.execute(
                "INSERT INTO song_slugs (slug, title, artist, song_source, song_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, current_timestamp)",
                (slug, title, artist, src, sid),
            )
            conn.commit()
            created += 1

        print(f"{src}: created {created} / {len(rows)} missing")
        total += created

    print(f"TOTAL created: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
