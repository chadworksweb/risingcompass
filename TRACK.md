# Track: audience-resonance

- Branch:   track/audience-resonance
- Worktree: C:\Users\chad\Local Sites\rc-tracks\audience-resonance
- Slot:     1
- Backend:  http://127.0.0.1:8010
- Frontend: http://127.0.0.1:3015

## Run

Backend:
    cd "C:\Users\chad\Local Sites\rc-tracks\audience-resonance\backend"
    .\.venv\Scripts\uvicorn app.main:app --port 8010

Frontend:
    cd "C:\Users\chad\Local Sites\rc-tracks\audience-resonance\frontend"
    python scripts/dev_server.py --port 3015 --backend http://127.0.0.1:8010

Or, from the main repo: pwsh tracks/rc-track.ps1 start audience-resonance

## Heads up
- The database is REMOTE and SHARED with every other track and with main.
  Do schema/migration work on ONE track only.
- .venv is junctioned to main: shared dependencies. If this track needs
  different deps, replace the junction with its own venv.
- .deploy.env / backend/.env were copied from main at creation time.
