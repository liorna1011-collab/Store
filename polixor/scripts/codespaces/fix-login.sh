#!/usr/bin/env bash
# Codespaces: repair Polixor login.
#   bash polixor/scripts/codespaces/fix-login.sh         keep the password, re-sync, restart
#   bash polixor/scripts/codespaces/fix-login.sh --new   make a new password, restart
# Checks which password the running server uses (without printing it),
# restarts Polixor from the single password file, then logs in for real.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"     # .../polixor
PORT="${POLIXOR_PORT:-8756}"
STORE="$HOME/.polixor/password"

fp() { printf '%s' "$1" | sha256sum | cut -c1-8; }            # fingerprint, not the password

echo "== Before"
if [ -n "${POLIXOR_ACCESS_PASSWORD:-}" ]; then
    echo "   Codespaces secret POLIXOR_ACCESS_PASSWORD: set (fingerprint $(fp "$POLIXOR_ACCESS_PASSWORD"))"
else
    echo "   Codespaces secret POLIXOR_ACCESS_PASSWORD: not set"
fi
[ -s "$STORE" ] && echo "   password file: present (fingerprint $(fp "$(cat "$STORE")"))" || echo "   password file: missing"

# Only real Polixor servers: argument list is exactly <python> -m polixor.main
# (checked argument by argument, so a shell that merely mentions it is never touched)
is_server() {
    local args
    mapfile -d '' -t args < "/proc/$1/cmdline" 2>/dev/null || return 1
    [ "${#args[@]}" -ge 3 ] && [[ "$(basename "${args[0]}")" == python* ]] \
        && [ "${args[1]}" = "-m" ] && [ "${args[2]}" = "polixor.main" ]
}
PIDS=""
for d in /proc/[0-9]*; do
    p="${d#/proc/}"
    is_server "$p" && PIDS="$PIDS $p"
done
for p in $PIDS; do
    ENVPW="$(tr '\0' '\n' < "/proc/$p/environ" 2>/dev/null | sed -n 's/^POLIXOR_ACCESS_PASSWORD=//p')"
    ENVFILE="$(tr '\0' '\n' < "/proc/$p/environ" 2>/dev/null | sed -n 's/^POLIXOR_ACCESS_PASSWORD_FILE=//p')"
    if [ -n "$ENVPW" ]; then
        echo "   running server $p: uses POLIXOR_ACCESS_PASSWORD (fingerprint $(fp "$ENVPW")) – this OVERRIDES the file"
    elif [ -n "$ENVFILE" ]; then
        echo "   running server $p: uses the password file $ENVFILE"
    else
        echo "   running server $p: no password configured"
    fi
done
[ -z "$PIDS" ] && echo "   no Polixor server running"

echo "== Fixing"
bash "$ROOT/scripts/codespaces/password.sh" "${1:-}"
for p in $PIDS; do kill "$p" 2>/dev/null; done
for _ in $(seq 1 20); do
    curl -fsS -m 1 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 || break
    sleep 0.5
done
rm -f "$HOME/.polixor/server.pid"
bash "$ROOT/scripts/codespaces/start.sh" || exit 1

echo "== Checking login"
JAR="$(mktemp)"
CODE="$(curl -s -o /dev/null -w '%{http_code}' -c "$JAR" \
        --data-urlencode "password@$STORE" -d 'next=/' "http://127.0.0.1:$PORT/login")"
API="$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" "http://127.0.0.1:$PORT/api/projects")"
rm -f "$JAR"
if [ "$CODE" = "303" ] && [ "$API" = "200" ]; then
    echo "   OK: the password in POLIXOR-PASSWORD.txt signs in (login $CODE, app $API)."
    echo "   Reload the Polixor tab and sign in. Open POLIXOR-PASSWORD.txt to copy the password."
else
    echo "   Login check FAILED (login $CODE, app $API). Details: $HOME/.polixor/server.log"
    exit 1
fi
