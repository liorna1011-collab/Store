"""
מדידת זמן לתתי-שלבים (למשל analyze → visual/faces, layout, semantic).

StageTiming בבסיס הנתונים מודד שלבים שלמים. כדי לדעת *איפה* הזמן הולך
בתוך שלב, קוד הפייפליין עוטף חלקים יקרים ב-`substage(...)`. המדידות
נאספות למשימה הפעילה (נקבעת ב-`collect(job_id)`) ונשמרות בארטיפקטים
של המשימה, מהם הן מוצגות בדוח הביצועים ובכלי scripts/profile_media.py.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator, Optional

_lock = threading.Lock()
_active: dict[int, list[dict[str, Any]]] = {}      # thread id → מדידות


@contextmanager
def collect() -> Iterator[list[dict[str, Any]]]:
    """מתחיל איסוף מדידות בתהליכון הנוכחי ומחזיר את הרשימה שמתמלאת."""
    tid = threading.get_ident()
    items: list[dict[str, Any]] = []
    with _lock:
        prev = _active.get(tid)
        _active[tid] = items
    try:
        yield items
    finally:
        with _lock:
            if prev is None:
                _active.pop(tid, None)
            else:
                _active[tid] = prev


def record(name: str, seconds: float, media_seconds: float = 0.0,
           **extra: Any) -> None:
    with _lock:
        items = _active.get(threading.get_ident())
        if items is None:
            return
        entry = {"name": name, "seconds": round(float(seconds), 3),
                 "media_seconds": round(float(media_seconds), 3)}
        entry.update(extra)
        items.append(entry)


@contextmanager
def substage(name: str, media_seconds: float = 0.0, **extra: Any) -> Iterator[None]:
    """עוטף קטע קוד ורושם כמה זמן לקח (אם יש איסוף פעיל)."""
    t0 = time.perf_counter()
    try:
        yield
    finally:
        record(name, time.perf_counter() - t0, media_seconds, **extra)


def rtf(seconds: float, media_seconds: float) -> Optional[float]:
    """Real-time factor: זמן עיבוד חלקי אורך החומר (0.5 = פי 2 ממהירות אמת)."""
    if media_seconds <= 0:
        return None
    return round(seconds / media_seconds, 4)
