#!/usr/bin/env bash
# Codespaces, every start: run Polixor in the background with the password
# gate on, and wait until it answers. Safe to run again.
#   bash polixor/scripts/codespaces/start.sh            start (no-op when running)
#   bash polixor/scripts/codespaces/start.sh restart    load a new version (jobs resume)
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"     # .../polixor
PORT="${POLIXOR_PORT:-8756}"
LOG="$HOME/.polixor/server.log"
mkdir -p "$HOME/.polixor"

if [ "${1:-}" = "restart" ]; then
    # stop the running server (a new version is picked up); jobs it was running resume
    # by themselves from their checkpoints when it starts again
    pkill -f "polixor.main" 2>/dev/null || true
    # wait until the old server has really exited (its jobs stop at a checkpoint first)
    for _ in $(seq 1 90); do
        pgrep -f "polixor.main" >/dev/null 2>&1 || break
        sleep 1
    done
    pkill -9 -f "polixor.main" 2>/dev/null || true
fi
if curl -fsS -m 2 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
    echo "Polixor is already running. (To load a new version: bash $0 restart)"
    exit 0
fi
if [ ! -f "$ROOT/.venv/.polixor-installed" ]; then
    echo "Polixor is not installed yet – running setup first."
    bash "$ROOT/scripts/codespaces/setup.sh"
else
    # requirements.txt changed since setup (e.g. a new version pin)
    "$ROOT/.venv/bin/python" "$ROOT/scripts/sync_deps.py"
fi
# The interface is rebuilt when its sources changed since the last build (after a git pull)
DIST="$ROOT/frontend/dist/index.html"
if [ ! -f "$DIST" ] || [ -n "$(find "$ROOT/frontend/src" "$ROOT/frontend/package.json" -newer "$DIST" -print -quit 2>/dev/null)" ]; then
    echo "Building the interface…"
    (cd "$ROOT/frontend" && { [ -d node_modules ] || npm ci --no-audit --no-fund --loglevel=error; } \
        && npm run build --silent) || echo "WARNING: the interface build failed – the previous build is served"
fi
# Every start: re-sync the password file and POLIXOR-PASSWORD.txt (and pick up
# a Codespaces secret added or changed after the codespace was created).
bash "$ROOT/scripts/codespaces/password.sh"

cd "$ROOT/backend"
# The server reads the password ONLY from the file: the secret variable is
# removed from its environment, so it can never override what the file says.
env -u POLIXOR_ACCESS_PASSWORD \
    POLIXOR_HOST=127.0.0.1 POLIXOR_PORT="$PORT" \
    POLIXOR_ACCESS_PASSWORD_FILE="$HOME/.polixor/password" \
    setsid nohup "$ROOT/.venv/bin/python" -m polixor.main >>"$LOG" 2>&1 < /dev/null &
echo $! > "$HOME/.polixor/server.pid"

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
