#!/usr/bin/env bash
# הפעלת Polixor ב-Linux/macOS
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -d "$ROOT/.venv" ]; then
    echo "יוצר סביבה וירטואלית..."
    python3 -m venv "$ROOT/.venv"
    "$ROOT/.venv/bin/pip" install --upgrade pip
    "$ROOT/.venv/bin/pip" install -r "$ROOT/backend/requirements.txt"
fi

if [ ! -f "$ROOT/frontend/dist/index.html" ]; then
    echo "שים לב: הממשק לא נבנה (cd frontend && npm install && npm run build)"
fi

command -v ffmpeg >/dev/null 2>&1 || echo "אזהרה: FFmpeg לא נמצא ב-PATH"

cd "$ROOT/backend"
exec "$ROOT/.venv/bin/python" -m polixor.main
