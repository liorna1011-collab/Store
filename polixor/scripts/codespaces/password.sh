#!/usr/bin/env bash
# Codespaces, first step: decide the Polixor password.
#   * Codespaces secret POLIXOR_ACCESS_PASSWORD set  -> use it.
#   * otherwise                                      -> create a random one.
# The server reads it from ~/.polixor/password. POLIXOR-PASSWORD.txt (git-ignored)
# is opened in the editor so you can copy it.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
mkdir -p "$HOME/.polixor"
chmod 700 "$HOME/.polixor"
umask 077

if [ -n "${POLIXOR_ACCESS_PASSWORD:-}" ]; then
    printf '%s' "$POLIXOR_ACCESS_PASSWORD" > "$HOME/.polixor/password"
    SHOWN="(the password you saved in the POLIXOR_ACCESS_PASSWORD Codespaces secret)"
elif [ -s "$HOME/.polixor/password" ]; then
    SHOWN="$(cat "$HOME/.polixor/password")"
else
    SHOWN="$(python3 -c 'import secrets; print("-".join(secrets.token_urlsafe(4) for _ in range(3)))')"
    printf '%s' "$SHOWN" > "$HOME/.polixor/password"
fi

cat > "$ROOT/POLIXOR-PASSWORD.txt" <<TXT
Polixor – your testing environment
==================================

Password:  $SHOWN

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

This file is not saved to git.
TXT
chmod 644 "$ROOT/POLIXOR-PASSWORD.txt"
echo "Polixor password ready."
