"""
Worker processes and the durable task queue (services/taskq.py, services/workers.py, workerd.py).

  queue      idempotent enqueue (double click / restart), priority order, atomic claim by
             concurrent workers, lease renewal, cancel (queued and running), an expired lease
             returns the task, a task that keeps losing its worker is given up
  process    a real project runs in a SEPARATE worker process (scripted editor, fixture
             transcript, POLIXOR_PAID_AI=off): the web process does no heavy work, the events
             reach the web process's bus through the relay, the job completes with Shorts
  crash      the worker is killed (SIGKILL) in the middle of a job: its lease expires, another
             worker continues the job from its checkpoints and completes it
  cancel     a cancel from the web process stops the job in the worker within seconds
  priority   an interactive re-render is claimed before queued project work

Run:  python3 tests/test_workers.py
"""

from __future__ import annotations

import os
import signal
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxwork_"))
os.environ["POLIXOR_PAID_AI"] = "off"
os.environ["POLIXOR_SEMANTIC_SCRIPTED"] = "1"
os.environ["POLIXOR_LEASE_SECONDS"] = "6"

from polixor.config import PATHS, SETTINGS, AppSettings            # noqa: E402
from polixor.db import init_db, session_scope                      # noqa: E402

PATHS.ensure()
init_db()

from polixor.models import Clip, ClipKind, ClipStatus, Job, JobStatus, ProjectPhase, RunScope, new_id  # noqa: E402
from polixor.services import taskq                                 # noqa: E402

TEST_VIDEO = Path(os.environ.get("POLIXOR_TEST_VIDEO", "/home/claude/testdata/polixor_test_stream.mp4"))


def _wait(fn, timeout: float = 20.0, step: float = 0.2) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(step)
    return False


def _clear_tasks() -> None:
    from sqlalchemy import text

    from polixor.db import get_engine

    with get_engine().begin() as c:
        c.execute(text("DELETE FROM tasks"))


# --------------------------------------------------------------------------
# the queue
# --------------------------------------------------------------------------
def test_enqueue_is_idempotent_per_key():
    _clear_tasks()
    a = taskq.enqueue("job", "j1", key="job:j1", priority=20)
    b = taskq.enqueue("job", "j1", key="job:j1", priority=20)
    assert a == b
    # a more urgent request for the same work raises its priority, never adds a second task
    c = taskq.enqueue("job", "j1", key="job:j1", priority=10)
    assert c == a
    t = taskq.claim("w1")
    assert t is not None and t["id"] == a and t["priority"] == 10
    taskq.finish(a, "w1", "done")
    # finished work can be queued again (a later regenerate)
    d = taskq.enqueue("job", "j1", key="job:j1")
    assert d != a


def test_claim_order_is_priority_then_age():
    _clear_tasks()
    long_ = taskq.enqueue("job", "a", key="k:a", priority=taskq.PRIO_LONGFORM)
    shorts = taskq.enqueue("job", "b", key="k:b", priority=taskq.PRIO_SHORTS)
    first = taskq.enqueue("job", "c", key="k:c", priority=taskq.PRIO_FIRST)
    rerender = taskq.enqueue("reexport", "d", ref="clip1", key="k:d", priority=taskq.PRIO_INTERACTIVE)
    got = [taskq.claim("w")["id"] for _ in range(4)]
    assert got == [rerender, first, shorts, long_], got
    assert taskq.claim("w") is None


def test_role_filter_keeps_the_interactive_worker_for_rerenders():
    _clear_tasks()
    taskq.enqueue("job", "a", key="k:a", priority=0)
    assert taskq.claim("w", ("reexport",)) is None
    r = taskq.enqueue("reexport", "a", ref="c", key="k:r", priority=0)
    assert taskq.claim("w", ("reexport",))["id"] == r


def test_concurrent_claims_never_hand_out_a_task_twice():
    _clear_tasks()
    ids = {taskq.enqueue("job", f"j{i}", key=f"k{i}") for i in range(40)}
    got: list[int] = []
    lock = threading.Lock()

    def worker(n: int) -> None:
        while True:
            t = taskq.claim(f"w{n}")
            if t is None:
                return
            with lock:
                got.append(t["id"])

    th = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in th:
        t.start()
    for t in th:
        t.join()
    assert sorted(got) == sorted(ids), (len(got), len(ids))


def test_cancel_queued_and_running():
    _clear_tasks()
    taskq.enqueue("job", "q", key="k:q")
    assert taskq.request_cancel("q") == "queued"
    assert taskq.claim("w") is None                      # a cancelled task is never started
    r = taskq.enqueue("job", "r", key="k:r")
    t = taskq.claim("w")
    assert t["id"] == r
    assert taskq.request_cancel("r") == "running"
    ours, cancel = taskq.renew(r, "w")
    assert ours and cancel
    assert taskq.request_cancel("nothing") == ""


def test_expired_lease_requeues_then_gives_up():
    _clear_tasks()
    r = taskq.enqueue("job", "x", key="k:x")
    for attempt in range(taskq.MAX_ATTEMPTS):
        t = taskq.claim(f"dead{attempt}")
        assert t is not None and t["id"] == r and t["attempts"] == attempt + 1
        from sqlalchemy import text

        from polixor.db import get_engine

        with get_engine().begin() as c:                  # the worker died: nobody renews
            c.execute(text("UPDATE tasks SET lease_expires=1 WHERE id=:i"), {"i": r})
        assert not taskq.renew(r, "someone-else")[0]
        res = taskq.reclaim_expired()
        if attempt + 1 < taskq.MAX_ATTEMPTS:
            assert [x["id"] for x in res["requeued"]] == [r]
        else:
            assert [x["id"] for x in res["gave_up"]] == [r]
    assert taskq.claim("w") is None


def test_a_lost_lease_is_not_active_and_can_be_queued_again():
    _clear_tasks()
    r = taskq.enqueue("job", "y", key="job:y")
    taskq.claim("w")
    from sqlalchemy import text

    from polixor.db import get_engine

    with get_engine().begin() as c:
        c.execute(text("UPDATE tasks SET lease_expires=1 WHERE id=:i"), {"i": r})
    taskq._invalidate()
    assert "y" not in taskq.active_jobs()
    assert taskq.enqueue("job", "y", key="job:y") != r


# --------------------------------------------------------------------------
# real worker processes
# --------------------------------------------------------------------------
def _project(minutes_source: Path, *, title: str) -> str:
    from polixor.project_config import clamp_config

    jid = new_id()
    base = SETTINGS.get().to_dict()
    base.update({"transcript_provider": "fixture", "ai_mode": "cloud"})
    cfg = clamp_config({"mode": "short", "clip_count": 3, "studio": {"auto_generate": "short"}})
    with session_scope() as s:
        s.add(Job(id=jid, title=title, input_url="", status=JobStatus.QUEUED, phase=ProjectPhase.ANALYZING.value,
                  run_scope=RunScope.ANALYZE.value, settings_snapshot=AppSettings.from_dict(base).to_dict(),
                  project_config=cfg, mode="short", artifacts={"source_path": str(minutes_source)},
                  completed_stages=[]))
    return jid


def _status(jid: str) -> JobStatus:
    with session_scope() as s:
        return s.get(Job, jid).status


def _generated(jid: str) -> bool:
    """Analysis AND the generation that follows it are finished."""
    with session_scope() as s:
        j = s.get(Job, jid)
        return j.status == JobStatus.COMPLETED and j.run_scope == RunScope.GENERATE.value


def _task_rows(job_id: str) -> list[dict]:
    from sqlalchemy import text

    from polixor.db import get_engine

    with get_engine().connect() as c:
        return [dict(r._mapping) for r in c.execute(text(
            "SELECT id, status, lease_owner, attempts FROM tasks WHERE job_id=:j ORDER BY id"), {"j": job_id}).all()]


class _Workers:
    """Process mode in this (web) process, with real worker processes started by the supervisor."""

    def __enter__(self):
        os.environ["POLIXOR_WORKER_MODE"] = "process"
        os.environ["POLIXOR_WORKERS"] = "general:1"
        fx = TEST_VIDEO.with_suffix(".transcript.json")
        os.environ["POLIXOR_FIXTURE_TRANSCRIPT"] = str(fx)
        from polixor.services import relay, workers

        self.relay, self.workers = relay, workers
        workers.start()
        relay.start_reader(start=0)
        return self

    def __exit__(self, *a):
        self.workers.stop(terminate_children=True)
        for ch in list(self.workers._children.values()):
            try:
                ch["proc"].wait(15)
            except Exception:                                  # noqa: BLE001
                ch["proc"].kill()
        os.environ["POLIXOR_WORKER_MODE"] = "inline"


def test_a_project_runs_in_a_separate_worker_process_and_events_reach_the_web_bus():
    if not TEST_VIDEO.exists():
        print("   (skipped: no test video)")
        return
    from polixor.events import BUS
    from polixor.worker import MANAGER

    _clear_tasks()
    jid = _project(TEST_VIDEO, title="in a worker")
    with _Workers():
        MANAGER.submit(jid)
        assert MANAGER.is_running(jid)                         # queued counts as active
        assert _wait(lambda: _generated(jid), timeout=420, step=1), _status(jid)
        assert _wait(lambda: not MANAGER.is_running(jid), timeout=10)
    rows = _task_rows(jid)
    assert rows and rows[-1]["status"] == "done", rows
    owner_pid = int(rows[-1]["lease_owner"].split(":")[1])
    assert owner_pid != os.getpid(), "the heavy work must not run in the web process"
    assert MANAGER.active_ids() == [] or jid not in MANAGER.active_ids()
    # the worker's progress reached this process's bus through the relay
    evs = BUS.recent(jid, limit=300)
    assert any(e.type == "job.progress" for e in evs), [e.type for e in evs][:20]
    assert any(e.type == "job.status" and e.data.get("status") == "completed" for e in evs)
    with session_scope() as s:
        made = s.query(Clip).filter(Clip.job_id == jid, Clip.kind != ClipKind.LONG,
                                    Clip.status == ClipStatus.READY).count()
    assert made >= 1, made


def test_a_killed_worker_loses_its_lease_and_the_job_completes_elsewhere():
    if not TEST_VIDEO.exists():
        print("   (skipped: no test video)")
        return
    from polixor.worker import MANAGER

    _clear_tasks()
    jid = _project(TEST_VIDEO, title="killed worker")
    with _Workers() as w:
        MANAGER.submit(jid)
        # wait until a worker has it and is past the first stages
        assert _wait(lambda: _status(jid) == JobStatus.RUNNING and any(r["status"] == "leased" for r in _task_rows(jid)),
                     timeout=60)
        time.sleep(4)
        first = [r for r in _task_rows(jid) if r["status"] == "leased"][0]
        victim = int(first["lease_owner"].split(":")[1])
        os.kill(victim, signal.SIGKILL)                        # no clean-up of any kind
        assert _wait(lambda: _generated(jid), timeout=480, step=1), (_status(jid), _task_rows(jid))
        rows = _task_rows(jid)
    done = [r for r in rows if r["status"] == "done"]
    assert done, rows
    assert int(done[-1]["lease_owner"].split(":")[1]) != victim
    with session_scope() as s:
        assert s.query(Clip).filter(Clip.job_id == jid, Clip.status == ClipStatus.READY).count() >= 1


def test_cancel_from_the_web_process_stops_the_job_in_its_worker():
    if not TEST_VIDEO.exists():
        print("   (skipped: no test video)")
        return
    from polixor.worker import MANAGER

    _clear_tasks()
    jid = _project(TEST_VIDEO, title="cancelled")
    with _Workers():
        MANAGER.submit(jid)
        assert _wait(lambda: any(r["status"] == "leased" for r in _task_rows(jid)), timeout=60)
        time.sleep(2)
        t0 = time.time()
        assert MANAGER.cancel(jid)
        assert _wait(lambda: _status(jid) == JobStatus.CANCELLED, timeout=60), _status(jid)
        took = time.time() - t0
        assert _wait(lambda: _task_rows(jid)[-1]["status"] == "cancelled", timeout=10), _task_rows(jid)
    assert took < 45, took


def _run_all() -> int:
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            t0 = time.time()
            fn()
            print(f"✓ {name} ({time.time() - t0:.1f}s)")
        except Exception as exc:                               # noqa: BLE001
            import traceback

            failed += 1
            print(f"✗ {name}: {exc}")
            traceback.print_exc()
    print(f"{len(tests) - failed}/{len(tests)} worker tests passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
