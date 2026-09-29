#!/usr/bin/env bash
# Codespaces, every start: run Polixor in the background with the password
# gate on, and wait until it answers. Safe to run again.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"     # .../polixor
PORT="${POLIXOR_PORT:-8756}"
LOG="$HOME/.polixor/server.log"
mkdir -p "$HOME/.polixor"

if curl -fsS -m 2 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
    echo "Polixor is already running."
    exit 0
fi
if [ ! -f "$ROOT/.venv/.polixor-installed" ]; then
    echo "Polixor is not installed yet – running setup first."
    bash "$ROOT/scripts/codespaces/setup.sh"
fi
if [ ! -s "$HOME/.polixor/password" ]; then
    bash "$ROOT/scripts/codespaces/password.sh"
fi

cd "$ROOT/backend"
POLIXOR_HOST=127.0.0.1 POLIXOR_PORT="$PORT" \
POLIXOR_ACCESS_PASSWORD_FILE="$HOME/.polixor/password" \
    setsid nohup "$ROOT/.venv/bin/python" -m polixor.main >>"$LOG" 2>&1 < /dev/null &

for _ in $(seq 1 60); do
    if curl -fsS -m 2 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
        echo "Polixor is running on port $PORT (password protected). Open it from the PORTS tab."
        exit 0
    fi
    sleep 1
done
echo "Polixor did not start – see $LOG"
tail -20 "$LOG"
exit 1
