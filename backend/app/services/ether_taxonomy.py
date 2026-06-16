"""The Ether Art Chart — closed 25-topic taxonomy (single source of truth).

Imported by the ether tagger prompt and the admin Ether Audits surface. Adding
a tag is a one-line dict insertion + redeploy — intentional friction, since
the audit-accept flow is rare-event work and code review is a feature.
"""

ETHER_TAXONOMY = {
    "romance": {
        "scope": "Love declaration, devotion, mutual care between partners.",
        "examples": [
            ("Stevie Wonder", "Isn't She Lovely",
             "joyful celebration of beloved"),
            ("Etta James", "At Last",
             "arrival of long-awaited love"),
        ],
    },
    "breakup": {
        "scope": "Heartbreak, leaving, ending of a romantic bond.",
        "examples": [
            ("Adele", "Someone Like You",
             "mourning a love that ended"),
            ("Gloria Gaynor", "I Will Survive",
             "post-breakup self-recovery"),
        ],
    },
    "longing": {
        "scope": "Pining, yearning, missing — held desire across distance.",
        "examples": [
            ("Roy Orbison", "Crying",
             "ache for someone unreachable"),
        ],
    },
    "sex": {
        "scope": "Lust, seduction, physicality — the body as the subject.",
        "examples": [
            ("Marvin Gaye", "Let's Get It On",
             "explicit invitation to physical intimacy"),
        ],
    },
    "flex": {
        "scope": "Wealth, status, clout — display of acquired position.",
        "examples": [
            ("Drake", "Started From The Bottom",
             "origin-story status display"),
        ],
    },
    "self-deprecation": {
        "scope": "Self-roast, lowering — narrator names own faults.",
        "examples": [
            ("Beck", "Loser",
             "self-mocking refrain as identity"),
        ],
    },
    "revenge": {
        "scope": "Getting even, spite — payback as the song's arc.",
        "examples": [
            ("Carrie Underwood", "Before He Cheats",
             "destruction of cheater's property as comeuppance"),
        ],
    },
    "betrayal": {
        "scope": "Cheated on, lied to, stabbed — the wound of broken trust.",
        "examples": [
            ("Fleetwood Mac", "Go Your Own Way",
             "the moment of being chosen against"),
        ],
    },
    "grief": {
        "scope": "Loss, death, mourning — interpersonal or self-mortality.",
        "examples": [
            ("Eric Clapton", "Tears in Heaven",
             "mourning a child"),
        ],
    },
    "addiction": {
        "scope": "Substance dependency or compulsive return to a destructive pattern.",
        "examples": [
            ("Amy Winehouse", "Rehab",
             "refusal to quit naming the dependency"),
        ],
    },
    "party": {
        "scope": "Hedonism, celebration, the night out as content.",
        "examples": [
            ("Kesha", "Tik Tok",
             "drinking and dancing as the song's whole subject"),
        ],
    },
    "political": {
        "scope": "Protest, system critique, civic argument.",
        "examples": [
            ("Bob Dylan", "The Times They Are a-Changin'",
             "explicit civic prophecy"),
        ],
    },
    "nostalgia": {
        "scope": "Back-then, memory — the past as warmer than the present.",
        "examples": [
            ("Bryan Adams", "Summer of '69",
             "specific era as object of longing"),
        ],
    },
    "ambition": {
        "scope": "Hustle, climbing — the drive toward something not yet achieved.",
        "examples": [
            ("Eminem", "Lose Yourself",
             "single-shot pursuit of breakthrough"),
        ],
    },
    "violence": {
        "scope": "Threat, menace, combat — physical aggression as content.",
        "examples": [
            ("The Beatles", "Maxwell's Silver Hammer",
             "narrative of casual murder, even in cheerful frame"),
        ],
    },
    "loneliness": {
        "scope": "Isolation, alone — the absence of others as the subject.",
        "examples": [
            ("Roy Orbison", "Only the Lonely",
             "the state of aloneness named directly"),
        ],
    },
    "self-affirmation": {
        "scope": "Identity, empowerment — claiming one's own worth.",
        "examples": [
            ("Lizzo", "Good as Hell",
             "explicit self-worth assertion"),
        ],
    },
    "rebellion": {
        "scope": "Anti-authority, fuck-off — refusal of imposed order.",
        "examples": [
            ("Twisted Sister", "We're Not Gonna Take It",
             "explicit refusal anthem"),
        ],
    },
    "escapism": {
        "scope": "Fantasy, dissociation — leaving reality as the song's mode.",
        "examples": [
            ("The Eagles", "Hotel California",
             "surreal place as escape that becomes trap"),
        ],
    },
    "family": {
        "scope": "Parents, kids, kin — blood/chosen-family relationships.",
        "examples": [
            ("Tupac", "Dear Mama",
             "tribute to mother across hardship"),
        ],
    },
    "place": {
        "scope": "Hometown, roots, city — geography as identity or subject.",
        "examples": [
            ("Bruce Springsteen", "Born in the U.S.A.",
             "place of origin as central frame"),
        ],
    },
    "fame": {
        "scope": "The machine, scrutiny — being looked at as the subject.",
        "examples": [
            ("Lady Gaga", "Paparazzi",
             "fame's gaze as the song's relationship"),
        ],
    },
    "existential": {
        "scope": "Mortality, meaning — the bigger questions of being alive.",
        "examples": [
            ("Pink Floyd", "Time",
             "the slipping away of life as content"),
        ],
    },
    "obsession": {
        "scope": "Fixation, unrequited — narrator cannot let the object go.",
        "examples": [
            ("The Police", "Every Breath You Take",
             "constant surveillance dressed as love"),
        ],
    },
    "resilience": {
        "scope": "Endurance, bouncing back, carrying on — weathering hard times as the subject.",
        "examples": [
            ("Destiny's Child", "Survivor",
             "enduring and outlasting what tried to break the narrator"),
        ],
    },
    "faith": {
        "scope": ("Devotion, trust, or belief in something larger: a god, the "
                  "universe, love, a calling, or oneself. Religious or secular. "
                  "The orientation of belief, not submission to doctrine "
                  "(doctrine is dogma, scored separately by the rubric, never a topic)."),
        "examples": [
            ("Leonard Cohen", "Hallelujah",
             "wracked, secular-spiritual devotion"),
            ("Florence + the Machine", "Shake It Out",
             "secular faith in deliverance from one's own darkness"),
        ],
    },
    "friendship": {
        "scope": ("Platonic loyalty and camaraderie, the ride-or-die bond "
                  "between friends or crew. Not romance, not kin."),
        "examples": [
            ("Randy Newman", "You've Got a Friend in Me",
             "plain loyalty-of-friendship pledge"),
        ],
    },
    "mental-health": {
        "scope": ("Anxiety, depression, or inner psychological struggle as the "
                  "song's subject. The state of the mind itself, named."),
        "examples": [
            ("Logic", "1-800-273-8255",
             "talking someone back from the edge"),
        ],
    },
    "joy": {
        "scope": ("Gratitude, hope, contentment, or wonder. The celebration of "
                  "being alive that is not a party and not a flex."),
        "examples": [
            ("Louis Armstrong", "What a Wonderful World",
             "unguarded gratitude for the world"),
        ],
    },
    "survival": {
        "scope": ("The grind, poverty, class, getting by. Hardship of material "
                  "circumstance as the subject, distinct from the flex that "
                  "follows it."),
        "examples": [
            ("Tracy Chapman", "Fast Car",
             "trying to escape generational poverty"),
        ],
    },
}


VALID_SLUGS = frozenset(ETHER_TAXONOMY.keys())

assert len(VALID_SLUGS) == 30, (
    "Taxonomy is 30 topics. Adding/removing one is a deliberate edit here "
    "(scope + examples feed the live tagger); update this count to match."
)


def _code_taxonomy_for_prompt() -> str:
    """Render the CODE taxonomy as a prompt-ready block (the fallback). The
    public taxonomy_for_prompt(db) below dispatches DB-vs-code; this is the code
    half, kept byte-for-byte so flipping the DB flag does not drift the prompt
    for the seeded topics."""
    lines = []
    for slug, data in ETHER_TAXONOMY.items():
        lines.append(f"  {slug}: {data['scope']}")
        for artist, title, why in data["examples"]:
            lines.append(f"    e.g. {artist} — \"{title}\" ({why})")
    return "\n".join(lines)


# === Topic hierarchy =======================================================
#
# A strict PRIMARY tree + optional SECONDARY facets. The rule that scales past
# these boxes (topics legitimately span more than one theme "all over the
# place"):
#
#   - Every topic has EXACTLY ONE primary theme -- its center of gravity, the
#     one shelf you'd file it on if forced to pick one. This is a strict tree
#     and is the ONLY mapping any rollup aggregates on, so share charts always
#     sum cleanly (no double-counting, no broken 100%).
#   - A topic may also carry SECONDARY themes (facets) -- real cross-cutting
#     affinities. Stored separately, NEVER summed; they power "also touches..."
#     labels and cross-links so nothing is lost, without corrupting the rollup.
#
# Re-shelving a topic is a one-line edit here; the assertions keep it honest.

ETHER_THEMES = {
    "romance":           "Romance",
    "friendship-bonds":  "Friendship & Bonds",
    "self-psyche":       "Self & Psyche",
    "status-survival":   "Status & Survival",
    "faith":             "Faith",
    "conflict-power":    "Conflict & Power",
    "hedonism-escape":   "Hedonism & Escape",
    "roots-belonging":   "Roots & Belonging",
    "meaning-mortality": "Meaning & Mortality",
}

# The spine: topic slug -> its single primary theme. Must cover all 30 exactly.
# Note: Romance and Friendship & Bonds are distinct themes -- romance is one
# specific shape of love, not love itself; platonic bonds stand on their own.
ETHER_TOPIC_PRIMARY = {
    "romance":          "romance",
    "breakup":          "romance",
    "longing":          "romance",
    "sex":              "romance",
    "betrayal":         "romance",
    "obsession":        "romance",
    "friendship":       "friendship-bonds",
    "loneliness":       "friendship-bonds",
    "flex":             "status-survival",
    "fame":             "status-survival",
    "ambition":         "status-survival",
    "survival":         "status-survival",
    "self-affirmation": "self-psyche",
    "self-deprecation": "self-psyche",
    "resilience":       "self-psyche",
    "mental-health":    "self-psyche",
    "joy":              "self-psyche",
    "faith":            "faith",
    "revenge":          "conflict-power",
    "violence":         "conflict-power",
    "rebellion":        "conflict-power",
    "political":        "conflict-power",
    "party":            "hedonism-escape",
    "addiction":        "hedonism-escape",
    "escapism":         "hedonism-escape",
    "family":           "roots-belonging",
    "place":            "roots-belonging",
    "nostalgia":        "roots-belonging",
    "grief":            "meaning-mortality",
    "existential":      "meaning-mortality",
}

# Facets: topic slug -> additional themes it genuinely touches. Optional; only
# where a real cross-cut exists. Never used in any sum.
ETHER_TOPIC_SECONDARY = {
    "ambition":      ["self-psyche"],
    "survival":      ["self-psyche"],
    "loneliness":    ["romance"],
    "friendship":    ["roots-belonging"],
    "grief":         ["romance"],
    "revenge":       ["romance"],
    "betrayal":      ["conflict-power"],
    "obsession":     ["self-psyche"],
    "nostalgia":     ["meaning-mortality"],
    "escapism":      ["meaning-mortality"],
    "mental-health": ["meaning-mortality"],
    "faith":         ["meaning-mortality"],
}

# --- Integrity guards: the hierarchy must stay total + valid ---
assert set(ETHER_TOPIC_PRIMARY.keys()) == VALID_SLUGS, (
    "Every topic must have exactly one primary theme (and no stragglers)."
)
assert all(t in ETHER_THEMES for t in ETHER_TOPIC_PRIMARY.values()), (
    "Every primary theme must be a defined ETHER_THEMES slug."
)
for _slug, _themes in ETHER_TOPIC_SECONDARY.items():
    assert _slug in VALID_SLUGS, f"Unknown topic in secondary map: {_slug}"
    for _t in _themes:
        assert _t in ETHER_THEMES, f"Unknown secondary theme '{_t}' for {_slug}"
        assert _t != ETHER_TOPIC_PRIMARY[_slug], (
            f"Secondary theme for {_slug} duplicates its primary."
        )


def _label_for(slug: str) -> str:
    """Default display label for a topic slug ("self-affirmation" -> "self
    affirmation"). Matches what the public surfaces have always rendered."""
    return slug.replace("-", " ")


def _code_hierarchy() -> dict:
    """The hierarchy straight from the code constants -- the fail-safe fallback
    the resolver returns when the DB tables are empty or unreachable."""
    return {
        "themes": [{"slug": s, "label": label} for s, label in ETHER_THEMES.items()],
        "topics": {
            slug: {
                "primary": ETHER_TOPIC_PRIMARY[slug],
                "also": list(ETHER_TOPIC_SECONDARY.get(slug, [])),
                "label": _label_for(slug),
            }
            for slug in ETHER_TAXONOMY.keys()
        },
    }


# === DB-aware resolver (Phase 1 of the admin taxonomy editor) ==============
#
# Phase 1 makes the DB the PRESENTATION source of truth for the theme list,
# labels, each topic's primary theme, secondary facets, and order. The tagger
# (VALID_SLUGS + taxonomy_for_prompt) stays CODE-driven and is untouched here.
#
# topic_hierarchy(db) prefers DB rows and FALLS BACK to the code constants when
# the tables are empty or the DB read fails (fail-safe -- the public page never
# breaks). Resolved data is cached module-level with a short TTL and BUSTED on
# any admin write, mirroring the kill-switch propagation pattern.

import logging as _logging
import time as _time

_logger = _logging.getLogger(__name__)

_RESOLVER_TTL = 30.0  # seconds; edits propagate within this window without a restart
_resolver_cache: dict = {"data": None, "expires": 0.0}
# Phase 2a: cached (valid_slugs, prompt_text) for the tagger definitions.
_defs_cache: dict = {"data": None, "expires": 0.0}


def bust_taxonomy_cache() -> None:
    """Drop the cached hierarchy + tagger definitions so the next read rebuilds
    from the DB. Called by every admin write (and the flag toggle) so edits
    propagate immediately."""
    _resolver_cache["data"] = None
    _resolver_cache["expires"] = 0.0
    _defs_cache["data"] = None
    _defs_cache["expires"] = 0.0


def _db_hierarchy(db) -> dict | None:
    """Build the hierarchy from the ether_themes/ether_topics rows, or return
    None when ether_themes is empty (signals: use the code fallback). Imports
    the models lazily -- this module is imported very early."""
    from app.models import EtherTheme, EtherTopic

    themes = (
        db.query(EtherTheme)
        .order_by(EtherTheme.sort_order.asc(), EtherTheme.id.asc())
        .all()
    )
    if not themes:
        return None
    topics = (
        db.query(EtherTopic)
        .order_by(EtherTopic.sort_order.asc(), EtherTopic.id.asc())
        .all()
    )
    return {
        "themes": [{"slug": t.slug, "label": t.label} for t in themes],
        "topics": {
            tp.slug: {
                "primary": tp.primary_theme_slug,
                "also": list(tp.secondary_themes or []),
                "label": tp.label,
            }
            for tp in topics
        },
    }


def topic_hierarchy(db=None) -> dict:
    """The hierarchy in one payload: themes (ordered) + per-topic primary/also/label.

    Shape:
      {
        "themes": [{"slug": ..., "label": ...}, ...],
        "topics": {slug: {"primary": theme_slug, "also": [theme_slug, ...], "label": ...}},
      }

    Called with no db -> the code constants (used by import-time / non-request
    callers). Called with a db Session -> the DB rows (cached, TTL'd), falling
    back to the code constants if the tables are empty or the read fails.
    """
    if db is None:
        return _code_hierarchy()

    now = _time.monotonic()
    cached = _resolver_cache["data"]
    if cached is not None and now < _resolver_cache["expires"]:
        return cached

    data = None
    try:
        data = _db_hierarchy(db)
    except Exception:
        _logger.exception("ether taxonomy DB resolve failed; using code fallback")
        data = None
    if data is None:
        data = _code_hierarchy()

    _resolver_cache["data"] = data
    _resolver_cache["expires"] = now + _RESOLVER_TTL
    return data


def themes(db=None) -> list[dict]:
    """Ordered themes [{slug, label}] -- DB-preferred, code fallback."""
    return topic_hierarchy(db)["themes"]


def topics(db=None) -> dict:
    """Per-topic mapping {slug: {primary, also, label}} -- DB-preferred."""
    return topic_hierarchy(db)["topics"]


# === Idempotent seed =======================================================
#
# Called once at startup (after migrations). If ether_themes is empty, seed both
# tables from the code constants -- order = dict order, topic labels = humanized
# slug. NEVER overwrites existing rows, so an admin's edits are safe across
# restarts. Fail-soft: a seed hiccup must not block startup (the resolver still
# falls back to code).


def seed_taxonomy_if_empty(db) -> bool:
    """Seed ether_themes + ether_topics from the code constants when empty.
    Returns True if anything was inserted. Idempotent."""
    from app.models import EtherTheme, EtherTopic

    inserted = False

    if db.query(EtherTheme).count() == 0:
        for i, (slug, label) in enumerate(ETHER_THEMES.items()):
            db.add(EtherTheme(slug=slug, label=label, sort_order=i))
        inserted = True

    if db.query(EtherTopic).count() == 0:
        for i, slug in enumerate(ETHER_TAXONOMY.keys()):
            db.add(EtherTopic(
                slug=slug,
                label=_label_for(slug),
                primary_theme_slug=ETHER_TOPIC_PRIMARY[slug],
                secondary_themes=list(ETHER_TOPIC_SECONDARY.get(slug, [])),
                sort_order=i,
                scope=ETHER_TAXONOMY[slug]["scope"],
                examples=_code_examples_for(slug),
            ))
        inserted = True

    if inserted:
        db.commit()
        bust_taxonomy_cache()
    return inserted


# === Phase 2a: DB-driven tagger definitions ================================
#
# When the `taxonomy_db_driven.enabled` flag is ON, the live ether tagger builds
# its prompt + valid-slug set from the ether_topics scope/examples rows instead
# of the code constants. Fail-safe: an empty/unreachable DB (or the flag off)
# returns the CODE definitions, so a flip is reversible and an outage degrades to
# code. The code render is kept byte-identical so flipping the flag does not
# drift the prompt for the seeded topics.


def _code_examples_for(slug: str) -> list[dict]:
    """Code examples for a topic as JSON-friendly dicts (the stored shape)."""
    return [
        {"artist": a, "title": t, "why": w}
        for (a, t, w) in ETHER_TAXONOMY.get(slug, {}).get("examples", [])
    ]


def backfill_topic_definitions(db) -> int:
    """Idempotent: fill scope/examples on ether_topics rows that predate the
    Phase-2a columns (the Phase-1 seed inserted them without definitions). Only
    touches rows whose scope IS NULL and whose slug exists in the code taxonomy;
    never overwrites an admin-authored definition. Returns rows updated."""
    from app.models import EtherTopic

    updated = 0
    rows = db.query(EtherTopic).filter(EtherTopic.scope.is_(None)).all()
    for row in rows:
        if row.slug in ETHER_TAXONOMY:
            row.scope = ETHER_TAXONOMY[row.slug]["scope"]
            row.examples = _code_examples_for(row.slug)
            updated += 1
    if updated:
        db.commit()
        bust_taxonomy_cache()
    return updated


def _render_definitions(defs: list[tuple]) -> str:
    """Render [(slug, scope, examples)] as the prompt taxonomy block. Mirrors
    _code_taxonomy_for_prompt byte-for-byte (same em-dash example form)."""
    lines = []
    for slug, scope, examples in defs:
        lines.append(f"  {slug}: {scope}")
        for ex in examples or []:
            if isinstance(ex, dict):
                a, t, w = ex.get("artist", ""), ex.get("title", ""), ex.get("why", "")
            else:  # tolerate a [artist, title, why] tuple/list shape
                a = ex[0] if len(ex) > 0 else ""
                t = ex[1] if len(ex) > 1 else ""
                w = ex[2] if len(ex) > 2 else ""
            lines.append(f"    e.g. {a} — \"{t}\" ({w})")
    return "\n".join(lines)


def _db_definitions(db) -> list[tuple] | None:
    """[(slug, scope, examples)] for topics that carry a non-empty scope, in
    sort order. None when no topic has a definition (signals: code fallback)."""
    from app.models import EtherTopic

    rows = (
        db.query(EtherTopic)
        .order_by(EtherTopic.sort_order.asc(), EtherTopic.id.asc())
        .all()
    )
    defs = [
        (r.slug, r.scope, list(r.examples or []))
        for r in rows
        if (r.scope or "").strip()
    ]
    return defs or None


def _resolve_definitions(db) -> tuple:
    """(valid_slugs frozenset, prompt_text). DB when the flag is on AND at least
    one topic has a definition; else the code constants. Fail-safe to code."""
    use_db = False
    if db is not None:
        try:
            from app.services.feature_flags import is_taxonomy_db_driven
            use_db = is_taxonomy_db_driven(db)
        except Exception:
            _logger.exception("taxonomy flag read failed; using code definitions")
            use_db = False

    if use_db:
        try:
            defs = _db_definitions(db)
        except Exception:
            _logger.exception("taxonomy DB definitions read failed; using code")
            defs = None
        if defs:
            return frozenset(s for s, _, _ in defs), _render_definitions(defs)

    return VALID_SLUGS, _code_taxonomy_for_prompt()


def _cached_definitions(db) -> tuple:
    now = _time.monotonic()
    cached = _defs_cache["data"]
    if cached is not None and now < _defs_cache["expires"]:
        return cached
    data = _resolve_definitions(db)
    _defs_cache["data"] = data
    _defs_cache["expires"] = now + _RESOLVER_TTL
    return data


def valid_slugs(db=None) -> frozenset:
    """The set of taggable topic slugs. No db -> the code frozenset (import-time /
    terminal callers). With a db -> DB-resolved when the flag is on, else code."""
    if db is None:
        return VALID_SLUGS
    return _cached_definitions(db)[0]


def taxonomy_for_prompt(db=None) -> str:
    """The taxonomy block for the tagger/synthesis prompt. No db -> the code
    render (unchanged for legacy callers). With a db -> DB-resolved when the flag
    is on, else code."""
    if db is None:
        return _code_taxonomy_for_prompt()
    return _cached_definitions(db)[1]
