"""Split the ego half out of `self-affirmation` into a new `ego-trip` topic.

WHY: `self-affirmation` was scoped as "claiming one's own worth", which drew in
every song where a narrator asserts themselves, including pure ego and dominance
boasts. A corpus audit found 42 of the 254 tagged songs sitting at Degraded or
Corrupted, most of them co-tagged with `flex` (supremacy over rivals, wealth as
proof of worth, conquest boasts). The tag was doing two opposite jobs at once, so
the Self & Psyche rollup counted ego trips as self-worth.

The split, which is a DEFINITION change plus one new slug, never a rename (topic
slugs are immutable, see CLAUDE.md "Ether taxonomy editor"):
  - `ego-trip` (NEW, primary theme `status`, secondary `self-psyche`) takes
    self-elevation as the claim: superiority, dominance, rivals ranked beneath.
  - `self-affirmation` is narrowed to inherent worth: dignity and enoughness
    resting on BEING, with no rival and no receipts.
  - `flex` keeps its meaning (display of acquired position) and gains one clause
    pointing the SELF-as-claim case at ego-trip.

Code is the source of truth (`app/services/ether_taxonomy.py`); this migration
carries the same change to the `ether_topics` rows, which the admin Taxonomy page
reads and which drive the tagger whenever `taxonomy_db_driven.enabled` is on. A
fresh install seeds from code and skips all of this (seed_taxonomy_if_empty only
fires on an empty table), so existing databases need the insert.

Existing tagged rows are NOT retagged here. Re-pointing `songs.topics` is a
judgement call per song, so it runs as a separate reviewed pass.

PG-compatible (063+). Idempotent (guarded insert, scope updates are set-to-value).
"""

from sqlalchemy import JSON, bindparam, text

EGO_TRIP_SCOPE = (
    "Self-elevation as the point: superiority, dominance, untouchability, "
    "rivals ranked beneath the narrator. Worth asserted by outranking someone, "
    "with or without possessions."
)

EGO_TRIP_EXAMPLES = [
    {"artist": "Bobby Brown", "title": "My Prerogative",
     "why": "the right to be above criticism as the whole claim"},
    {"artist": "Gwen Stefani", "title": "Hollaback Girl",
     "why": "supremacy over a rival, called out to be proven"},
]

SELF_AFFIRMATION_SCOPE = (
    "Inherent worth claimed: dignity, enoughness, self-regard that rests on "
    "BEING rather than on having (flex) or on outranking anyone (ego-trip). "
    "No rival, no scoreboard, no receipts."
)

SELF_AFFIRMATION_EXAMPLES = [
    {"artist": "Lizzo", "title": "Good as Hell",
     "why": "worth asserted as a given, with nobody ranked below her"},
    {"artist": "Whitney Houston", "title": "Greatest Love of All",
     "why": "dignity located inside the self, offered without a target"},
]

FLEX_SCOPE = (
    "Wealth, status, clout - display of acquired position. The goods are the "
    "claim; when the SELF is the claim, use ego-trip."
)


def up(conn):
    # Nothing to do on a fresh install: the seeder plants all 32 from code.
    if conn.execute(text("SELECT COUNT(*) FROM ether_topics")).scalar() == 0:
        return

    exists = conn.execute(text(
        "SELECT 1 FROM ether_topics WHERE slug = 'ego-trip'"
    )).first()
    if not exists:
        # Sort right after `flex` so the admin list and the rendered prompt keep
        # the two neighbours adjacent, which is what makes the contrast legible.
        flex_order = conn.execute(text(
            "SELECT sort_order FROM ether_topics WHERE slug = 'flex'"
        )).scalar()
        if flex_order is None:
            flex_order = conn.execute(text(
                "SELECT COALESCE(MAX(sort_order), 0) FROM ether_topics"
            )).scalar()
        conn.execute(
            text("UPDATE ether_topics SET sort_order = sort_order + 1 "
                 "WHERE sort_order > :flex_order"),
            {"flex_order": flex_order},
        )
        # bindparam(type_=JSON) so the driver serialises the list/dict itself.
        # A hand-written CAST(:p AS json) is PG-only and silently mangles the
        # value on any other dialect.
        conn.execute(
            text(
                "INSERT INTO ether_topics "
                "(slug, label, primary_theme_slug, secondary_themes, sort_order, "
                " scope, examples) "
                "VALUES ('ego-trip', 'ego trip', 'status', :secondary, "
                "        :sort_order, :scope, :examples)"
            ).bindparams(
                bindparam("secondary", type_=JSON),
                bindparam("examples", type_=JSON),
            ),
            {
                "secondary": ["self-psyche"],
                "sort_order": flex_order + 1,
                "scope": EGO_TRIP_SCOPE,
                "examples": EGO_TRIP_EXAMPLES,
            },
        )

    # Re-point the two definitions the split changes. Deliberately overwrites any
    # admin edit on these two rows: the old wording is the defect being fixed.
    conn.execute(
        text("UPDATE ether_topics SET scope = :scope, examples = :examples "
             "WHERE slug = 'self-affirmation'").bindparams(
            bindparam("examples", type_=JSON),
        ),
        {"scope": SELF_AFFIRMATION_SCOPE, "examples": SELF_AFFIRMATION_EXAMPLES},
    )
    conn.execute(
        text("UPDATE ether_topics SET scope = :scope WHERE slug = 'flex'"),
        {"scope": FLEX_SCOPE},
    )
