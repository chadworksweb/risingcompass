#!/bin/bash
# Fix ownership of mounted volumes (they come in as root)
chown -R appuser:appuser /app/data
exec gosu appuser uvicorn app.main:app --host 0.0.0.0 --port 8000 \
  --workers 2 --proxy-headers --forwarded-allow-ips '*'
