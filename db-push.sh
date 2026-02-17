#!/bin/bash
# Push local database to production (first deploy or DB restore)
# Run from the project root: bash db-push.sh
#
# WARNING: This REPLACES the production database. The server containers
# will be restarted to pick up the new file.

set -e

SERVER="root@YOUR_DROPLET_IP"
LOCAL_DB="backend/data/rising_compass.db"
REMOTE_DIR="/root/rising-compass"

if [ ! -f "$LOCAL_DB" ]; then
    echo "ERROR: Local database not found at $LOCAL_DB"
    exit 1
fi

echo "=== Push Local DB to Production ==="
echo "WARNING: This will REPLACE the production database."
read -p "Continue? (y/N) " confirm
if [ "$confirm" != "y" ]; then
    echo "Aborted."
    exit 0
fi

echo "Uploading database..."
scp "$LOCAL_DB" "$SERVER:/tmp/rising_compass.db"

echo "Stopping backend, copying DB into volume, restarting..."
ssh "$SERVER" << 'EOF'
cd /root/rising-compass
docker compose stop backend
# Find the volume mount path and copy the DB in
VOLUME_PATH=$(docker volume inspect rising-compass_db-data -f '{{.Mountpoint}}')
cp /tmp/rising_compass.db "$VOLUME_PATH/rising_compass.db"
rm /tmp/rising_compass.db
docker compose start backend
echo "Backend restarted with new database."
EOF

echo "Done. Production DB replaced with local copy."
