# Phase 5b cutover -- legacy-binding read-path audit (2026-06-04)

Discovered while resuming 5b: native writers emit `song_source="songs"`, but
many READ paths still bind to legacy `(song_source, song_id)` / the 4 legacy
models. The pickup listed only the writer conversions; these reads must flip too
or the cutover deploy breaks the live site for NEW content (not just at the drop).

## Severity timing
- **Breaks AT the cutover deploy (public, new content):** song detail 404,
  artist pages miss new songs, because new songs exist only in `songs`.
- **Breaks AT the 5d drop (would 500):** every direct legacy-model query.

## Public read paths (must ship in the cutover deploy)
- `routers/songs.py` -- `_resolve_song`, `_synthesize_initial_run`,
  `_find_by_generated_slug`, `song_search` GET "" (legacy model queries);
  `_get_or_create_slug` writes legacy-only slug pointers; `_enrich_with_release_context`,
  `song_history`, `song_calibration_runs` filter capture tables by legacy source.
- `routers/artists.py` -- `_resolve_song_row`/`_resolve_song`/`_get_release_song_charges`
  (legacy resolvers), `artist_top_songs`, `artist_songs`, `artist_search` raw SQL
  (count + UNION over 3 legacy tables). `_song_charges_for_artist` already unified.
- `routers/misread.py` -- `_resolve_polymorphic_song` (public submit path).
- `services/audience_vibe.py` -- `_resolve_song` + all needle/push/review-case
  reads+writes keyed on legacy source (no `unified_song_id`).
- `services/calibration_log_feed.py` -- `_lookup_song_anchor` + its 2 callers.
- `services/artist_utils.py` -- `count_songs_by_artist`, `_find_artist_songs_full`
  (read 3 legacy models); `_apply_musicbrainz_data`/`_apply_catch_all` write
  legacy ReleaseSong pointers.
- `services/agents/email_notifier.py` -- incomplete-titles lookup queries CompassSong.
- `routers/admin.py` -- command-palette search unions 3 legacy models.

## Admin tools (must ship before the 5d drop; can be a follow-up deploy)
- `routers/db_search.py` -- browse tabs (drop 4 legacy keys; `songs` already
  browsable) + reset/delete/merge `_repoint` (legacy `RESETTABLE_SOURCES`).
- `routers/prose_admin.py` -- SONG_MODELS map -> read/write prose on legacy row.
- `routers/recalibrations.py` -- `_resolve_song` + writes canonical charge back to
  legacy row + SongRecalibration/CalibrationRun legacy pointers.
- `routers/submissions_admin.py` -- reads SubmittedSong, "promote" writes LibrarySong.
  (obsolete in unified model -- candidate to retire/stub.)
- `routers/ether_audits.py` -- queries CompassSong; LANDMINE: reads `row.year` /
  `row.chart_position`, which are NOT on `Song` (moved to chart_appearances).
- `routers/artists_admin.py` -- merge/rename raw SQL over compass_songs/library_songs/
  submitted_songs + junction repoint by legacy source; refresh-aggregates resolver.

## Edge / dead
- `routers/v1_test.py` + `v1_frozen` few-shot examples query CompassSong (admin V1 tab).
- `services/agents/compass_agent_rubric.build_few_shot_examples` -- dead in v2 path.
- Dead imports to remove with the drop: calibrator.py (CompassSong),
  compass_agent.py (func, CompassSong), analyzer.py (SubmittedSong).

## Standard fix
Replace `{"compass":CompassSong,...}.get(source)` + `query(model).get(id)` with
`query(Song).get(unified_song_id)`; switch polymorphic joins/filters/writes from
`(song_source, song_id)` to `unified_song_id` / `song_source="songs"`.

## Already unified (no change)
`services/song_store.py`, `services/song_search.py`; native write+cache paths in
`analyzer.py`, `compass_agent.py`, `calibrator.py` (dead imports only);
`routers/artist_verification.py` (opaque pass-through, data-convention caveat only).
