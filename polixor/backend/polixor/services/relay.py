"""
Events across processes: a worker's progress reaches the browser through the database.

Worker side (`start_writer`): every BUS event is buffered and written in one transaction every
FLUSH seconds (progress is throttled at the source, so this is a handful of rows per second).
Web side (`start_reader`): new rows are read every POLL seconds and published on the web
process's BUS, where the WebSocket clients get them exactly as if the work ran in-process.
Old rows are pruned (taskq.prune); a web restart starts after the newest row (no replay).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Optional

from sqlalchemy import text

from ..db import get_engine
from ..events import BUS, Event

log = logging.getLogger("polixor.relay")

FLUSH = 0.2
POLL = 0.25

_buf: list[Event] = []
_buf_lock = threading.Lock()
_stop = threading.Event()
_threads: list[threading.Thread] = []


def _buffer(ev: Event) -> None:
    with _buf_lock:
        _buf.append(ev)
        if len(_buf) > 5000:                    # the database is unreachable: keep the newest
            del _buf[:1000]


def flush() -> int:
    with _buf_lock:
        items = list(_buf)
        _buf.clear()
    if not items:
        return 0
    try:
        with get_engine().begin() as c:
            c.execute(text("INSERT INTO event_relay (type, job_id, data, ts) VALUES (:t, :j, :d, :ts)"),
                      [{"t": e.type, "j": e.job_id, "d": json.dumps(e.data, ensure_ascii=False, default=str),
                        "ts": e.ts} for e in items])
    except Exception:                           # noqa: BLE001
        log.warning("event relay write failed (%d events kept)", len(items), exc_info=True)
        with _buf_lock:
            _buf[:0] = items
    return len(items)


def _writer() -> None:
    while not _stop.wait(FLUSH):
        flush()
    flush()


def start_writer() -> None:
    BUS.forward = _buffer
    t = threading.Thread(target=_writer, name="polixor-relay-out", daemon=True)
    t.start()
    _threads.append(t)


def _last_id() -> int:
    with get_engine().connect() as c:
        return int(c.execute(text("SELECT COALESCE(MAX(id), 0) FROM event_relay")).scalar() or 0)


def read_new(after: int) -> tuple[int, int]:
    """Publishes the rows after `after` on this process's BUS. Returns (new last id, count)."""
    with get_engine().connect() as c:
        rows = c.execute(text("SELECT id, type, job_id, data, ts FROM event_relay WHERE id > :a ORDER BY id "
                              "LIMIT 2000"), {"a": after}).all()
    for r in rows:
        try:
            data = json.loads(r.data) if isinstance(r.data, str) else (r.data or {})
        except ValueError:
            data = {}
        BUS.publish(Event(type=r.type, job_id=r.job_id, data=data, ts=r.ts))
        after = r.id
    return after, len(rows)


def _reader(start: Optional[int]) -> None:
    last = _last_id() if start is None else start
    while not _stop.wait(POLL):
        try:
            last, _ = read_new(last)
        except Exception:                       # noqa: BLE001 – the reader never dies
            log.debug("event relay read failed", exc_info=True)


def start_reader(start: Optional[int] = None) -> None:
    t = threading.Thread(target=_reader, args=(start,), name="polixor-relay-in", daemon=True)
    t.start()
    _threads.append(t)


def stop() -> None:
    _stop.set()


__all__ = ["start_writer", "start_reader", "flush", "read_new", "stop"]
