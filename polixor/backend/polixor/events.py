"""
אפיק אירועים בתהליך: שלב העיבוד רץ בתהליכון רקע ומפרסם אירועים,
וה-WebSocket (asyncio) צורך אותם ומעביר לדפדפן.

הגשר בין העולמות נעשה עם `loop.call_soon_threadsafe`.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class Event:
    type: str                       # job.progress | job.status | job.log | clip.updated | ...
    job_id: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "job_id": self.job_id, "data": self.data, "ts": self.ts}


class EventBus:
    """Pub/Sub בטוח לשימוש מתהליכונים ומ-asyncio כאחד."""

    def __init__(self, history: int = 300) -> None:
        self._lock = threading.RLock()
        self._subscribers: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue[Event]]] = []
        self._history: deque[Event] = deque(maxlen=history)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # in a worker process: every event is also handed to the relay (services/relay.py), which
        # passes it through the database to the web process and its WebSocket clients
        self.forward: Optional[Callable[[Event], None]] = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """נקרא בעליית השרת כדי שתהליכוני עבודה ידעו לאן לפרסם."""
        self._loop = loop

    # ---- צד המפרסם (תהליכון עבודה) ----
    def publish(self, event: Event) -> None:
        with self._lock:
            self._history.append(event)
            subs = list(self._subscribers)

        for loop, queue in subs:
            try:
                loop.call_soon_threadsafe(_safe_put, queue, event)
            except RuntimeError:
                # לולאת האירועים נסגרה – מסירים את המנוי
                self.unsubscribe(loop, queue)
        fwd = self.forward
        if fwd is not None:
            try:
                fwd(event)
            except Exception:                   # noqa: BLE001 – an event is never worth a failed job
                pass

    def emit(self, type_: str, job_id: str = "", **data: Any) -> None:
        self.publish(Event(type=type_, job_id=job_id, data=data))

    # ---- צד המנוי (WebSocket) ----
    def subscribe(self) -> tuple[asyncio.AbstractEventLoop, asyncio.Queue[Event]]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=1000)
        with self._lock:
            self._subscribers.append((loop, queue))
        return loop, queue

    def unsubscribe(self, loop: asyncio.AbstractEventLoop,
                    queue: asyncio.Queue[Event]) -> None:
        with self._lock:
            self._subscribers = [
                (l, q) for (l, q) in self._subscribers if not (l is loop and q is queue)
            ]

    def recent(self, job_id: str = "", limit: int = 50) -> list[Event]:
        with self._lock:
            items = list(self._history)
        if job_id:
            items = [e for e in items if e.job_id == job_id]
        return items[-limit:]

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)


def _safe_put(queue: "asyncio.Queue[Event]", event: Event) -> None:
    """הוספה לתור בלי לחסום; אם התור מלא – מוותרים על האירוע הישן ביותר."""
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()
            queue.put_nowait(event)
        except Exception:
            pass


BUS = EventBus()
