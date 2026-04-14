# Lyrical Charger — API Specification

## Context

CRW (chadrising.com) needs a public "Lyrical Charger" that lets users submit their own music and get real-time Rising Compass calibrations. This extends the existing Rising Compass FastAPI backend with new public endpoints that reuse the calibration engine without polluting the compass/drift data pipeline.

**Guiding constraint:** No job queue. SSE streaming delivers per-song results in real time. Real-world timing (30s–2min for 10 songs) makes this viable without background workers.

---

## Endpoint Overview

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| `POST` | `/api/analyzer/sessions` | Public (rate-limited) | Submit songs, get session ID |
| `GET` | `/api/analyzer/sessions/{session_id}/stream` | Public | SSE stream — per-song results + aggregate |
| `GET` | `/api/analyzer/sessions/{session_id}` | Public | Reconnect / fetch completed results |
| `POST` | `/api/analyzer/resolve-playlist` | Public (rate-limited) | Resolve Spotify playlist URL → track list |

---

## 1. POST /api/analyzer/sessions

Creates an analysis session. Validates input, stores song list in memory, returns a session ID. Does NOT start processing — that begins when the client connects to the SSE stream.

### Request

```json
{
  "songs": [
    {"title": "Bohemian Rhapsody", "artist": "Queen"},
    {"title": "Blinding Lights", "artist": "The Weeknd"},
    {"title": "Hotline Bling", "artist": "Drake"}
  ]
}
```

**Constraints:**
- `songs`: required, 1–50 items
- Each song: `title` (string, required), `artist` (string, required)
- `weighted`: optional boolean, default `true`
  - `true`: position = list index + 1 (first song weighted highest, same as chart position in `compute_degree()`). Use for manually-ordered songs where the user placed their most important songs first.
  - `false`: all songs weighted equally (position = 1 for all). Use for Spotify imports where track order is arbitrary (shuffle order, alphabetical, etc.).
- The `resolve-playlist` response includes a note that the frontend should default `weighted: false` when submitting Spotify-resolved songs, and `weighted: true` for manual entry.

### Response — 201 Created

```json
{
  "session_id": "a1b2c3d4e5",
  "song_count": 3,
  "stream_url": "/api/analyzer/sessions/a1b2c3d4e5/stream",
  "expires_at": "2026-02-23T15:30:00Z"
}
```

### Error Responses

- `400` — Empty song list, exceeds 50 songs, missing title/artist
- `429` — Rate limit exceeded (10 sessions/hour per IP)

### Schema

```python
class AnalyzerSongIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    artist: str = Field(..., min_length=1, max_length=200)

class AnalyzerSessionCreate(BaseModel):
    songs: list[AnalyzerSongIn] = Field(..., min_length=1, max_length=50)
    weighted: bool = True         # False = equal weight (Spotify import), True = ordered (manual entry)

class AnalyzerSessionOut(BaseModel):
    session_id: str
    song_count: int
    stream_url: str
    expires_at: datetime
```

---

## 2. GET /api/analyzer/sessions/{session_id}/stream

SSE (Server-Sent Events) endpoint. When the client connects, processing begins. Each song's calibration is emitted as it completes. After all songs, the aggregate and narrative are emitted.

The frontend opens this with `new EventSource(stream_url)`.

### SSE Event Sequence

#### Event: `session_start`
Emitted immediately on connection.

```
event: session_start
data: {"total_songs": 3, "status": "processing"}
```

#### Event: `song_processing`
Emitted when a song begins processing (before lyrics fetch / calibration).

```
event: song_processing
data: {"index": 0, "title": "Bohemian Rhapsody", "artist": "Queen"}
```

#### Event: `song_result`
Emitted when a song's calibration is complete (or fails). Every song produces exactly one `song_result` — the `status` field distinguishes outcomes.

**Status values:**
- `"scored"` — fully calibrated with tier + charge
- `"no_lyrics"` — lyrics not found from any source, cannot calibrate
- `"error"` — lyrics found but calibration failed (Claude API error, JSON parse failure, etc.)

Successful calibration:
```
event: song_result
data: {
  "index": 0,
  "title": "Bohemian Rhapsody",
  "artist": "Queen",
  "status": "scored",
  "tier": "violet",
  "tier_label": "Ascended",
  "charge": 92,
  "contaminated": false,
  "contamination_note": null,
  "charge_summary": "Rock opera that processes mortality, identity, and surrender through escalating catharsis",
  "message": "Confronting death and seeking forgiveness through confession",
  "expression": "Operatic escalation from whisper to full theatrical release",
  "intention": "Forces the listener through grief into acceptance",
  "confidence": 1.0,
  "lyrics_found": true
}
```

No lyrics found:
```
event: song_result
data: {
  "index": 2,
  "title": "Some Obscure Track",
  "artist": "Unknown",
  "status": "no_lyrics",
  "tier": null,
  "tier_label": null,
  "charge": null,
  "contaminated": false,
  "contamination_note": null,
  "charge_summary": null,
  "message": null,
  "expression": null,
  "intention": null,
  "confidence": 0.0,
  "lyrics_found": false
}
```

Calibration error (lyrics found, scoring failed):
```
event: song_result
data: {
  "index": 3,
  "title": "Another Song",
  "artist": "Some Artist",
  "status": "error",
  "tier": null,
  "tier_label": null,
  "charge": null,
  "contaminated": false,
  "contamination_note": null,
  "charge_summary": null,
  "message": null,
  "expression": null,
  "intention": null,
  "confidence": 0.0,
  "lyrics_found": true
}
```

#### Event: `aggregate`
Emitted after all songs are calibrated. Contains the frequency profile.

```
event: aggregate
data: {
  "compass_degree": 48.2,
  "charge_score": 47,
  "charge_level": "blue",
  "charge_label": "Elevated",
  "tier_distribution": {
    "ascended": 1,
    "elevated": 1,
    "decent": 0,
    "degraded": 1,
    "corrupted": 0
  },
  "contamination_count": 0,
  "total_songs": 3,
  "calibrated_songs": 3,
  "uncalibrated_songs": 0
}
```

#### Event: `narrative`
Emitted after aggregate. The user-facing frequency reading.

```
event: narrative
data: {"text": "Your music skews toward honest processing — what you listen to confronts rather than escapes, with enough range to suggest someone who doesn't need comfort but isn't afraid of it."}
```

#### Event: `complete`
Terminal event. Stream closes after this.

```
event: complete
data: {"session_id": "a1b2c3d4e5", "status": "completed"}
```

#### Event: `error`
Emitted on fatal errors. Stream closes after this.

```
event: error
data: {"message": "Session expired or not found"}
```

### Error Responses

- `404` — Session not found or expired
- `409` — Session already streaming (prevents double-connect)
- `410` — Session already completed (use GET /sessions/{id} to retrieve results)

### Reconnection Flow (409/410 handling)

If a user refreshes mid-stream, the frontend hits 409 (stream active) or 410 (already done). The frontend should handle this:

1. On `409`: Fall back to `GET /sessions/{id}` to fetch already-completed songs, then display them immediately. The original SSE stream is still running and will complete — the session results accumulate in memory regardless of whether the client is listening.
2. On `410`: Call `GET /sessions/{id}` to retrieve the full completed results. Render directly, no streaming needed.
3. On `404`: Session expired. Show "session expired, start a new analysis" message.

The frontend should store `session_id` in `sessionStorage` so it can attempt reconnection on page refresh.

### Schema (per-song result)

```python
class AnalyzerSongResult(BaseModel):
    index: int
    title: str
    artist: str
    status: str               # "scored" | "no_lyrics" | "error"
    tier: str | None          # "violet", "blue", "green", "orange", "red", or None
    tier_label: str | None    # "Ascended", "Elevated", etc., or None
    charge: int | None        # -100 to +100, or None
    contaminated: bool
    contamination_note: str | None
    charge_summary: str | None
    message: str | None       # M/E/I — max 20 words each
    expression: str | None
    intention: str | None
    confidence: float         # 0.0–1.0
    lyrics_found: bool
    # NOTE: `cached` is tracked internally (server logs) but excluded from
    # the public response — users don't need to see the caching layer.

class AnalyzerAggregate(BaseModel):
    compass_degree: float     # 0–180
    charge_score: int         # -100 to +100
    charge_level: str         # color
    charge_label: str         # tier name
    tier_distribution: dict[str, int]  # {ascended: N, elevated: N, ...}
    contamination_count: int
    total_songs: int
    calibrated_songs: int
    uncalibrated_songs: int
```

---

## 3. GET /api/analyzer/sessions/{session_id}

Fetch session status and results. Used for:
- Reconnecting after SSE drop
- Fetching completed results without re-streaming
- Checking session status

### Response — Processing (200)

```json
{
  "session_id": "a1b2c3d4e5",
  "status": "processing",
  "total_songs": 3,
  "completed_songs": 1,
  "songs": [
    {"index": 0, "title": "Bohemian Rhapsody", "artist": "Queen", "tier": "violet", "charge": 92, "...": "..."}
  ],
  "aggregate": null,
  "narrative": null
}
```

### Response — Completed (200)

```json
{
  "session_id": "a1b2c3d4e5",
  "status": "completed",
  "total_songs": 3,
  "completed_songs": 3,
  "songs": [
    {"index": 0, "title": "Bohemian Rhapsody", "artist": "Queen", "tier": "violet", "charge": 92, "...": "..."},
    {"index": 1, "title": "Blinding Lights", "artist": "The Weeknd", "tier": "blue", "charge": 45, "...": "..."},
    {"index": 2, "title": "Hotline Bling", "artist": "Drake", "tier": "orange", "charge": -38, "...": "..."}
  ],
  "aggregate": {
    "compass_degree": 48.2,
    "charge_score": 47,
    "charge_level": "blue",
    "charge_label": "Elevated",
    "tier_distribution": {"ascended": 1, "elevated": 1, "decent": 0, "degraded": 1, "corrupted": 0},
    "contamination_count": 0,
    "total_songs": 3,
    "calibrated_songs": 3,
    "uncalibrated_songs": 0
  },
  "narrative": "Your music skews toward honest processing..."
}
```

### Error Responses

- `404` — Session not found or expired

---

## 4. POST /api/analyzer/resolve-playlist

Resolves a Spotify playlist URL to a track list. The frontend calls this first, shows the user the resolved tracks (so they can review/remove songs), then submits confirmed songs to `/sessions`.

This keeps playlist resolution separate from analysis — the user sees what they're submitting.

### Request

```json
{
  "spotify_url": "https://open.spotify.com/playlist/37i9dQZEVXbLRQDuF5jeBp"
}
```

**Accepted formats:**
- Full URL: `https://open.spotify.com/playlist/{id}`
- URI: `spotify:playlist:{id}`
- Bare ID: `37i9dQZEVXbLRQDuF5jeBp`

**Constraints:**
- Public playlists only (client credentials flow, no user OAuth)
- Returns first 50 tracks max

### Response — 200

```json
{
  "playlist_name": "Top 50 - USA",
  "playlist_owner": "Spotify",
  "track_count": 50,
  "tracks": [
    {"title": "Not Like Us", "artist": "Kendrick Lamar"},
    {"title": "APT.", "artist": "ROSE & Bruno Mars"}
  ]
}
```

### Error Responses

- `400` — Invalid URL/ID format
- `404` — Playlist not found or private
- `429` — Rate limit exceeded (20 resolves/hour per IP)
- `502` — Spotify API error

### Schema

```python
class PlaylistResolveIn(BaseModel):
    spotify_url: str = Field(..., min_length=1)

class PlaylistTrackOut(BaseModel):
    title: str
    artist: str

class PlaylistResolveOut(BaseModel):
    playlist_name: str
    playlist_owner: str
    track_count: int
    tracks: list[PlaylistTrackOut]
```

---

## Narrative Prompt (Analyzer-Specific)

The existing `EDITORIAL_VOICE` is tuned for daily chart readings ("where culture is heading"). The Analyzer needs a different voice tuned for personal music.

### ANALYZER_NARRATIVE_VOICE

```
You are the diagnostic voice of The Rising Compass Lyrical Charger — a tool that reads the energetic charge of someone's personal music.

## How the Analyzer Sounds
- Direct. Tells the person what their music says about them. No hedging.
- Second person. "You" and "your" — this is about THEIR frequency, not culture.
- Perceptive. Names what the music reveals, including things the person might not see.
- Balanced. Acknowledges the full picture — high and low. Doesn't flatter, doesn't scold.
- Grounded. Speaks plainly. No mysticism, no therapy-speak.

## Hard Constraints
- 2-3 sentences max.
- Present tense.
- Never use: "Normalizes", "Activates", "Models", "Wrapped in", "Journey", "Vibe", "Energy", "Playlist"
- Never use passive voice
- Never list tier names or colors
- Never name specific songs
- Never reference The Rising Compass, Chad Rising, tier names, or color names. The reader has no context for these. Speak only about what their music reveals.
- Profanity: censor f**k, s**t, c**t, b***h. Ass/damn/hell uncensored.
- Em-dashes: use 1 out of every 10 times you want to.

## Writing Rules
1. Read the aggregate, not individual songs. What does the COLLECTION say?
2. Name specific patterns — "your music avoids resolution" not "your music is interesting"
3. If the mix is contradictory, name the contradiction.
4. Don't moralize. State what IS.
5. Don't project intent the music doesn't show.
6. Plain language hits harder.

Respond with ONLY the narrative. Nothing else.
```

### Narrative User Prompt Builder

```python
def build_narrative_prompt(song_results: list[dict], aggregate: dict) -> tuple[str, str]:
    system_prompt = ANALYZER_NARRATIVE_VOICE

    lines = [
        f"Overall charge: {aggregate['charge_label']} ({aggregate['charge_score']:+d})",
        f"Distribution: {aggregate['tier_distribution']}",
        f"Contamination: {aggregate['contamination_count']}/{aggregate['total_songs']}",
        "",
        "Songs analyzed:",
        "",
    ]
    for s in song_results:
        if s.get("tier"):
            line = f'"{s["title"]}" by {s["artist"]} — {s["tier"]}'
            if s.get("contaminated"):
                line += " (contaminated)"
            if s.get("charge_summary"):
                line += f" — {s['charge_summary']}"
            lines.append(line)

    return system_prompt, "\n".join(lines)
```

---

## Processing Pipeline

### Step-by-step flow inside the SSE stream handler:

```
1. Emit session_start

2. PARALLEL LYRICS FETCH
   - For each song, check calibrated cache first
   - Songs with cache hits: skip lyrics entirely
   - Songs without cache: fetch lyrics in parallel via asyncio
   - Use asyncio.to_thread(fetch_lyrics, title, artist) to wrap sync calls
   - All lyrics fetching happens concurrently (not one-by-one)

3. ASSIGN POSITIONS
   - If weighted=True: position = index + 1 (first song = highest weight)
   - If weighted=False: position = 1 for all songs (equal weight)

4. SEQUENTIAL CLASSIFICATION + STREAMING
   For each song (in order):
     a. Emit song_processing event
     b. If cache hit → use cached result, emit song_result (status="scored")
     c. If cache miss + lyrics found → try calibrate_song(title, artist, lyrics, db)
        - Success → emit song_result (status="scored")
        - Exception (Claude API failure, JSON parse error) → emit song_result (status="error")
     d. If cache miss + no lyrics → emit song_result (status="no_lyrics")
     e. Songs with status="error" or "no_lyrics" are excluded from aggregate calculation

5. COMPUTE AGGREGATE
   - compute_degree(calibrated_songs)
   - degree_to_charge(degree)
   - count_contaminated(calibrated_songs)
   - Build tier_distribution from results
   - Emit aggregate event

6. GENERATE NARRATIVE
   - build_narrative_prompt(song_results, aggregate)
   - Call Claude (max_tokens=256)
   - Emit narrative event

7. Emit complete event, close stream
```

### Why parallel lyrics + sequential calibration:
- Lyrics fetching is I/O-bound (HTTP calls). Parallelizing eliminates the dominant bottleneck.
- Calibration is CPU/API-bound (Claude calls). Streaming results one-by-one gives the frontend smooth per-song updates.
- For 20 songs: parallel lyrics fetch takes ~2-3s total (lyrics.ovh responds in 1-2s). Then calibration streams at ~3-5s per uncached song. **Total: ~15-60s for 20 songs** depending on cache hit ratio.

---

## Data Isolation

| Action | Allowed? |
|--------|----------|
| READ from CompassSong cache (calibrated) | Yes |
| READ few-shot examples from CompassSong | Yes |
| WRITE to CompassSong | **No** — user songs don't enter the historical archive |
| WRITE to AgentDraft / AgentDraftSong | **No** — no draft workflow for user analyses |
| WRITE to DailyReading / ReadingSong | **No** — not chart data |

User analysis results exist only in memory (session dict). TTL 30 minutes, then garbage collected.

---

## Session Storage (In-Memory)

```python
# Simple dict, no Redis/DB needed at this scale
_sessions: dict[str, AnalyzerSession] = {}

class AnalyzerSession:
    session_id: str
    songs_input: list[dict]           # submitted songs
    status: str                        # "pending" | "processing" | "completed" | "error"
    results: list[dict]               # per-song results as they complete
    aggregate: dict | None
    narrative: str | None
    created_at: datetime
    expires_at: datetime               # created_at + 30 min
    streaming: bool                    # True while SSE active (prevents double-connect)
```

Cleanup: a background task runs every 5 minutes, evicts expired sessions.

**v1.1 consideration — sharing:** 30-minute TTL means shared links die fast. When we add sharing, generate a static share artifact (image + metadata) at completion time that lives independently of the session. The share URL resolves to a static card, not a live session. This decouples sharing from session lifetime entirely.

---

## Rate Limiting

**Dependency:** `slowapi`

```
POST /api/analyzer/sessions        — 10/hour per IP
POST /api/analyzer/resolve-playlist — 20/hour per IP
GET  /api/analyzer/sessions/*/stream — no separate limit (tied to session creation)
GET  /api/analyzer/sessions/*       — 60/hour per IP
```

Implementation: `slowapi` Limiter instance with `get_remote_address` key function, applied as route decorators.

---

## How This Extends the Existing App

### New Files

| File | Purpose |
|------|---------|
| `app/routers/analyzer.py` | All 4 endpoints, SSE streaming logic, session management, rate limiter |
| `app/services/analyzer_engine.py` | Orchestrator — parallel lyrics fetch, sequential calibration, aggregate computation |

### Modified Files

| File | Change |
|------|--------|
| `app/main.py` | Add analyzer router + session cleanup background task in lifespan + slowapi handler |
| `app/config.py` | Add `spotify_client_id`, `spotify_client_secret`, `analyzer_max_songs`, `analyzer_session_ttl` |
| `app/schemas.py` | Add analyzer request/response models |
| `app/constants.py` | Add `TIER_LABELS_REVERSE` |
| `app/services/agents/compass_agent_rubric.py` | Add `ANALYZER_NARRATIVE_VOICE` + `build_narrative_prompt()` |
| `requirements.txt` | Add `slowapi`, `sse-starlette` |

### Reused Without Changes

| File | What's Reused |
|------|--------------|
| `app/services/agents/calibrator.py` | `calibrate_song()` — called per-song, same as today |
| `app/services/agents/lyrics_source.py` | `fetch_lyrics()` — wrapped in `asyncio.to_thread()` for parallel I/O |
| `app/services/compass_calc.py` | `compute_degree()` — works with any number of songs |
| `app/services/charge_calc.py` | `degree_to_charge()` |
| `app/services/contamination.py` | `count_contaminated()` |
| `app/services/agents/compass_agent_rubric.py` | `build_few_shot_examples()` — provides calibration context |
| `app/services/agents/compass_agent.py` | `_lookup_cached()` — calibrated song cache |

### What's NOT Reused

`run_compass_agent()` is NOT called. It bundles too much (draft creation, email, CompassSong writes). The Analyzer engine calls the same underlying functions directly but skips the draft/email/storage layer.

---

## Spotify Integration

### Auth: Client Credentials Flow (no user login)

Requires `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` in `.env`. Works for public playlists only.

```
POST https://accounts.spotify.com/api/token
  grant_type=client_credentials
  → returns access_token (1 hour TTL)

GET https://api.spotify.com/v1/playlists/{id}/tracks?limit=50&fields=items(track(name,artists(name)))
  Authorization: Bearer {token}
  → returns track list
```

Token is cached in memory, refreshed when expired. No Playwright needed.

---

## CORS

Add CRW's domain to `cors_origins` in `.env`:

```
CORS_ORIGINS=["http://localhost:3000","https://risingcompass.net","https://api.risingcompass.net","https://chadrising.com"]
```

---

## Implementation Order

1. Schemas + session storage + `POST /sessions` endpoint
2. `POST /resolve-playlist` (Spotify client credentials)
3. `analyzer_engine.py` — parallel lyrics + sequential calibration
4. `GET /sessions/{id}/stream` — SSE streaming with engine
5. `GET /sessions/{id}` — status/reconnect endpoint
6. Narrative prompt + generation
7. Rate limiting (slowapi)
8. Session cleanup background task
