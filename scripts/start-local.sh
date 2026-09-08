#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "$0")/.." && pwd)"
cd "$project_dir"

if [[ ! -d .venv || ! -d frontend/node_modules ]]; then
  echo "Run bash scripts/setup-mac.sh first."
  exit 1
fi

# Fast checksum check when already present; downloads the exact checkpoint if missing.
bash scripts/download-wind-model.sh

source .venv/bin/activate
# backend.app loads .env with python-dotenv. Do not source it as shell code:
# valid dotenv values may contain spaces and must never be executed as commands.

cleanup() {
  if [[ -n "${api_pid:-}" ]] && kill -0 "$api_pid" 2>/dev/null; then
    kill "$api_pid" 2>/dev/null || true
    wait "$api_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

mkdir -p work
backend_log="$project_dir/work/backend-startup.log"

echo "Starting SaxScribe backend..."
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 \
  > >(tee "$backend_log") 2>&1 &
api_pid=$!

backend_ready=false
for _ in {1..90}; do
  if curl --silent --fail --max-time 1 http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
    backend_ready=true
    break
  fi

  if ! kill -0 "$api_pid" 2>/dev/null; then
    if wait "$api_pid"; then
      backend_status=0
    else
      backend_status=$?
    fi
    echo "SaxScribe backend exited before it became ready (status $backend_status)."
    echo "The full startup error is saved in: $backend_log"
    exit 1
  fi

  sleep 1
done

if [[ "$backend_ready" != true ]]; then
  echo "SaxScribe backend did not become ready within 90 seconds."
  echo "The full startup log is saved in: $backend_log"
  exit 1
fi

echo "SaxScribe backend ready at http://127.0.0.1:8000"

cd frontend
npm run dev -- --host 127.0.0.1 --port 5173
