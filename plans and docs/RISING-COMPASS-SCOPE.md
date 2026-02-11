# THE RISING COMPASS — Project Scope

## WHAT THIS IS

The Rising Compass is a living, daily-updating consciousness diagnostic for popular music. It is the only tool of its kind on the internet. "Rising" connects to the Chad Rising brand — the compass tells you if today's music is rising or falling.

**Tagline:** "Where is popular music pointed today?"

---

## CONTEXT & EXISTING ASSETS

### Existing Blog Posts (reference only — do not build on top of)
There are two existing blog posts:
- `/popular-music-has-lost-its-moral-compass/` — contains a comprehensive analysis of Billboard Hot 100 #1-#10 hits from 1960-2024. 650+ songs classified by an older version of the rubric using a simpler green/red color system.
- `/the-moral-rot-and-decay-of-popular-music/` — a separate blog post.

These posts use an older color system and naming convention. **The Rising Compass is a fresh build** with an expanded five-tier rubric (Elevated / Grounded / Lateral / Shallow / Degraded). The existing posts serve as:
1. **Data source** — the 650+ song classifications need to be extracted, re-mapped to the new five-tier system, and structured into the database
2. **Reference** — the analysis methodology and reasoning are useful context
3. **Historical foundation** — they can be linked as background reading

Do NOT inherit their design, color system, or page structure.

### The 650+ Song Dataset
These songs are currently embedded in the blog post HTML only. **FIRST BUILD TASK:** Extract and structure this data into the database. Each song needs: title, artist, year, decade, chart position, rubric color (bright green / green / yellow / orange / red), one-line charge summary, and message/expression/intention analysis. This structured dataset serves two purposes:
1. Historical foundation for the compass's position calculation
2. Training data for the AI agent (Phase 2)

### 27 Philosophical Framework Blogs
All published on chadrising.com. These articulate WHY the rubric works — what constitutes elevation vs. degradation, what processing vs. sparking means, how to detect the charge underneath the narrative. These become the AI agent's grounding context in Phase 2.

### DtMF Analysis
A complete track-by-track analysis of Bad Bunny's 2025 Album of the Year (16 tracks). This becomes the first "Album Deep Dive" entry. The full analysis is published at: https://chadrising.com/the-2026-album-of-the-year-bait-and-switch-on-a-global-scale/ — use this as both a training reference for the agent's rubric calibration and a light design reference for album deep dive presentation.

---

## STACK & INFRASTRUCTURE

- **This is a standalone product site at risingcompass.com — NOT a WordPress page**
- **Build on local development environment first** — do not deploy until ready
- **Frontend:** Static HTML/CSS/JS served via Cloudflare (Pages or CDN). The dashboard is one page — the compass, charge level, contamination counter, and expandable sections. Fast, lightweight, no CMS overhead.
- **Backend:** Python or Node on a DigitalOcean droplet ($5/month). Handles the API endpoints, agent pipeline, cron jobs, email approval workflow, and database writes.
- **Database:** PostgreSQL or SQLite on the same droplet. Stores the 650+ historical song classifications, daily readings, agent drafts, and offset alerts.
- **SVG-based visuals** for the compass and charge level (no heavy JS libraries, no Canvas)
- **Agents run on the droplet** via scheduled cron jobs — Compass Agent and Offset Agent each on their own schedule
- **No WordPress, no ACF, no CMS** — data entry for Phase 1 (manual readings) happens through a simple admin interface on the backend
- **chadrising.com links to risingcompass.com** — editorial, methodology, and deep dives stay on chadrising.com as blog content

---

## THE PAGE: SINGLE-MODULE DESIGN

The landing experience is ONE dominant visual module — like the Death Clock. The compass IS the page. Everything else fractalizes off from there.

### Primary Module: THE COMPASS + charge level (hero, full viewport)

**The Compass (macro view):**
- A modern, clean gauge graphic — think Ookla speedometer, not antique compass. Digital, minimal, striking.
- True north = consciousness, elevation, the sacred function of music
- The needle shows the current aggregate direction based on rolling data
- Shows degrees from true north (e.g., "Popular music is currently pointed 158° from true north")
- Moves slowly — this is the macro trend, the civilizational story
- The 60-year Billboard dataset provides the historical context for how the needle got here
- **Phase 1:** Calculated from the structured historical dataset (650+ songs, 1960-2024)
- **Phase 2:** Incorporates rolling weekly/monthly aggregate of new daily readings

**The charge level (micro view — integrated with or directly below compass):**
- A daily reading of the cultural charge being absorbed by millions of listeners RIGHT NOW
- Visual: color-banded indicator showing today's level
- Color bands:
  - **BRIGHT GREEN** — "Elevated" — music is actively raising consciousness
  - **GREEN** — "Grounded" — music is processing honestly, serving the listener
  - **YELLOW** — "Lateral" — moves the listener sideways, toward the artist or toward nothing. Fills time. Neither serves nor harms.
  - **ORANGE** — "Shallow" — ego, materialism, shallow pursuit
  - **RED** — "Degraded" — sexual objectification, substance celebration, possession, contempt
- Updated daily based on current top songs
- Shows today's date
- Shows the aggregate: e.g., "Today's charge: ORANGE — 7 out of 10 top songs carry red-charge lyrics"
- Brief one-line editorial summary of today's reading
- **Calculated programmatically from the song data** even in Phase 1 (don't manually position the indicator — let the data drive the visual)

**The Contamination Counter (supplemental context — Geiger counter metaphor):**
- A separate reading that tracks how many songs carry contamination — genuine substance undermined by low-frequency elements woven into the expression or intention
- Does NOT affect the charge level calculation. The charge tells you the direction. The contamination counter tells you how much of what sounds clean is actually carrying something underneath.
- Visual: a small, minimal counter element near the compass. Not a full instrument, just a reading. Subtle tick/pulse animation to reinforce the Geiger metaphor.
- Example reading: "Contamination detected in 4 of 10 songs"
- "Contaminated" is a MODIFIER that can attach to any tier — a Green (Contaminated) song still counts as Green for the charge level, but the contamination is tracked separately
- Examples of contamination: real nostalgia undermined by drug references, genuine cultural pride with ego woven through, honest emotional processing wrapped in objectification
- This is the subtle energy detection made into a live daily metric — the thing no other tool measures

### Expandable: Daily Breakdown (click/tap to expand from the main module)
- Song title, artist, chart position
- Rubric result (color + one-line charge summary)
- Expandable detail showing the message/expression/intention analysis
- NO full lyrics reproduced — just the rubric assessment and brief paraphrased evidence

### Secondary Navigation (accessible from main module, not cluttering it)
- **The Drift** — historical data showing decade-by-decade compass movement (links to or integrates the existing Billboard analysis)
- **Methodology** — the rubric explained (message, expression, intention + color system)
- **Album Deep Dives** — linked case studies (DtMF first)
- **Archive** — past daily readings browsable by date

---

## THE RUBRIC (methodology, permanent reference)

**Message:** What the song is about on the surface.

**Expression:** How the message is delivered — what imagery and language carry it.

**Intention:** What frequency the song activates in the listener — does it process (help the listener grow, reflect, heal) or spark (initiate lower-vibration behavior)?

**Color System:**
- **Bright Green — Elevated:** Actively elevates. Addresses something beyond the self — community, sovereignty, healing, philosophy, the sacred.
- **Green — Grounded:** Processes honestly. Meets the listener at a real emotional place and helps them move through it with dignity.
- **Yellow — Lateral:** Moves the listener sideways — toward the artist or toward nothing. Fills time. Neither serves nor harms.
- **Orange — Shallow:** Ego, materialism, shallow pursuit — not overtly harmful but not serving the listener.
- **Red — Degraded:** Activates lower frequencies. Sexual objectification, substance celebration, possession, contempt, degradation.

---

## DATA ENTRY INFRASTRUCTURE (Phase 1 — Manual)

Build a simple admin interface on the backend that lets the site owner:
- Add a date
- Add songs with: title, artist, chart position, rubric color (select field), contaminated flag, contamination note, one-line summary, and optional expanded analysis (message/expression/intention)
- The frontend auto-generates the visual reading from the data via API
- The charge level indicator position is CALCULATED from the song data (ratio of colors)
- Historical entries are preserved and browsable

**Daily Reading data structure:**
Fields:
- Date
- Compass degree (calculated from historical + accumulated daily data)
- Editorial summary (one-line text)
- Songs (array):
  - Song title (text)
  - Artist (text)
  - Chart position (number)
  - Rubric color (select: bright_green / green / yellow / orange / red)
  - Contaminated (boolean: yes/no)
  - Contamination note (text, optional — what contaminates it, e.g., "drug references undercut genuine nostalgia")
  - One-line charge summary (text)
  - Message analysis (text, optional)
  - Expression analysis (text, optional)
  - Intention analysis (text, optional)
  - Chart source (select: spotify / apple_music / billboard)

---

## THE AI AGENT (Phase 2 — build infrastructure now, automate later)

### What the agent does:
1. Pulls the current top 10 songs from Spotify public charts (charts.spotify.com) and Apple Music charts (API, when available)
2. Reads lyrics via Genius API (backend only — lyrics are never displayed or stored publicly)
3. Applies the rubric (message, expression, intention)
4. Assigns a charge (bright green → green → yellow → orange → red)
5. Generates a one-line summary for each song
6. Calculates the aggregate charge level
7. Drafts the complete daily reading
8. Emails the draft to Chad for approval
9. On approval, publishes the reading to the database and the frontend updates

### What the agent does NOT do:
- Make final editorial calls. Chad approves before publishing. The agent drafts, Chad confirms.
- Display, store, or reproduce copyrighted lyrics. The output is always the rubric assessment, never the content.

### Agent Stack:
- **Anthropic API (Claude)** — API key and billing already set up at console.anthropic.com
- **Genius API** — backend lyrics research (no public attribution, no lyrics displayed)
- **Spotify public charts** — charts.spotify.com as primary chart source
- **Apple Music API** — added later when Apple Developer account is set up (not blocking)
- **Backend API** — for publishing approved readings to the database
- **Email** — approval workflow (agent sends draft, Chad replies to approve/correct)

### Training Data:
- 650+ Billboard songs (1960-2024) classified by the rubric — extracted and structured from the blog
- 16 DtMF tracks analyzed with the same methodology
- 27 blogs on chadrising.com articulating the philosophical framework
- The agent must learn the UNIQUE distinctions of this rubric: the difference between a breakup song that processes with dignity and one that sparks contempt. Between cultural pride that's genuine and cultural pride that's wrapping paper. This is what separates it from generic sentiment analysis.

### Calibration Phase:
Before going live, the agent enters a calibration period:
- Agent classifies songs
- Chad reviews and corrects
- Agent adjusts
- This continues until Chad determines it's ready

---

## THE OFFSET AGENT (Phase 2 — separate agent, same dashboard)

### What it is:
A second AI agent that tracks the gap between what artists SAY publicly and what their music DOES to listeners. When an artist makes public statements about social issues, activism, or industry critique, The Offset Agent cross-references those statements against the artist's musical charge as classified by the Compass Agent.

### What the Offset Agent does:
1. Scans news, social media, interviews, press, and documentaries for public statements by artists currently on the charts (or artists with recent Album Deep Dives)
2. Identifies statements that carry a positive, activist, or socially conscious framing
3. Cross-references the artist's musical catalog charge against their public persona
4. Flags offsets — where the public statement contradicts the musical charge
5. Generates a brief offset summary for each flagged instance
6. Feeds flagged offsets into the daily reading draft for Chad's approval

### What the Offset Agent does NOT do:
- Attack artists personally. It documents the gap between words and work. The data speaks.
- Make final editorial calls. Same approval workflow — Chad reviews before anything publishes.
- Scan for negative statements. It specifically looks for POSITIVE public positioning that contradicts the musical charge. The diagnostic is about the offset, not the person.

### Examples of offsets:
- Artist speaks publicly about ICE / immigration → their catalog is classified Red (degradation, objectification)
- Artist critiques the music industry in a documentary → their music feeds the exact patterns they're critiquing
- Artist promotes charity work or social causes → their lyrics celebrate the opposite frequencies

### Offset Agent Stack:
- **Anthropic API (Claude)** — same billing, separate agent configuration
- **Web search / news APIs** — for scanning public statements (specific sources TBD during build)
- **Cross-references Compass Agent data** — needs access to the artist's rubric classifications
- **Same approval workflow** — drafts feed into the daily reading email for Chad's review

### Visual Integration:
The Offset feeds into The Rising Compass page as a supplemental section — not part of the charge level or contamination counter, but visible in the daily reading when offsets are detected. Think: "Offset Alert: [Artist] spoke publicly about [issue] this week. Their current catalog charge: [Red/Orange]."

### Calibration:
Separate calibration from the Compass Agent. The nuance required to detect genuine activism vs. PR offset is a harder problem than song classification. This agent may take longer to calibrate. Chad determines when it's ready independently of the Compass Agent.

---

## DOMAIN STRATEGY

- **risingcompass.com from day one** — standalone product site
- **chadrising.com** links to it, writes about it, hosts editorial voice, methodology deep dives, and album deep dive blog posts
- **Relationship:**
  - risingcompass.com = the instrument (neutral, shareable, tool)
  - chadrising.com = the voice behind the instrument (editorial, methodology, case studies)
  - LEAM = the organizational home (when it goes public)

---

## DESIGN & UX

- This should feel like a TOOL, not a blog. Think dashboard, not article. Think Death Clock.
- **Layout:** Compass and all related assets/data keys anchor the LEFT side. Everything else (daily breakdown, methodology, archive, deep dives) lives on the RIGHT. Desktop gets the split view. Mobile stacks compass on top, everything else below.
- The compass IS the page. One module dominates. Everything else is secondary.
- The daily reading should be immediately visible and visually striking
- The color system is the dominant visual language
- Mobile-first — people will share this
- SVG-based visuals, CSS transitions for the needle, no heavy libraries
- No CMS dependencies — this is a standalone static frontend with an API backend

---

## INTEGRATION POINTS

Build this so it can:
- Link to from blog posts (DtMF analysis = first Album Deep Dive)
- Embed the compass and/or charge level widget on other sites (iframe/embed code)
- Share daily readings on social media (shareable image/card generation — Phase 3)
- Link individual song assessments to other parts of the site
- Eventually link to LEAM and Artist World when those are public

---

## BUILD PRIORITY

### Phase 1 — MVP (ship first):
1. Register risingcompass.com, set up DigitalOcean droplet + Cloudflare
2. Extract and structure the 650+ song dataset from blog HTML into database
3. Build the backend API and simple admin interface for manual data entry
4. Build the single-module compass page (static frontend)
5. Build the charge level visual (SVG, data-driven)
6. Build the compass visual (SVG, calculated from historical data)
7. Build the contamination counter visual
8. Build the expandable daily breakdown
9. Manual data entry workflow — enter a reading, frontend renders it via API

### Phase 2 — The Living System:
1. Compass Agent pipeline: Spotify charts → Genius lyrics → Claude classification → email draft → approve → publish
2. Compass Agent calibration period (classify → correct → repeat until Chad says ready)
3. Offset Agent pipeline: web scanning for artist public statements → cross-reference against catalog charge → flag offsets → feed into daily reading draft
4. Offset Agent calibration (separate from Compass Agent — may take longer)
5. Rolling compass calculation from accumulated daily readings
6. Historical browsing (calendar/archive of past readings)
7. The Drift visualization (decade-by-decade animated compass)
8. Add links from existing blog posts to The Rising Compass — no redirects, posts stay independent

### Phase 3 — Reach:
1. Apple Music API integration
2. Social sharing card auto-generation
3. Embeddable widget (iframe/embed code for other sites)
4. Album Deep Dive section on risingcompass.com (or linked back to chadrising.com blog posts)

---

## CRITICAL RULES

- Do NOT store or display copyrighted lyrics anywhere
- Build the compass degree calculation as a standalone function from day one (swap input source later)
- Build the charge level calculation from song data programmatically (never manually positioned)
- The existing Billboard analysis content on chadrising.com is a data source — extract from it, don't modify it
- The agent is the labor. Chad is the architect and the conscience.
