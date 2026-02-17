#!/bin/bash
# Deploy The Rising Compass to a DigitalOcean droplet
# Run from the project root on the server

set -e

DOMAIN="risingcompass.net"
API_DOMAIN="api.risingcompass.net"
EMAIL="chad@crystopaforge.com"

echo "=== The Rising Compass — Deploy ==="

# Pull latest code
git pull origin master

# ------------------------------------------------------------------
# First deploy: bootstrap SSL certs before starting nginx with SSL
# ------------------------------------------------------------------
if [ ! -d "/etc/letsencrypt/live/$DOMAIN" ]; then
    echo ""
    echo "=== First deploy — obtaining SSL certificates ==="

    # Check .env exists
    if [ ! -f .env ]; then
        echo "ERROR: .env file not found. Copy .env.example and fill in secrets first."
        exit 1
    fi

    # Start with a temporary HTTP-only nginx config for certbot challenge
    cat > /tmp/nginx-init.conf <<'NGINX'
server {
    listen 80;
    server_name risingcompass.net api.risingcompass.net;
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }
    location / {
        return 204;
    }
}
NGINX

    # Temporarily swap nginx config
    cp deploy/nginx.conf deploy/nginx.conf.bak
    cp /tmp/nginx-init.conf deploy/nginx.conf

    # Start just nginx (no SSL yet) and backend
    docker compose up -d --build backend nginx

    # Obtain certificates
    docker compose run --rm certbot certonly \
        --webroot -w /var/www/certbot \
        -d "$DOMAIN" -d "$API_DOMAIN" \
        --email "$EMAIL" --agree-tos --no-eff-email

    # Restore real nginx config
    mv deploy/nginx.conf.bak deploy/nginx.conf

    # Restart nginx with SSL config
    docker compose up -d --force-recreate nginx

    echo "SSL certificates obtained."
else
    echo "SSL certificates already exist, skipping certbot init."
fi

# ------------------------------------------------------------------
# Build and start all containers
# ------------------------------------------------------------------
echo ""
echo "Building and starting containers..."
docker compose up -d --build

# ------------------------------------------------------------------
# Set up daily reading cron (idempotent)
# ------------------------------------------------------------------
CRON_CMD="0 10 * * * curl -s -X POST \"http://localhost:8000/api/admin/agent/classify-live\" -H \"X-Admin-Key: \$(grep RC_ADMIN_KEY $(pwd)/.env | cut -d= -f2)\" > /dev/null 2>&1"

if ! crontab -l 2>/dev/null | grep -q "classify-live"; then
    echo ""
    echo "Setting up daily reading cron (10:00 UTC)..."
    (crontab -l 2>/dev/null; echo "$CRON_CMD") | crontab -
    echo "Cron installed."
else
    echo "Daily reading cron already exists."
fi

# ------------------------------------------------------------------
# Set up certbot renewal cron (idempotent)
# ------------------------------------------------------------------
RENEW_CMD="0 3 * * * cd $(pwd) && docker compose run --rm certbot renew --quiet && docker compose exec nginx nginx -s reload > /dev/null 2>&1"

if ! crontab -l 2>/dev/null | grep -q "certbot renew"; then
    echo "Setting up certbot renewal cron..."
    (crontab -l 2>/dev/null; echo "$RENEW_CMD") | crontab -
    echo "Certbot renewal cron installed."
else
    echo "Certbot renewal cron already exists."
fi

echo ""
echo "=== Deploy complete ==="
echo "Frontend: https://$DOMAIN"
echo "API:      https://$API_DOMAIN"
echo "Admin:    https://$API_DOMAIN/api/admin/dashboard"
echo ""
echo "--- Post-deploy checklist ---"
echo "1. curl https://$API_DOMAIN/api/health"
echo "2. Test classify-live → email → approve flow"
echo "3. Verify cron: crontab -l"
