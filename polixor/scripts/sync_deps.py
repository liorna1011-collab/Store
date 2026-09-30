"""
Keeps an existing Polixor environment in step with backend/requirements.txt.

The launchers install packages only on the first run. When requirements.txt
changes later (for example the PyAV pin: faster-whisper 1.2.1 breaks with
PyAV 19), an existing environment would keep the old packages. This script
stores a hash of requirements.txt inside the environment and re-runs
`pip install -r` only when the file has changed since the last install.

    python scripts/sync_deps.py          # install if requirements changed
    python scripts/sync_deps.py --mark   # record the current file as installed

Messages are in English on purpose: the Windows console shows Hebrew reversed.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "backend" / "requirements.txt"
MARKER = Path(sys.prefix) / ".polixor-requirements"


def current_hash() -> str:
    return hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()


def main(argv: list[str]) -> int:
    if not REQUIREMENTS.exists():
        return 0
    want = current_hash()
    if "--mark" in argv:
        MARKER.write_text(want, encoding="ascii")
        return 0
    try:
        have = MARKER.read_text(encoding="ascii").strip()
    except OSError:
        have = ""
    if have == want:
        return 0
    print("Updating Python packages to match requirements.txt ...", flush=True)
    code = subprocess.call([sys.executable, "-m", "pip", "install", "-q",
                            "-r", str(REQUIREMENTS)])
    if code != 0:
        print("Package update failed - Polixor starts with the packages it has.")
        return 0
    MARKER.write_text(want, encoding="ascii")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
