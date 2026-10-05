"""
Report files read by the interface on every poll (intel_report.json can be megabytes on a
long source): parsed once per version of the file (path + mtime + size), kept for a few
files. A changed file is read again; a missing or broken one is None.
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

_CACHE: "OrderedDict[str, tuple[tuple[int, int], Any]]" = OrderedDict()
_LOCK = threading.Lock()
MAX_FILES = 24


def read_json(path: Any) -> Any:
    if not path:
        return None
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return None
    key, ver = str(p), (st.st_mtime_ns, st.st_size)
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and hit[0] == ver:
            _CACHE.move_to_end(key)
            return hit[1]
    try:
        data = json.loads(p.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    with _LOCK:
        _CACHE[key] = (ver, data)
        _CACHE.move_to_end(key)
        while len(_CACHE) > MAX_FILES:
            _CACHE.popitem(last=False)
    return data
