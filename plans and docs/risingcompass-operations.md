# Rising Compass — Operations Manual

## Nomenclature

Three distinct data workflows. They are NOT the same thing.

- **Reading** — daily live chart classification. Spotify Top 20 → classify → calibrate → publish as "Today's Charge." This drives the compass needle on the live site. One per day.
- **Calibration** — training batches for the agent. Billboard year-end, test sets, or any group of songs classified for the purpose of correcting the agent and building training data. Feeds the Song table (few-shot examples) and the aggregate trajectory. NOT a daily reading.
- **Backfill** — historical reclassification. 1960 onward, 10 songs/year. Agent classifies, human calibrates. Feeds the Song table and aggregate trajectory.

Songs from Calibration and Backfill contribute to the aggregate (trajectory chart, decade data) but NEVER appear as a Reading.

---

## Reading (Daily)

A Reading classifies today's top 20 songs from Spotify US Top 50 and produces the compass charge for the site.

### Steps

1. **Start the backend** (if not already running):
   ```
   cd "C:\Users\chad\Local Sites\rising-compass\backend" && .venv\Scripts\uvicorn app.main:app --port 8000
   ```

2. **Trigger the live classification:**
   ```
   curl -s -X POST "http://localhost:8000/api/admin/agent/classify-live" \
     -H "X-Admin-Key: change-me" -H "Content-Type: application/json"
   ```
   This fetches today's Spotify US Top 50 (top 20), classifies each song via the agent, and creates an **AgentDraft** (status: pending).

3. **Identify which songs need review.** Most songs in a Reading will already be calibrated from previous readings or calibration batches — the agent pulls these from the Song table cache at confidence 1.0. Only uncalibrated songs need human review. Check the Song table:
   ```python
   SELECT title, artist, calibrated FROM songs WHERE title LIKE '%Song Name%'
   ```
   Skip any song that's already calibrated. Focus only on new entries.

4. **Review uncalibrated songs.** For each new song, check:
   - Correct tier assignment
   - Accurate charge value
   - Contamination flags (only applies to Decent, Elevated, Ascended)
   - M/E/I summaries (~20 words max each)

5. **Review the editorial summary.** Even if all songs are calibrated, the editorial is generated fresh each time.

6. **Edit if needed** — update songs via the draft edit endpoint before approving.

7. **Approve the draft** to publish it as the day's Reading:
   ```
   curl -s -X POST "http://localhost:8000/api/admin/agent/drafts/{DRAFT_ID}/approve" \
     -H "X-Admin-Key: change-me"
   ```

### Notes
- Only one reading per date. If one already exists, delete it first or reject the draft.
- The agent emails a notification when a draft is created (if SMTP is configured).
- Draft can also be rejected if the classification is too far off to salvage.

---

## Backfill (Historical Calibration)

Separate process from daily readings. Classifies uncalibrated songs from past Billboard Year-End Top 10 charts, one year at a time. Feeds the Song table (few-shot training data) and the aggregate trajectory. Never appears as a daily reading.

**Progress:** 1960s complete (1960–1969). Resume at 1970.
**Goal:** 5 consecutive years without correction = agent runs unsupervised through 2023.

### Steps

1. **Verify the chart.** Look up `Billboard_Year-End_Hot_100_singles_of_{year}` on Wikipedia. Confirm the correct top 10 before proceeding — the DB may have wrong entries or missing songs.

2. **Run the backfill endpoint:**
   ```
   curl -s -X POST "http://localhost:8000/api/admin/agent/backfill/{YEAR}?limit=10" \
     -H "X-Admin-Key: change-me"
   ```
   Reclassifies all uncalibrated songs for that year. Uses `skip_cache=True` for fresh classification but still loads calibrated songs as few-shot examples. Does NOT auto-calibrate.

3. **Delete any bad entries** (instrumentals without lyrics, wrong songs, duplicates):
   ```
   curl -s -X DELETE "http://localhost:8000/api/admin/agent/songs/{SONG_ID}" \
     -H "X-Admin-Key: change-me"
   ```

4. **Add missing songs** if any top 10 entries aren't in the DB:
   ```
   curl -s -X POST "http://localhost:8000/api/admin/agent/songs" \
     -H "X-Admin-Key: change-me" -H "Content-Type: application/json" \
     -d '{"title":"...","artist":"...","year":YEAR,"rubric_color":"...","charge_value":0,"chart_source":"billboard_hot_100"}'
   ```

5. **Human assigns tiers.** Review each song. Assign: Ascended / Elevated / Decent / Degraded / Corrupted. Use tier + qualifier (high/mid/low) for charge mapping:

   | Tier | High | Mid | Low |
   |------|------|-----|-----|
   | Ascended | +100 | +88 | +75 |
   | Elevated | +74 | +50 | +25 |
   | Decent | +24 | 0 | -24 |
   | Degraded | -25 | -50 | -74 |
   | Corrupted | -75 | -88 | -100 |

   High/low = up/down on the number line (high = toward ceiling, low = toward floor). "Very high" = near tier ceiling.

6. **Fix summaries and M/E/I.** Agent summaries often reflect the wrong tier's framing. Rewrite charge_summary, message, expression, intention to match corrected tier. M/E/I max ~20 words each.

7. **Push calibration:**
   ```
   curl -s -X POST "http://localhost:8000/api/admin/agent/calibrate" \
     -H "X-Admin-Key: change-me" -H "Content-Type: application/json" \
     -d '{"songs":[{"id":ID,"rubric_color":"COLOR","charge_value":N,"charge_summary":"...","message_analysis":"...","expression_analysis":"...","intention_analysis":"..."}]}'
   ```
   Sets `calibrated=True` on each song.

8. **Log results** in `risingcompass-calibration-log.md` — agent accuracy, corrections, new blind spots.

### Rules
- Contamination only applies to top 3 tiers (Decent, Elevated, Ascended). Degraded/Corrupted are already negative.
- Substance REFERENCE is not contamination; substance PROMOTION is.
- Instrumentals without lyrics: DELETE from DB.
- Era-weighted few-shot: `build_few_shot_examples(db, target_year)` prioritizes same-decade calibrated songs.
- Rule 11 (training wheels): face-value lyrics both directions. Tagged for removal after 5 consecutive clean years.

---

## Startup Checklist

| Service | Command | URL |
|---------|---------|-----|
| Backend | `.venv\Scripts\uvicorn app.main:app --port 8000` (from `backend/`) | http://localhost:8000 |
| Frontend | `python -m http.server 3000` (from `frontend/`) | http://localhost:3000 |
| API docs | — | http://localhost:8000/docs |

---

## Key Endpoints

| Action | Method | Endpoint |
|--------|--------|----------|
| Trigger daily reading | POST | `/api/admin/agent/classify-live` |
| List drafts | GET | `/api/admin/agent/drafts` |
| View draft | GET | `/api/admin/agent/drafts/{id}` |
| Edit draft | PUT | `/api/admin/agent/drafts/{id}` |
| Approve draft | POST | `/api/admin/agent/drafts/{id}/approve` |
| Reject draft | POST | `/api/admin/agent/drafts/{id}/reject` |
| Backfill year | POST | `/api/admin/agent/backfill/{year}` |
| Calibrate songs | POST | `/api/admin/agent/calibrate` |
| Delete song | DELETE | `/api/admin/agent/songs/{id}` |
| Manual backup | POST | `/api/admin/backup` |
