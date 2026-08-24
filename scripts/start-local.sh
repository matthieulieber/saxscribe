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
  [[ -n "${api_pid:-}" ]] && kill "$api_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 --reload &
api_pid=$!

cd frontend
npm run dev -- --host 127.0.0.1 --port 5173
