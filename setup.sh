#!/usr/bin/env bash
# Installs everything the reel engine needs. Safe to run repeatedly.
set -e
cd "$(dirname "$0")"
command -v ffmpeg >/dev/null || { sudo apt-get update -qq && sudo apt-get install -y -qq ffmpeg; } || echo "!! install ffmpeg manually"
python3 -c "import faster_whisper" 2>/dev/null || pip3 install -q faster-whisper
mkdir -p input work out
# Pre-download the speech model once (needs huggingface.co network access).
python3 - <<'PY' || echo "!! Whisper model download blocked: allow huggingface.co in the environment's network settings"
from faster_whisper import WhisperModel
WhisperModel("small.en", device="cpu", compute_type="int8")
print("whisper model ready")
PY
