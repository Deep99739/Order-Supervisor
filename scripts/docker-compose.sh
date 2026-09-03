#!/bin/sh
set -eu

if docker compose version >/dev/null 2>&1; then
  exec docker compose "$@"
fi

if command -v docker-compose >/dev/null 2>&1; then
  exec docker-compose "$@"
fi

echo "Docker Compose is required. Install either 'docker compose' or 'docker-compose'." >&2
exit 1
