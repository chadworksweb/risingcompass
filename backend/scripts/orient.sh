#!/usr/bin/env bash
# orient.sh -- one-call session orientation for Rising Compass terminal calibration.
#
# Collapses the slow "get tapped in" ramp into a single command. It reproduces the
# LIVE LEC rubric LOCALLY and deterministically (the canonical instrument is the
# composed gospel + rc-lyric lens), then makes ONE cheap SSH call to confirm the
# local compose's rubric_version matches what prod is serving RIGHT NOW. If they
# match, the local compose IS canonical and is written to disk for the session. If
# they differ, it warns and pulls the live text so you never calibrate off a stale
# instrument.
#
# Canonical rule (see feedback_rc_no_api_in_terminal + risingcompass-backfill-process.md):
# the rubric is whatever LEC prod serves NOW (composed gospel+lens, verified by
# rubric_version). NEVER a _snapshots/rubric-snapshot-* file -- those are frozen
# parity baselines that go stale.
#
# Usage:
#   bash orient.sh                 # rubric only
#   bash orient.sh "Let It Be"     # rubric + that song's current backfill state
#
# Env overrides:
#   RC_PROD_HOST   default root@138.197.111.66
#   LEC_BACKEND    default C:/Users/chad/Local Sites/libra-engine-compass/backend
#   RC_BACKEND     default C:/Users/chad/Local Sites/rising-compass/backend
#   ORIENT_OUTDIR  default $LEC_BACKEND/.orient (rubric halves land here)

set -euo pipefail

PROD="${RC_PROD_HOST:-root@138.197.111.66}"
LEC_BACKEND="${LEC_BACKEND:-C:/Users/chad/Local Sites/libra-engine-compass/backend}"
RC_BACKEND="${RC_BACKEND:-C:/Users/chad/Local Sites/rising-compass/backend}"
OUTDIR="${ORIENT_OUTDIR:-$LEC_BACKEND/.orient}"
SONG="${1:-}"

PY_LEC="$LEC_BACKEND/.venv/Scripts/python.exe"
[ -x "$PY_LEC" ] || PY_LEC="python"

mkdir -p "$OUTDIR"

echo "== 1/3  Composing the canonical rubric locally (gospel + rc-lyric lens) =="
# Force the composed path on regardless of the local .env flag, then use LEC's OWN
# code to render the definition + format + version hash -- byte-identical to what the
# server publishes. Writes the two prompt halves the calibration actually uses.
( cd "$LEC_BACKEND" && LEC_COMPOSE_RUBRIC=true "$PY_LEC" - "$OUTDIR" <<'PYEOF'
import sys, pathlib
out = pathlib.Path(sys.argv[1])
from app.routers.lec_score import rubric_version, published_definition
from app.services.agents.lec_compass_agent_rubric import CALIBRATION_FORMAT
definition = published_definition()
(out / "rubric_text.txt").write_text(definition, encoding="utf-8")
(out / "calibration_format.txt").write_text(CALIBRATION_FORMAT, encoding="utf-8")
(out / "version.txt").write_text(rubric_version(), encoding="utf-8")
print(f"LOCAL_VERSION\t{rubric_version()}")
print(f"definition_chars\t{len(definition)}")
PYEOF
)
LOCAL_VER="$(cat "$OUTDIR/version.txt")"

echo ""
echo "== 2/3  Confirming against LIVE prod (one SSH round trip) =="
LIVE_VER="$(ssh -o ConnectTimeout=20 "$PROD" 'cd /root/rising-compass && docker compose exec -T backend python3 -c "
import os, urllib.request, json
req=urllib.request.Request(os.environ[\"LEC_BASE_URL\"]+\"/api/rubric\", headers={\"X-Api-Key\":os.environ[\"LEC_API_KEY\"]})
d=json.load(urllib.request.urlopen(req, timeout=20))
print(d[\"version\"])
"' 2>/dev/null | tr -d '[:space:]')"

echo "  local compose : $LOCAL_VER"
echo "  live  serving : $LIVE_VER"
if [ "$LOCAL_VER" = "$LIVE_VER" ]; then
  echo "  IN SYNC -- local compose IS canonical. Calibrate against $OUTDIR/rubric_text.txt + calibration_format.txt"
else
  echo "  *** OUT OF SYNC *** local compose != live. Pulling the LIVE text so you do not calibrate off a stale instrument..."
  ssh -o ConnectTimeout=20 "$PROD" 'cd /root/rising-compass && docker compose exec -T backend python3 -c "
import os, urllib.request, json
req=urllib.request.Request(os.environ[\"LEC_BASE_URL\"]+\"/api/rubric\", headers={\"X-Api-Key\":os.environ[\"LEC_API_KEY\"]})
d=json.load(urllib.request.urlopen(req, timeout=20))
open(\"/tmp/_orient_rubric.txt\",\"w\").write(d[\"rubric_text\"])
open(\"/tmp/_orient_format.txt\",\"w\").write(d[\"calibration_format\"])
"'
  ssh "$PROD" 'cd /root/rising-compass && docker compose cp backend:/tmp/_orient_rubric.txt /tmp/_orient_rubric.txt && docker compose cp backend:/tmp/_orient_format.txt /tmp/_orient_format.txt'
  scp -O "$PROD:/tmp/_orient_rubric.txt" "$OUTDIR/rubric_text.txt"
  scp -O "$PROD:/tmp/_orient_format.txt" "$OUTDIR/calibration_format.txt"
  echo "$LIVE_VER" > "$OUTDIR/version.txt"
  echo "  Live text written to $OUTDIR (version $LIVE_VER). Investigate the local drift when convenient."
fi

echo ""
if [ -n "$SONG" ]; then
  echo "== 3/3  Current backfill state for: $SONG =="
  PY_RC="$RC_BACKEND/.venv/Scripts/python.exe"; [ -x "$PY_RC" ] || PY_RC="python"
  ( cd "$RC_BACKEND" && "$PY_RC" - "$SONG" <<'PYEOF'
import sys
title = sys.argv[1]
from app.database import SessionLocal
from sqlalchemy import text
db = SessionLocal()
try:
    rows = db.execute(text("""
        select id, title, artist, charge_value, rubric_color, charge_summary
        from songs
        where lower(title) = lower(:t)
        limit 5
    """), {"t": title}).fetchall()
    if not rows:
        print(f"  no song titled '{title}' in songs (fresh calibration)")
    for r in rows:
        calibrated = "UNCALIBRATED" if r.charge_value is None else f"charge={r.charge_value} {r.rubric_color}"
        print(f"  id={r.id} | {r.title} -- {r.artist} | {calibrated}")
        if r.charge_summary:
            print(f"      summary: {r.charge_summary[:100]}")
finally:
    db.close()
PYEOF
  ) || echo "  (song lookup skipped: RC DB query failed -- check RC_BACKEND/.env)"
else
  echo "== 3/3  (no song arg; pass a title to also see its backfill state) =="
fi

echo ""
echo "Oriented. Canonical rubric = live composed LEC ($LOCAL_VER). Lyrics: Dropbox/Debug/dd.txt."
