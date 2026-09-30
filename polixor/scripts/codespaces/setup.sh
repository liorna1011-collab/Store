#!/usr/bin/env bash
# Codespaces, once per codespace: FFmpeg + fonts, Python packages, the built
# interface, and the transcription model (so the first analysis is fast).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"     # .../polixor
SUDO=""; [ "$(id -u)" != "0" ] && SUDO="sudo"

echo "==> [1/4] FFmpeg and fonts (Hebrew and Latin subtitles)"
$SUDO apt-get update -qq
$SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
    ffmpeg fontconfig fonts-dejavu-core fonts-noto-core fonts-noto-hinted \
    libgl1 libglib2.0-0 >/dev/null
$SUDO fc-cache -f >/dev/null
ffmpeg -version | head -1

echo "==> [2/4] Python packages"
PY="$(command -v python3.12 || command -v python3)"
[ -x "$ROOT/.venv/bin/python" ] || "$PY" -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/python" -m pip install -q --upgrade pip
"$ROOT/.venv/bin/python" -m pip install -q -r "$ROOT/backend/requirements.txt"
"$ROOT/.venv/bin/python" "$ROOT/scripts/sync_deps.py" --mark
touch "$ROOT/.venv/.polixor-installed"

echo "==> [3/4] Interface"
cd "$ROOT/frontend"
npm ci --no-audit --no-fund --loglevel=error || npm install --no-audit --no-fund --loglevel=error
npm run build --silent

echo "==> [4/4] Transcription model (one-time download)"
cd "$ROOT/backend"
if "$ROOT/.venv/bin/python" - <<'PY'
import sys
from faster_whisper import WhisperModel
from polixor.config import PATHS, SETTINGS
PATHS.ensure()
name = SETTINGS.get().whisper_model
try:
    WhisperModel(name, device="cpu", compute_type="int8", download_root=str(PATHS.models))
except Exception as exc:  # noqa: BLE001
    print(f"model '{name}': {type(exc).__name__}: {str(exc)[:160]}")
    sys.exit(1)
print(f"model '{name}' ready")
PY
then :
else
    echo "Model download did not finish – Polixor will retry on the first analysis."
fi

echo "==> Setup complete."
