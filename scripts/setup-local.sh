#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"

for command in docker uv node npm; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "Missing required command: $command" >&2
    exit 1
  fi
done

./scripts/docker-compose.sh up -d --wait

if [ ! -f backend/.env ]; then
  cp backend/.env.example backend/.env
fi

if [ ! -f frontend/.env.local ]; then
  cp frontend/.env.example frontend/.env.local
fi

(cd backend && uv sync --locked && uv run python -m app.migrate)
(cd frontend && npm ci)

echo
echo "Setup complete. Add your model provider and API key to backend/.env, then run:"
echo "  make start"
