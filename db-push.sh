#!/bin/bash
# Push specific tables from local DB to production.
# Run from the project root: bash db-push.sh
#
# SAFE BY DEFAULT: Only syncs tables you explicitly choose.
# Always backs up production DB first.
# Production-only tables (daily_readings, reading_songs, agent_drafts,
# agent_draft_songs) are NEVER overwritten.

set -e

SERVER="deploy@138.197.111.66"
LOCAL_DB="backend/data/rising_compass.db"

# Tables that are SAFE to push (local is the source of truth)
SAFE_TABLES="compass_songs library_songs collections recommendations album_deep_dives album_tracks weekly_album_readings weekly_album_entries"

# Tables that are NEVER pushed (production is the source of truth)
PROTECTED_TABLES="daily_readings reading_songs agent_drafts agent_draft_songs misread_submissions misread_bans"

if [ ! -f "$LOCAL_DB" ]; then
    echo "ERROR: Local database not found at $LOCAL_DB"
    exit 1
fi

echo "=== Rising Compass — Safe DB Push ==="
echo ""
echo "SAFE tables (can be pushed):"
python -c "
import sqlite3
conn = sqlite3.connect('$LOCAL_DB')
for t in '$SAFE_TABLES'.split():
    try:
        count = conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
        print(f'  - {t} ({count} rows)')
    except:
        print(f'  - {t} (not in local DB)')
conn.close()
"
echo ""
echo "PROTECTED tables (never pushed):"
for t in $PROTECTED_TABLES; do
    echo "  - $t"
done
echo ""

# Let user pick which tables to push
echo "Options:"
echo "  1) Push compass_songs only (most common — backfill calibrations)"
echo "  2) Push all safe tables"
echo "  3) Pick specific tables"
echo "  4) FULL DB REPLACE (emergency only — requires double confirmation)"
echo ""
read -p "Choose [1-4]: " choice

TABLES_TO_PUSH=""

case "$choice" in
    1)
        TABLES_TO_PUSH="compass_songs"
        ;;
    2)
        TABLES_TO_PUSH="$SAFE_TABLES"
        ;;
    3)
        echo ""
        echo "Enter table names separated by spaces:"
        echo "  Available: $SAFE_TABLES"
        read -p "> " TABLES_TO_PUSH
        # Validate each table is in the safe list
        for t in $TABLES_TO_PUSH; do
            if ! echo "$SAFE_TABLES" | grep -qw "$t"; then
                if echo "$PROTECTED_TABLES" | grep -qw "$t"; then
                    echo "ERROR: '$t' is a PROTECTED table and cannot be pushed."
                    echo "       Production is the source of truth for this table."
                    exit 1
                else
                    echo "ERROR: Unknown table '$t'"
                    exit 1
                fi
            fi
        done
        ;;
    4)
        echo ""
        echo "!!! DANGER: Full DB replace will DESTROY production-only data !!!"
        echo "!!! This includes daily readings, approved drafts, and reading songs !!!"
        echo ""
        read -p "Type 'REPLACE PRODUCTION DB' to confirm: " danger_confirm
        if [ "$danger_confirm" != "REPLACE PRODUCTION DB" ]; then
            echo "Aborted."
            exit 0
        fi
        # Full replace path
        echo ""
        echo "Uploading full database..."
        scp "$LOCAL_DB" "$SERVER:/tmp/rising_compass.db"
        echo "Backing up production DB, then replacing..."
        ssh "$SERVER" "sudo bash -s" << 'FULLEOF'
cd /root/rising-compass
docker compose exec -T backend python -c "from app.services.backup import run_backup; p = run_backup(); print(f'Backup: {p}')"
docker compose stop backend
CONTAINER=$(docker ps -a --filter "name=backend" -q | head -1)
VOLUME_PATH=$(docker volume inspect rising-compass_db-data -f '{{.Mountpoint}}')
cp /tmp/rising_compass.db "$VOLUME_PATH/rising_compass.db"
rm /tmp/rising_compass.db
docker compose start backend
echo "Backend restarted with FULL database replacement."
FULLEOF
        echo "Done. Production DB fully replaced. Backup was created first."
        exit 0
        ;;
    *)
        echo "Invalid choice."
        exit 1
        ;;
esac

if [ -z "$TABLES_TO_PUSH" ]; then
    echo "No tables selected. Aborted."
    exit 0
fi

echo ""
echo "Will push these tables: $TABLES_TO_PUSH"
read -p "Continue? (y/N) " confirm
if [ "$confirm" != "y" ]; then
    echo "Aborted."
    exit 0
fi

# Upload local DB to server temp location
echo ""
echo "Uploading local database..."
scp "$LOCAL_DB" "$SERVER:/tmp/rising_compass_local.db"

echo "Backing up production DB, then merging tables..."
ssh "$SERVER" "sudo bash -s" << MERGEEOF
cd /root/rising-compass

# Backup first
docker compose exec -T backend python -c "from app.services.backup import run_backup; p = run_backup(); print(f'Backup: {p}')"

# Copy local DB into the container's data volume for SQLite ATTACH
VOLUME_PATH=\$(docker volume inspect rising-compass_db-data -f '{{.Mountpoint}}')
cp /tmp/rising_compass_local.db "\$VOLUME_PATH/local_import.db"

# Merge tables: upsert from local, preserve production-only rows
docker compose exec -T backend python -c "
import sqlite3
conn = sqlite3.connect('/app/data/rising_compass.db')
conn.execute('ATTACH DATABASE \"/app/data/local_import.db\" AS local_db')
conn.execute('BEGIN')
tables = '${TABLES_TO_PUSH}'.split()
for t in tables:
    cols = [row[1] for row in conn.execute(f'PRAGMA table_info({t})').fetchall()]
    cols_no_id = [c for c in cols if c != 'id']
    col_list_no_id = ', '.join(cols_no_id)
    before = conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
    local_count = conn.execute(f'SELECT COUNT(*) FROM local_db.{t}').fetchone()[0]

    if t == 'compass_songs':
        # compass_songs: match on (title, artist) — both local and production write to this table
        # Update existing matches with local data, insert new ones, leave production-only rows alone
        updated = 0
        inserted = 0
        local_rows = conn.execute(f'SELECT {col_list_no_id} FROM local_db.{t}').fetchall()
        for row in local_rows:
            # row[0]=title, row[1]=artist (first two cols after id)
            title_val = row[0]
            artist_val = row[1]
            existing = conn.execute('SELECT id FROM compass_songs WHERE lower(title)=lower(?) AND lower(artist)=lower(?)', (title_val, artist_val)).fetchone()
            if existing:
                sets = ', '.join([f'{c}=?' for c in cols_no_id])
                conn.execute(f'UPDATE {t} SET {sets} WHERE id=?', list(row) + [existing[0]])
                updated += 1
            else:
                placeholders = ', '.join(['?' for _ in cols_no_id])
                conn.execute(f'INSERT INTO {t} ({col_list_no_id}) VALUES ({placeholders})', row)
                inserted += 1
        after = conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
        prod_only = after - local_count
        print(f'  {t}: {updated} updated, {inserted} inserted, {prod_only} production-only preserved ({after} total)')
    else:
        # Other safe tables: full replace (local is sole source of truth)
        col_list = ', '.join(cols)
        conn.execute(f'DELETE FROM {t}')
        conn.execute(f'INSERT INTO {t} ({col_list}) SELECT {col_list} FROM local_db.{t}')
        after = conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
        print(f'  {t}: replaced with {after} rows')

conn.execute('COMMIT')
conn.execute('DETACH DATABASE local_db')
conn.close()
print('Merge complete. Production-only rows preserved.')
"

# Clean up
rm "\$VOLUME_PATH/local_import.db"
rm /tmp/rising_compass_local.db
echo "Done."
MERGEEOF

echo ""
echo "Safe push complete. Only these tables were updated: $TABLES_TO_PUSH"
echo "Production-only data (readings, drafts) was NOT touched."
