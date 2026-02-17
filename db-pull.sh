#!/bin/bash
# Pull the production database to your local machine
# Run from the project root: bash db-pull.sh

set -e

API="https://api.risingcompass.net"
LOCAL_DB="backend/data/rising_compass.db"

# Load admin key from local .env
if [ -f .env ]; then
    ADMIN_KEY=$(grep RC_ADMIN_KEY .env | cut -d= -f2)
elif [ -f backend/.env ]; then
    ADMIN_KEY=$(grep RC_ADMIN_KEY backend/.env | cut -d= -f2)
fi

if [ -z "$ADMIN_KEY" ]; then
    echo "ERROR: Could not find RC_ADMIN_KEY in .env"
    echo "Set it in .env or backend/.env"
    exit 1
fi

echo "Pulling production database..."

# Back up current local DB if it exists
if [ -f "$LOCAL_DB" ]; then
    BACKUP="${LOCAL_DB}.local-backup-$(date +%Y%m%d-%H%M%S)"
    cp "$LOCAL_DB" "$BACKUP"
    echo "Local DB backed up to: $BACKUP"
fi

# Download from production
curl -s -f -H "X-Admin-Key: $ADMIN_KEY" "$API/api/admin/db-export" -o "$LOCAL_DB"

echo "Done. Production DB saved to $LOCAL_DB"
echo "Song count: $(sqlite3 "$LOCAL_DB" 'SELECT COUNT(*) FROM songs;' 2>/dev/null || echo 'install sqlite3 to verify')"
