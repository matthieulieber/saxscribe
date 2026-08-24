#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "$0")/.." && pwd)"
cd "$project_dir"

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is required. Install it from https://brew.sh and run this script again."
  exit 1
fi

brew list ffmpeg >/dev/null 2>&1 || brew install ffmpeg

python_cmd=""
for candidate in python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] <= (3, 11) else 1)' 2>/dev/null; then
    python_cmd="$candidate"
    break
  fi
done

if [[ -z "$python_cmd" ]]; then
  echo "Python 3.10 or 3.11 is required by the sax transcription model. Try: brew install python@3.11"
  exit 1
fi

"$python_cmd" -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip wheel setuptools
python -m pip install -r backend/requirements.txt

if ! command -v node >/dev/null 2>&1; then
  echo "Node 20+ is required. Try: brew install node"
  exit 1
fi

(cd frontend && npm install)

[[ -f .env ]] || cp .env.example .env
echo "Downloading and verifying the exact UVR wind checkpoint..."
bash scripts/download-wind-model.sh
echo "Setup complete. Run: bash scripts/start-local.sh"
