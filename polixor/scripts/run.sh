#!/usr/bin/env bash
# הפעלת Polixor ב-Linux/macOS
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# --phone: נגיש גם מטלפון באותה רשת Wi-Fi (אין סיסמה – רשת פרטית בלבד)
if [ "${1:-}" = "--phone" ]; then
    export POLIXOR_HOST=0.0.0.0
fi

# Python 3.10 ומעלה. ב-macOS ה-python3 המובנה הוא לרוב 3.9, ולכן מחפשים גרסה חדשה
PY=""
for cand in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$cand" >/dev/null 2>&1 && \
       "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
        PY="$cand"; break
    fi
done

# הסימון נכתב רק אחרי התקנה מלאה, כך שהתקנה שנקטעה ממשיכה בהרצה הבאה
DONE="$ROOT/.venv/.polixor-installed"
if [ ! -f "$DONE" ]; then
    if [ -z "$PY" ]; then
        echo "Python 3.10 or newer is required."
        echo "  macOS:  brew install python@3.12 ffmpeg"
        echo "  Ubuntu: sudo apt install python3 python3-venv ffmpeg"
        exit 1
    fi
    echo "First run: installing Polixor (a few minutes, only once)..."
    [ -x "$ROOT/.venv/bin/python" ] || "$PY" -m venv "$ROOT/.venv"
    "$ROOT/.venv/bin/python" -m pip install --upgrade pip
    "$ROOT/.venv/bin/python" -m pip install -r "$ROOT/backend/requirements.txt"
    touch "$DONE"
fi

if [ ! -f "$ROOT/frontend/dist/index.html" ]; then
    echo "Note: the interface is not built (cd frontend && npm install && npm run build)"
fi

command -v ffmpeg >/dev/null 2>&1 || echo "Warning: FFmpeg is not on PATH (macOS: brew install ffmpeg · Ubuntu: sudo apt install ffmpeg)"

cd "$ROOT/backend"
exec "$ROOT/.venv/bin/python" -m polixor.main
