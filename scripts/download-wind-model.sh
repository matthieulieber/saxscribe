#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "$0")/.." && pwd)"
model_dir="$project_dir/.models/audio-separator"
model_name="17_HP-Wind_Inst-UVR.pth"
model_path="$model_dir/$model_name"
model_url="https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/$model_name"
expected_sha256="acc6d472b4b478da9c9ab5af45b167749e05a7f65b30c7d5988b3700a513aeee"

mkdir -p "$model_dir"

checksum() {
  shasum -a 256 "$1" | awk '{print $1}'
}

if [[ -f "$model_path" ]]; then
  actual_sha256="$(checksum "$model_path")"
  if [[ "$actual_sha256" == "$expected_sha256" ]]; then
    echo "UVR wind model ready: $model_path"
    echo "SHA-256: $actual_sha256"
    exit 0
  fi
  echo "Existing UVR wind model has the wrong checksum; replacing it."
fi

temporary_path="$(mktemp "$model_dir/.${model_name}.download.XXXXXX")"
cleanup() {
  rm -f "$temporary_path"
}
trap cleanup EXIT INT TERM

echo "Downloading the exact UVR wind model (about 214 MB)..."
curl --fail --location --retry 3 --output "$temporary_path" "$model_url"
actual_sha256="$(checksum "$temporary_path")"
if [[ "$actual_sha256" != "$expected_sha256" ]]; then
  echo "Downloaded model checksum mismatch."
  echo "Expected: $expected_sha256"
  echo "Actual:   $actual_sha256"
  exit 1
fi

mv "$temporary_path" "$model_path"
trap - EXIT INT TERM
echo "UVR wind model ready: $model_path"
echo "SHA-256: $actual_sha256"
