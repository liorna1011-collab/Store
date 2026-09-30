#!/usr/bin/env bash
# Codespaces: decide the Polixor password and keep every copy of it in sync.
#   * Codespaces secret POLIXOR_ACCESS_PASSWORD set -> it is the password.
#   * otherwise -> the saved password, or a new random one the first time.
#   * --new     -> replace a saved random password with a new one.
# The server reads ONLY ~/.polixor/password (start.sh removes the variable
# from its environment), and POLIXOR-PASSWORD.txt is rewritten from that same
# file every time, so what you see is always what the server expects.
# Runs on create and on every start. The password is never printed.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
STORE="$HOME/.polixor/password"
mkdir -p "$HOME/.polixor"
chmod 700 "$HOME/.polixor"
umask 077

new_password() {
    # letters and digits only: a double-click selects all of it, and there is
    # nothing to mistype (no 0/O, 1/l/I)
    python3 -c 'import secrets; a="abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"; print("".join(secrets.choice(a) for _ in range(16)))'
}

SECRET="$(printf '%s' "${POLIXOR_ACCESS_PASSWORD:-}" | tr -d '\r\n' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
if [ -n "$SECRET" ]; then
    printf '%s' "$SECRET" > "$STORE"
    SOURCE="secret"
elif [ "${1:-}" = "--new" ] || [ ! -s "$STORE" ]; then
    printf '%s' "$(new_password)" > "$STORE"
    SOURCE="generated"
else
    SOURCE="generated"
fi

if [ "$SOURCE" = "secret" ]; then
    SHOWN="(the value of your POLIXOR_ACCESS_PASSWORD Codespaces secret)"
else
    SHOWN="$(cat "$STORE")"
fi

cat > "$ROOT/POLIXOR-PASSWORD.txt" <<TXT
Polixor – your testing environment
==================================

Password (double-click it to select, then copy):

$SHOWN

How to open Polixor
-------------------
1. Wait until the setup in the terminal finishes (about 5 minutes the first
   time). Polixor then opens in a new browser tab by itself.
2. If it does not open: click the "PORTS" tab at the bottom of this window,
   find "Polixor (8756)" and click the globe icon (Open in Browser).
3. Enter the password above.

On your iPhone
--------------
Copy the address from the PORTS tab (right-click the address -> Copy Local
Address). Open it in Safari, sign in to GitHub when asked, then enter the
password above. Keep the port "Private": only your GitHub account can open it.

Login problems?
---------------
In the terminal run:   bash polixor/scripts/codespaces/fix-login.sh
It re-syncs the password, restarts Polixor and checks that login works.
For a brand-new password:   bash polixor/scripts/codespaces/fix-login.sh --new

This file is not saved to git.
TXT
chmod 644 "$ROOT/POLIXOR-PASSWORD.txt"
echo "Polixor password ready (source: $SOURCE)."
