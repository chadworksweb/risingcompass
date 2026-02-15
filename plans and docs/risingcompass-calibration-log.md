# Rising Compass — Agent Calibration Log

How we train the Compass Agent. Every draft gets human calibration — tier corrections, charge value rankings, and documented blind spots. This log tracks what the agent gets wrong, why, and how the rubric evolves in response.

The agent absorbs the same cultural blind spots as the mass populace. Calibration is the process of breaking those blind spots one by one.

---

## Three Workflows (Nomenclature)

The system has three distinct data workflows. They are NOT the same thing.

- **Reading** — daily live chart classification. Spotify Top 20 → classify → calibrate → publish as "Today's Charge." This is the only thing that drives the compass needle on the live site. One per day.
- **Calibration** — training batches for the agent. Billboard year-end, test sets, or any group of songs classified for the purpose of correcting the agent and building training data. Feeds the Song table (few-shot examples) and the aggregate trajectory. NOT a daily reading. Examples: Draft #12 (2024 Billboard), Draft #15 (2025 Billboard).
- **Backfill** — historical reclassification. 1960 onward, 10 songs/year. Agent classifies, human calibrates. Feeds the Song table and aggregate trajectory. The goal: once the agent nails 5 consecutive years without correction, it runs unsupervised through 2023.

Songs from Calibration and Backfill contribute to the aggregate (trajectory chart, decade data) but NEVER appear as a daily Reading. Songs that repeat across years (e.g., on both 2024 and 2025 Billboard charts) contribute to EVERY year they appear — they don't count once and disappear.

---

## Calibration Process

1. **Agent classifies** — Spotify top 20 → Genius lyrics → Claude Sonnet 4.5 classifies each song with rubric + few-shot examples from Song table
2. **Human reviews** — Chad reviews all 20 songs, corrects tiers, identifies contamination
3. **Rankings within tiers** — Songs ranked from least to most extreme within each tier; charge_values spread evenly across tier range
4. **Corrections pushed** — Updated tiers and charge_values pushed to both the Draft AND the Song table (training data for future classifications)
5. **Blind spots documented** — What the agent missed and why, tracked here
6. **Rubric updated** — If a blind spot reveals a systemic pattern, a new rule is added to `rising_compass_agent_rubric.py`

**Goal:** Agent nails 5 consecutive years of historical classification (10 songs/year) without human correction. Then it runs unsupervised through 2023.

---

## Draft-by-Draft Calibration

### Draft #8 (First Rubric Sharpening Run) — 2026-02-13
**Status:** Partially reviewed, not fully calibrated. Superseded by Draft #9.

**Key observations:**
- Agent clustering charge_values at tier midpoints (-45, +45, +68, -88) — not using full range
- Man I Need (Olivia Dean) dropped yellow → orange after rubric sharpening
- NUEVAYoL dropped green → orange after rubric sharpening
- "Balanced" tier renamed to "Decent" this session — agent had been treating middle tier as "healthy"

**Rubric changes triggered:**
- "Topics Don't Determine Tiers" core section added
- "Songs, Not Artists" rule added
- Love song spectrum examples added
- Rule 5: Pairing-off default is NOT automatically elevated
- Per-song charge_value system created (-100 to +100)

---

### Draft #9 (Spotify Top 50 USA Daily) — 2026-02-14
**Status:** Fully calibrated. First complete human calibration pass.

**Agent accuracy before correction:** Poor. Consistently overrated romance and underrated subtle corruption.

**Tier corrections:**

| Song | Agent Tier | Corrected Tier | Why Agent Was Wrong |
|------|-----------|---------------|-------------------|
| DtMF | Elevated | Decent | Basic 1:1 romance. No growth, no processing. Agent treated coupling as inherently good. |
| BAILE INOLVIDABLE | Elevated | Decent | Same blind spot — surface romance read as elevated. |
| Choosin' Texas | Elevated | Decent | Same blind spot — pairing-off = elevated in agent's model. |
| Ordinary (Alex Warren) | Degraded | Decent | Self-subjugation, not ego. Agent confused self-deprecation with degradation. Opposite error. |
| Homewrecker (sombr) | Degraded | Corrupted | Active infidelity, not just temptation. Agent underrated the severity. |
| Golden (KPop Demon Hunters) | Elevated | Ascended (+78) | Full self-actualization. Agent couldn't distinguish strong Elevated from Ascended. |
| So Easy (Olivia Dean) | Degraded | Degraded (-28) | Correct tier, but barely. Flirty nothingness — almost Decent. |

**Blind spots identified:**
1. **Romance = Elevated by default.** Agent treats any love song with positive tone as Elevated. Three songs corrected down for this.
2. **Can't distinguish Elevated from Ascended.** Golden missed entirely — agent has no model for transcendence.
3. **Self-deprecation confused with ego.** Ordinary was the opposite of what the agent thought.
4. **Infidelity severity underrated.** Homewrecker should have been obvious Corrupted.

**Rubric changes triggered:**
- Effect buckets added per tier (what the song does to the listener)
- Intra-tier range descriptions (low/mid/high within each tier)
- Anti-clustering instruction added
- M/E/I word cap: max 20 words (agent was gaming sentence limits with run-on clauses)

---

### Draft #12 (2024 Billboard Year-End Top 20) — 2026-02-14
**Status:** Fully calibrated.

**Agent output:** 3 Corrupted, 11 Degraded, 0 Decent, 6 Elevated, 0 Ascended
**After calibration:** 8 Corrupted, 8 Degraded, 4 Decent, 0 Elevated, 0 Ascended

The agent thought 6 of the top 20 were Elevated. Zero actually were. This is the clearest evidence that the agent mirrors the cultural blind spot: it assumes popular = good.

**Full calibrated results:**

**Corrupted (8):**
| Rank | Song | Charge | Notes |
|------|------|--------|-------|
| 1 | A Bar Song (Tipsy) | -76 | |
| 2 | Lovin on Me | -79 | |
| 3 | Snooze (SZA) | -80 | |
| 4 | Please Please Please | -82 | |
| 5 | Espresso | -84 | |
| 6 | Agora Hills | -87 | |
| 7 | Like That | -90 | |
| 8 | Not Like Us | -95 | |

**Degraded (8):**
| Rank | Song | Charge | Notes |
|------|------|--------|-------|
| 1 | Saturn | -28 | |
| 2 | Good Luck, Babe! | -30 | |
| 3 | Million Dollar Baby | -38 | |
| 4 | Greedy | -44 | |
| 5 | I Had Some Help | -50 | |
| 6 | Cruel Summer | -58 | |
| 7 | Lose Control | -65 | |
| 8 | Too Sweet | -72 | |

**Decent (4, 3 contaminated):**
| Rank | Song | Charge | Notes |
|------|------|--------|-------|
| 1 | Beautiful Things | +12 | Clean |
| 2 | Stick Season | -10 | Contaminated (substance coping) |
| 3 | Birds of a Feather | -18 | Contaminated (codependent attachment) |
| 4 | I Remember Everything | -22 | Contaminated (alcohol framing) |

**Elevated: 0. Ascended: 0.**

**Blind spots identified:**
1. **Agent rated 6 songs Elevated that were actually Degraded/Decent.** Massive overrating. The entire Elevated tier was a hallucination.
2. **Agent only found 3 Corrupted; actual count was 8.** Consistent underrating of corruption severity.
3. **Diss tracks not automatically Corrupted.** Not Like Us is us-vs-them by definition — agent needed explicit instruction.
4. **Contamination on Decent songs missed.** Three of four Decent songs had contaminants the agent didn't flag.

**Rubric changes triggered:**
- Rule 7: "What doesn't kill me makes me want you more" — acknowledging red flags and leaning IN = Degraded or worse
- Rule 8: Rejecting innate knowing (gut feeling, friends warning, clear red flags) and overriding it = Corrupted. Self-abuse.
- Rule 9: Progressive packaging does not automatically elevate. Social justice/queer advocacy wrapped in ego/contempt is still Degraded.
- War-to-peace axis added to charge_value scale description
- Contamination clarification: requires actual degraded/corrupted artifacts. Not being deep enough ≠ contaminated.
- Few-shot examples bumped from 5/tier to 20/tier
- Diss tracks = automatically Corrupted (convention established)

---

### Draft #15 (2025 Billboard Year-End Top 20) — 2026-02-14 (IN PROGRESS)
**Status:** Agent classified. Human calibration in progress.

**Agent output:** 6 Corrupted, 9 Degraded, 3 Decent, 2 Elevated, 0 Ascended
**8 songs carried over from 2024 calibration** (pulled from Song table at confidence 1.0)

**Corrections so far:**

| Song | Agent Tier | Corrected Tier | Why Agent Was Wrong |
|------|-----------|---------------|-------------------|
| Die with a Smile (Lady Gaga & Bruno Mars) | Degraded (-48) | Decent, contaminated | Agent missed that this is fear-based urgency, not degradation. The love only activates under threat of destruction — without the apocalypse there's no song. Contamination: fear as engine, not love. |
| Luther (Kendrick Lamar & SZA) | Elevated (+48) | Degraded | Agent's worst miss this draft. Sexual innuendo throughout ("in/out/ride/slide", "I know you'll come"), materialism ("'Rari, crown, wrist froze"), noncommittal posturing ("might even settle down"). Agent heard melancholic R&B + Kendrick's name and assumed honest processing. The lyrics say otherwise. |
| APT. (Rosé & Bruno Mars) | Degraded (-48) | Decent (-24), contaminated | Not harmful enough to degrade — it's a drinking game with a melody. Contaminated by empty repetition chant ("APT APT APT") — hypnotic compliance structure with drinking ritual as payload. |
| Pink Pony Club (Chappell Roan) | Elevated (+68) | Decent (0) | Rule 9 in action. Agent saw "queer self-discovery + leaving small town + chosen family" and auto-elevated. Strip the progressive packaging and it's someone going to a club. The agent didn't get this framing from the rubric or lyrics — it got it from the base model's training data, where the internet already labeled this song a queer anthem. The AI classified the cultural narrative, not the song. |
| I'm the Problem (Morgan Wallen) | Degraded (-48) | Degraded | Agent correct on tier. |
| That's So True (Gracie Abrams) | Degraded (-48) | Corrupted | Relatability masks corruption. Narrator is angry without tools — relatable, but channels it into contempt, stalking the new girl, planning interference. Zero processing, all action. The listener bonds through aggression. |
| TV Off (Kendrick Lamar ft. Lefty Gunplay) | Corrupted (-82) | Corrupted | Agent correct. |
| Timeless (The Weeknd & Playboi Carti) | Corrupted (-78) | Corrupted (deep end) | Agent correct on tier, underrated severity. |
| Just in Case (Morgan Wallen) | Degraded (-68) | Degraded (low end) | Agent correct. Actively using people as placeholders while refusing to process loss. Holding the future hostage. |
| Taste (Sabrina Carpenter) | Corrupted (-78) | Corrupted | Agent correct. Directing aggression at a stranger with pure territorial contempt. |
| Squabble Up (Kendrick Lamar) | Degraded (-58) | Corrupted | Poetic delivery doesn't change what it activates — violence, ego, dominance, contempt. The craft makes it more dangerous, not less. Same press narrative bias as Pink Pony Club — Kendrick gets cultural savior status, the compass reads the actual output. |
| Love Somebody (Morgan Wallen) | Decent (-18) | Decent (+24) | The most important correction in this draft — and the one that caught the calibrator too. First instinct: Elevated. It brings tears. A man looking at his own pattern of avoidance and wanting something real. But wanting isn't processing. The song recognizes the pattern, names it, longs for something better — and stops there. That's the ceiling of Decent, not the floor of Elevated. Elevated requires movement. This song is max Decent: highest possible awareness without actual transformation. Still the definitive "Songs, Not Artists" moment — the agent buried it at -18 because of the name on the track. The compass moved it to the top of Decent because the lyrics earned it. Triggered Rule 10: longing is not elevation. |

**Blind spots identified (so far):**
1. **Sexual innuendo invisible when wrapped in vulnerability.** Luther has explicit sexual double entendres throughout. Agent classified it as "genuine emotional processing" because the TONE sounds vulnerable. The agent listens to tone, not lyrics.
2. **Artist reputation biases tier upward.** Kendrick Lamar + SZA = "must be deep." The agent can't separate who made it from what it says.
3. **Materialism overlooked when it's scattered.** A few lines of Ferrari/crown/frozen wrist don't register as a pattern to the agent. To the rubric, they're contaminants at minimum.
4. **Fear-based framing mistaken for genuine emotion.** Die with a Smile reads as emotional to the agent because fear IS an emotion. But fear-driven urgency isn't love — it's codependence under duress.
5. **Pre-built cultural narrative bias.** Pink Pony Club was classified based on what the internet says it is, not what the lyrics do to the listener. The base model's training data pre-labeled the song as a queer anthem before the rubric engaged. This is measurable proof that media framing functions as brainwashing — the AI reproduced the press narrative unprompted. Same pattern as Bad Bunny.
6. **Empty repetition invisible.** APT.'s chant structure ("APT APT APT") didn't register as a contamination vector. The agent has no model for structural hypnosis yet.
7. **Artist reputation bias works BOTH directions.** The agent underrated Love Somebody because Morgan Wallen's cultural profile is "country bro." Three of his other songs on this chart are Degraded. The agent couldn't separate the artist's reputation from what this specific song does. It assumed Wallen = surface. This is the mirror working exactly as designed — and the most powerful proof that "Songs, Not Artists" isn't just a rule. It's the whole point.
8. **Relatability masks corruption.** That's So True — the agent saw the self-awareness ("I know I'm jealous") and read it as processing. It's not. The narrator is relatable because the anger is real, but the song channels that anger into contempt, stalking, and planned interference. Zero processing, all action. Relatability makes corrupted content MORE dangerous — the listener bonds through the narrator's worst impulses.

---

## Landmark Moments

### The Pink Pony Club / Love Somebody Inversion (Draft #15, 2026-02-14)

Back to back in the same draft. Same chart. Opposite errors. This is the compass working.

**Pink Pony Club** (Chappell Roan) — the internet's darling. The press pre-labeled it a queer anthem of self-discovery. The AI absorbed that framing and gave it Elevated +68. The compass read the actual lyrics: someone going to a club. Decent. Zero. The progressive packaging earned it a free pass from the culture and from the AI. The compass didn't care.

**Love Somebody** (Morgan Wallen) — the name nobody would take seriously for emotional depth. Three of his other songs on this same chart are Degraded. The AI saw "Morgan Wallen" and filed it as surface-level longing. Decent -18. The compass read the actual lyrics: a man recognizing his own pattern of avoidance — whiskey, hookups, emptiness — and wanting something real. First instinct during calibration: Elevated. It brought tears. But tears aren't the test. The song longs for growth but stops at the longing. That's max Decent (+24) — the highest point you can reach without actual movement. This correction caught the calibrator mid-stream and triggered a new rule: longing is not elevation.

The culture elevated what had the right branding. The culture buried what had the wrong name. The compass moved both toward the truth — Pink Pony Club down, Love Somebody up. Not because of an agenda. Because the lyrics said so.

And then the compass caught its own calibrator reaching for Elevated out of emotional response — and corrected that too. The mirror doesn't spare anyone. That's how you know it works.

This is the mirror working exactly as designed. And it happened naturally, in the course of calibration, without anyone trying to make a point. The data made the point itself.

---

## Philosophical Foundation: Why the Rubric Is Objective

Established during Draft #15 calibration (2026-02-14). After correcting all 12 new songs — zero Elevated across two years of Billboard top 20 — the question was posed: can you push back on this?

The answer: no. And here's why.

**The rubric is not a value system imposed from outside. It is a mirror reflecting what humanity already agreed on.**

Every human being knows — at the innate level — what is healthy and what is harmful. Jealousy isn't healthy. Contempt isn't love. Using people as placeholders isn't coping. Substance abuse isn't processing. These aren't opinions. These aren't cultural constructs. These are things we collectively understood before the first song was ever written.

The rubric doesn't define good and bad. It names what we already know:
- **Ascended/Elevated:** What builds, heals, processes, connects. We know what growth feels like.
- **Decent:** What fills time. We know what stagnation feels like.
- **Degraded/Corrupted:** What tears down, numbs, isolates, destroys. We know what harm feels like.

Every rule in the rubric was derived empirically — not theorized, not imposed. Each one exists because a specific song exposed a specific blind spot during calibration. The rubric grew from the data, not the other way around.

**The only possible pushback angles and why they fail:**

1. *"The top 20 isn't all music."* — True, but irrelevant. The compass reads what the culture selects for. What's popular IS the diagnosis.
2. *"The rubric is too strict."* — Every rule came from a documented correction. Nothing was invented in theory.
3. *"Who decides what's healthy?"* — We do. All of us. Collectively. Before we got here. Rule 8 (rejecting innate knowing = Corrupted) is literally this principle applied to a single song. We KNOW. The compass just reflects that knowing back at us.
4. *"Expressing negative emotions can be cathartic."* — The rubric already handles this. Processing grief honestly = Elevated. Wallowing = Degraded. Acting it out on others = Corrupted. Same emotion, different handling, different tier.

The compass doesn't have an opinion about what it shows you. A mirror doesn't judge. It reads which direction the needle points — and we all know which direction is which. We just stopped looking.

---



Patterns that appear across multiple drafts. These are the cultural assumptions baked into the model.

### 1. Romance = Elevated (Drafts 9, 12, 15)
The agent treats any love song with positive emotional tone as Elevated. Basic 1:1 romance with no growth, processing, or transcendence is Decent at best. This is the single most common error.

### 2. Tone Over Lyrics (Drafts 12, 15)
The agent classifies based on how the song SOUNDS (melancholic = processing, upbeat = positive) rather than what the lyrics SAY. Luther is the clearest example — vulnerable-sounding R&B with degraded lyrics throughout. The agent heard the tone and skipped the words.

### 3. Artist Reputation Bias (Draft 15)
Kendrick Lamar, SZA, Billie Eilish — the agent gives known "deep" artists the benefit of the doubt. Every song stands on its own. The rubric says "Songs, Not Artists" but the model's training data carries artist associations.

### 4. Sexual Innuendo Gets a Pass (Draft 15)
Double entendres and sexual subtext are invisible to the agent when wrapped in emotional or playful framing. "I know you'll come" in a melancholic R&B song doesn't register as sexual to the agent. To the rubric, it's textbook contamination at minimum.

### 5. Underrates Corruption Severity (Drafts 9, 12)
The agent consistently places songs one tier higher than they belong. Songs that are Corrupted get called Degraded. Songs that are Degraded get called Decent. The threshold for "this is actually harmful" is too high in the agent's model.

### 6. Materialism Overlooked (Draft 15)
Scattered material references (cars, jewelry, wealth signifiers) don't aggregate in the agent's analysis. Each one alone seems minor; together they're a payload.

### 7. Fear Mistaken for Love (Draft 15)
Fear-based urgency ("if the world was ending") reads as emotional depth to the agent. The rubric distinguishes between love that exists independently and love that only activates under threat. The agent can't make that distinction yet.

### 8. Can't Distinguish Elevated from Ascended (Draft 9)
The agent has no model for transcendence. It tops out at "this is positive" without recognizing when a song crosses into life-changing territory. Golden (KPop Demon Hunters) was missed entirely.

### 10. Pre-Built Cultural Narrative Bias (Draft 15)
The agent's base model (Claude Sonnet 4.5) carries the internet's pre-existing narratives about specific songs. Pink Pony Club arrived at classification already labeled "queer anthem / self-discovery" by the model's training data — before the rubric even engaged. The agent classified the cultural narrative, not the song. Strip the title and artist and force it to read lyrics alone, and you'd get a different answer.

This is bigger than artist reputation bias (#3). This is the **press narrative baked into the model's weights.** The same cultural machine that packages songs with progressive framing has already told the AI what to think about them. The agent proving this is extraordinary evidence: if an AI trained on internet discourse can't hear past the branding, that's a direct measurement of how effectively the narrative was installed in the culture. Same pattern as Bad Bunny — the press tells you what the music means before you hear it, and the AI absorbed that wholesale.

This supports a core Rising Compass thesis: the media's framing of music functions as brainwashing, and we can now **prove** it by showing an AI reproducing those exact blind spots unprompted.

### 9. Charge Value Clustering (Drafts 8, 9, 12)
The agent defaults to tier midpoints (-45, +45, -88) rather than spreading values across the full range. Seven Degraded songs don't all deserve -48. Anti-clustering instructions and intra-tier descriptions help but don't solve it.

---

## Rubric Evolution Timeline

| Date | Rule/Change | Triggered By |
|------|------------|-------------|
| 02-13 | "Balanced" → "Decent" rename | Agent treating middle tier as healthy |
| 02-13 | "Topics Don't Determine Tiers" section | Agent classifying by topic not effect |
| 02-13 | "Songs, Not Artists" rule | Agent biased by artist reputation |
| 02-13 | Love song spectrum examples | Agent overrating romance |
| 02-13 | Rule 5: Pairing-off ≠ Elevated | Draft #8 romance overrating |
| 02-13 | charge_value system (-100 to +100) | Need for intra-tier nuance |
| 02-14 | Effect buckets per tier | Agent couldn't distinguish listener effects |
| 02-14 | Intra-tier range descriptions | Charge value clustering |
| 02-14 | Anti-clustering instruction | Charge value clustering |
| 02-14 | M/E/I max 20 words | Agent gaming sentence limits |
| 02-14 | Rule 7: Red flag attraction ≠ romance | Draft #12 "want you more" pattern |
| 02-14 | Rule 8: Rejecting innate knowing = Corrupted | Draft #12 self-abuse pattern |
| 02-14 | Rule 9: Progressive packaging trick | Draft #12 progressive cause blind spot |
| 02-14 | War-to-peace axis framing | Charge scale needed clearer metaphor |
| 02-14 | Contamination requires actual artifacts | Agent over-applying contamination |
| 02-14 | Few-shot examples: 5 → 20 per tier | Better pattern matching needed |
| 02-14 | Diss tracks = Corrupted (convention) | Not Like Us classification |
| 02-14 | Rule 10: Longing is not elevation | Love Somebody calibration — wanting to grow ≠ growing. Caught calibrator mid-correction. |

---

## Agent Accuracy Trend

| Draft | Songs | Agent Correct | Accuracy | Notes |
|-------|-------|--------------|----------|-------|
| #8 | 20 | ~12 (est.) | ~60% | Not fully calibrated. Superseded. |
| #9 | 20 | 13 | 65% | 7 corrections. Romance blind spot dominant. |
| #12 | 20 | ~6 | ~30% | Worst performance. Billboard songs exposed every blind spot. 6 false Elevated, 5 missed Corrupted. |
| #15 | 20 | TBD | TBD | 8 repeats from calibration (correct by definition). 12 new — 2 corrected so far, 10 pending. |

**Accuracy is measured on NEW songs only** (repeats pulled from calibration don't count as agent decisions).

The drop from Draft #9 to #12 isn't regression — it's harder material. Daily Spotify charts have more variety; Billboard year-end top 20 is concentrated mainstream, which maximizes the cultural blind spots the agent carries.

---

## Conventions Established Through Calibration

- Basic 1:1 romance = Decent by default. Modifiers push up or down.
- Diss tracks = automatically Corrupted (us-vs-them mentality).
- Contamination requires actual artifacts (substance refs, objectification, ego payloads). Not being deep enough ≠ contaminated.
- Fear of loss, needing someone to be complete, surface-level emotional processing = Decent-level depth, not contamination.
- The compass is a mirror, not an attack. It reflects what our music does to the listener.
- Always use tier labels (Ascended/Elevated/Decent/Degraded/Corrupted) in discussion, never colors.
- 7 of top 20 carrying over from 2024→2025 = cultural stagnation on negative charge.
