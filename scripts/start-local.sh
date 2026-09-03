#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"

if [ ! -f backend/.env ] || [ ! -f frontend/.env.local ]; then
  echo "Run 'make setup' first." >&2
  exit 1
fi

pids=""

stop_processes() {
  trap - INT TERM EXIT
  if [ -n "$pids" ]; then
    kill $pids 2>/dev/null || true
    wait $pids 2>/dev/null || true
  fi
}

trap stop_processes INT TERM EXIT

(cd backend && exec uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8010) &
pids="$pids $!"
(cd backend && exec uv run python -m app.worker) &
pids="$pids $!"
(cd frontend && exec npm run dev) &
pids="$pids $!"

echo "Order Supervisor is starting at http://localhost:3000"
echo "Press Ctrl-C to stop the API, worker, and frontend."

while :; do
  for pid in $pids; do
    if ! kill -0 "$pid" 2>/dev/null; then
      if wait "$pid"; then
        exit 0
      else
        exit $?
      fi
    fi
  done
  sleep 1
done
