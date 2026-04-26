"""The Ether Art Chart — closed 24-topic taxonomy (single source of truth).

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
}


VALID_SLUGS = frozenset(ETHER_TAXONOMY.keys())

assert len(VALID_SLUGS) == 24, (
    "Taxonomy must remain at 24 entries unless audit-accept adds one."
)


def taxonomy_for_prompt() -> str:
    """Render the taxonomy as a prompt-ready block."""
    lines = []
    for slug, data in ETHER_TAXONOMY.items():
        lines.append(f"  {slug}: {data['scope']}")
        for artist, title, why in data["examples"]:
            lines.append(f"    e.g. {artist} — \"{title}\" ({why})")
    return "\n".join(lines)
