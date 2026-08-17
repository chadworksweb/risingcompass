"""Seed the Best-Selling Albums of All Time board (US / RIAA, top 50).

Idempotent upsert by rank: re-running updates rows in place. Figures are the
RIAA-certified units published on Wikipedia's "List of best-selling albums in
the United States" (certified multiplier for display + estimated copies in
millions for sorting). This only seeds the RANKING + metadata; charge/deadpan
are filled later by linking each row to a charged Release in the admin.

Run on the server against prod:
    docker exec rc-backend python scripts/seed_alltime_albums.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone
from app.database import SessionLocal
from app.models import AlltimeAlbum

# (rank, album_title, artist, certified_units, units_millions, release_year)
ALBUMS = [
    (1, "Their Greatest Hits (1971-1975)", "Eagles", "40x Platinum", 40.0, 1976),
    (2, "Thriller", "Michael Jackson", "34x Platinum", 34.0, 1982),
    (3, "Hotel California", "Eagles", "28x Platinum", 28.0, 1976),
    (4, "Back in Black", "AC/DC", "27x Platinum", 27.0, 1980),
    (5, "Led Zeppelin IV", "Led Zeppelin", "24x Platinum", 24.0, 1971),
    (6, "Rumours", "Fleetwood Mac", "21x Platinum", 21.0, 1977),
    (7, "Legend", "Bob Marley & The Wailers", "18x Platinum", 18.0, 1984),
    (8, "Appetite for Destruction", "Guns N' Roses", "18x Platinum", 18.0, 1987),
    (9, "Greatest Hits", "Journey", "18x Platinum", 18.0, 1988),
    (10, "No Fences", "Garth Brooks", "18x Platinum", 18.0, 1990),
    (11, "Metallica", "Metallica", "20x Platinum", 17.81, 1991),
    (12, "Come On Over", "Shania Twain", "20x Platinum", 17.72, 1997),
    (13, "Greatest Hits", "Elton John", "17x Platinum", 17.0, 1974),
    (14, "Boston", "Boston", "17x Platinum", 17.0, 1976),
    (15, "Born in the U.S.A.", "Bruce Springsteen", "17x Platinum", 17.0, 1984),
    (16, "Saturday Night Fever", "Bee Gees & Various Artists", "16x Platinum", 16.0, 1977),
    (17, "Jagged Little Pill", "Alanis Morissette", "17x Platinum", 15.55, 1995),
    (18, "The Dark Side of the Moon", "Pink Floyd", "15x Platinum", 15.0, 1973),
    (19, "Greatest Hits 1974-78", "Steve Miller Band", "15x Platinum", 15.0, 1978),
    (20, "Slippery When Wet", "Bon Jovi", "15x Platinum", 15.0, 1986),
    (21, "Cracked Rear View", "Hootie & the Blowfish", "22x Platinum", 14.58, 1994),
    (22, "Tapestry", "Carole King", "14x Platinum", 14.0, 1971),
    (23, "Simon and Garfunkel's Greatest Hits", "Simon & Garfunkel", "14x Platinum", 14.0, 1972),
    (24, "Bat Out of Hell", "Meat Loaf", "14x Platinum", 14.0, 1977),
    (25, "Whitney Houston", "Whitney Houston", "14x Platinum", 14.0, 1985),
    (26, "Dirty Dancing", "Various Artists", "14x Platinum", 14.0, 1987),
    (27, "Millennium", "Backstreet Boys", "13x Platinum", 13.89, 1999),
    (28, "The Bodyguard", "Whitney Houston & Various Artists", "19x Platinum", 13.45, 1992),
    (29, "Supernatural", "Santana", "15x Platinum", 13.11, 1999),
    (30, "1", "The Beatles", "11x Platinum", 13.0, 2000),
    (31, "Grease", "Various Artists", "13x Platinum", 13.0, 1978),
    (32, "Purple Rain", "Prince and the Revolution", "13x Platinum", 13.0, 1984),
    (33, "The Marshall Mathers LP", "Eminem", "11x Platinum", 12.94, 2000),
    (34, "No Strings Attached", "NSYNC", "12x Platinum", 12.68, 2000),
    (35, "...Baby One More Time", "Britney Spears", "14x Platinum", 12.3, 1999),
    (36, "21", "Adele", "17x Platinum", 12.1, 2011),
    (37, "The Beatles (White Album)", "The Beatles", "24x Platinum", 12.0, 1968),
    (38, "Abbey Road", "The Beatles", "12x Platinum", 12.0, 1969),
    (39, "Led Zeppelin II", "Led Zeppelin", "12x Platinum", 12.0, 1969),
    (40, "Chronicle, Vol. 1", "Creedence Clearwater Revival", "12x Platinum", 12.0, 1976),
    (41, "The Stranger", "Billy Joel", "12x Platinum", 12.0, 1977),
    (42, "Greatest Hits", "Aerosmith", "12x Platinum", 12.0, 1980),
    (43, "Greatest Hits", "Kenny Rogers", "12x Platinum", 12.0, 1980),
    (44, "No Jacket Required", "Phil Collins", "12x Platinum", 12.0, 1985),
    (45, "Hysteria", "Def Leppard", "12x Platinum", 12.0, 1987),
    (46, "Breathless", "Kenny G", "12x Platinum", 12.0, 1992),
    (47, "Hybrid Theory", "Linkin Park", "12x Platinum", 11.96, 2000),
    (48, "Backstreet Boys", "Backstreet Boys", "14x Platinum", 11.92, 1997),
    (49, "Falling into You", "Celine Dion", "12x Platinum", 11.89, 1996),
    (50, "Human Clay", "Creed", "11x Platinum", 11.7, 1999),
]


def main():
    db = SessionLocal()
    created = updated = 0
    try:
        by_rank = {r.rank: r for r in db.query(AlltimeAlbum).all()}
        for rank, title, artist, units, millions, year in ALBUMS:
            row = by_rank.get(rank)
            if row is None:
                row = AlltimeAlbum(rank=rank)
                db.add(row)
                created += 1
            else:
                updated += 1
            row.album_title = title
            row.artist = artist
            row.certified_units = units
            row.units_millions = millions
            row.release_year = year
            row.last_reviewed_at = datetime.now(timezone.utc)
        db.commit()
        print(f"Seeded all-time albums: {created} created, {updated} updated "
              f"({len(ALBUMS)} total).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
