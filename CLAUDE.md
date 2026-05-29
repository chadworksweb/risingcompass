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

## Database

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
    call. Use this for any song with no prior `compass_songs` row.
  - `backend/scripts/correct_song.py` — override of an already-calibrated
    song. Only mirrors to `compass_songs` if `agent_draft_songs.compass_song_id`
    is already set; do not use for fresh songs.
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

## Album Charger (Lyrical Charger tab)

A second top-level tab in the Lyrical Charger frontend (`frontend/lyrical-charger/`,
`Song Charger` default + `Album Charger`). Charges a whole album by calibrating
each track and aggregating.

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

### Required env (M2, both local and prod)

- `STRIPE_BILLING_WEBHOOK_SECRET` -- billing webhook signing secret (distinct).
- `STRIPE_PRICE_PLUS`, `STRIPE_PRICE_PRO` -- subscription Stripe Price IDs.
- `STRIPE_PRICE_PACK_25`, `STRIPE_PRICE_PACK_100`, `STRIPE_PRICE_PACK_300` --
  one-time credit pack Stripe Price IDs.
- `STRIPE_BILLING_RETURN_URL` -- fallback success URL (caller usually overrides).

Unset price IDs return 503 from the matching checkout endpoint without
breaking the rest of `/api/billing/*`. All env passthroughs are in
`docker-compose.yml` under the `backend` service.

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
