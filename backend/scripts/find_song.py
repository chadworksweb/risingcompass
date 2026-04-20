"""Locate a song across all four song tables by title (+ optional artist).

Usage: python find_song.py "t.g.a." [--artist "chad lewine"]

Prints matches with song_source, song_id, title, artist, rubric_color,
charge_value. Useful for one-shot admin workflows (rubric_update
recalibrations, manual investigations).
"""
import argparse
import os
import sys

import libsql


TABLES = [
    ("compass", "compass_songs"),
    ("library", "library_songs"),
    ("submitted", "submitted_songs"),
    ("stream", "stream_songs"),
]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("title")
    p.add_argument("--artist", default=None)
    args = p.parse_args()

    url = os.environ["DATABASE_URL"]
    token = os.environ["TURSO_AUTH_TOKEN"]
    conn = libsql.connect(database=url, auth_token=token)

    title_like = f"%{args.title.lower()}%"
    artist_clause = ""
    params = [title_like]
    if args.artist:
        artist_clause = "AND LOWER(artist) LIKE ?"
        params.append(f"%{args.artist.lower()}%")

    found_any = False
    for source, table in TABLES:
        rows = conn.execute(
            f"SELECT id, title, artist, rubric_color, charge_value "
            f"FROM {table} "
            f"WHERE LOWER(title) LIKE ? {artist_clause} "
            f"ORDER BY id DESC LIMIT 20",
            tuple(params),
        ).fetchall()
        if rows:
            found_any = True
            print(f"\n[{source}]")
            for sid, title, artist, color, charge in rows:
                print(f"  id={sid:<5} color={color or '—':<7} charge={charge if charge is not None else '—':<5}  {title}  —  {artist}")

    if not found_any:
        print("No matches found.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
