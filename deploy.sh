#!/usr/bin/env sh
# One-shot deploy/update on the server. Run from the repo root:  ./deploy.sh
# Safe to re-run. Does NOT touch .env (your ANTHROPIC_API_KEY / secrets stay).
set -e
cd "$(dirname "$0")"

echo "==> pulling latest"
git fetch origin
git reset --hard origin/main

echo "==> rebuilding app images"
docker compose up -d --build

# Caddy serves the frontend via single-file bind mounts (index.html, support.js,
# suppliers-data.js). git reset replaces those files (new inode), which a running
# container's mount does NOT track — so force-recreate Caddy to re-bind them.
echo "==> re-binding static frontend (Caddy)"
docker compose up -d --force-recreate caddy

echo "==> status"
docker compose ps
