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
  frontend lanes parallelize freely. Migration NUMBERING is no longer a collision
  risk -- the runner keys on filename, not a high-water version (see Database
  below), so two tracks can pick the same number and both apply, and you do NOT
  need to renumber a migration that another track raced ahead of. Still serialize
  the actual DDL run (one track at a time, or repoint that track's `DATABASE_URL`
  at a throwaway DB) to avoid lock contention while a migration is executing.

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
- **Migration runner = filename identity (`app/migrate.py`, 2026-06-21).** Tracked
  in `schema_migrations` (name PK), NOT by `MAX(version)`. The old high-water gate
  silently SKIPPED a genuinely-unapplied migration whenever a parallel track had
  recorded an equal-or-higher number against the shared DB (the 099/100 -> 102/103
  and the governance-drop 130 -> 134 renumbers were both this bug). Now every file
  runs exactly once keyed on its name: a reused number can't shadow another track,
  and a late-added lower number still applies. A one-time bridge imported the
  already-applied history from `schema_version` (still read for that, still stamped
  by baseline tooling) and froze the high-water mark with a `__legacy_baseline__`
  sentinel. New migrations: just use the next free number; do NOT renumber to dodge
  another track. Self-test: `python -m app.migrate_selftest` (10/10).
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
    Claude-Code-supplied calibration object. The server skips Anthropic ONLY
    for what the object carries; `--listener-effects-prose-file` is REQUIRED
    (omitting it makes `record_and_reconcile` call Anthropic to generate the
    prose). Use this for any song with no prior `songs` row. See the
    "Terminal calibration" section below.
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

## Terminal calibration (Claude Code is the model -- ZERO Anthropic)

Operator-initiated calibration in a terminal / Claude Code session (daily +
chart reading, backfill, recalibration, draft repair) makes ZERO Anthropic
calls -- not for calibration, prose, summary, ether, or editorial. The droplet's
`ANTHROPIC_API_KEY` is for live public traffic only, and the account has run
dry; Claude Code does the model's job and SUPPLIES the result, then writes it
through the live server (`calibrate_song.py` POSTs to the prod `/lyrics`
endpoint, which stores a supplied calibration with no model call).

**Canonical rubric = the saved local copy `plans and docs/LEC-RUBRIC-LIVE.md`
(NOT a per-session live pull).** LEC owns the rubric, but the live `GET /api/rubric`
text is snapshotted to that file (version-stamped in its header comment; currently
`069e4968a63c`, pulled 2026-07-07). **Read that file every session and calibrate
against it. Do NOT re-pull live each time** — the old "always pull, from-memory is
VOID" gate is retired (changed 2026-07-07 at Chad's direction). **Re-pull + re-save
ONLY when Chad says the rubric changed**, then update `rubric_version` in the file
header. To re-pull, query the service-key-gated endpoint from inside the RC backend
container, which already holds `LEC_BASE_URL` (`http://lec:8012`) + `LEC_API_KEY`:
`docker compose exec -T backend python3 -c "import os,urllib.request,json; req=urllib.request.Request(os.environ['LEC_BASE_URL']+'/api/rubric', headers={'X-Api-Key':os.environ['LEC_API_KEY']}); print(json.load(urllib.request.urlopen(req))['version'])"`
then overwrite `LEC-RUBRIC-LIVE.md` with the new `rubric_text` + bumped version.
The saved text is the full live system prompt (tiers, all 58 tenets, the live rule
set, the routes, Start-at-Zero) — calibrate against that. This repo carries ZERO
rubric/calibration code (the `agents/tenets/` mirror + the whole in-process
apparatus were REMOVED 2026-06-21). Reading the saved file, LEC files, or
`/api/rubric` is not an Anthropic call. (The immutable `lec-golden-*` snapshots
DRIFT behind the live deploy and are NOT the canonical copy — the saved
`LEC-RUBRIC-LIVE.md` is.)

**No server-side prose generation from terminal.** The terminal `/lyrics` path
(`calibrate_song.py` -> `_store_calibration` -> `record_and_reconcile`) calls
Anthropic to generate `listener_effects_prose` whenever the calibration object
does not carry it (`prose_missing`, `calibration_corpus.py`), and
`societal_effects_prose` whenever topics are supplied without the societal prose.
So Claude Code WRITES both prose pieces and supplies them:
`--listener-effects-prose-file` is REQUIRED (the script now enforces it);
`--societal-prose-file` is required whenever any ether field is supplied. **Both
proses follow the voice defined in the code constants themselves -- read
`LISTENER_EFFECTS_VOICE` / `SOCIETAL_VOICE` (in `listener_effects_prose.py` /
`societal_effects_prose.py`) FRESH each session. They are the source of truth and
get retuned, so this file does NOT restate the voice, length, or person -- take
those from the constants, never from a doc.** The server generators gate prose
through a deterministic tell-guard (A-R) + a
semantic judge fail-closed, but the terminal SUPPLY path is NOT auto-gated -- so
scan every supplied prose yourself before shipping: `cat file | .venv/Scripts/
python.exe -m app.services.prose_tell_guard [--societal] [--negative]`, clear every
`hard` finding, and self-apply the semantic judge (Claude Code IS the judge on
terminal). Ship NULL over slop.** The
ether tagger does NOT run on the terminal path, so omitting `--topic` is safe
(topics stay NULL, no API call). Editorial is supplied separately via
`set_editorial.py`; the server has no editorial-generation path at all (the
in-process rubric apparatus was removed 2026-06-21), so editorial is always
terminal-supplied.

## Public Participation (Lobby + Misread)

Two audience-facing surfaces live alongside the dashboard. (Governance --
Motion Desk, the Deliberation Chamber, and the amendment pipeline -- MOVED OFF
Rising Compass to the Libra Engine Compass legislature at lecg.libraengine.com;
RC's governance routers + tables `motions` / `motion_arguments` / `rubric_changes`
were REMOVED + DROPPED. RC is the music/lyric lens that consumes LEC; it does not
own the rubric or the law.)

**Lobby** (Phase 1) -- Reddit-style threaded comments on song / artist
pages. Email-verified Tier 1 account required to post; anonymous read
allowed. Auto-hide after 3 reports. Tables: `users`, `comments`,
`comment_reports`, `moderation_events`, `admin_alert_prefs`.

**Misread Reports** (Phase 2) -- per-song "the agent got this wrong"
flag. Tier 1 gated. Admin can spawn a recalibration directly from the
queue. Migration 057 added `user_id` FK on `misread_submissions`.

### Auth (Clerk-backed Tier 1, Stripe Identity Tier 2)

**Tier 1:** Clerk email account + claimed handle. Provisioned lazily
on first authenticated API call via `require_clerk_user` in
`backend/app/auth.py`. Onboarding flow in `frontend/account/`.

**Tier 2:** Real ID via Stripe Identity. Webhook at
`/api/stripe-identity-webhook` flips `users.tier` to `id_verified`.
Tier 1 is enough for Lobby + Misread. (Tier 2 originally gated filing
motions; governance has since moved off RC to the LEC legislature, so RC
no longer hosts a Tier-2-gated motion form.)

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

### Deliberation venue aesthetic (moved to LEC legislature)

The cream/brown deliberation palette (Cardo + Cormorant SC) and the dark
constitution/tenets surface moved off RC with governance -- they now live on the
LEC legislature (lecg.libraengine.com) + the LEC instrument (lec.libraengine.com).
The guiding line stands where it lives now: "the constitution and the room where
the constitution is argued over should not look the same." RC no longer ships a
Motion Desk or its palette.

## Ether taxonomy editor (Site Admin -> Calibration -> Taxonomy, 2026-06-16)

Edit the Ether theme/topic hierarchy AND the tagger definitions from admin, no
redeploy. Two new tables: `ether_themes` (slug/label/sort_order) + `ether_topics`
(slug/label/primary_theme_slug/secondary_themes JSON/sort_order, plus Phase-2a
`scope` TEXT + `examples` JSON). Migrations 128 + 129. Models in `models.py`.
Spec: `RISING-COMPASS-TAXONOMY-EDITOR-SCOPE.md`.

- **Resolver (`services/ether_taxonomy.py`).** `topic_hierarchy(db)` /
  `themes(db)` / `topics(db)` and `valid_slugs(db)` / `taxonomy_for_prompt(db)`
  prefer DB rows, FALL BACK to the code constants (`ETHER_TAXONOMY` etc.) when
  the tables are empty / unreachable / the flag is off. Module-level cache (30s
  TTL) busted on every write via `bust_taxonomy_cache()`. The no-arg forms still
  return code (import-time / terminal callers). `_code_taxonomy_for_prompt()` is
  kept byte-identical so flipping the DB flag does not drift the prompt.
- **Seed + backfill (startup, idempotent).** `seed_taxonomy_if_empty(db)` seeds
  both tables from the code constants when empty; `backfill_topic_definitions(db)`
  fills scope/examples on pre-2a rows. Never overwrites admin edits.
- **Admin API + UI.** `routers/ether_taxonomy_admin.py` (cookie auth, site-admin),
  prefix `/api/admin/taxonomy`: GET (themes + topics grouped + `has_definition`),
  theme + topic CRUD, `/reorder`, `/tagger-source`. Section `taxonomy`
  (`templates/admin/taxonomy.html`); kept OUT of `API_ADMIN_SECTIONS`. Validation
  mirrors the code asserts: kebab/unique slugs, exactly-one existing primary,
  secondary valid and != primary, theme-delete guard. **Topic-slug rename is
  disabled** (songs.topics JSON stores slugs -- a rename orphans history, a
  Phase-2b alias-migration concern); theme-slug rename rewrites referencing rows
  in-txn.
- **Phase 1 (presentation) is unconditional**: `routers/topic_trends.py` reads
  `topic_hierarchy(db)` (response shape unchanged). **Phase 2a (DB drives the
  tagger) is FLAG-GATED**: `system_flags` `taxonomy_db_driven.enabled`
  (fail-CLOSED, in `feature_flags.py`). When ON, `ether_tagger.py` +
  `album_synthesis.py` build their prompt + valid-slug set from the DB (each
  resolves via its OWN short-lived `SessionLocal` -- tag_song runs in a worker
  thread, so it must not touch the request Session), fail-safe to code. Flip it
  from the admin "Tagger source" toggle. **Currently OFF in prod** (tagger reads
  code). Terminal scripts (`calibrate_song.py`/`backfill_album.py`) still
  validate against the code set by design.
- **Phase 2b (NOT built):** topic-slug rename/remove + an `ether_topic_aliases`
  map honored by the rollups + a server-side retag tool. Until then the slug is
  immutable.

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
- **Status:** DEPLOYED to prod (migration 105 applied; `artist_outreach`
  verified present 2026-07-11). Never run migrations against the shared prod DB
  locally. Send is
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

> **HOMEPAGE SECONDARY SLOT NOW SHOWS NEW MUSIC FRIDAY (2026-06-19).**
> `renderItunesPanel()` was repointed from `itunes` to `new-music-friday`
> (fetch source + labels only; element ids stay `itunes-*`, the function name is
> unchanged). The iTunes Download Chart still exists as a standalone `/charts/itunes/`
> page + the Calendar Daily Downloads toggle -- only the homepage panel swapped.
> The section below still describes the iTunes chart mechanism, which is intact.
> NOTE: `chart-shell.js` / `app.js` / chart `page.js` are Cloudflare-edge-cached
> (4h TTL); any edit to those needs a `?v=` bump on the script tag (currently
> `?v=20260619b`) or the edge serves stale.

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

  **Editorial is TERMINAL-SUPPLIED (server has NO generation path, 2026-06-21).**
  The editorial used to be the one server-side Anthropic call left in the daily/
  chart reading pipeline (calibration is cache-hits + terminal; the ether tagger
  does not run on the terminal path, and prose is skipped only when Claude Code
  SUPPLIES it -- `calibrate_song.py` requires the listener prose file so the
  `record_and_reconcile` hook never calls Anthropic; see "Terminal calibration").
  The Decoupling removed RC's in-process rubric apparatus entirely
  (`compass_agent_rubric` / `rubric_builder` / `agents/tenets/`), so
  `_generate_editorial` is now a permanent None-returning stub -- NEITHER
  draft-creation NOR approval ever generates an editorial, and the
  `editorial_terminal_only` flag is gone (there is nothing left to gate). Claude
  Code writes the editorial during the reading calibration session and supplies it
  via `POST /api/admin/agent/drafts/{ref}/editorial` (`scripts/set_editorial.py`,
  lyrics-supply key -- the same lane `calibrate_song.py` uses). Approval does not
  regenerate: `_generate_editorial` returns None and the approval regen fail-softs
  (keeps the existing editorial on None), so the terminal-supplied editorial
  publishes. **SOP:** calibrate every song -> `python scripts/set_editorial.py
  <draft_ref> --editorial "..."` -> approve. Restoring a server-side editorial would
  now require re-introducing a rubric source (LEC owns it); the dry-account
  workaround is permanent design, not a flag. (History: approval USED to ALWAYS
  regenerate over the full calibrated set, 2026-06-13; then gated behind
  `EDITORIAL_TERMINAL_ONLY`, 2026-06-19; then the generation path was removed
  outright, 2026-06-21.)

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
  answered there and routes them to the Misread report (song page). (It used to
  also point framework questions at RC's Motion Desk; governance moved off RC to
  the LEC legislature at lecg.libraengine.com, so that route is gone.)

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
  (misread/satirical), `payments` (billing + `credit_ledger`), and `activity`
  (unified reverse-chron timeline merging all of the above + verifications; pulls
  <=300/source, merges, slices). (The former `motions` endpoint / Motions tab --
  filed motions + chamber arguments -- went away when governance moved off RC and
  the `motions` / `motion_arguments` tables were dropped.)
- `templates/admin/user_detail.html` -- tabs Profile | Activity | Calibrations |
  Submissions | Payments | Comments (Activity default), plus a
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
  script `deploy/leit-sweep.sh`, LIVE in the server crontab at `0 17 * * *`
  (after the reading/itunes lane; verified 2026-07-11).
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
- **Phase 1 status:** DEPLOYED 2026-06-13 (migration 122 applied, schema at 122).
  The backfill surfaced one real dupe pair (ids 2778/3293).

### Phase 2 (merge queue + song-merge endpoint + trgm rung, 2026-06-13)

- **Migration 123** -- `song_merge_candidates` (the human-audit queue, mirrors
  `clutter_audits`: one OPEN row per pair, env-tagged, partial-unique) +
  `song_merge_events` (permanent merge audit log, mirrors `artist_admin_events`)
  + `pg_trgm` extension and a GIN trigram index on `canonical_key_clean`
  (created in SAVEPOINTs, FAIL-SOFT -- a privilege error can't brick startup,
  the trgm rung ships dark anyway). Backfills migration-122's clean-key
  collisions into the queue as OPEN candidates (reason='clean_collision').
- **Song-merge service** (`services/song_merge.py::merge_songs`) -- DESTRUCTIVE,
  raw-SQL over a passed connection (caller owns commit), mirrors the artist-merge
  Step 0. Repoints all 26 song-referencing tables (7 with UNIQUE constraints get
  dedup-first: user_calibrations, song_artists, audience_vibe_needles/pushes,
  artist_verification_blocks, chart_appearances, release_songs; song_ingestions
  deduped on method), preserves the richer calibration (fills a stub target from
  a calibrated source), writes a `song_merge_events` row, deletes the source.
  `prose_provenance_anchors` is intentionally NOT repointed (re-attributing a
  hash would corrupt the provenance recipe). NEVER auto-merges.
- **Admin API** (`routers/song_merge_admin.py`): `GET /song-merge-candidates`
  (+`/stats`), `POST /song-merge-candidates/{id}/resolve {action:
  merge|keep_separate|dismiss, keep_id}`, `POST /songs/{id}/merge-into
  {target_id}` (direct, the artist-merge analog), `GET /song-merge-events`.
  Resolving/merging supersedes other open candidates for the dropped song BEFORE
  the delete (the FK SET NULL would otherwise hide them). Section **Song Merge**
  (`templates/admin/song_merge.html`, nav under Lyrical Charger, section
  `song-merge`); two-song pair view, direction-explicit merge buttons.
- **Rung 3 (trgm fuzzy)** in `resolve_song_identity` -- SHIPS DARK behind
  `system_flags` `identity_trgm.enabled` (fail-closed, `feature_flags.
  is_identity_trgm_enabled`). On a clean-key miss it scores title+artist
  similarity off the clean key's two parts: BOTH >= ~0.9 auto-links
  (via='trgm'); the gray band returns `candidates` and the write chokepoint
  (`upsert_unified_song`) queues each as a `reason='trgm'` merge candidate (never
  auto-merges). Fully fail-soft (non-PG / no-extension / flag-off -> exact+clean
  behavior unchanged). Uses `similarity() >=` not the `%` operator (psycopg
  escaping).
- **Status:** DEPLOYED to prod (migration 123 applied; `song_merge_candidates`
  + `song_merge_events` verified present 2026-07-11). `tests/test_song_merge.py`
  + `tests/test_feeder_clean.py` green, merge verified end-to-end. Phase 3
  (pgvector semantic rung, shared with the semantic-search roadmap) still pending.

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
(103); see `RISING-COMPASS-DATABASE-SCHEMA.md` section 10. (Historically renumbered
from 099/100 to 102/103 on deploy because migration 101 had already shipped and the
old runner skipped any version <= the current max. That renumber-to-dodge dance is
no longer needed -- the runner now keys on filename, see Database > Migration
runner.)

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
- **Notification preferences (2026-06-14, mirrors chadlewine).** Three opt-out
  category toggles on `rc_subscribers` (migration 127, all `DEFAULT true` so
  existing confirmed subscribers stay on readings + are opted into the two new
  ones): `pref_daily_reading` (the digest), `pref_moments_of_notice` (spikes /
  anomalies / notable shifts), `pref_config_updates` (features / framework /
  version launches). Single source of truth = `subscribers.NOTIFY_CATEGORIES`
  (`key`/`col`/`label`/`desc`/`broadcast`); add a category there + a column and
  every surface picks it up. `prefs_dict` serializes a row as `{key: bool}`.
  - **Preference center (tokenized, no login).** Reuses the stable
    `unsubscribe_token` as the manage key (came from the subscriber's own inbox).
    `GET/POST /api/subscribe/preferences` (added to the UNAUTHED `subscribe.py`
    router; no-leak: bad token -> `found:false` / `ok:true`). Frontend page
    `frontend/subscribe/preferences/` (toggle per category + master
    unsubscribe/resubscribe, saves on change). Manage link added to the confirm +
    digest email footers.
  - **Digest gating.** `subscriber_digest.send_digest` now also filters
    `pref_daily_reading IS TRUE`.
  - **Admin category broadcast.** `services/subscriber_broadcast.py` +
    `POST /api/admin/subscribers/broadcast` (cookie auth, `dry_run`): a one-off
    plain-text note (rendered into the branded shell) to confirmed subscribers
    opted into a `BROADCAST_KEYS` category (moments_of_notice / config_updates;
    daily_reading has its own digest). NOT deduped -- preview the count first.
    Composer + per-row D/M/U preference chips on the Subscribers admin page.
- **Status:** Build 2b (capture + digest) deployed (102/103). Preferences pass
  deployed 2026-06-14 (migration 127 applies on deploy). Plan:
  `RISING-COMPASS-HOCKEY-STICK-PLAN.md` Build 2; session notes
  `2026-06-10b` / `2026-06-10c` / `2026-06-14`.

## Social broadcaster (Hockey Stick Build 6, reoriented 2026-06-17) -- LIVE

Automated own-account broadcast of RC's OWN daily-chart readings (the only reach
vector in the plan; the rest is organic-search pull). The most compliant
automation there is: proactive publisher-posts-its-own-work, no scraping/DMs/
individual outreach, no terminal Anthropic. Package `app/services/social/`,
routers `social_broadcast.py` (cron + public card) + `social_admin.py` (admin),
migration 121. Plan: `RISING-COMPASS-HOCKEY-STICK-PLAN.md` Build 6. Full operator +
code reference: `RISING-COMPASS-SOCIAL-BROADCASTER.md`.

**Reorientation (2026-06-17):** the original "top-3 trending songs + reading"
model was dropped. The automated job is now the **daily charts only**; individual
songs are posted by hand, off-platform. The classic Buffer REST API turned out to
be closed to new accounts, so the client was rewritten to Buffer's GraphQL API.

- **What it posts.** The day's card-ready daily charts, Daily Listens first:
  Daily Listens (from the latest `DailyReading`, always the anchor -- no reading,
  no run) plus Daily Downloads / Shazam / YouTube **only when published with
  aggregates** (`ChartSnapshot.published` + `compass_degree` stamped at approval;
  unapproved charts are gracefully skipped). `broadcaster._gather_charts`.
- **Routing.** `CAROUSEL_PLATFORMS = (instagram, tiktok)` get a **carousel** of
  all card-ready charts; every other platform (x/bluesky/threads/facebook) gets
  the **single Daily Listens** card. `_platforms()` = configured channels.
- **Buffer = GraphQL** (`services/social/buffer_client.py`). Targets
  `https://api.buffer.com` (the classic `api.bufferapp.com/1` REST API is closed
  to new accounts). Auth = a **personal API key** as `Authorization: Bearer`.
  `post_items(items)` sends ONE `createPost` mutation per platform with
  `schedulingType: automatic`, `mode: shareNow` (publish now), and an ordered
  `assets:[{image:{url}}]` list (one image = single, several = carousel). Returns
  `{posted:{platform:id}, errors:{platform:msg}}` -- per-platform failures are
  collected, not fatal. **Per-service metadata** via `_platform_metadata`:
  Instagram REQUIRES `{instagram:{type:"post", shouldShareToFeed:true}}`; other
  services send none (TikTok's photo-post metadata is unverified -- confirm at
  first TikTok run, same way IG needed type/shouldShareToFeed).
- **Card render = Playwright** screenshot of `frontend/cards/index.html`
  (`?type=reading|song&data=<urlsafe-b64>&ratio=square|portrait`, exposes
  `window.__cardReady`/`__cardPng()`). `services/social/card_render.py` drives
  headless Chromium (prod image runs `playwright install chromium`). The
  broadcaster renders one card per chart (kicker = DAILY LISTENS / DAILY DOWNLOADS
  / SHAZAM / YOUTUBE) and **commits the `social_cards` row BEFORE pushing** so the
  public card URL is fetchable by Buffer cross-session (an uncommitted row 404s ->
  image fetch fails). The backend passes no `ratio`, so broadcast renders at the
  `portrait` default.
- **ONE master card generator (consolidated 2026-06-18).**
  `frontend/cards/rc-charge-card-generator.js` (`window.RCChargeCard`) is the
  SINGLE generator for the whole site: the broadcaster AND the public song-page /
  Lyrical Charger share card. The old public file
  (`frontend/lyrical-charger/rc-charge-card-generator.js`) and the broadcast fork
  (`rc-charge-card-generator-social.js`) are DELETED. It exposes `render` (song
  card), `renderReading` (daily/chart card), `shareOrDownload`, `_pick`,
  `_pickReading`.
  - **Ratio:** `opts.ratio` = `'square'` (1080x1080) | `'portrait'` (1080x1440);
    `opts.height` (number) overrides; default `portrait`. Broadcast uses portrait;
    the public song card defaults `square`.
  - **Public ratio toggle (Square 1:1 / Instagram 3:4):** on the song page
    (`#charge-card-ratio` in `songs/song.html`, wired in `songs/songs.js`) and the
    Lyrical Charger result modal (`#cc-ratio` in `lyrical-charger/index.html`,
    wired in `charger.js` -- re-renders the preview live). Both surfaces render
    SONG cards, so the toggle only ever drives `render()` (not the reading card).
  - **Design (new):** the tier badge is a **tier-colored rounded box** with the
    charge score (large) over the tier label -- it replaced the old compass-gauge
    tile; text flips dark/light by tier luminance (`textOn`). Song card: title +
    charge box share a top row dropped to `P+99`, deadpan + summary below
    (tighter on square), auto-shrinking summary (`fitText` 44->32 / 54->38), and a
    **centered enlarged footer** (#topics / wordmark / url). Reading card: kicker
    top-left, charge box **to the right of the date** (vertically centered, top-
    right corner left EMPTY so IG's carousel icon never overlaps), editorial
    (38px, up to 7 lines), top-5 song list, centered enlarged footer. Flush phosphor
    border. All loaders carry a `?v=` cache-bust (`song.html`,
    `lyrical-charger/index.html`, `cards/index.html`).
- **Captions: objective, data-only, per channel** (`broadcaster`). Carousel
  (IG/TikTok): date header + one line per published chart (`Daily Listens: -14
  (Degraded)`) + "Full readings: link in bio." + hashtags. Single Daily Listens
  (clickable-link platforms): the measurement line + `N measured, M contaminated`
  + a UTM `/charts/daily-listens/` link (+ `#RisingCompass` on x/bluesky). No
  editorializing.
- **Ledger** (migration 121): `social_cards` (rendered PNG, served public at
  `GET /api/social/card/{token}.png`, mounted unauthed) + `social_posts` (one row
  per platform). `dedup_key` per date: `charts:{date}:{platform}` (carousel) |
  `reading:{date}:{platform}` (single). ON CONFLICT upsert never overwrites a live
  (queued/posted) row; a same-day re-run is a no-op. Per-platform `status`
  posted/error/dark. (A carousel row stores the Daily Listens card as its
  `card_token` thumbnail; the multiple images are assembled at post time.)
- **Trigger: MANUAL, no cron.** Chad approves the day's charts, then Site Admin ->
  Community -> **Broadcasts** -> "Run broadcast now" (`social_admin`, cookie auth).
  The cron endpoint `POST /api/admin/agent/cron/social-broadcast`
  (`X-Reading-Cron-Key`) still exists and is reused for manual server triggers,
  but `deploy/social-broadcast.sh` is intentionally NOT in the crontab.
- **Cloudflare gotcha (important).** `risingcompass.net` is behind Cloudflare,
  which edge-caches the card JS and throws a managed challenge at `/cards/`. Two
  zone rules fix it: a **Cache Rule** (bypass cache) and a **WAF Skip rule** for
  `starts_with(path,"/cards/") or path eq "/lyrical-charger/rc-charge-card-
  generator.js"`. The master generator lives UNDER `/cards/` so the bypass covers
  it -- otherwise the edge serves a stale card and design edits never appear.
  (The `/lyrical-charger/rc-charge-card-generator.js` clause is now vestigial --
  that file was deleted in the 2026-06-18 consolidation -- but harmless; leave it.)
  Headless Chromium passes the managed challenge, so renders work regardless.
- **Env (prod `.env` + docker-compose passthrough):** `SOCIAL_BROADCAST_ENABLED=
  true`, `BUFFER_ACCESS_TOKEN` (personal GraphQL key), `BUFFER_PROFILE_IDS` (JSON
  platform->Buffer CHANNEL id), `BUFFER_API_BASE` (default `https://api.buffer.com`),
  `CARD_RENDER_BASE_URL` / `SOCIAL_LINK_BASE` (default `https://risingcompass.net`).
  Get channel ids by querying the Buffer GraphQL `account{organizations{id}}` then
  `channels(input:{organizationId})`.
- **Status:** LIVE 2026-06-17 on **X + Instagram** (the two connected channels).
  Bluesky / Threads / TikTok / Facebook not yet connected to Buffer -- add their
  channel ids to `BUFFER_PROFILE_IDS` when connected (carousel auto-extends to
  TikTok; the rest take the single Daily Listens).

- **Single-song one-click publish (2026-06-18).** Manual path to publish ONE
  calibrated song's charge card, separate from the daily machine.
  `broadcaster.publish_song(song_id, platforms=None, force=False)` reuses the
  render -> commit-card -> push -> ledger spine but `scope='song'`, dedup key
  `song:{id}:{platform}` (NO date -> posts once per channel unless `force`). Renders
  the `type=song` card; refuses uncalibrated songs; dark path identical. Endpoints
  in `social_admin.py` (cookie auth): `GET /api/admin/social/config`,
  `GET /api/admin/social/song-search?q=` (calibrated only, charge-DESC, cap 12),
  `POST /api/admin/social/publish-song {song_id, platforms?, force?}`. Two UIs:
  a "Broadcast to Buffer" card on `templates/admin/song_detail.html` and a "Publish
  a single song" search panel atop `templates/admin/social.html`. **DEPLOYED to prod
  (`publish-song` route verified in the running `social_admin.py` 2026-07-11).** Full reference:
  `RISING-COMPASS-SOCIAL-BROADCASTER.md` "Single-song publish".

## Sentinel Auditor Team (DEPLOYED DARK 2026-06-22)

A mission-driven red-team program: instead of defending RC's reputation from people
hunting for holes in its results/algorithm, RC invites them in. Vetted outsiders apply,
get approved, and file findings (inconsistencies, algorithm/methodology holes, data
errors, suggestions); Chad triages each one. **NOT gamified** -- no score, no rank, no
leaderboard, no payout; it is for people who care whether the readings are right. **Ships
DARK** behind the fail-closed flag `sentinel_auditor.enabled` -- apply/me/findings 503 and
the portal renders closed until an admin flips it; the admin triage side + the notify-me
waitlist work while dark. Full spec: `RISING-COMPASS-SENTINEL-AUDITOR-SCOPE.md`.

- **Locked design:** apply + admin approve; NOT gamified (the only auditor-facing metric is
  a plain contribution record, findings filed / confirmed); findings are structured --
  `scope='song'` (FK songs) OR `scope='general'` with a category enum
  (algorithm/methodology/data/ux/other), an auditor-proposed severity the admin can override.
- **Tables (migrations 136 + 137, models in `models.py`):** `sentinel_auditors` (one row per
  applying user, `user_id` UNIQUE, status pending|approved|rejected|revoked; mirrors the
  artist_verifications funnel) + `sentinel_findings` (auditor_id, song_id SET NULL, scope,
  category, title, description, evidence_url, proposed/accepted_severity, status,
  disposition, points_awarded, `environment` local|prod) + `sentinel_waitlist` (email unique,
  created_at, notified_at -- the notify-me list, mirrors `lyrical_charger_subscribers`).
  `contribution()` (`{filed, confirmed}`) is the auditor-facing metric. `points_awarded` is
  kept ONLY as an internal admin severity weight (stamped on entry to `accepted` from
  accepted_severity/proposed `1/3/8/20`, zeroed on reopen); never shown to users. NO
  leaderboard / tiers (removed in the de-gamification).
- **Lifecycle (mirrors faultline_triage, in `services/sentinel.py`):**
  `new -> triaged -> investigating -> confirmed -> fixed -> accepted`, plus terminal
  `rejected|duplicate|wont_fix`; active -> any active/terminal, terminal -> reopen to
  {triaged,investigating} only, unknown status -> 400.
- **Backend:** flag accessors in `feature_flags.py`; shared logic `services/sentinel.py`
  (no HTTP/auth); waitlist dispatcher `services/sentinel_waitlist_notifier.py` (Resend,
  mirrors `lc_subscriber_notifier`); public router `routers/sentinel.py` (prefix
  `/api/sentinel`, mounted BARE, self-auths via `require_clerk_user`, reuses
  `analyzer.limiter` + bot check; `/config` + `POST /waitlist` stay open while dark, the rest
  503; posting findings needs a claimed handle); admin router `routers/sentinel_admin.py`
  (prefix `/api/admin/sentinel`, cookie auth, NOT flag-gated, applications review + findings
  triage + severity + flag toggle + `GET /waitlist` & `POST /waitlist/notify`). Both
  registered in `main.py`. Findings carry `environment=settings.environment`; the admin
  findings queue defaults to `prod` (local-dev shares the tunnel DB -- filter to Local).
- **Admin UI:** Site Admin -> Community -> **Sentinel Auditors** (`templates/admin/sentinel.html`,
  section `sentinel`, kept OUT of `API_ADMIN_SECTIONS`). Flag toggle + waitlist count/notify
  bar + Applications/Findings subtabs.
- **Frontend:** `frontend/sentinel/` (intake landing `index.html` + `portal/`), vanilla JS,
  `rc-elevated`, config-gated. The landing is an RC-idiom intake (deadpan callout +
  charge-tier spectrum + an "Auditor intake" card); copy is ALWAYS visible and the card
  adapts: dark -> a notify-me **waitlist** email form (`POST /api/sentinel/waitlist`); live
  signed-out -> sign-in; live signed-in -> the application form. Portal shows a plain
  contribution panel (filed/confirmed). The portal song picker calls `/api/songs/search`
  which returns `{items:[...]}` (NOT `{results}` -- that bit the first build). Footer link
  **"Become an Auditor" -> /sentinel/ is LIVE** (Participate column). NO leaderboard page.
- **Go-live (separate step):** flip `POST /api/admin/sentinel/flag/toggle {"enabled":true}`
  (opens apply + portal), then add `/sentinel/` to `sitemap.xml` (remove the `sentinel` entry
  from `generate-sitemap.py`'s EXCLUDED_DIR_NAMES). **Pre-launch TODO:** wire a visible
  Turnstile widget onto the apply/finding forms (honeypot + rate limits are already active).
- **Verified:** Playwright + throwaway Postgres (dark gating, admin flag toggle + waitlist,
  real Clerk apply->approve->file->triage->accept, contribution counts, reopen-zeroes-points,
  re-dark). Prod confirmed live-but-dark (config `{enabled:false}`, `/me` 401, `/leaderboard`
  404, `/waitlist` POST-only).

## Psyche Facts family (per-song metadata, storage build 2026-07-10)

The "Drug Facts" prescription label per song. `songs.psyche_facts` (migration 138)
is a JSON-encoded **Text** column (RC's convention for per-song JSON bundles, same
as `topics`/`topic_audit`/`activations`; the badge `_parse_json` decodes it) holding
the sibling keys: `purpose`, `indicated_for[]`, `do_not_use_if`, `directions`,
`onset`, `duration`, `warning`. The `psyche_effects` tag axis (a felt-effect
vocabulary, sibling to `topics`) joins this family LATER, once its vocabulary is
re-derived against a corpus sample (the chadlewine 20-term set is catalog-specific).

- **Origin.** The panel lives on chadlewine (`SongLabel.tsx`, `songs.label_meta`
  jsonb). The expansion moves generation INTO RC so every ingested song carries the
  full label and RC becomes the single source; chadlewine renders it from the badge.
- **STORED, not generated.** The storage + write path exist; GENERATION does not.
  - **Terminal: operator-supplied only.** `calibrate_song.py --psyche-facts-file
    <json>` (allowlist-cleaned to the 7 keys; Claude Code authors the bundle, ZERO
    Anthropic). Omit the flag -> `psyche_facts` stays NULL. There is NO
    auto-generation on the terminal path.
  - **Public Lyrical Charger: NOT wired.** No server-side generator yet, so public
    runs leave `psyche_facts` NULL. Planned: a synthesis module composing the bundle
    from the already-generated per-song fields (charge/summary/topics/deadpan/
    listener+societal prose), NOT a fresh lyric read (cheap, lyric-free so it clears
    the verbatim-lyric guards), plus adding `psyche_facts` to
    `analyzer._song_persist_fields`.
- **Write path.** `TerminalCalibrationIn.psyche_facts` (schemas.py) ->
  `_compose_terminal_calibration` (passes it through) -> `_store_calibration` ->
  `store_calibrated_song` -> `song_sync.calibration_to_columns` (json.dumps'd; in
  `_CALIB`; str-passthrough so it never double-wraps; None-safe so a re-read with
  `only_set_present` never nulls it) -> `songs.psyche_facts`. Badge
  `_find_calibration` returns it decoded.
- **Effects sections.** The label's two effects sections render the PLAIN
  `listener_effects_prose` / `societal_effects_prose` in full, labeled "Listener
  Effects" / "Societal Effects". The distilled `effects[]`/`at_scale[]` bullets are
  RETIRED (the CLI allowlist drops them).
- **chadlewine retroactive (NOT done).** Relabel SongLabel's two sections, retire the
  `effects[]`/`at_scale[]` replacement path, read `psyche_facts` from the badge
  instead of authoring via `psyche-label.ts`; wire `psyche_facts` into
  `chadlewine_webhook.push_song_classification`.
- **First song through the path:** I Just Got Mad by Malcolm Todd (`songs.id 3913`,
  green/+8), written into the NMF draft with the full 7-key bundle. Session record:
  `plans and docs/session notes/2026-07-10b - Psyche Facts Family + Storage Build.md`.
