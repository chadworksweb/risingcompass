# RC Tracks: Parallel Agent Workflow

A track is an isolated work lane for Rising Compass. Each one is its own git
worktree on its own branch, with its own env files, its own `.venv`, and its own
pair of ports. Several agents (or terminals) can run a full local stack at the
same time without colliding.

Script: `tracks/rc-track.ps1` (in the main repo).
Worktrees and registry live in a sibling folder: `Local Sites/rc-tracks/`.

## Mental model

```
Local Sites/
  rising-compass/          main repo, branch master, ports 8000 / 3005
  rc-tracks/
    registry.json          local-only map: track -> slot, ports, branch
    calibrator-v4/         worktree, branch track/calibrator-v4, ports 8010 / 3015
    admin-charts/          worktree, branch track/admin-charts, ports 8020 / 3025
```

One track = one branch = one worktree = one agent's lane. Agents never share a
working directory, so file edits never clash. Merging happens through git, on
your terms.

## Commands

Run from the main repo (the script finds everything via its own location):

```
pwsh tracks/rc-track.ps1 new   <name> [-From master]   create a track
pwsh tracks/rc-track.ps1 start <name>                  launch its backend + frontend
pwsh tracks/rc-track.ps1 stop  <name>                  kill its backend + frontend
pwsh tracks/rc-track.ps1 sync  <name>                  rebase the track onto origin/master
pwsh tracks/rc-track.ps1 list                          show tracks + git worktrees
pwsh tracks/rc-track.ps1 ports                         show the slot -> port table
pwsh tracks/rc-track.ps1 viz                           open the live visualizer dashboard
pwsh tracks/rc-track.ps1 remove <name> [-DeleteBranch] [-Force]
```

`<name>` is kebab-case. The branch is always `track/<name>`.

## Visualizer

A live dashboard for watching every lane at once. Run `pwsh tracks/rc-track.ps1
viz` (or the Desktop shortcut "RC Track Visualizer", or
`node tracks/visualizer/server.js` directly), then open
`http://127.0.0.1:4310`.

It is a zero-dependency local Node server (no npm install, no build step) that
reads `rc-tracks/registry.json`, `netstat`, and per-worktree `git` state. Each
track and main shows as a module card, refreshed every 2s: stack up/down (backend
+ frontend ports listening), branch, dirty working tree, ahead/behind
origin/master, and the last commit. It is read-only -- it never touches the
worktrees or the database. Lives in `tracks/visualizer/`, so it travels with the
repo like this script.

## Starting a parallel agent on a track

1. Create the track:
   `pwsh tracks/rc-track.ps1 new calibrator-v4`
2. Open a new Claude Code session (new terminal window) with its working
   directory set to the printed worktree path, for example
   `C:\Users\chad\Local Sites\rc-tracks\calibrator-v4`.
3. Give that agent its task. It commits to `track/calibrator-v4` and never
   touches any other lane.
4. Repeat for each parallel lane. Keep it to a few at once (matches the max-3
   parallel-agents preference).

Each session is a real, separate agent with its own context. They do not see
each other's uncommitted work, which is the point.

## Running stacks side by side

`start` opens two windows for the track on its assigned ports and wires the
frontend proxy to that track's backend automatically:

```
slot 1: backend 8010  frontend 3015
slot 2: backend 8020  frontend 3025
slot 3: backend 8030  frontend 3035
```

So you can have main on 8000/3005 and two tracks on 8010/3015 and 8020/3025 all
live at once. `stop <name>` frees those ports.

Manual run (what `start` does under the hood), from inside a worktree:

```
cd backend
.\.venv\Scripts\uvicorn app.main:app --port 8010

cd ..\frontend
python scripts/dev_server.py --port 3015 --backend http://127.0.0.1:8010
```

Backend does not auto-reload. Restart uvicorn after backend code changes.

## The one shared resource: the database

The database is REMOTE (DO Postgres via `DATABASE_URL`) and SHARED by every
track and by main. Worktrees isolate code, not data. So:

- Pure code or frontend work runs in parallel safely. Go wide.
- Migration NUMBERING is no longer a collision risk. The runner (`app/migrate.py`)
  keys on FILENAME via a `schema_migrations` ledger (name PK), NOT on a high-water
  `MAX(version)` gate. So two tracks can pick the SAME next number and BOTH apply
  (each recorded by name), and a late-added lower number still runs. Just take the
  next free number on each track; do NOT renumber a migration because another track
  raced ahead of it. (The old "renumber on collision" / "version <= max gets
  skipped" dance is OBSOLETE -- that skip bug is exactly what the filename runner
  fixed.)
- Still SERIALIZE the actual DDL run: run schema/migration work on ONE track at a
  time, or point a track's `backend/.env` `DATABASE_URL` at a throwaway database
  before running migrations, to avoid lock contention while a migration executes.
  The env file is per-track, so a throwaway DSN does not leak into other lanes.

## Integrating finished work

When a track's work is ready, fold it back through git like any branch:

```
# from the track worktree, get current first
pwsh tracks/rc-track.ps1 sync calibrator-v4     # rebase onto origin/master

# then from the main repo, fold it in
git -C "C:\Users\chad\Local Sites\rising-compass" merge track/calibrator-v4
```

Or push the track branch and open it as a PR if you want review. Deploy still
follows the normal RC path (push to origin/master, server-direct pull). The
deploy key is read-only, so commits made on the server need the
format-patch / push-from-local route. Tracks do not change that.

Run `sync` on the other live tracks afterward so they pick up the merged work
and you resolve any conflicts early instead of at the end.

## Teardown

```
pwsh tracks/rc-track.ps1 remove calibrator-v4                 keep the branch
pwsh tracks/rc-track.ps1 remove calibrator-v4 -DeleteBranch   drop the branch too
```

`remove` refuses if the worktree has uncommitted work that is not just the
scaffolded env files. Re-run with `-Force` only if you mean to discard it. The
`.venv` junction is removed without touching the main repo's `.venv`.

## What each track gets

- A worktree on branch `track/<name>`.
- `.deploy.env` and `backend/.env`, copied from main at creation. Edit per track.
- `backend/.venv`, junctioned to the main repo's venv (shared dependencies, no
  reinstall). If a track needs different deps, delete the junction and build its
  own venv there.
- A `TRACK.md` in the worktree root with its ports and run commands.
- A slot with a reserved backend/frontend port pair, freed on removal.

## Notes

- `registry.json` and the `rc-tracks/` folder are local machine state, outside
  the repo and not committed. The script (`tracks/rc-track.ps1`) is committed so
  the workflow travels with the project.
- Slots are reused. Remove a track and the next `new` takes its freed slot.
