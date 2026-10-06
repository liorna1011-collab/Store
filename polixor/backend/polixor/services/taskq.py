"""
Durable task queue: heavy work lives in the database, not in a web-server thread.

The web process only records what has to be done (`enqueue`); worker processes (workerd.py)
claim it, renew a lease while they work, and record the outcome. Everything survives a
restart of either side:

  * a web restart (new version, crash, a Codespace that slept): the workers keep working –
    the browser was never needed and neither is the web server
  * a worker that dies (OOM, kill -9, the machine rebooted): its lease is not renewed and
    expires, the task returns to the queue and another worker continues it from the job's
    checkpoints (stages, transcript chunks, model answers and finished clips are reused); after
    MAX_ATTEMPTS the job is marked "needs attention" instead of looping
  * idempotency: one active (queued / leased) task per key – a double click, a retry, a resume
    or a restart never queues the same work twice (a partial unique index enforces it)
  * cancel: a queued task is cancelled at once; a running one gets `cancel_requested`, which its
    worker sees within a second (FFmpeg is terminated, the pipeline stops at its next check)

Claim order: lowest `priority` first, then oldest. An interactive re-render (the user is
waiting) beats a project's first results, which beat further Shorts, which beat long-form.

POLIXOR_WORKER_MODE=process turns this on (the server entry point sets it; tests and
`run_job` called directly keep running work in-process).
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from ..db import get_engine

log = logging.getLogger("polixor.taskq")

LEASE_SECONDS = float(os.environ.get("POLIXOR_LEASE_SECONDS", "45"))   # renewed every RENEW_EVERY; a missed lease = a lost worker
RENEW_EVERY = 1.0             # also how fast a running task sees a cancel
MAX_ATTEMPTS = 3              # claims per task before the job needs attention (crash-loop guard)

PRIO_INTERACTIVE = 0          # a re-render the user is waiting for
PRIO_FIRST = 10               # a project's analysis and generation up to its first results
PRIO_SHORTS = 20
PRIO_LONGFORM = 40

ACTIVE = ("queued", "leased")


def process_mode() -> bool:
    return os.environ.get("POLIXOR_WORKER_MODE", "inline").strip().lower() == "process"


def owner_id() -> str:
    return f"{socket.gethostname()[:24]}:{os.getpid()}"


def _row(r: Any) -> dict[str, Any]:
    d = dict(r._mapping)
    if isinstance(d.get("payload"), str):
        try:
            d["payload"] = json.loads(d["payload"])
        except ValueError:
            d["payload"] = {}
    return d


# --------------------------------------------------------------------------
# producer side (web)
# --------------------------------------------------------------------------
def enqueue(kind: str, job_id: str, *, key: str, priority: int = PRIO_SHORTS, ref: str = "",
            payload: Optional[dict[str, Any]] = None) -> int:
    """The id of the active task for `key` – a new one, or the one already queued / running."""
    now = time.time()
    eng = get_engine()
    for _ in range(3):
        with eng.begin() as c:
            # a lease nobody renews any more belongs to a dead worker: that task is lost
            c.execute(text("UPDATE tasks SET status='lost', finished_at=:now WHERE idempotency_key=:k "
                           "AND status='leased' AND lease_expires < :now"), {"k": key, "now": now})
            r = c.execute(text("SELECT id, priority FROM tasks WHERE idempotency_key=:k AND status IN "
                               "('queued','leased') ORDER BY id DESC LIMIT 1"), {"k": key}).first()
            if r is not None:
                if priority < r.priority:
                    c.execute(text("UPDATE tasks SET priority=:p WHERE id=:id AND status='queued'"),
                              {"p": priority, "id": r.id})
                return int(r.id)
            try:
                res = c.execute(text(
                    "INSERT INTO tasks (kind, job_id, ref, priority, status, payload, idempotency_key, attempts,"
                    " lease_owner, lease_expires, heartbeat_at, cancel_requested, error, created_at, started_at,"
                    " finished_at) VALUES (:kind, :job, :ref, :p, 'queued', :payload, :k, 0, '', 0, 0, 0, '', :now,"
                    " 0, 0)"), {"kind": kind, "job": job_id, "ref": ref, "p": priority,
                                "payload": json.dumps(payload or {}, ensure_ascii=False), "k": key, "now": now})
                _invalidate()
                return int(res.lastrowid)
            except IntegrityError:
                continue                       # another producer won the race: read theirs
    raise RuntimeError(f"task {key} could not be queued")


def request_cancel(job_id: str = "", *, task_id: int = 0) -> str:
    """'queued' (cancelled before it started), 'running' (its worker will stop it) or ''."""
    where = "id=:tid" if task_id else "job_id=:jid"
    args = {"tid": task_id, "jid": job_id, "now": time.time()}
    with get_engine().begin() as c:
        q = c.execute(text(f"UPDATE tasks SET status='cancelled', finished_at=:now WHERE {where} "
                           "AND status='queued'"), args).rowcount
        r = c.execute(text(f"UPDATE tasks SET cancel_requested=1 WHERE {where} AND status='leased' "
                           "AND lease_expires >= :now"), args).rowcount
    _invalidate()
    return "running" if r else ("queued" if q else "")


# --------------------------------------------------------------------------
# consumer side (worker)
# --------------------------------------------------------------------------
def claim(owner: str, kinds: Optional[tuple[str, ...]] = None) -> Optional[dict[str, Any]]:
    """Atomically takes the most urgent queued task (one statement: no two workers get the same)."""
    now = time.time()
    kind_sql = ""
    args: dict[str, Any] = {"o": owner, "now": now, "exp": now + LEASE_SECONDS}
    if kinds:
        names = [f":k{i}" for i in range(len(kinds))]
        kind_sql = f" AND kind IN ({', '.join(names)})"
        args.update({f"k{i}": k for i, k in enumerate(kinds)})
    with get_engine().begin() as c:
        r = c.execute(text(
            "UPDATE tasks SET status='leased', lease_owner=:o, lease_expires=:exp, heartbeat_at=:now,"
            " attempts=attempts+1, started_at=:now WHERE id = (SELECT id FROM tasks WHERE status='queued'"
            f"{kind_sql} ORDER BY priority, id LIMIT 1) AND status='queued' RETURNING id, kind, job_id, ref,"
            " priority, payload, attempts, cancel_requested, created_at"), args).first()
    if r is None:
        return None
    _invalidate()
    return _row(r)


def renew(task_id: int, owner: str) -> tuple[bool, bool]:
    """(still ours, cancel requested). Not ours = the lease expired and someone else took it."""
    now = time.time()
    with get_engine().begin() as c:
        r = c.execute(text("UPDATE tasks SET lease_expires=:exp, heartbeat_at=:now WHERE id=:id AND "
                           "lease_owner=:o AND status='leased' RETURNING cancel_requested"),
                      {"exp": now + LEASE_SECONDS, "now": now, "id": task_id, "o": owner}).first()
    if r is None:
        return False, True
    return True, bool(r.cancel_requested)


def finish(task_id: int, owner: str, status: str = "done", error: str = "") -> None:
    with get_engine().begin() as c:
        c.execute(text("UPDATE tasks SET status=:st, error=:err, finished_at=:now, lease_expires=0 "
                       "WHERE id=:id AND lease_owner=:o AND status='leased'"),
                  {"st": status, "err": error[:2000], "now": time.time(), "id": task_id, "o": owner})
    _invalidate()


def reclaim_expired() -> dict[str, list[dict[str, Any]]]:
    """
    Leases nobody renewed: back to the queue (the job continues from its checkpoints), or –
    after MAX_ATTEMPTS – given up (the caller marks the job "needs attention").
    """
    now = time.time()
    with get_engine().begin() as c:
        rows = [_row(r) for r in c.execute(text(
            "SELECT id, kind, job_id, ref, attempts, lease_owner FROM tasks WHERE status='leased' AND "
            "lease_expires < :now"), {"now": now}).all()]
        requeued, gave_up = [], []
        for r in rows:
            if int(r["attempts"]) >= MAX_ATTEMPTS:
                c.execute(text("UPDATE tasks SET status='failed', error='worker lost', finished_at=:now "
                               "WHERE id=:id AND status='leased'"), {"id": r["id"], "now": now})
                gave_up.append(r)
            else:
                c.execute(text("UPDATE tasks SET status='queued', lease_owner='', lease_expires=0 "
                               "WHERE id=:id AND status='leased'"), {"id": r["id"]})
                requeued.append(r)
    if rows:
        _invalidate()
        log.warning("tasks with an expired lease: requeued %s, gave up %s",
                    [r["id"] for r in requeued], [r["id"] for r in gave_up])
    return {"requeued": requeued, "gave_up": gave_up}


# --------------------------------------------------------------------------
# queries (cached briefly: the project list asks for every project)
# --------------------------------------------------------------------------
_cache: dict[str, Any] = {"at": 0.0, "active": set(), "running": set()}
_cache_lock = threading.Lock()


def _invalidate() -> None:
    with _cache_lock:
        _cache["at"] = 0.0


def _refresh() -> None:
    now = time.time()
    with _cache_lock:
        if now - _cache["at"] < 1.0:
            return
    with get_engine().connect() as c:
        rows = c.execute(text("SELECT job_id, status, lease_expires FROM tasks WHERE kind='job' AND status IN "
                              "('queued','leased')")).all()
    active = {r.job_id for r in rows if r.status == "queued" or r.lease_expires >= now}
    running = {r.job_id for r in rows if r.status == "leased" and r.lease_expires >= now}
    with _cache_lock:
        _cache.update(at=now, active=active, running=running)


def active_jobs() -> set[str]:
    """Projects with a queued or running task (a lease that is still renewed)."""
    _refresh()
    with _cache_lock:
        return set(_cache["active"])


def running_jobs() -> set[str]:
    _refresh()
    with _cache_lock:
        return set(_cache["running"])


def waiting_before(priority: int) -> bool:
    """Is queued work more urgent than `priority` waiting for a worker?"""
    with get_engine().connect() as c:
        return c.execute(text("SELECT 1 FROM tasks WHERE status='queued' AND priority < :p LIMIT 1"),
                         {"p": priority}).first() is not None


def active_task(kind: str, ref: str) -> Optional[dict[str, Any]]:
    now = time.time()
    with get_engine().connect() as c:
        r = c.execute(text("SELECT id, status, lease_expires FROM tasks WHERE kind=:k AND ref=:r AND status IN "
                           "('queued','leased') ORDER BY id DESC LIMIT 1"), {"k": kind, "r": ref}).first()
    if r is None or (r.status == "leased" and r.lease_expires < now):
        return None
    return {"id": r.id, "status": r.status}


def snapshot(limit: int = 30) -> dict[str, Any]:
    """Admin view: the queue, recent tasks, the worker processes."""
    now = time.time()
    with get_engine().connect() as c:
        counts = {r.status: r.n for r in c.execute(text(
            "SELECT status, COUNT(*) AS n FROM tasks GROUP BY status")).all()}
        recent = [{**_row(r), "lease_left": round(r.lease_expires - now, 1) if r.status == "leased" else None}
                  for r in c.execute(text(
                      "SELECT id, kind, job_id, ref, priority, status, attempts, lease_owner, lease_expires, "
                      "cancel_requested, error, created_at, started_at, finished_at FROM tasks "
                      "ORDER BY id DESC LIMIT :n"), {"n": limit}).all()]
        workers = [dict(r._mapping) for r in c.execute(text(
            "SELECT id, pid, role, build, state, task_id, started_at, heartbeat_at FROM worker_processes "
            "WHERE heartbeat_at > :t ORDER BY started_at"), {"t": now - 3600}).all()]
    for w in workers:
        w["alive"] = now - float(w["heartbeat_at"] or 0) < LEASE_SECONDS
    return {"mode": "process" if process_mode() else "inline", "counts": counts, "tasks": recent,
            "workers": workers}


def prune(keep_seconds: float = 7 * 86400) -> None:
    """Finished tasks and relayed events are kept for a while (admin view), then removed."""
    cut = time.time() - keep_seconds
    with get_engine().begin() as c:
        c.execute(text("DELETE FROM tasks WHERE status NOT IN ('queued','leased') AND finished_at > 0 "
                       "AND finished_at < :c"), {"c": cut})
        c.execute(text("DELETE FROM event_relay WHERE ts < :c"), {"c": time.time() - 3600})
        c.execute(text("DELETE FROM worker_processes WHERE heartbeat_at < :c"), {"c": time.time() - 86400})


__all__ = ["process_mode", "enqueue", "request_cancel", "claim", "renew", "finish", "reclaim_expired",
           "active_jobs", "running_jobs", "active_task", "snapshot", "prune", "owner_id",
           "PRIO_INTERACTIVE", "PRIO_FIRST", "PRIO_SHORTS", "PRIO_LONGFORM"]
