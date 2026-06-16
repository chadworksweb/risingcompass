"""Admin Taxonomy Editor (Phase 1) -- create ether_themes + ether_topics.

The DB becomes the presentation source of truth for the Ether theme list,
labels, each topic's primary theme, secondary facets, and ordering. The live
song tagger is untouched (its topic slug set + scope/examples stay code-driven
in services/ether_taxonomy.py); only topic_hierarchy() + labels become
DB-backed via a fail-safe resolver.

This migration creates the tables only. Seeding is idempotent at startup
(_init_database -> seed_taxonomy_if_empty), so it covers BOTH a fresh install
(create_all builds the tables) and an existing prod DB (this migration builds
them). NO scope/examples columns in Phase 1.

PG-compatible (063+). Idempotent (CREATE TABLE IF NOT EXISTS).
"""

from sqlalchemy import text


def up(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS ether_themes (
            id          SERIAL PRIMARY KEY,
            slug        TEXT NOT NULL UNIQUE,
            label       TEXT NOT NULL,
            sort_order  INTEGER NOT NULL DEFAULT 0,
            created_at  TIMESTAMP DEFAULT (now() at time zone 'utc'),
            updated_at  TIMESTAMP DEFAULT (now() at time zone 'utc')
        )
    """))

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS ether_topics (
            id                 SERIAL PRIMARY KEY,
            slug               TEXT NOT NULL UNIQUE,
            label              TEXT NOT NULL,
            primary_theme_slug TEXT NOT NULL,
            secondary_themes   JSON NOT NULL DEFAULT '[]',
            sort_order         INTEGER NOT NULL DEFAULT 0,
            created_at         TIMESTAMP DEFAULT (now() at time zone 'utc'),
            updated_at         TIMESTAMP DEFAULT (now() at time zone 'utc')
        )
    """))

    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_ether_topics_primary "
        "ON ether_topics (primary_theme_slug)"
    ))
