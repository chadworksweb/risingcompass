# Rising Compass

## Structure

- `backend/` — Python FastAPI app. One container, two front doors (single-origin
  migration 2026-05-25): `risingcompass.net` (root) serves the public site,
  first-party `/api/*`, and the Site Admin section; `api.risingcompass.net`
  serves the machine API for external clients (key auth) and the API Admin
  section (API Monitor only). Same backend, host-gated per section.
- `frontend/` — static HTML/JS (risingcompass.net). Calls the API with a
  RELATIVE base (`''`), i.e. same-origin `/api/*`. nginx on root proxies
  `/api/` and `/rc-admin-` to the backend.

## Deploy

**Server:** le-projects-01 (138.197.111.66), SSH as `deploy`

```bash
cd "C:/Users/chad/Local Sites/rising-compass"
bash deploy.sh
```

Script auto-detects what changed (backend vs frontend):
- **Backend changes:** `git pull` on server → `docker compose up -d --build --no-deps backend` → restart nginx
- **Frontend changes:** `git pull` on server (volume-mounted, no restart needed)
- Runs smoke test against `https://api.risingcompass.net/api/compass/current`

**Requires:** code must be pushed to GitHub first (`git push origin master`), then `deploy.sh` does `git pull` on the server.

## Local Dev

- Backend: `cd backend && .venv\Scripts\uvicorn app.main:app --port 8000`
- Frontend: `cd frontend && python scripts/dev_server.py --port 3005`
  (NOT `python -m http.server` — the frontend uses a relative API base, so it
  needs dev_server.py's reverse proxy that forwards `/api/*` and `/rc-admin-*`
  to the backend on :8000, mirroring prod's single origin. Plain http.server
  404s every API call.)
- Backend does NOT auto-reload — kill and restart uvicorn after code changes

## Parallel Work Tracks

When work should run in parallel lanes (multiple agents/terminals at once), use
the track system. Do NOT hand-roll worktrees or branches for parallel work; use
this so lanes get isolated env, venv, and ports instead of colliding.

- Manager: `tracks/rc-track.ps1`. Full guide: `plans and docs/RC-TRACKS-WORKFLOW.md`.
- Each track = its own worktree on branch `track/<name>` under
  `Local Sites/rc-tracks/<name>`, with copied `backend/.env` + `.deploy.env`, a
  junctioned `.venv`, and its own ports (slot 1 -> 8010/3015, slot 2 -> 8020/3025...).
- Create: `pwsh tracks/rc-track.ps1 new <name>`. Then run a session with cwd set
  to the printed worktree path. Run stack: `... start <name>`. Tear down:
  `... remove <name> [-DeleteBranch]`. Integrate: rebase (`... sync <name>`) then
  merge `track/<name>` into master.
- SHARED DATABASE: the DB is remote and shared by every track and main. Code and
  frontend lanes parallelize freely; run schema/migration work on ONE track at a
  time, or repoint that track's `DATABASE_URL` at a throwaway DB.

If you are running inside a track worktree (cwd under `rc-tracks/`), commit only
to this track's branch; never reach into sibling worktrees.

## Database

**Song entity (renovation COMPLETE 2026-06-05, schema_version 88).** `songs` is
the ONE atomic song table = the entire Library (superset of all calibrated
songs). "Charting" is a derived role via `chart_appearances`; ingestion is logged
in `song_ingestions`. The old four-table model (`compass_songs` / `library_songs`
/ `submitted_songs` / `cl_stream_songs`) and the polymorphic `(song_source,
song_id)` pointer are GONE — every reference table now carries a single `song_id`
FK to `songs(id)`. Full record: `plans and docs/RISING-COMPASS-SONG-ENTITY-
RENOVATION.md`. **Migrations run only on app startup (lifespan), never on bare
import** — do NOT validate by importing `app.main` against a prod-pointed
`DATABASE_URL`.

DigitalOcean Managed Postgres (NYC3), reached through DO's PgBouncer pool
(transaction mode). Migrated off Turso/libSQL 2026-05-24; the full pre-migration
implementation is tagged `pre-postgres-turso` in git, and the migration plan +
porting/loading scripts (`backend/scripts/pg_baseline.py`, `pg_load.py`) document
the move. Set `DATABASE_URL` in both local `backend/.env` and prod
`/root/rising-compass/.env`.

- `DATABASE_URL` form: `postgresql+psycopg://USER:PASS@HOST:25061/rc-pool?sslmode=require`
  (port 25061 = the PgBouncer pool).
- **Local dev** can't reach the DB directly (trusted-sources firewall is the
  droplet only), so it tunnels through the droplet:
  `ssh -L 25061:<db-host>:25061 deploy@138.197.111.66` and points `DATABASE_URL`
  at `127.0.0.1:25061`.
- `BACKUP_DATABASE_URL` — separate **direct** DSN (port 25060 / db `defaultdb`)
  used only by `pg_dump` in `app/services/backup.py`. PgBouncer transaction
  pooling breaks `pg_dump`, so it must NOT use the pool.
- Schema: `Base.metadata.create_all()` handles fresh installs; numbered
  `migrations/NNN_*.py` are SQLite-dialect history and are NOT replayed on PG
  (a fresh baseline was stamped to v062). New migrations (063+) must be
  PG-compatible.
- Backups: DO managed daily backups + 7-day PITR, PLUS the custom 30-day S3
  copy (`pg_dump` -> DO Spaces) via the cron at `POST /api/admin/backup`.

## Multi-statement write transactions

Plain ORM everywhere — Postgres MVCC means writes don't wedge reads and a
transaction can span a long Opus call without a connection dying, so the old
direct-libsql/background-thread scaffolding is gone. Two patterns remain:
- High-frequency telemetry (`api_call_log`, `lc_events`, `last_used_at`,
  `claude_meter`) writes via short-lived `SessionLocal()` sessions; the
  `api_call_log` write is offloaded with `run_in_threadpool` so the middleware
  doesn't block the event loop.
- The Lyrical Charger calibrate endpoints keep a read -> AI -> write phase
  split (close the read session before the Opus call, open a fresh write
  session after) to avoid holding a pooled connection idle, not for any Hrana
  reason. See `app/routers/analyzer.py`.

## Admin auth

Multi-user admin login (added 2026-04-25). The single shared `RC_ADMIN_KEY`
header is gone — every admin endpoint authenticates via the
`rc_admin_session` HttpOnly cookie minted by the obscured login URL.

**Login URL:** `https://risingcompass.net/rc-admin-{ADMIN_LOGIN_URL_TOKEN}`
(GET serves the form, POST authenticates; any other token returns 404).
Moved to the ROOT host in the single-origin migration (2026-05-25); the api
host now 404s `/rc-admin-*` at nginx. Successful login redirects to
`/api/admin/dashboard`. Unauthed `/api/admin/*` GETs return 404; mutating
endpoints return 401. Bookmark the login URL — it isn't linked from anywhere.

**Two-host admin split (2026-05-25):** the session cookie is set with
`Domain=risingcompass.net` (env `ADMIN_COOKIE_DOMAIN`, defaulted in
docker-compose), so one login covers both hosts (root + api. are same-site
subdomains). Site Admin pages (everything but API Monitor) live on root and
404 on the api host; API Monitor lives on the api host and 404s on root
(host-gating in `routers/admin.py:_gate_admin_section`). `/api/admin/dashboard`
lands on `db` on root, `api-monitor` on api. Localhost is ungated (both
sections reachable for local testing). `SITE_URL=https://risingcompass.net`
so admin links in emails (ether-audits, lobby-mod, draft approval) resolve to
the Site Admin host.

**Required env (both local and prod):**
- `ADMIN_LOGIN_URL_TOKEN=<6+ char string>` — the prefix in the login URL
- `RC_BACKUP_KEY=<service token>` — used by the cron at `POST /api/admin/backup`
  with the `X-Backup-Key` header. Falls back to `RC_ADMIN_KEY` during the
  transition; remove the fallback once cron is migrated.
- `RC_READING_CRON_KEY=<service token>` — used by the daily reading cron at
  08:00 UTC against `POST /api/admin/agent/cron/calibrate-live` with the
  `X-Reading-Cron-Key` header. Distinct from `RC_BACKUP_KEY` so cron lanes
  can be rotated independently.
- `RC_LYRICS_SUPPLY_KEY=<service token>` — accepted by
  `POST /api/admin/agent/drafts/{ref}/songs/{id}/lyrics` (lyrics endpoint) and
  `POST /api/admin/agent/drafts/{ref}/songs/{id}/correct` (override endpoint)
  via the `X-Lyrics-Supply-Key` header, in addition to the browser session cookie.
  Terminal scripts that use this:
  - `backend/scripts/calibrate_song.py` — fresh calibration, sends lyrics +
    Claude-Code-supplied calibration object. Server skips every Anthropic
    call. Use this for any song with no prior `songs` row.
  - `backend/scripts/correct_song.py` — override of an already-calibrated
    song. Writes through to the unified `songs` row via the draft's
    `agent_draft_songs.song_id`; do not use for fresh songs.
  - `backend/scripts/supply_lyrics.py` — legacy server-side Anthropic
    calibrator path; do not run from terminal (API boundary lockdown).
  If env-listed in `docker-compose.yml`, must also be added there for the
  container to see it.
- `RC_ADMIN_KEY` is now used **only** to sign one-time HMAC tokens in
  approval emails. Keep it set and stable across deploys.

**Session policy:** 8h sliding window, 24h absolute cap, 5 failed
attempts / 15min per IP+username = 429, 10 consecutive failures = 1h
lockout. Argon2id password hashes.

**Seeding admins:**
```
cd backend
.venv\Scripts\python.exe scripts\seed_admin.py
```
Prompts for username + password (12-char minimum). Re-running with the
same username resets the password and clears the lockout. Pass
`--revoke-sessions` to kill any active sessions for the user.

## Public Participation (Phases 1-3.2 built)

Three audience-facing surfaces live alongside the dashboard:

**Lobby** (Phase 1) -- Reddit-style threaded comments on song / artist
pages. Email-verified Tier 1 account required to post; anonymous read
allowed. Auto-hide after 3 reports. Tables: `users`, `comments`,
`comment_reports`, `moderation_events`, `admin_alert_prefs`.

**Misread Reports** (Phase 2) -- per-song "the agent got this wrong"
flag. Tier 1 gated. Admin can spawn a recalibration directly from the
queue. Migration 057 added `user_id` FK on `misread_submissions`.

**Motion Desk** (Phase 3.2) -- formal proposals about the framework.
Three routed pages: `/motion-desk/` (landing), `/motion-desk/file-a-motion/`
(Tier 2 gated form), `/motion-desk/motion-ledger/` (public list).
Motions deliberate tenets / rules / modifiers / methodology -- never
songs. Tables: `motions` (migration 060 + taxonomy correction in 061),
`account_verifications` (migration 059).

Motion types: `amend_tenet | new_tenet | remove_tenet | amend_rule |
new_rule | remove_rule | process`. Target via polymorphic
`target_kind` + `target_ref`. The `recalibration_challenge` type from
the original plan was dropped before any real motions were filed --
that conflated motions with misread reports.

**Deliberation Chamber** (Phase 4, in progress) -- sub-room of Motion
Desk that hosts the structured argument thread for any motion in
`in_deliberation`. Route: `/motion-desk/deliberation-chamber/{id}/`.
Posts are typed (`argument_for | argument_against | rebuttal | citation
| clarification`), Tier 2 gated to write, public to read. 2-level depth:
top-level posts + flat rebuttals. Table: `motion_arguments`
(migration 062). Spec lives in
`Dropbox/Libra Engine/Rising Compass/plans and docs/RISING-COMPASS-PUBLIC-PARTICIPATION-BUILD-PLAN.md`
under "Phase 4 -- Deliberation Chamber".

### Auth (Clerk-backed Tier 1, Stripe Identity Tier 2)

**Tier 1:** Clerk email account + claimed handle. Provisioned lazily
on first authenticated API call via `require_clerk_user` in
`backend/app/auth.py`. Onboarding flow in `frontend/account/`.

**Tier 2:** Real ID via Stripe Identity. Webhook at
`/api/stripe-identity-webhook` flips `users.tier` to `id_verified`.
Required for filing motions; Tier 1 is enough for Lobby + Misread.

**Frontend auth singleton:** `frontend/js/auth.js` -- wraps Clerk JS,
exposes `authedFetch`, `getMe`, `openSignIn`, `signOut`, `onChange`.
Owns the header-link state sync via `_syncAuthState()`. Tracks
sign-in / sign-out transitions; passes `justSignedIn` to onChange
listeners.

**returnTo flow:** Every page's Sign-in link writes
`/account/?returnTo=<current path>` when signed-out. `account.js`
stashes the param in sessionStorage so it survives the Stripe
roundtrip, and navigates back manually on the `justSignedIn`
transition (Clerk's `signInForceRedirectUrl` proved unreliable across
dashboard configs).

**Local env vars required:**
- `CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`, `CLERK_JWKS_URL`
- `STRIPE_SECRET_KEY` (sandbox), `STRIPE_IDENTITY_WEBHOOK_SECRET`
- `ADMIN_ALERT_EMAIL`

**Local dev:** Frontend served via `python scripts/dev_server.py
--port 3005` (handles nginx-style `/songs/<slug>` rewrites AND reverse-proxies
`/api/*` + `/rc-admin-*` to :8000, so the relative API base + admin login work
locally like prod). Backend on :8000. Stripe CLI listener: `stripe listen
--forward-to http://localhost:8000/api/stripe-identity-webhook` (do NOT pass
`--events "identity.verification_session.*"` — that filter is invalid and
forwards nothing; omit it or use a valid comma-separated event list).

### Deliberation venue aesthetic

Motion Desk + amendments share a cream/brown palette (Cardo + Cormorant
SC). Tenets stays dark on purpose -- "the constitution and the room
where the constitution is argued over should not look the same."
Tokens documented in `STYLE-GUIDE.md` "Deliberation Venue Palette".

## Calibration Runs admin (Site Admin -> Calibration -> Runs, 2026-06-06)

Read-only window onto `calibration_runs` (the run ledger behind each song's
consensus calibration). `routers/runs_admin.py`, template `admin/runs.html`,
section `runs`. Two views: **All Runs** (flat reverse-chron list, filter by
song/trigger/window/superseded) and **By Song** (songs ranked by run count,
most-calibrated first). `run_at` is surfaced as "Created". Endpoints (cookie
auth): `GET /api/admin/runs` (flat, paginated, returns distinct `triggers`) and
`GET /api/admin/runs/by-song`. Song titles link to the public song page; the
public per-song timeline is `GET /api/songs/{slug}/calibration-runs`.

**Stored agent argument (`calibration_runs.reasoning`, 2026-06-08).** Each run
now stores the agent's structured argument. The SINGLE write lock is
`calibration_corpus.log_run -> _guard_reasoning`: it persists `reasoning` only
after scrubbing any >=6-word verbatim lyric run (`lyric_quote_guard`) and
**fails closed** (stores nothing) when no `lyrics` are passed to check against;
lyrics are used for the check only, never persisted. `reasoning` rides in the
`calibration` dict -- the server calibrator emits it, `record_and_reconcile`/
`log_run` take `lyrics=`, and terminal supplies it via `calibrate_song.py
--reasoning`/`--reasoning-file` (`TerminalCalibrationIn.reasoning`). Wired:
daily compass, Lyrical Charger, terminal. NOT yet wired (store NULL):
album-charger, stream, admin recal/correct. Surfaced in: `/api/admin/runs`
(expandable **Argument** column in All Runs) and the DB explorer
(`calibration_runs`).

**Per-song admin detail page (2026-06-08).** `/api/admin/dashboard/song/{id}`
(route in `admin.py`, template `admin/song_detail.html`, gated under `runs`),
data from `GET /api/admin/songs/{id}/detail` (`runs_admin.song_detail`):
canonical calibration + enrichment, chart appearances, ingestion history, and
the full run timeline with each run's stored argument. Reachable from Runs ->
By Song -> "Open page". This is the only per-song admin page (`library_admin.py`
is API-only CRUD).

**Rubric rule R14 (2026-06-08, `core.json`):** load-bearing dogma RAISES the
Ascended bar -- doctrinal submission is not transcendence; run the
`dogma_referenced` flag and the tier together. The reverence-halo mirror of
R6/R12.

**Public run cap (`PUBLIC_RUN_CAP = 10`, in `calibration_corpus.py`).** Once a
song has 10 **live** (non-superseded) `calibration_runs`, the public Lyrical
Charger refuses new runs: its reading is considered settled. Counted via
`live_run_count()` (excludes superseded), so a `rubric_update` or admin
recalibration -- which supersedes the prior runs -- resets the budget and
**reopens** the song to the public ("the measuring stick moved, read it fresh").
- Single song: the two public calibrate endpoints (`/api/analyzer/calibrate-lyrics`,
  `/calibrate-search`) resolve the canonical song BEFORE the Opus call and
  short-circuit with `status="run_capped"` (+ `run_count`/`run_cap`/`song_slug`/
  `block_reason`); `charger.js` renders `showCappedCard`.
- Album Charger: a maxed track is **skipped** in Phase A (`status="skipped"`,
  run-limit reason) -- the album won't re-run it; checked regardless of cache
  state because even a cache hit logs a run via `record_and_reconcile`.
Only `tier=='public'` is gated -- service/terminal callers (RC_SERVICE_KEY,
`calibrate_song.py`/`correct_song.py`) bypass, so admin/terminal can still run a
maxed song. The admin Runs "By Song" view badges maxed songs (`capped` =
`live_count >= cap`). Capped attempts log an `lc_events` `submission_run_capped`.

## Artist admin endpoints

Authed via the admin session cookie (above). Merge/rename run as one atomic
transaction over a single SQLAlchemy Core connection (`engine.connect()` +
`text()`), keeping the intricate dedupe SQL verbatim.

- `POST /api/admin/artists/{slug}/merge-into` — body `{target_slug, notes?}`.
  Atomically rewrites FKs + denormalised `artist` strings, handles
  `UNIQUE(title, artist)` and `UNIQUE(artist_id, title)` collisions, deletes
  source Artist, writes an `artist_admin_events` audit row.
- `POST /api/admin/artists/{slug}/rename` — body `{new_name, new_slug?, notes?}`.
- `POST /api/admin/artists/{slug}/refresh-release-aggregates` — recomputes
  `track_count / calibrated_count / charge_value / rubric_color` on every real
  Release for the artist. Idempotent.
- `GET /api/admin/artists/events` — paginated audit log for merge/rename.

## Artist CRM + outreach log (Hockey Stick Build 8, 2026-06-11)

The Artist Verified funnel (`artist_verifications`: stages lead -> contacted ->
in_conversation -> active; `contact_email`/`phone`/`handle`; `conversation_log`;
deepfake gate; publishes `ArtistVerificationBlock` on the song page) was grown
into a mini-CRM with an OUTBOUND lead path + a manual outreach ledger.
Single-song scope (no album/catalog aggregate). Router `artist_verification.py`,
admin section **Artist Verified** (`templates/admin/artist_verified.html`).

- **Outbound lead.** Start-Outreach artist search -> empty-body
  `PUT .../artists/{id}/verification` ensures an `ArtistVerification` at
  `lead` (no-op if the artist is already in the funnel). Previously the ONLY
  way in was promoting an inbound "Are you the artist?" inquiry.
- **Full manual CRUD of the CRM record.** Every verification meta field is
  hand-editable, including manual `contacted_at` / `verified_at` overrides
  (added to `ArtistVerificationUpdate`; auto-stamp still fills them on stage
  advance when null). `DELETE .../artists/{id}/verification` removes the
  record but **refuses while a published block exists** (unpublish first);
  outreach history survives (it hangs off the artist).
- **Outreach log (`artist_outreach`, migration 105, model `ArtistOutreach`).**
  Manually entered touches: `song_id` (FK, SET NULL) + `song_title` snapshot,
  `channel` (email|dm|other), `contact_used`, `sent_at` (the "when", defaults
  now), `notes`. `GET/POST .../artists/{id}/outreach`, `PATCH/DELETE
  .../outreach/{touch_id}`; the list is bundled into `artist_detail`.
- **Song picker = charge-to-send surface.** `GET .../artists/{id}/songs`
  returns the artist's calibrated songs charge-DESC (reuses the public
  `artist_top_songs` release_songs+song_artists UNION SQL + `SongSlug`); the
  admin form shows each song's charge so Chad picks what to send.
- **Status:** BUILT local, py-compile clean, NOT deployed. Migration 105
  applies on deploy; never run against the shared prod DB locally. Send is
  manual (Chad) -- the funnel is human-operated, so no automated-outbound
  compliance concern (Lookout discipline by construction). Plan:
  `RISING-COMPASS-HOCKEY-STICK-PLAN.md` Build 8.

## Album Charger (Lyrical Charger tab)

A second top-level tab in the Lyrical Charger frontend (`frontend/lyrical-charger/`,
`Song Charger` default + `Album Charger`). Charges a whole album by calibrating
each track and aggregating.

- **Kill switch (`album_charger.disabled`, 2026-06-04).** An independent
  `system_flags` gate that closes ONLY the Album Charger while the single-song
  Song Charger stays open. **Fail-closed** (absent flag = disabled), so a fresh
  deploy ships with album charging CLOSED until an admin opens it. When closed:
  `/config` returns `album_charger_enabled: false` (frontend hides the whole
  Album Charger top-tab via `charger.js`), and the album endpoints
  (`/calibrate`, `/search`, `/search-tracks`) 503 via
  `_check_album_available_or_503()`. Toggle from **Site Admin -> LC Status ->
  Album Charger** (`POST /api/admin/lc-status/album-toggle {"disabled": false}`,
  `lc_status_admin.py`) -- no redeploy, ~30s propagation. Accessors in
  `feature_flags.py`; mirrors the `lyrical_charger.disabled` whole-LC pattern.

- **Async job model.** A full album is minutes of sequential Opus work, too long
  to hold an HTTP connection open for, so charging is a background job + polling
  (table `album_charge_jobs`, migration 071). Router `app/routers/album_charger.py`,
  prefix `/api/analyzer/album/`:
  - `POST /calibrate` — validates + bot-checks **synchronously**, creates an
    `album_charge_jobs` row, launches an `asyncio.create_task` worker (refs held
    in a module set so the loop doesn't GC them), and returns a `job_token`
    immediately (202). Reuses `analyzer._check_bot_protection / _validate_lyrics /
    _resolve_source / _song_persist_fields / _record_user_calibration` and
    `analyzer.limiter` (6/day). Search-Album (`/search`, `/search-tracks`) is
    **Musixmatch-gated** (`musixmatch.is_configured()`) and ships dark.
  - `GET /status/{job_token}` — poll progress (`phase` + `calibrated_tracks`) and,
    once `status='done'`, the full `AlbumCalibrateOut` result. A job stuck
    queued/running past 15 min (e.g. a redeploy mid-charge) is reported as errored
    so the UI stops polling. Limited 600/hour for the poll.
  - Worker flow (`_run_album_charge`, off the request): read lyrics + per-track
    cache lookup -> calibrate every track **concurrently** (`asyncio.gather`, cap
    `ALBUM_TRACK_CONCURRENCY=5`; each track is the normal song path: calibrator +
    effects + ether + societal), persisting progress as each completes -> one
    **album synthesis** call (`services/album_synthesis.py`, mirrors
    `effects_prose.py`) that compiles the album reading FROM the per-song
    listener/societal prose (songs are the atomic unit; never re-analyzes lyrics)
    -> single write txn -> store result on the job. Completes (incl. the Release
    write) even if the client disconnects. The frontend (`charger.js`) submits then
    polls every 3s, driving the real progress bar.
  - This removes the timeout problem entirely (every request is short), so no
    nginx `proxy_read_timeout` change is needed.
- **No prompt caching.** It was tried (rubric is ~10.9k tokens) but caching only
  saves input cost, and making the cache hit needs warm-then-serialize ordering
  that costs more wall-time than it saves. `cache_advisor` will email when LC
  traffic density makes it worth turning on.
- **Artist-page attachment:** the Album Charger is the first public path that
  creates a real `Release` (it has real release metadata). It upserts the artist,
  creates/updates `Release` with `source='album_charger'` + the synthesis columns
  (migration 069: `charge_summary, arc_prose, societal_prose, source, submitted_at`),
  links each scored track via `ReleaseSong` (pointed at the canonical row), and
  writes aggregates exactly like `refresh-release-aggregates`. Album charge =
  `compute_release_charge` (mean of track charges). Per-track featured artists are
  layered onto each track's credit (album artists + that track's features); the
  Release attaches to the album's primary artist only.
- **Release date** (form field, encouraged; trusted-source prefill from Musixmatch
  search) matters: the artist page's default `/releases?status=released` filters
  out releases with no `release_date`, so a year-only album lands in `unreleased`.
- **15-track cap** (schema `max_length=15`; frontend disables "+ Add track" and
  links to the inquiry form). Longer albums -> general inquiry.
- **Monitoring:** album events flow into LC Activity (`album_success`,
  `album_search_query`, `album_no_tracks`, `album_other_error` in `lc_events`).
  `GET /api/admin/lc-events/albums` + an "Album Charger" strip on the LC Activity
  page (counts + recent charged albums). Email alert `album_charged` (Activity,
  default-on, toggleable) via `alerts.emit_album_charged`.

## Release pages + cover art (2026-06-06)

Per-release detail pages + Cover Art Archive artwork. Full spec:
`Dropbox/Libra Engine/Rising Compass/plans and docs/RISING-COMPASS-ARTIST-RELEASES.md`
(Release Pages + Cover Art).

- **URL: `/artists/{artist}/{release}`**, release resolved by `slugify(title)`
  within the artist -- **rebuild-stable** (never keys on `releases.id`, which churns
  on re-resolve). SSR via `page_ssr.ssr_release` (meta + JSON-LD + baked hero glow);
  endpoint `GET /api/artists/{slug}/releases/{release_slug}` (`release_detail`).
  Page reuses the song-page shell (`release.html`/`release.js`).
- **Cover art (`mb_cover_art`, migration 091, schema_version 91):** cache keyed by
  the **release-group MBID** (matches `releases.musicbrainz_id`), NOT `releases.id`.
  `has_art` true/false (false = checked-none -> tier dot). URLs **derived** from the
  MBID (`coverartarchive.org/release-group/{mbid}/front-250|500`), **hotlinked**
  (referrer, not host -- legal posture on the Fair Use page). `services/coverart.py`;
  populated by `resolve_artist_releases` + `scripts/backfill_cover_art.py` (~1/sec).
  List endpoint adds `cover_thumb_url`; thumbnail replaces the tier dot on the
  artist page and every release row links to its page.
- **Album Charger MB match:** user-charged albums have no MBID, so the worker now
  searches MusicBrainz and **auto-attaches when confident** (score >=92, title-slug
  match, margin >=12) else returns candidates for a **picker**
  (`POST /api/analyzer/album/choose-release/{job_token}`, validates against offered
  candidates). Admin verify email either way (`alerts.emit_album_mb_match`, key
  `album_mb_match`, default-on). Attaching the MBID is what gives the album cover art.
- **Routing:** dev_server proxies `^/artists/[^/.]+/[^/.]+/?$` to the backend. **Prod
  nginx needs NO change** -- the existing `location /artists/ { try_files $uri $uri/
  @artist_detail; }` already falls multi-segment paths through to the backend, where
  `ssr_release` handles them (verified live on deploy 2026-06-06).

## Charger Activity (public feeds page, LIVE 2026-06-06)

Public page at `/lyrical-charger/activity/` showing what's moving through the
Lyrical Charger. Three feeds, all derived from existing tables -- **no schema
change**:

- **New Additions** (first-time calibration): songs whose *earliest*
  `song_ingestions` row is `method='lyrical_charger'` (a non-LC ingestion that
  predates it disqualifies -- excludes chart songs later re-run through LC).
  Ordered by that ingestion `created_at` desc. NOTE: historical ingestion rows
  predate the `created_at` default, so `first_calibrated_at` is often NULL on
  legacy songs (blank "Added ..." label); the column populates going forward.
- **Recently Calibrated**: distinct songs by `MAX(occurred_at)` of
  `lc_events` rows with `event_type='submission_success'` AND `song_id` set
  (counts ALL runs -- anon + signed-in, since that event is written for both).
- **Most Calibrated**: `COUNT(*)` of `calibration_runs` per song (the true run
  ledger -- one row per calibration pass, incl. superseded), all-time or
  trailing-30-day window. NOT `lc_events`: that event is is_public-gated +
  best-effort background-written, so it diverges from the real run count (a song
  can have 3 runs / 1 event, or a cache-hit event / 0 new runs). Recently
  Calibrated still uses `lc_events` (most-recent successful public run).

Backend: `app/routers/charger_activity.py` (`public_router`, prefix
`/api/charger-activity`, registered with `_api_key_dep` like the calibration-log
public router). Endpoints: `/overview` (all three at once for first paint),
`/new-additions`, `/recent`, `/most-run?window=all|30d`. Each row is shaped like
`song_search` (reuses `song_search._attach_slugs` / `_attach_artist_slugs`) plus
a feed metric; rows with NULL `rubric_color` are dropped so a tier always
renders. No prose (public summary fields only -- no entitlement logic).

Frontend: `frontend/lyrical-charger/activity/` (`index.html` + `activity.css` +
`activity.js`), `<body class="rc-elevated">`. Cards reuse the album-track-result
vocabulary: tier dot, the homepage radioactive contam icon (`.contam-icon`,
U+2622), dogma glyph, tier-colored charge. Linked from the LC page (above + below
the tool), the footer (Lyrical Charger group), and `sitemap.xml`.

Possible follow-up if a feed query gets slow: a `(event_type, song_id,
occurred_at)` index on `lc_events` (not added -- current volume is fine).

## Origin chart (`songs.origin_chart`, Build 7, 2026-06-10)

The chart a song **first surfaced on** = the `chart_source` of its earliest
`chart_reading` ingestion. Stamped once, **immutable** (`origin_chart IS NULL`
guard), NULL for non-chart births (lyrical_charger / terminal / catalog_backfill).
First-class queryable form of a fact that was previously only in
`song_ingestions.detail` JSON -- and the ONLY provenance for Shazam / YouTube /
iTunes, which create no `chart_appearance` (excluded from the charge aggregate).
Instruments the thesis that degraded music surfaces via the social-discovery
gutter (migration `101`, column on `Song`).

- **Stamped** in `song_sync.upsert_unified_song` (chart_reading writes) AND via
  `song_sync.record_chart_ingestion()` on the **cache-hit chart path**
  (`compass_agent.run_compass_agent`): a cache hit skips the storage chokepoint,
  so without the helper a song already in the Library that first surfaces on a
  chart would log no chart_reading ingestion -- the gutter-migration signal would
  be invisible. The helper is idempotent + fail-soft.
- **Surfaced**: public song page ("First surfaced on ...", `songs._resolve_song`
  -> `songs.js`) + admin song detail identity line (`runs_admin.song_detail`).
  Label via `constants.chart_source_label()` (real chart names, NOT the Daily
  Listens/Downloads rebrand). origin values: `spotify_top50_usa`,
  `itunes_download_usa`, `shazam_top200_usa`, `youtube_trending_usa`, + legacy.
- No regression: leit_sweep + charger-activity "New Additions" key on EARLIEST
  ingestion, unaffected by a later chart_reading row.
- Phase 2 (not built): tier/charge distribution by `origin_chart` over time.

### Sitewide footer (grouped, rebuilt 2026-06-06)

`partials/footer.html` is now a grouped footer (brand block + columns: Explore /
Lyrical Charger / Concept / Framework / Participate, plus a bottom account+legal
menu). Styles live in `css/main.css` under `.footer-grid` / `.footer-*`. Edit the
partial then run `scripts/build_partials.py` to bake into all pages. The
`/artists/` A-Z index was also aligned to `--rc-max-width` (was a stray 900px
centered column) with button-style letter nav, group cards, and smooth scroll.

## Chart snapshots: iTunes Download Chart (homepage panel, LIVE 2026-06-07)

A secondary chart panel on the homepage, separate from the daily reading.
Originally the Spotify Viral 50, but Spotify retired its Viral 50 charts in May
2026 (the playlist 404s), so the slot was reskinned to the **iTunes Download
Chart - USA (top 20, daily)**. Mechanism in `routers/chart_snapshots.py`
(`CHART_REGISTRY` -- `itunes` + `top50`); fetcher in
`services/agents/chart_source.py` (`fetch_itunes_songs`, a plain HTTP GET against
Apple's public RSS JSON feed -- no Playwright). **Lyrics are supplied manually**
-- the only manual step; everything else mirrors the daily reading SOP.

- **Approve-before-public gate (`chart_snapshots.published`, migration 094).**
  Snapshot rows are written UNPUBLISHED at fetch time. The public endpoint
  `GET /api/compass/chart/{key}/current` serves only `published=True` rows (404
  otherwise -> the homepage panel stays hidden). **Approval is what publishes:**
  the chart branch of `agent.approve_draft` rebuilds the snapshot FROM
  `draft.songs` as published (mirrors the daily path building `ReadingSong` from
  `draft.songs`, so admin edits are reflected and `published == approved`).
  Approval already blocks while any song lacks lyrics, so a published chart is
  guaranteed fully calibrated. Nothing goes public until Chad clicks Approve.
- **Full daily-reading safeguard parity** (2026-06-07): HMAC approval token +
  GET-confirm/POST-publish prefetch safety (shared); chart re-click after
  approval shows an "Already Approved" page (`_published_chart_for_ref` +
  `_chart_slug_from_ref`; `_published_reading_for_ref` guarded so a chart ref
  never mis-resolves to the same-date daily reading); `refresh_snapshot` no-ops
  if today's chart is already published (chart approval deletes the draft, so
  draft-pinning alone couldn't guard a re-trigger from un-publishing). The iTunes
  Download Chart is excluded from the compass charge + drift aggregates
  (`AGGREGATING_CHART_SLUGS` in `constants.py`) and never creates a `DailyReading`.
- **Frontend:** `renderItunesPanel()` in `frontend/js/app.js` renders the live
  published snapshot (hidden on 404). Song-page links render only once a row is
  calibrated.
- **Daily fetch cron (server, le-projects-01):**
  `/root/risingcompass-readings/itunes.sh` (reference copy `deploy/itunes.sh`)
  runs **daily** after the daily reading at 08:00 UTC, so any Spotify Top 50
  overlap is already calibrated (free cache hits). Hits `POST /api/admin/agent/
  cron/refresh-chart-snapshot/itunes` with `X-Reading-Cron-Key` (same
  `RC_READING_CRON_KEY` as the daily reading). Mirrors `reading.sh`: RSS fetch ->
  unpublished snapshot + draft -> auto-calibrate cache hits -> email Chad the
  awaiting-lyrics list.
- **Daily SOP:** cron fires (or trigger manually) -> Chad supplies lyrics for
  fresh songs (`scripts/calibrate_song.py`, lyrics from `Dropbox/Debug/dd.txt`)
  -> click Approve in the email -> panel publishes. Labor = iTunes chart songs not
  already in the library (cache hits are free).
- **Per-day aggregate + chart-agnostic Calendar (2026-06-08).** `chart_snapshots`
  now stores `compass_degree` + `charge_level` (migration 095), stamped at approval
  (`agent.approve_draft` chart branch), so the Calendar can paint any chart day.
  The Calendar is **chart-agnostic** via a toggle: **Daily Listens** = the daily
  reading (drift endpoints), **Daily Downloads** = this iTunes chart (new
  `/api/compass/chart/{key}/years | years/{year}/dates | reading/{date}`). Front-
  facing rebrand: daily reading = **Daily Listens**, iTunes = **Daily Downloads**
  (internal table/`draft_type` names unchanged). Standalone pages
  `/charts/daily-listens/` + `/charts/daily-downloads/` (`frontend/charts/`); footer
  **Charts** column. Daily Downloads paints/show only from approved snapshots, so it
  fills forward. Plan + source matrix:
  `plans and docs/RISING-COMPASS-CONSUMPTION-METHODOLOGY.md`.

- **Canon chart shell (`frontend/js/chart-shell.js`, 2026-06-12).** The paired
  chart-reading view -- left card (charge band `.reading-charge-group` + editorial
  + song list) beside the Ether Art Chart card -- is ONE module rendered by every
  surface: the homepage daily reading (`app.js renderReading`), the homepage iTunes
  panel (`renderItunesPanel`), `js/ether-art-chart.js` (its row template), and all
  standalone `/charts/*` pages (`charts/chart.js`). It owns the charge group,
  editorial, song-list row+tooltip (union: MEI + dogma + contam + charge_summary +
  preorder + instrumental, feature-detected), ether row, and `wireTooltips`, plus
  the one `COLOR_HEX`/`CHARGE_LABELS` table. Do NOT re-implement any of these inline
  -- extend the shell so every surface moves together (this replaced three drifted
  copies). Input = a normalized `reading` object `{date, degree, charge,
  contaminationCount, editorial, songs[]}`. Charts carry `editorial` on
  `chart_snapshots.editorial` (migration 119), stamped at approval from the draft's
  editorial; `ChartSnapshotOut` exposes
  `compass_degree`/`charge_level`/`contamination_count`/`editorial`.

  **Editorial is REGENERATED at approval (2026-06-13).** `approve_draft`
  re-runs `_generate_editorial` over the FINAL calibrated song set right before
  stamping the DailyReading / ChartSnapshot -- so the published editorial always
  reflects the whole reading, for the daily reading AND every chart. This closed
  a gap: the draft editorial is generated at draft-creation time over only the
  cache-hit songs, and the terminal calibration SOP (`calibrate_song.py`,
  `terminal_mode`) skips editorial regen (no Anthropic from terminal), so a
  fresh-release-heavy reading (New Music Friday) used to publish a stale "one
  calibrated song in a field of twenty" editorial. Approval is browser/admin, so
  the Anthropic call is allowed there; it ALWAYS regenerates (a hand-edited
  editorial set via `PUT /drafts` before approval is overwritten) and is
  fail-soft (keeps the existing editorial on error). NOTE: this means approval
  now makes one editorial Anthropic call.

  **Adding a chart shell (the whole recipe):**
  1. Backend: register a `CHART_REGISTRY` entry (`routers/chart_snapshots.py`) +
     a fetcher (`services/agents/chart_source.py`). No schema change.
  2. Frontend: copy an existing page dir under `frontend/charts/<key>/` (e.g.
     `charts/itunes/`), set `window.RC_CHART = {source:'<key>', title, sub}`. The
     page already loads `api.js` + `chart-shell.js` + `chart.js` + `main.css` +
     `responsive.css` + `chart.css`; `chart.js` renders BOTH cards into the
     `.chart-shell-grid` (mirrors the homepage `.main-dashboard`). `source:'daily'`
     pulls `/compass/current` + `/ether-art-chart/today`; any other source pulls
     `/compass/chart/<key>/current` (one fetch fills both cards). Add the page to
     the footer Charts column (`partials/footer.html` -> `build_partials.py`) +
     `sitemap.xml`.
  3. Run the chart's refresh cron + approve to publish. The charge band +
     editorial appear once the snapshot is approved; the editorial line is blank
     only until that approval.
  A chart's `cadence` can be `weekly` -- set it and OMIT `calendar_label` so the
  Calendar (day-grid, daily-only) doesn't list it, and cron it on its own day.
  Example: **Spotify New Music Friday - USA** (key `new-music-friday`, slug
  `spotify_nmf_usa`, weekly), added 2026-06-13. Its fetcher (`fetch_nmf_songs`)
  reuses `_fetch_playlist` (same Spotify playlist DOM as the Top 50); cron
  `deploy/new-music-friday.sh` runs Fridays. Because NMF is mostly fresh releases
  (few cache hits), it is the reading that exposed the stale-editorial gap above.
  Session records: `plans and docs/session notes/2026-06-12d - Canon Chart Shell.md`,
  `2026-06-13 - Spotify New Music Friday Tier 2 Chart.md`.

## General Inquiry form

Reusable account-free contact form (`frontend/inquiry.html`). First caller is the
Album Charger's "need more than 15 tracks?" link (`?topic=album_charger&source=...`).

- `app/routers/inquiries.py`: public `POST /api/inquiry` (bot-protected, rate
  limited 5/hour, no login), admin `GET/PATCH /api/admin/inquiries`. Table
  `general_inquiries` (migration 070), model `GeneralInquiry`.
- Admin section `inquiries` (`admin/inquiries.html`) with status/topic filters.
- Email alert `general_inquiry` (Moderation `[RC-MOD]`, default-on) via
  `alerts.emit_general_inquiry`.
- The form **disclaims** that score/calibration/algorithm questions are not
  answered there and routes them to the Misread report (song page) or the Motion
  Desk (`/motion-desk/file-a-motion/`).

## Monetization core (M0-M6, built 2026-05-28)

Two-bucket credit metering + two-tier subscription, both wrapping the same
engine. Source of truth is `credit_ledger`; row counts on `users` are the
denormalised fast read.

- **Migrations 072 / 073 / 074** add the billing columns, `credit_ledger`,
  and rename `api_clients.plan_tier` default `trial` -> `free`. Schema baseline
  is now 74.
- **Entitlement.** `is_paid = is_paid_user(user_sub) OR is_paid_api_client(plan_tier, behavior)`.
  `PAID_API_TIERS = {plus, pro, internal}`; `behavior == 'service'` also paid.
  **`system + public`** (legacy-public) is intentionally **anon-grade** -- it
  backs the unsigned RC frontend, so anon visitors see the free-tier cap.
- **Costs** (`app/billing_config.py`): `COST_SONG_MISS=1`, `COST_SONG_CACHE_HIT=0`
  (subscription benefit, no Opus), `COST_ALBUM_TRACK_MISS=1` (worst-case
  hold; settle refunds cache hits), `ANON_CHARGER_DAILY_LIMIT=3` per-IP per-day.
- **Charger flow.** Signed-in users: `check_credits` pre-Opus at the ACTUAL
  cost (cache hit = 0, so a zero-balance subscriber isn't blocked from a free
  re-read), `charge_credits` after success; anon: per-IP rate limit only.
  Album: `hold_credits` at submit, `settle_hold` in worker `finally` (success /
  no-tracks / crash all reconcile).
- **Library flow.** `/api/songs/search` returns 20-row cap + no pagination
  for free; `user_subscription_tier` is in the response so the frontend can
  render the right paywall hint.
- **Limiter.** `analyzer.py:limiter.key_func` is `user:{id}` for signed-in,
  IP for anon. The dynamic limit provider is `_calibrate_daily_limit(key)` --
  slowapi passes the rate-limit KEY only to a param named exactly `key` (else
  it calls the provider with no args), returning `100/day` per-user backstop /
  `3/day` per-IP anon. NOTE: the original `(request)` signature matched neither
  convention and 500'd every calibrate request -- keep the param named `key`.
- **Idempotency.** `credit_ledger` partial UNIQUE `(reason, ref_id, bucket)
  WHERE ref_id IS NOT NULL` -- declared on the `CreditLedger` model AND in
  migration 073 (so fresh `pg_baseline` installs are replay-safe). Stripe
  webhook replays (`event.id`/`invoice.id` as ref_id) are no-ops; `charge_credits`
  also catches the duplicate `IntegrityError` (concurrent same-song) as a no-op.
  Settlement/refund/expiry rows use ref_id suffixes (`:refund:allowance`,
  `:refund:purchased`, `:extra`, `:expire`, `:cancel:expire`) to stay
  independently idempotent.
- **Ledger integrity.** Forfeited allowance (monthly `reset_allowance` +
  `subscription.deleted`) writes a negative `allowance_expire` row, so
  `row buckets == signed ledger sum` holds (verified live). `rejected` /
  `settlement` / `unbilled_overrun` rows are `delta=0`.
- **Webhook.** `/api/billing-webhook` has its own signing secret
  (`STRIPE_BILLING_WEBHOOK_SECRET`) distinct from donations / identity. Always
  returns 200 after signature verification. **`invoice.paid` is the sole
  `monthly_grant` authority** (fires for the first invoice too; derives tier
  from the subscription event, not the row); `checkout.session.completed`
  mode=subscription only syncs tier/customer/period_end -- it does NOT grant.

- **Pre-launch gate (`launch.locked`, 2026-05-29).** A `system_flags` flag
  (default LOCKED / fail-closed) that gates public sign-up AND billing checkout;
  sign-IN for existing accounts is unaffected. Frontend fades the Clerk SignUp
  form + Wallet buttons (`account.js` reads `GET /api/launch-status`); backend
  returns 503 `launch_locked` from the checkout endpoints (money safety on live
  Stripe). Open it with `POST /api/admin/launch-lock/toggle {"locked": false}`
  (admin session, `launch_admin.py`) -- no redeploy. Mirrors the
  `lyrical_charger.disabled` kill-switch pattern.

- **Admin a-la-carte credits + unlimited comp (2026-06-04).** Two admin
  abilities on the user detail Payments tab (`users_admin.py` +
  `templates/admin/user_detail.html`):
  - **Credit grant/deduct.** `POST /api/admin/users/{ident}/grant-credits
    {amount}` -- positive grants to the permanent **purchased** bucket
    (`admin_grant`), negative deducts purchased-first-then-allowance, clamped at
    zero (`admin_deduct`). `billing.admin_adjust_credits`; signed ledger rows
    keep the balance == ledger-sum invariant. ref_id is a per-click uuid.
  - **Unlimited Lyrical Charger comp.** `users.comp_unlimited` boolean
    (migration 083), orthogonal to `subscription_tier` (a comped user stays
    `free` / no Stripe sub). `POST /api/admin/users/{ident}/comp {unlimited}`.
    When true: `billing.is_unlimited` short-circuits `check_credits` /
    `charge_credits` / `charge_song` to zero-cost (writes a `delta=0` `comp`
    ledger row, `comp_unlimited` reason, so runs stay auditable), the album
    hold/settle path no-ops (settle finds no hold rows -> harmless marker), and
    `analyzer._calibrate_daily_limit` lifts the per-user daily backstop (cached
    comp-id set, ~30s TTL). **Charger-only** -- Library entitlement
    (`is_paid_user`) is unchanged. Comp grant/revoke writes a `comp_grant` /
    `comp_revoke` audit row.

### Required env (M2, both local and prod)

- `STRIPE_BILLING_WEBHOOK_SECRET` -- billing webhook signing secret (distinct).
- `STRIPE_PRICE_PLUS`, `STRIPE_PRICE_PRO` -- subscription Stripe Price IDs.
- `STRIPE_PRICE_PACK_25`, `STRIPE_PRICE_PACK_100`, `STRIPE_PRICE_PACK_300` --
  one-time credit pack Stripe Price IDs.
- `STRIPE_BILLING_RETURN_URL` -- fallback success URL (caller usually overrides).

Unset price IDs return 503 from the matching checkout endpoint without
breaking the rest of `/api/billing/*`. All env passthroughs are in
`docker-compose.yml` under the `backend` service.

### PostHog (server-side analytics, 2026-05-30)

- `POSTHOG_API_KEY` -- PostHog project key (`phc_...`, same project as the
  frontend snippet) used by the Python SDK to capture server-side revenue +
  async events (`app/services/posthog_analytics.py`). **Prod `.env` only** --
  intentionally NOT set in local `.env` so server capture is a no-op locally.
- `POSTHOG_HOST` -- defaults to `https://us.i.posthog.com` in docker-compose.
- Unset key -> server capture is a fail-soft no-op (billing/album unaffected).
  See `RISING-COMPASS-ANALYTICS.md` for the full integration (client snippet,
  `/ph` first-party proxy, events, dashboards).

### Spec docs

- `RISING-COMPASS-FINANCIALS.md` -- pricing, costs, refund/downgrade
  semantics, gross-margin model.
- `RISING-COMPASS-DATABASE-SCHEMA.md` -- the 072-073-074 migrations + the
  ledger integrity invariant.
- `RISING-COMPASS-API-SPEC.md` -- every `/api/billing/*` route, the
  entitlement changes to `/api/songs/search` and the calibrate endpoints,
  the error model.

### Status (deployed 2026-05-29)

DEPLOYED to production 2026-05-29 (`origin/master`). M0-M6 live; gate LOCKED.
- Stripe migrated to a **new dedicated Rising Compass account** (off the shared
  chadlewine account). Billing + donations + Identity all run on the new account;
  live price IDs + 3 distinct webhook secrets are in prod `/root/rising-compass/.env`
  (old Stripe config backed up at `.env.bak.predeploy`). Local `.env` uses a Stripe sandbox.
- Library prose withheld server-side for free/anon (`/api/songs/search`), not just a UI lock.
- Verified live: `/api/launch-status` locked; `/api/billing/estimate` returns credits;
  billing schema present (`users.subscription_tier`/`allowance_credits`, `credit_ledger`).

**Only remaining step:** open the gate when ready --
`POST /api/admin/launch-lock/toggle {"locked": false}` (admin session). No redeploy.

## Prose provenance (societal-prose anchoring, LIVE 2026-05-31)

Tamper-evident provenance for `societal_effects_prose`. Three layers; full spec
in `Dropbox/Libra Engine/Rising Compass/plans and docs/RISING-COMPASS-PROSE-PROVENANCE.md`.

- **Seal (always on).** Every `societal_effects_prose` write also stamps
  `societal_prose_generated_at` + `societal_prose_model`, in lockstep, on the
  unified `songs` row (post song-entity renovation; the former 4 prose tables
  compass/library/submitted/stream were dropped in Phase 5d). Centralised via the
  shared mappers (`analyzer._song_persist_fields`,
  `backfill/engine._apply_generated_fields`) + the direct sites. **Terminal-supplied prose** (Claude-Code via
  `compass_agent._store_calibration`) carries no server seal, so a **write-time
  floor** stamps `model='terminal_supplied'` + `utcnow()` (parallels the
  migration-075 `legacy_unknown` proxy). Migration 075.
- **External anchor (`services/provenance_anchor.py`, migration 076/077).** The
  `sweep` publishes **hash-only** records (`sha256(table:id | generated_at | model
  | prose)` -- prose never leaves the DB) to a public GitHub repo
  (`chadworksweb/rising-compass-provenance`) and OpenTimestamps each batch to
  Bitcoin; `upgrade` confirms proofs on-chain; `reverify` re-`ots verify`s a
  sample of complete proofs (integrity). Table `prose_provenance_anchors`.
- **Endpoints** (`routers/provenance.py`, `X-Provenance-Cron-Key`): `POST
  /api/admin/provenance/{sweep,upgrade,reverify,health-check}`; `GET .../status`
  (admin) feeds the **Site Admin -> System -> Provenance** monitoring page.
- **Crons** (le-projects-01, `/root/risingcompass-provenance/provenance-cron.sh`):
  sweep `0 16` UTC (noon ET, after the reading), upgrade `13 */4`, reverify `40 6`,
  health-check `30 16`. Alerts `provenance_health` + `provenance_integrity` (opt-in,
  enabled).
- **Config / dark switch.** Gated on `PROVENANCE_ENABLED` (now true in prod).
  Other env: `PROVENANCE_REPO_PATH=/provenance` (volume-mounted write clone),
  `RC_PROVENANCE_CRON_KEY`, `PROVENANCE_REVERIFY_SAMPLE`, `PROVENANCE_HEARTBEAT_URL`
  (dead-man's-switch, unset), `PROVENANCE_REPO_URL` + `PROVENANCE_BLOCK_EXPLORER`
  (admin link bases). The image needs `git` + `openssh-client` + the `ots` CLI
  (`opentimestamps-client`); push uses the SSH deploy key mounted at `/provenance-key`.
  Everything is fail-soft and OFF the calibration hot path.

## Per-user admin activity view (2026-06-02)

Site Admin -> Community -> Users -> click a user. The detail page shows
**everything a user does**, keyed by their handle (the chosen handle IS the
pseudonym -- no real-name masking, no anon_id "reveal" gate; that build-plan-1.7
scheme was dropped. `anon_id` remains only as the stable public ID + URL key).

- `routers/users_admin.py` -- detail returns billing summary + counts;
  `_resolve_user` matches anon_id -> handle -> id; `list_users` takes `?q=`
  (handle/anon_id ILIKE; the Users list page has a search box). New admin-gated
  endpoints under `/api/admin/users/{ident}/`: `comments`, `calibrations`
  (signed-in Lyrical Charger runs from `user_calibrations`), `submissions`
  (misread/satirical), `motions` (filed + chamber arguments), `payments`
  (billing + `credit_ledger`), and `activity` (unified reverse-chron timeline
  merging all of the above + verifications; pulls <=300/source, merges, slices).
- `templates/admin/user_detail.html` -- tabs Profile | Activity | Calibrations |
  Submissions | Motions | Payments | Comments (Activity default), plus a
  **"View in PostHog"** button (hidden unless `POSTHOG_PROJECT_ID` set).
- General Inquiries are intentionally NOT linked (no user_id; `users` stores no
  email -- Clerk holds it).
- **PostHog identify is already done in `frontend/js/auth.js`** (distinct_id =
  Clerk user id, with handle/tier person props). The admin button deep-links to
  `{POSTHOG_UI_HOST}/project/{POSTHOG_PROJECT_ID}/person/{clerk_user_id}`.
- New env: `POSTHOG_PROJECT_ID`, `POSTHOG_UI_HOST` (default us.posthog.com).

## Cookie consent bar (geo-aware, 2026-06-02)

Ported from chadlewine into vanilla JS (static frontend). Two categories:
Essential (always on) + Analytics (PostHog + GA). Choice stored in the
`rc_cookie_consent` cookie (`essential:1|analytics:X`, 1yr, SameSite=Lax).

- **Geo-aware default:** EU/UK/EEA = opt-in (analytics OFF until Accept), rest =
  opt-out (load unless Rejected). Country from `GET /api/geo-country`
  (`routers/geo.py`, MaxMind GeoLite2 via `geoip2`, **no X-Api-Key** so the bar
  can call it anon). **Fail-closed:** null country (DB missing / lookup fail /
  2.5s timeout) -> treated as opt-in. So until the mmdb is placed, the bar
  behaves as opt-in everywhere.
- **Analytics are consent-gated.** `partials/analytics.html` keeps the PostHog
  stub but moved the real init into `window.rcInitAnalytics()`, called by
  `js/consent.js` only on consent. `rcInitAnalytics()` still enforces the hard
  overrides: prod host only, admin `rc_ph_optout` cookie, personal
  `rc_skip_analytics`, **Do Not Track, and Global Privacy Control (GPC)** --
  DNT/GPC also suppress the bar (no nag, analytics never load), honored
  regardless of stored consent.
- **Assets:** `css/consent.css` + `js/consent.js`; CSS link is in the analytics
  head partial, `<script defer src="/js/consent.js">` + a "Cookie preferences"
  link (`rcOpenCookiePrefs()`) are in the footer partial -> on every page after
  `scripts/build_partials.py`. `privacy.html` documents it.
- **Ops:** mmdb mounted `/root/geoip:/geoip:ro`, path `GEOIP_DB_PATH`. Fetch /
  refresh with `deploy/refresh_geoip.sh` (needs `MAXMIND_LICENSE_KEY`; cron it
  weekly, restart backend after). `geoip2` is in `requirements.txt`.

## Logging + Faultline (error ledger, LIVE 2026-06-05)

**Durable logging (read this before adding any `except: logger.exception`).**
The app used to rely on uvicorn's console only -- no `FileHandler` -- so a real
prod failure once left ZERO durable trace (full analysis +standards in
`plans and docs/RISING-COMPASS-ERROR-CONSOLE-POSTMORTEM.md`; read it). Now
`app/logging_config.py::configure_logging()` runs at the TOP of `main.py` and
attaches a rotating file handler (`backend/logs/backend.log`, gitignored) to the
root logger. Every `logger.exception` survives to disk.

**Faultline** is the queryable layer above that file -- an internal error ledger
+ agent-driven triage. Self-contained subsystem; spec in
`plans and docs/RISING-COMPASS-FAULTLINE-SCOPE.md`.

- **Capture (decoupled, fail-safe).** An `ErrorLedgerHandler` (in
  `services/faultline.py`, attached in `logging_config.py`) on the root logger
  turns every ERROR record into a deduplicated fault. ZERO business-code
  coupling -- it rides Python's logging contract, nothing imports it from app
  logic. Fingerprint = `sha256(exc_type + innermost frames)`; one
  `error_signatures` row, many `error_occurrences`. Non-blocking (bounded queue +
  daemon writer thread, own session), drop-on-overflow, never raises. Every fault
  is tagged `environment` (`prod` | `local`) -- **local dev shares the prod DB via
  the tunnel, so always filter the panel by env.**
- **Tables (migration 085):** `error_signatures` (the issue, keyed by
  `fingerprint`), `error_occurrences` (hits, retention-pruned), `error_actions`
  (the phase-mgmt audit log). Models in `models.py`.
- **Lifecycle (shared service `services/faultline_triage.py`):** `new -> triaged
  -> investigating -> fix_proposed -> fix_applied -> verifying -> resolved`, plus
  `wont_fix|duplicate`, `muted` (orthogonal), and auto-`regressed` (a new hit on a
  resolved fault). Both the admin panel and the agent API drive faults through
  this one module -- identical rules.
- **Admin panel:** Site Admin > System > **Faultline** (`admin/faultline.html`,
  router `routers/faultline.py`). List/filter/detail (traceback + occurrence
  timeline + action log) + triage controls + Promote to Dev Ledger + prune.
- **Agent API (`routers/faultline_agent.py`, `/api/agent/faultline/*`).** Key lane
  `X-Error-Agent-Key` == `RC_ERROR_AGENT_KEY`. **Ships dark: 503 on every endpoint
  until the key is set.** Queue (ranked), detail + parsed `code_pointers`,
  claim/heartbeat/release (leasing), actions/triage/status/promote/prune -- all
  idempotent. Runner harness `scripts/faultline_agent.py` (swap `diagnose()` for a
  real agent: `claude -p` or the Agent SDK). Works fully manually without any
  agent -- the agent is an optional accelerant.
- **Seams (only two, deliberate):** inbound = the logging handler; outbound =
  one-way promote to a Dev Ledger `bug` (`dev_ledger.create_internal_bug`, sets
  `error_signatures.dev_ledger_item_id`). Walled from `lc_events`, `api_call_log`,
  Status Page, Misread. Alerts `faultline_new_signature` (every brand-new fault,
  deduped per fingerprint), `faultline_new_critical`, and `faultline_regression`
  (all default-on, fail-safe) reuse the existing alerts system. **All three are
  prod-gated** in `faultline._persist` (`environment == "prod"`): local-dev faults
  on the shared tunnel DB are captured but never email. The new-signature alert
  (added 2026-06-06) closes the gap where a brand-new prod-down fault stayed
  silent until a human triaged it to critical.
- **Flag/env:** `system_flags` `faultline.enabled` (fail-OPEN kill switch);
  `RC_ERROR_AGENT_KEY`, `FAULTLINE_CAPTURE_LEVEL` (ERROR), `FAULTLINE_OCCURRENCE_RETENTION`
  (50), `ENVIRONMENT` (defaults `prod` in docker-compose). All in `docker-compose.yml`.
- **Retention:** `prune_occurrences` keeps N/signature, never deletes signatures;
  admin `POST /api/admin/faultline/prune` or agent `/api/agent/faultline/prune`
  (cron-able). Not yet cronned.

## Charger reliability: salvage + refund (2026-06-06)

Both single-song calibrate endpoints (`analyzer.py` `calibrate_lyrics_endpoint`
+ `calibrate_search`) commit the song BEFORE charging, so a charge implies a
durable, delivered reading. The blanket `except` was turning any post-save hiccup
(today's `schedule_event` regression) into a user-facing 500 on a saved+charged
run. Now:
- `submitted_id` is set only AFTER `write_db.commit()` succeeds (so "saved" is
  truthful even on a failed commit), and the optional `schedule_event` tail is
  wrapped so telemetry can never fail a saved run.
- **Salvage (public charger only):** if the song was saved but a later step
  threw, the public endpoints return `status="saved_view_on_page"` + `song_slug`
  (no 500, no refund -- it was delivered). The frontend (`charger.js`) shows a
  "Your song was completed and saved -- view your reading" card linking to
  `/songs/{slug}` (their choice to click, not a redirect). Machine API/service
  callers (`is_public` False) keep the 500 -- contract unchanged, their retry
  hits the cache.
- **Refund net:** `billing.refund_song(user_id, charge_result, ref_id=...)`
  reverses the exact allowance/purchased split, idempotent `:refund` ref_id, and
  is called only when a charge was made WITHOUT a saved song. With the current
  ordering that can't happen (charge is post-commit), so it's a defensive
  guarantee, not a live path -- covered by `tests/test_refund_song.py` (fake
  session, no DB) since nothing exercises it e2e. Album already refunds via
  `settle_hold`. TOS clause: `frontend/terms.html` section 8.

## Timezone model (2026-06-06)

Standard "store UTC, convert at the edges," split by audience:
- **Code + billing + crons: UTC (unchanged).** All `datetime.utcnow()` storage,
  `billing._utc_day_start()` daily-free boundary, slowapi `/day` limits, the
  Charger Activity 30-day window, and the server crons stay UTC. Zero billing
  risk; no migration to day-boundary logic.
- **Public UI: user-local.** The frontend already renders in the visitor's own
  zone via `toLocaleString`; the two UTC-pinned outliers were fixed
  (`calibration-log.js` dropped `getUTC*`; `account.js` daily-free reset now
  shows the reset instant in the viewer's local time instead of "midnight UTC").
- **Admin: per-admin selectable zone, default ET, hot-reload.**
  `AdminUser.timezone` (migration 092, IANA name, default `America/New_York`),
  set on `request.state.admin_timezone` in `auth.py`. A picker in
  `templates/admin/_base.html` swaps `window.ADMIN_TZ` and reformats every
  `<time data-utc="<iso>">` element in place (shared `adminFmt` +
  `MutationObserver`) -- no page refresh -- persisting via
  `POST /api/admin/auth/timezone` (validated against the IANA db with `zoneinfo`).
  Admin templates emit `<time data-utc>` (add `data-tz-date` for date-only)
  instead of pre-formatted strings. The one server-bucketed chart
  (`claude_usage_admin.py` `date_trunc`) takes a `tz` param and the page refetches
  on zone change via `onAdminTzChange`.

## LEIT clutter control (2026-06-09)

Keeps non-music / clutter out of the Library/corpus. Two feeders, ONE human-audit
queue (`clutter_audits`, migration 097, model `ClutterAudit`). Flag-only -- nothing
auto-changes the live site; an admin resolves each finding. Rows are tagged
`environment` (local|prod) like Faultline (local dev shares the prod DB via the
tunnel), and the admin queue filters by env. Shared write helper:
`services/clutter.py::record_clutter_finding` (deduped to ONE open row per song via
partial unique `uq_clutter_open_song`; `db=None` = own fail-soft session for the LC
hot path, `db=` = caller's txn for the sweep).

- **Submit-time warning (Lyrical Charger).** The commercial-release verdict is
  FOLDED INTO the existing identity-guard Opus call (`services/identity_guard.py`
  now returns `commercial` + `commercial_reason` alongside `verdict`; one call, no
  extra latency). In `analyzer.calibrate_lyrics_endpoint`, a confident
  `commercial=="no"` on a public submission WITHOUT `confirm_commercial` short-
  circuits `status="not_commercial_warning"` (+ `commercial_reason`, no save/charge);
  `charger.js` shows the "Is this a released song?" modal (Creative/Curio shown as
  coming soon). `unsure`/`yes` pass silently (niche/indie never nagged -- same
  anti-false-reject stance as the identity verdict). On the resubmit with
  `confirm_commercial=true` the run completes AND writes a `clutter_audits`
  `source='lc_push'` row for human audit. Heuristic pre-filter
  `analyzer.detect_noncommercial_signals` is logged as a signal only. The
  Musixmatch-trusted `calibrate_search` path is intentionally NOT gated. New
  `lc_events`: `submission_commercial_warned`, `submission_commercial_flagged`.
- **Daily sweep agent.** `services/agents/leit_sweep.py::run_leit_sweep` scans
  LC-BORN songs (earliest `song_ingestions` row is `lyrical_charger`; chart/terminal
  trusted) new since a `system_flags` watermark (`leit_sweep.last_run_at`), excluding
  any song already in `clutter_audits`, capped at 200/run, batched 20/Opus call.
  **Classifies from METADATA ONLY (title/artist/charge_summary/prose/topics) -- LC
  never stores lyrics.** Writes `source='daily_sweep'` findings + emails a digest
  (`alerts.emit_leit_sweep_digest`, key `leit_sweep_digest`, default-on). Cron
  endpoint `POST /api/admin/agent/cron/leit-sweep` (`routers/leit_sweep.py`, auth
  `X-Reading-Cron-Key` -- reuses the daily-reading cron lane, no new secret). Host
  script `deploy/leit-sweep.sh` (suggested `30 16 * * *`, after the reading/itunes
  lane). Not yet added to the server crontab.
- **Admin queue.** Site Admin -> Lyrical Charger -> **Audit Queue**
  (`routers/clutter_admin.py`, `templates/admin/clutter.html`, section `clutter`).
  List/stats/resolve (`keep` | `remove` | `dismiss`) + a "Run sweep now" trigger
  (same orchestrator as cron). `remove` reuses `submissions_admin.delete_submission`
  (orphan-aware song deletion). Env-filtered (default prod).

## Song identity resolution + codified disposition (Phase 1, 2026-06-13)

Stops the social-discovery feeders (Spotify daily reading, youtube_trending_usa,
shazam_top200_usa, itunes_download_usa) from minting DUPLICATE Library rows when
the same song re-enters under a different title/artist string, and codifies the
per-song draft disposition once (feeder-agnostic) instead of in SOP prose. Full
spec + status: `plans and docs/RISING-COMPASS-SONG-IDENTITY-RESOLUTION.md`.

- **Identity ladder.** `song_identity.resolve_song_identity(db, title, artist) ->
  Resolution(exact|clean|new)`. Rung 1 = exact `canonical_key` (unchanged fast
  path, zero regression). Rung 2 = `canonical_key_clean`, computed after a CLOSED
  feeder-cruft cleaning pass (`services/feeder_clean.py::clean_title_artist`):
  strips MV/lyric-video bracket+trailing cruft, a leading `ARTIST - ` prefix, the
  K-pop `ARTIST 'TITLE' Official MV` quote form, and `| @channel` tails;
  reconciles `*VEVO` / `- Topic` / label channels (HYBE LABELS, ...) to the real
  primary artist. The artist suffix is cleaned BEFORE the prefix match so a
  stored `OliviaRodrigoVEVO` and an incoming `Olivia Rodrigo` clean symmetrically.
  Rungs 3/4 (pg_trgm, pgvector) are stubs that fall through to `new` (Phases 2-3).
- **Clean key column.** `songs.canonical_key_clean` (migration 122: add col +
  index + Python backfill; NOT unique -- a collision is a dupe to surface). The
  backfill LOGS collision groups (the historical dupe tail). Stored on every
  write (`song_sync.upsert_unified_song` insert + opportunistic stamp on hit).
- **Chokepoints routed through the ladder:** `upsert_unified_song` (write),
  `store_calibrated_song` (`created` flag), `calibrator.lookup_calibrated` (the
  cache rung that stops the daily awaiting-lyrics re-list of an already-calibrated
  song), `song_store.find_song_by_title_artist` (snapshot/broadcast -> song).
- **Codified disposition.** `services/disposition.py::resolve_draft_song_disposition`
  runs identity (CACHE_LINK) -> release-state (PREORDER) -> non-song (DROP_NONSONG)
  -> NEEDS_LYRICS. The NEW piece is AUTOMATIC release-state detection
  (`detect_release_state`, iTunes Search API, FAIL-OPEN to NEEDS_LYRICS on any
  uncertainty -- never swallows a real song). Wired into
  `compass_agent.run_compass_agent`: an un-cached, lyric-less, future-dated
  charting single is auto-marked `preorder` on the draft song (exempt from the
  approval gate at `agent.py:572`, excluded from aggregates, re-lists until real
  lyrics drop and a calibration clears the flag). DROP_NONSONG is NOT automatic --
  the LEIT clutter audit queue stays the human-confirmed home for non-songs.
- **Status:** BUILT local, py-compile clean, `tests/test_feeder_clean.py` 9/9,
  NOT deployed. Migration 122 applies on deploy (or a track) -- never run against
  the shared prod DB from local. Phases 2-3 (trgm fuzzy rung + merge-candidate
  audit queue mirroring `clutter_audits` + `POST /api/admin/songs/{id}/merge-into`
  to clean the historical dupe tail; then pgvector semantic rung) still pending.

## Agent mini-warehouse (2026-06-09)

The external LEIT Agent Warehouse + Mickey were decommissioned, so RC's own
autonomous agents get a home INSIDE RC admin: **Site Admin -> System -> Agents**.
This is the agent's OWN identity + health + run history + cost -- separate from
what it FINDS. **Dusty (`custodian-001`, "Custodian 001")** -- the daily clutter
sweep -- is the first resident.

- **Ledger:** `agent_runs` (migration 098, model `AgentRun`) -- one row per run,
  agent_id-keyed + generic so future RC agents share it. `run_leit_sweep(trigger)`
  opens a row at the start and closes it (ok/error + scanned/flagged/duration);
  a crash is recorded as a failed run AND re-raised (Faultline + cron alert still
  fire). Env-tagged.
- **Registry + derivation:** `services/agents/warehouse.py` holds the static
  `AGENTS` registry, the `start_run`/`finish_run` helpers, and the health/metrics
  derivation. **Health = last-run recency + status** (these are cron agents, not
  daemons -- no PM2/heartbeat to poll): healthy | overdue (no ok run in
  `overdue_hours`=36) | error | stalled (running >2h) | never_run. **Cost** is
  derived from `claude_api_usage` by the agent's `call_site` (`leit_sweep`).
- **Admin:** `routers/agents_admin.py` (`GET /api/admin/agents`,
  `/{id}`, `/{id}/runs`), page `templates/admin/agents.html` (section `agents`).
  Cards show identity + health badge + metrics (runs/success/scanned/flagged) +
  cost (all-time + 30d) + run-history table + a "Run now" button (Dusty's wired to
  the clutter `run-sweep` trigger) and a "View findings" link to the audit queue.
  Env-filtered (default prod).

## On-site subscribers + reading digest (Build 2b, 2026-06-10)

RC's OWN email-subscriber layer -- the top of RC's subscriber funnel (NOT
chadlewine; you cannot sign people up to chadlewine from RC). Separate from
`lyrical_charger_subscribers` (the LC-outage notice list). Tables: `rc_subscribers`
(migration 102) + `users.email_hash` (102) + `rc_subscribers.last_digest_key`
(103); see `RISING-COMPASS-DATABASE-SCHEMA.md` section 10. (Renumbered from
099/100 to 102/103 on deploy: migration 101 had already shipped, and the
runner applies only versions above the current max.)

- **Capture (double opt-in).** Router `routers/subscribe.py`, mounted UNAUTHED like
  `geo.router` (the POST is honeypot+Turnstile+`10/hour` protected; the GET links are
  inbox-clicked). `POST /api/subscribe` -> `pending` row + Resend confirm email;
  `GET /api/subscribe/confirm?token=` -> `confirmed` (single-use token) + a branded
  page that promotes to a Clerk account with the email prefilled
  (`/account/?mode=signup&prefill_email=...`); `GET /api/unsubscribe?token=`.
  Service `services/subscribers.py`. Frontend: drop-in `frontend/js/subscribe.js`
  card on the song page + homepage.
- **No-duplicate / promote-to-Clerk.** `users` stores NO plaintext email; the link
  key is `users.email_hash` (sha256 of the Clerk email), set fail-soft at provision
  (`clerk.ensure_user_for_clerk_id` -> `get_clerk_user_email`) and matched against
  `rc_subscribers.email_hash` both directions. Pre-existing accounts have NULL
  `email_hash` until next sign-in (optional one-time backfill).
- **Admin.** Site Admin -> Community -> **Subscribers** (`routers/subscribers_admin.py`,
  `templates/admin/subscribers.html`, section `subscribers`): counts + filterable list
  + a "Send digest now / preview" trigger.
- **Reading digest (Phase 2).** `services/subscriber_digest.py` renders the **public
  mirror of the daily admin reading email** (`agents/email_notifier.send_draft_email`):
  charge metrics + editorial + song list, minus admin affordances, plus a Lyrical
  Charger visual ad (banner `frontend/lyrical-charger/lc-splash-ad.png`, **701x158
  shown at 350x79 = a 2x/retina slot** -- displaying it at 701px CSS rendered 2x-big +
  blurry on HiDPI) and a boxed "created and managed by Chad Lewine" chadlewine link.
  Sends to confirmed subscribers via Resend, deduped per-recipient by reading date.
  Cron `POST /api/admin/agent/cron/subscriber-digest` (`X-Reading-Cron-Key`), script
  `deploy/subscriber-digest.sh` (cadence = crontab choice, weekly recommended). NOT
  yet in the server crontab.
- **Status:** Deployed (migrations 102/103 apply on deploy). Plan:
  `RISING-COMPASS-HOCKEY-STICK-PLAN.md` Build 2; session notes
  `2026-06-10b` / `2026-06-10c`.

## Social broadcaster (Hockey Stick Build 6, 2026-06-13) -- ships DARK

Automated own-account broadcast of RC's OWN verdicts (the only reach vector in the
plan; the rest is organic-search pull). Posts the objective charge of whatever is
trending TODAY + the daily-aggregate reading, to RC's own accounts, fanned out
through ONE Buffer integration. Distinct from The Lookout (reactive, human-sent
outreach): this is proactive publisher-posts-its-own-work, the most compliant
automation there is. Package `app/services/social/`, router
`routers/social_broadcast.py`, migration 121. Plan:
`RISING-COMPASS-HOCKEY-STICK-PLAN.md` Build 6.

- **Ships DARK.** `settings.social_broadcast_enabled` (default false) + Buffer
  config gate the push. Dark = the cron still selects, renders, stores the card,
  and writes the ledger as `status='dark'`, but never calls Buffer -- so the whole
  pipeline is verifiable before any account/credential exists. Go live = connect
  the accounts to Buffer, set `BUFFER_ACCESS_TOKEN` + `BUFFER_PROFILE_IDS` (JSON
  map platform->profile id), flip `SOCIAL_BROADCAST_ENABLED=true`.
- **Card render = Playwright, NOT Pillow.** The card is authored once in JS
  (`frontend/lyrical-charger/rc-charge-card-generator.js`: `RCChargeCard.render`
  for per-song, `renderReading` for the daily aggregate -- same CRT chrome / badge
  / wordmark). `frontend/cards/index.html` is the render surface: it draws either
  card from `?type=song|reading` + `?data=<urlsafe-base64 JSON>` and exposes
  `window.__cardReady` + `window.__cardPng()` (1080x1080 PNG data URL), doubling as
  a human preview + PNG download. `services/social/card_render.py` drives headless
  Chromium to that page and screenshots the canvas. The prod image already runs
  `playwright install chromium` (Dockerfile), so no image change. Local: point
  `CARD_RENDER_BASE_URL` at the dev server (e.g. http://localhost:3005).
- **Selection** (`broadcaster.run_social_broadcast`): top-N (`SOCIAL_TRENDING_COUNT`,
  default 3) trending CALIBRATED songs -- latest Shazam + YouTube `chart_snapshots`
  resolved to `songs` via `find_song_by_title_artist`, dropped if uncalibrated /
  instrumental / preorder, ranked by |charge_value| (the strongest verdict
  travels), deduped against the ledger -- PLUS the latest `DailyReading` (skipped
  if already broadcast). Corpus-hit only: no Opus spend, no auto-calibration.
- **Ledger** (migration 121): `social_cards` (the rendered PNG, served public at
  `GET /api/social/card/{token}.png` so Buffer can fetch the media; mounted
  unauthed like geo/subscribe) + `social_posts` (one row per (item, platform),
  UNIQUE `dedup_key` = `song:{id}:{platform}` | `reading:{date}:{platform}`).
  Writes are an ON CONFLICT upsert that never overwrites a live (queued/posted)
  row, so a dark row upgrades to a real post on a later configured run, and
  re-runs are idempotent. Selection treats only queued/posted as "already
  broadcast" -- dark rows stay selectable.
- **Post text = locked "Plain instrument" voice** (Build 6 decision): per-song
  `Rising Compass measured "{title}" by {artist}.` / `Charge: {+/-N} ({tier}).` /
  UTM-tagged song link; reading = the parallel one-line measurement + the
  daily-listens chart link. No editorializing (objectivity lock).
- **Admin:** Site Admin -> Community -> **Broadcasts** (`routers/social_admin.py`,
  `templates/admin/social.html`, section `social`). A LIVE/DARK config banner +
  status stat-cards, and the ledger collapsed to one card per broadcast ITEM (the
  rendered card thumbnail via the public route + the post text + a per-platform
  status strip). "Run broadcast now" calls the same orchestrator (cookie auth).
- **Cron:** `POST /api/admin/agent/cron/social-broadcast` (`X-Reading-Cron-Key`,
  reuses the reading lane -- no new secret), script `deploy/social-broadcast.sh`,
  intended droplet lane AFTER youtube (reading 08:00 -> itunes 09:00 -> shazam
  10:00 -> youtube 11:00 -> social 12:00 UTC). Not yet in the server crontab.
- **Compliance:** own-account broadcast of own corpus-hit content; no scraping,
  no DMs, no individual outreach. No terminal Anthropic calls.
