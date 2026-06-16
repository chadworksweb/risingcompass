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


def taxonomy_for_prompt() -> str:
    """Render the taxonomy as a prompt-ready block."""
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


def topic_hierarchy() -> dict:
    """The hierarchy in one payload: themes (ordered) + per-topic primary/also.

    Shape:
      {
        "themes": [{"slug": ..., "label": ...}, ...],
        "topics": {slug: {"primary": theme_slug, "also": [theme_slug, ...]}},
      }
    """
    return {
        "themes": [{"slug": s, "label": label} for s, label in ETHER_THEMES.items()],
        "topics": {
            slug: {
                "primary": ETHER_TOPIC_PRIMARY[slug],
                "also": ETHER_TOPIC_SECONDARY.get(slug, []),
            }
            for slug in ETHER_TAXONOMY.keys()
        },
    }
