"""
A Polixor worker process: claims heavy work from the durable queue and does it.

    python -m polixor.workerd [--role general|interactive] [--once]

The web server starts these itself (services/workers.py) when POLIXOR_WORKER_MODE=process; they
are separate operating-system processes, so a render, a speech model or an FFmpeg pass can
never slow down a page, an upload or a WebSocket. They need no browser and no web server: a
closed tab, a phone that went to sleep or a restart of the web server do not touch them.

  * claim → lease renewed every second (taskq.RENEW_EVERY); the same tick reads the cancel flag
  * a stop signal (SIGTERM / Ctrl+C) stops the job at its next checkpoint and returns the task to
    the queue – the next worker continues from the job's checkpoints
  * the code on disk changed (git pull + restart): the worker finishes its task with the code it
    started with, then exits; the supervisor starts one with the new code
  * no web server heartbeat for WEB_GONE seconds while idle: the worker exits (nothing would
    ever give it work again)

Roles: `general` takes any task (most urgent first); `interactive` takes only re-renders the
user is waiting for, so one is always free for them however busy the general workers are.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import signal
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("polixor.workerd")

ROLE_KINDS: dict[str, Optional[tuple[str, ...]]] = {
    "general": None,
    "interactive": ("reexport",),
}
WEB_GONE = 180.0
CODE_CHECK = 20.0


def code_build() -> str:
    """A fingerprint of the backend code on disk (and of the paid-AI switch, which the worker inherits)."""
    root = Path(__file__).resolve().parent
    h = hashlib.sha1()
    for p in sorted(root.rglob("*.py")):
        try:
            st = p.stat()
        except OSError:
            continue
        h.update(f"{p.relative_to(root)}:{st.st_mtime_ns}:{st.st_size};".encode())
    h.update(os.environ.get("POLIXOR_PAID_AI", "").encode())
    return h.hexdigest()[:16]


class Worker:
    def __init__(self, role: str = "general") -> None:
        from .services import taskq

        self.role = role if role in ROLE_KINDS else "general"
        self.kinds = ROLE_KINDS[self.role]
        self.owner = f"{taskq.owner_id()}:{int(time.time() * 1000) % 100000}"
        self.build = code_build()
        self.stop = threading.Event()
        self.cancel: Optional[threading.Event] = None
        self.task: Optional[dict[str, Any]] = None
        self.started = time.time()

    # ---- registry ----
    def _register(self, state: str) -> None:
        from sqlalchemy import text

        from .db import get_engine

        now = time.time()
        with get_engine().begin() as c:
            c.execute(text(
                "INSERT INTO worker_processes (id, pid, role, build, state, task_id, started_at, heartbeat_at, info)"
                " VALUES (:id, :pid, :role, :b, :st, :t, :s, :now, '{}') ON CONFLICT(id) DO UPDATE SET"
                " state=:st, task_id=:t, heartbeat_at=:now"),
                {"id": self.owner, "pid": os.getpid(), "role": self.role, "b": self.build, "st": state,
                 "t": int((self.task or {}).get("id") or 0), "s": self.started, "now": now})

    def _web_build(self) -> str:
        """The build of the most recently started live web server ('' when none)."""
        from sqlalchemy import text

        from .db import get_engine

        try:
            with get_engine().connect() as c:
                r = c.execute(text("SELECT build FROM worker_processes WHERE role='web' AND heartbeat_at > :t "
                                   "ORDER BY started_at DESC LIMIT 1"), {"t": time.time() - 30}).first()
        except Exception:                          # noqa: BLE001
            return ""
        return str(r.build) if r else ""

    def _web_alive(self) -> bool:
        from sqlalchemy import text

        from .db import get_engine

        with get_engine().connect() as c:
            t = c.execute(text("SELECT MAX(heartbeat_at) FROM worker_processes WHERE role='web'")).scalar()
        return bool(t) and time.time() - float(t) < WEB_GONE

    # ---- work ----
    def execute(self, task: dict[str, Any], cancel: threading.Event) -> None:
        kind = task["kind"]
        if kind == "job":
            from .pipeline import MANAGER  # noqa: F401 – importing the pipeline installs the runner

            MANAGER.run_claimed(task["job_id"], cancel, float(task.get("created_at") or 0))
        elif kind == "reexport":
            from .api.routes_clips import ReExportRequest, run_reexport_task

            p = dict(task.get("payload") or {})
            run_reexport_task(task["ref"], ReExportRequest(**(p.get("request") or {})), p.get("lang") or "he")
        else:
            raise RuntimeError(f"unknown task kind {kind}")

    def run_one(self, task: dict[str, Any]) -> str:
        from .services import taskq

        self.task = task
        self.cancel = threading.Event()
        if task.get("cancel_requested"):
            self.cancel.set()
        self._register("busy")
        err: list[str] = []

        def body() -> None:
            try:
                self.execute(task, self.cancel)       # type: ignore[arg-type]
            except BaseException as exc:              # noqa: BLE001 – recorded on the task
                log.exception("task %s crashed", task["id"])
                err.append(f"{type(exc).__name__}: {exc}")

        t = threading.Thread(target=body, name=f"polixor-task-{task['id']}", daemon=True)
        t.start()
        last_reg = time.time()
        while t.is_alive():
            t.join(taskq.RENEW_EVERY)
            if not t.is_alive():
                break
            try:
                ours, cancel = taskq.renew(task["id"], self.owner)
            except Exception:                          # noqa: BLE001 – a locked database: try again next tick
                log.debug("lease renewal failed", exc_info=True)
                continue
            if cancel or not ours or self.stop.is_set():
                self.cancel.set()
            if time.time() - last_reg > 10:
                last_reg = time.time()
                try:
                    self._register("busy")
                except Exception:                      # noqa: BLE001
                    pass
        if self.stop.is_set() and task["kind"] == "job" and not self._cancel_requested(task["id"]):
            # a stop, not a cancel: the job stays RUNNING and the task goes back to the queue
            status = "queued"
            _release(task["id"], self.owner)
        else:
            status = "failed" if err else ("cancelled" if self.cancel.is_set() else "done")
            taskq.finish(task["id"], self.owner, status, err[0] if err else "")
        self.task, self.cancel = None, None
        return status

    @staticmethod
    def _cancel_requested(task_id: int) -> bool:
        from sqlalchemy import text

        from .db import get_engine

        with get_engine().connect() as c:
            return bool(c.execute(text("SELECT cancel_requested FROM tasks WHERE id=:i"), {"i": task_id}).scalar())

    def loop(self, once: bool = False) -> None:
        from .services import taskq

        last_code, idle_since = time.time(), time.time()
        self._register("idle")
        while not self.stop.is_set():
            task = None
            try:
                task = taskq.claim(self.owner, self.kinds)
            except Exception:                          # noqa: BLE001 – a locked database: try again
                log.debug("claim failed", exc_info=True)
            if task is not None:
                log.info("worker %s (%s): task %s %s %s", os.getpid(), self.role, task["id"], task["kind"],
                         task["job_id"] or task["ref"])
                self.run_one(task)
                idle_since = time.time()
                self._register("idle")
                if once:
                    return
                continue
            if once:
                return
            now = time.time()
            if now - last_code > CODE_CHECK:
                last_code = now
                if code_build() != self.build or self._web_build() not in ("", self.build):
                    # new code on disk, or the web server runs another version / paid-AI setting
                    log.info("worker %s: a new version is running – exiting so it takes over", os.getpid())
                    return
                try:
                    if now - idle_since > WEB_GONE and not self._web_alive():
                        log.info("worker %s: no web server for %.0fs – exiting", os.getpid(), WEB_GONE)
                        return
                except Exception:                      # noqa: BLE001
                    pass
            try:
                self._register("idle")
            except Exception:                          # noqa: BLE001
                pass
            self.stop.wait(0.5)

    def shutdown(self, *_: Any) -> None:
        self.stop.set()
        if self.cancel is not None:
            from .worker import MANAGER

            MANAGER._shutting_down = True             # a stop, not a user cancel (job stays RUNNING)
            self.cancel.set()


def _release(task_id: int, owner: str) -> None:
    """Back to the queue at once (the attempt is not counted: nothing failed)."""
    from sqlalchemy import text

    from .db import get_engine

    with get_engine().begin() as c:
        c.execute(text("UPDATE tasks SET status='queued', lease_owner='', lease_expires=0, "
                       "attempts=MAX(0, attempts-1) WHERE id=:i AND lease_owner=:o AND status='leased'"),
                  {"i": task_id, "o": owner})


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Polixor worker process")
    ap.add_argument("--role", default=os.environ.get("POLIXOR_WORKER_ROLE", "general"))
    ap.add_argument("--once", action="store_true", help="run at most one task, then exit (tests)")
    a = ap.parse_args(argv)
    # inside a worker the job manager runs work in-process (it IS the worker)
    os.environ["POLIXOR_WORKER_MODE"] = "inline"
    os.environ["POLIXOR_IN_WORKER"] = "1"
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format=f"%(asctime)s %(levelname)s worker[{os.getpid()}] %(name)s: %(message)s")
    from .config import PATHS
    from .db import init_db
    from .services import health, relay
    from .util import profiler

    PATHS.ensure()
    init_db()
    profiler.install()
    relay.start_writer()                    # this process's events reach the browser via the web server
    from . import pipeline  # noqa: F401 – installs the job runner

    health.start(orphans=False)             # heartbeats + stalled-job detection for this worker's jobs
    w = Worker(a.role)
    signal.signal(signal.SIGTERM, w.shutdown)
    signal.signal(signal.SIGINT, w.shutdown)
    log.info("worker %s ready (role %s, build %s, host %s)", os.getpid(), w.role, w.build, socket.gethostname())
    try:
        w.loop(once=a.once)
    finally:
        try:
            w._register("stopped")
        except Exception:                   # noqa: BLE001
            pass
        relay.flush()
        relay.stop()
        health.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
