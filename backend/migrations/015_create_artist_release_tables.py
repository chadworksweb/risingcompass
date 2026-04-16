"""Create artists, releases, release_songs, and song_slugs tables for Artist Trajectory feature."""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS artists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            slug VARCHAR(250) UNIQUE NOT NULL,
            musicbrainz_id TEXT,
            spotify_id TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """))

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS releases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artist_id INTEGER NOT NULL REFERENCES artists(id),
            title TEXT NOT NULL,
            release_type VARCHAR(20) NOT NULL,
            release_date DATE,
            release_year INTEGER,
            rubric_color TEXT,
            charge_value INTEGER,
            track_count INTEGER DEFAULT 0,
            calibrated_count INTEGER DEFAULT 0,
            contamination_count INTEGER DEFAULT 0,
            musicbrainz_id TEXT,
            spotify_id TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(artist_id, title)
        )
    """))

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS release_songs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            release_id INTEGER NOT NULL REFERENCES releases(id),
            song_source VARCHAR(20) NOT NULL,
            song_id INTEGER NOT NULL,
            track_number INTEGER
        )
    """))

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS song_slugs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug VARCHAR(300) UNIQUE NOT NULL,
            title TEXT NOT NULL,
            artist TEXT NOT NULL,
            song_source VARCHAR(20),
            song_id INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """))
