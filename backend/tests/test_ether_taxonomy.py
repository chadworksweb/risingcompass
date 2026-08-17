"""Unit tests for the Admin Taxonomy Editor (Phase 1) -- no live DB.

Builds the schema on in-memory SQLite (stripping PG-only now() server-defaults
so the DDL is portable), then exercises:
  - resolver fallback: empty DB -> the code constants
  - seed idempotency: run twice, no dupes, totals match the code constants
  - resolver reads DB rows after seeding; edits propagate after a cache bust
  - totality + validation: every topic's primary exists; the admin validators
    reject orphan primary, secondary==primary, duplicate slug, bad kebab, and a
    guarded theme delete.

Run standalone:  python tests/test_ether_taxonomy.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from fastapi import HTTPException

from app.models import Base, EtherTheme, EtherTopic
from app.services import ether_taxonomy as tax
from app.services.ether_taxonomy import (
    ETHER_THEMES, ETHER_TAXONOMY, ETHER_TOPIC_PRIMARY,
    topic_hierarchy, seed_taxonomy_if_empty, bust_taxonomy_cache,
    valid_slugs, taxonomy_for_prompt, backfill_topic_definitions,
    _code_taxonomy_for_prompt,
)
from app.services.feature_flags import set_taxonomy_db_driven
from app.routers import ether_taxonomy_admin as adm


def _session():
    for t in Base.metadata.sorted_tables:
        for col in t.columns:
            sd = col.server_default
            if sd is not None and "now(" in str(getattr(sd, "arg", "")).lower():
                col.server_default = None
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def test_resolver_fallback_empty_db():
    db = _session()
    bust_taxonomy_cache()
    h = topic_hierarchy(db)  # empty tables -> code fallback
    assert len(h["themes"]) == len(ETHER_THEMES), h["themes"]
    assert len(h["topics"]) == len(ETHER_TAXONOMY)
    # Every topic's primary is a real theme slug.
    theme_slugs = {t["slug"] for t in h["themes"]}
    for slug, meta in h["topics"].items():
        assert meta["primary"] in theme_slugs
    bust_taxonomy_cache()
    print("ok: resolver fallback -> code constants")


def test_seed_idempotency():
    db = _session()
    assert seed_taxonomy_if_empty(db) is True          # first seed inserts
    assert db.query(EtherTheme).count() == len(ETHER_THEMES)
    assert db.query(EtherTopic).count() == len(ETHER_TAXONOMY)
    assert seed_taxonomy_if_empty(db) is False          # second is a no-op
    assert db.query(EtherTheme).count() == len(ETHER_THEMES)
    assert db.query(EtherTopic).count() == len(ETHER_TAXONOMY)
    print("ok: seed idempotency")


def test_resolver_reads_db_and_busts():
    db = _session()
    seed_taxonomy_if_empty(db)
    bust_taxonomy_cache()
    h = topic_hierarchy(db)
    assert len(h["topics"]) == len(ETHER_TAXONOMY)
    # Edit a label directly, bust, re-read -> reflects the edit.
    row = db.query(EtherTopic).filter(EtherTopic.slug == "romance").first()
    row.label = "ROMANCE EDITED"
    db.commit()
    bust_taxonomy_cache()
    h2 = topic_hierarchy(db)
    assert h2["topics"]["romance"]["label"] == "ROMANCE EDITED"
    bust_taxonomy_cache()
    print("ok: resolver reads DB + cache bust")


def _expect_http(code, fn, *a, **k):
    try:
        fn(*a, **k)
    except HTTPException as e:
        assert e.status_code == code, f"expected {code}, got {e.status_code}: {e.detail}"
        return
    raise AssertionError(f"expected HTTPException {code}, none raised")


def test_validation_rejections():
    db = _session()
    seed_taxonomy_if_empty(db)

    # bad kebab slug
    _expect_http(400, adm.create_theme, adm.ThemeIn(slug="Bad_Slug", label="x"), db)
    # duplicate theme slug
    _expect_http(409, adm.create_theme, adm.ThemeIn(slug="romance", label="dup"), db)
    # topic with non-existent primary
    _expect_http(400, adm.create_topic,
                 adm.TopicIn(slug="newtopic", label="n", primary_theme="nope"), db)
    # topic secondary == primary
    _expect_http(400, adm.create_topic,
                 adm.TopicIn(slug="newtopic2", label="n", primary_theme="romance",
                             secondary=["romance"]), db)
    # delete a theme still used as primary -> guarded
    _expect_http(409, adm.delete_theme, "romance", db)

    # a clean create succeeds and is DB-only (no tagger definition)
    out = adm.create_topic(
        adm.TopicIn(slug="wanderlust", label="wanderlust",
                    primary_theme="roots-belonging", secondary=["meaning-mortality"]), db)
    assert out["has_definition"] is False, out
    assert out["secondary"] == ["meaning-mortality"], out
    print("ok: validation rejections + clean create")


def test_theme_slug_rename_rewrites_topics():
    db = _session()
    seed_taxonomy_if_empty(db)
    # romance theme -> rename slug; topics primary'd on it must follow.
    before = db.query(EtherTopic).filter(EtherTopic.primary_theme_slug == "romance").count()
    assert before > 0
    adm.update_theme("romance", adm.ThemePatch(slug="romance-love"), db)
    assert db.query(EtherTopic).filter(EtherTopic.primary_theme_slug == "romance").count() == 0
    assert db.query(EtherTopic).filter(EtherTopic.primary_theme_slug == "romance-love").count() == before
    print("ok: theme slug rename rewrites referencing topics")


def test_definitions_flag_gating_and_parity():
    """Phase 2a: valid_slugs/taxonomy_for_prompt fall back to code when the flag
    is off, switch to the DB when on, and the seeded render matches code 1:1."""
    db = _session()
    seed_taxonomy_if_empty(db)  # seeds scope/examples on fresh installs
    bust_taxonomy_cache()

    # Flag OFF -> code definitions regardless of DB contents.
    assert valid_slugs(db) == tax.VALID_SLUGS
    assert taxonomy_for_prompt(db) == _code_taxonomy_for_prompt()

    # Flag ON -> DB definitions. Seeded from code, so byte-identical parity.
    set_taxonomy_db_driven(db, True)
    bust_taxonomy_cache()
    assert valid_slugs(db) == tax.VALID_SLUGS, "seeded DB slug set must match code"
    assert taxonomy_for_prompt(db) == _code_taxonomy_for_prompt(), "DB prompt must match code render"

    # A topic with no scope is NOT taggable (dropped from valid_slugs).
    db.add(EtherTopic(slug="wanderlust", label="wanderlust",
                      primary_theme_slug="roots-belonging", secondary_themes=[],
                      sort_order=99, scope=None, examples=[]))
    db.commit(); bust_taxonomy_cache()
    assert "wanderlust" not in valid_slugs(db)

    # Give it a scope -> now taggable + appears in the prompt.
    row = db.query(EtherTopic).filter(EtherTopic.slug == "wanderlust").first()
    row.scope = "The pull of the open road; leaving to roam."
    db.commit(); bust_taxonomy_cache()
    assert "wanderlust" in valid_slugs(db)
    assert "wanderlust:" in taxonomy_for_prompt(db)

    set_taxonomy_db_driven(db, False)
    bust_taxonomy_cache()
    print("ok: definitions flag gating + code/DB parity")


def test_backfill_definitions():
    """Phase-1-style rows (seeded without scope) get scope/examples backfilled
    from code, idempotently, without clobbering an admin edit."""
    db = _session()
    # Simulate a Phase-1 seed: topics with NO scope.
    for i, (slug, label) in enumerate(ETHER_THEMES.items()):
        db.add(EtherTheme(slug=slug, label=label, sort_order=i))
    for i, slug in enumerate(ETHER_TAXONOMY.keys()):
        db.add(EtherTopic(slug=slug, label=slug.replace("-", " "),
                          primary_theme_slug=ETHER_TOPIC_PRIMARY[slug],
                          secondary_themes=[], sort_order=i, scope=None, examples=None))
    db.commit()

    n = backfill_topic_definitions(db)
    assert n == len(ETHER_TAXONOMY), n
    romance = db.query(EtherTopic).filter(EtherTopic.slug == "romance").first()
    assert romance.scope == ETHER_TAXONOMY["romance"]["scope"]
    assert len(romance.examples) == len(ETHER_TAXONOMY["romance"]["examples"])
    # Idempotent: a second run touches nothing (all scopes now set).
    assert backfill_topic_definitions(db) == 0
    print("ok: backfill definitions (idempotent, code-sourced)")


def test_create_topic_with_definition():
    db = _session()
    seed_taxonomy_if_empty(db)
    out = adm.create_topic(adm.TopicIn(
        slug="wanderlust", label="wanderlust", primary_theme="roots-belonging",
        secondary=[], scope="The pull of the open road.",
        examples=[adm.ExampleIn(artist="Lord Huron", title="Ends of the Earth", why="roaming as the whole subject")],
    ), db)
    assert out["has_definition"] is True
    assert out["scope"].startswith("The pull")
    assert out["examples"][0]["artist"] == "Lord Huron"
    print("ok: create topic with definition")


if __name__ == "__main__":
    test_resolver_fallback_empty_db()
    test_seed_idempotency()
    test_resolver_reads_db_and_busts()
    test_validation_rejections()
    test_theme_slug_rename_rewrites_topics()
    test_definitions_flag_gating_and_parity()
    test_backfill_definitions()
    test_create_topic_with_definition()
    print("\nALL PASSED")
