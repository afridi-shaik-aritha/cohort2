#!/bin/bash
# Single-command runner: builds the frontend (if needed) and serves the
# WHOLE system (API + UI) as one process on http://localhost:8000.
set -e
cd "$(dirname "$0")"

if [ ! -f backend/.env ]; then
  cp backend/.env.example backend/.env
  echo "Created backend/.env from .env.example — edit it (e.g. LLM_PROVIDER=lmstudio)."
fi

if [ ! -d frontend/dist ] || [ -n "$(find frontend/src -newer frontend/dist/index.html 2>/dev/null | head -n 1)" ]; then
  echo "Building frontend..."
  (cd frontend && npm install --no-audit --no-fund >/dev/null 2>&1 && npm run build)
else
  echo "Frontend build is up to date, skipping."
fi

echo "Starting system on http://localhost:8000 ..."
cd backend
exec uvicorn app.main:app --port 8000
