# Rising Compass — Backlog

## Phase 3: Admin & Data Management

- [ ] **Admin song/album manager** — CRUD interface for the Song and Album tables, similar to CPT post management in WordPress. Browse, search, edit, delete. Inline editing for tier, charge_value, contamination, M/E/I. Bulk actions.
- [ ] **Billboard Hot 100 scraper** — Agent scrapes Wikipedia for Billboard Hot 100 year-end top 10 per year, adds to Song table. Original dataset was 650 songs (top 10 each year through 2023). Priority: 2024 and 2025 (missing, will skew heavily red — that context matters for launch).
- [ ] **Charge value calibration tooling** — Agent clusters charge_values at tier midpoints (-45, +45, +68, -88). Needs more calibration data and possibly prompt refinement to use full -100 to +100 range.

## Rubric Evolution: Control Tactics Dimension

Future rubric dimension that goes beyond M/E/I to identify **songwriting techniques used as control mechanisms** — how the song is structurally engineered to bypass conscious thought.

This is separate from what the song says (M/E/I). This is about **how the delivery mechanism itself manipulates the listener.**

Techniques to detect and flag:
- **Empty repetition** — repeating meaningless lines ("UP UP UP", "yeah yeah yeah") to induce hypnotic/trance state. Not all repetition — repeating a line with weight drives it deeper (intentional craft). Repeating nothing is structural padding that numbs the mind and makes it receptive.
- **Melodic loops** — short, cycling melodic phrases designed to hook without substance. The earworm as delivery vehicle.
- **Chant structures** — call-and-response or group chant patterns that bypass individual thought and activate herd compliance.
- **Lyrical density manipulation** — drowning meaning in speed (mumble rap, rapid-fire delivery) so the conscious mind can't process what's being absorbed.
- **More TBD as patterns are identified**

The thesis: these aren't just cheap songwriting. They are techniques — whether consciously deployed or culturally inherited — that put the listener in a suggestible state. The low-frequency payload (objectification, substance celebration, ego worship) lands harder when the delivery mechanism has already turned off critical thinking. Repetition is how you program. Music is the most efficient delivery system humanity has ever built.

**Proof point:** Michael Jackson — the biggest pop artist in history — never used empty triple repetition. Every repeated line in his catalog carries weight. "Billie Jean is not my lover" hits harder each time because the line means something. The repetition serves the message. That's the standard. The fact that today's biggest hits rely on repeating nothing tells you everything about where the craft went.

Connection to existing rubric: this would inform the Expression dimension and could act as a contamination-like modifier or a separate axis alongside charge_value.

## "Compass in Action" Public Feed
- [ ] **Ongoing feed of landmark moments** — short, punchy entries where the compass reveals something the culture missed. Lives on risingcompass.com. Not editorial, not blog posts — the compass showing its work in public. Each entry: the songs, what the culture says, what the compass read, one line on why it matters. Mirror, not megaphone. First entry: Pink Pony Club vs. Love Somebody inversion from Draft #15.
- [ ] **Frontend component** — feed section on the site, each entry is a card. Minimal design. Let the data speak.
- [ ] **Backend model** — CompassMoment or similar. Title, songs involved, narrative, date. Admin creates via API or future admin panel.

## Phase 4: Albums & Launch Prep

- [ ] **Weekly Top 10 Albums reading** — album charge panel with mini compass, editorial, and track-level breakdown. Backend: album reading model, agent classification. Frontend: album-reading-panel populated with real data (currently showing "under development" placeholder).
- [ ] **Album Deep Dives section** — full album analyses with track-by-track scoring, already wired in frontend + backend but needs content pipeline.
- [ ] Items TBD as Phase 3 solidifies
