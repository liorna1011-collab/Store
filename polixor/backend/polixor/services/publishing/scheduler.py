"""
המתזמן: תהליכון רקע בתוך שרת Polixor שמריץ את service.tick כל TICK שניות
(ומיד כשנוצרת בקשה חדשה).

עמידות: כל המצב נמצא ב-DB (לא בזיכרון), ולכן הפעלה מחדש של השרת ממשיכה
מאותה נקודה – פרסומים מתוזמנים נשארים, העלאות שנקטעו מסומנות לבדיקה.
אין תלות ב-Codespaces: אותו קוד רץ על VPS, בענן או במחשב שדלוק.
תזמון שהפלטפורמה עצמה מבצעת לא תלוי בכלל בכך שהשרת פעיל.

POLIXOR_SCHEDULER=0 מכבה את התהליכון (למשל כשרצים כמה עותקים והמתזמן
רץ בתהליך נפרד: python -m polixor.services.publishing.scheduler).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

log = logging.getLogger("polixor.scheduler")

TICK = 20.0
_wake = threading.Event()
_stop = threading.Event()
_thread: Optional[threading.Thread] = None
_last_tick: dict[str, float] = {"at": 0.0}


def wake() -> None:
    _wake.set()


def enabled() -> bool:
    return os.environ.get("POLIXOR_SCHEDULER", "1").strip() not in ("0", "false", "off")


def _loop() -> None:
    from ...config import SETTINGS
    from . import service

    log.info("publishing scheduler started (every %.0fs)", TICK)
    while not _stop.is_set():
        try:
            service.tick(SETTINGS.get())
            _last_tick["at"] = time.time()
        except Exception:                               # noqa: BLE001
            log.exception("scheduler tick failed")
        _wake.wait(TICK)
        _wake.clear()


def start() -> bool:
    global _thread
    if not enabled() or (_thread is not None and _thread.is_alive()):
        return False
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="polixor-scheduler", daemon=True)
    _thread.start()
    return True


def stop() -> None:
    _stop.set()
    _wake.set()


def status() -> dict[str, object]:
    return {"running": bool(_thread and _thread.is_alive()), "enabled": enabled(),
            "last_tick": _last_tick["at"] or None, "interval_seconds": TICK}


if __name__ == "__main__":                              # מתזמן בתהליך נפרד
    from ...config import PATHS
    from ...db import init_db

    logging.basicConfig(level=logging.INFO)
    PATHS.ensure()
    init_db()
    _loop()
