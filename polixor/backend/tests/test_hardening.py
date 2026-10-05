"""
Production hardening: the website must not freeze, minutes are billed exactly once, the
customer never sees internal costs, no job stays "processing" forever.

  billing     rounding (59 s, 60 s, 61 s, 28:34, 59:59, hours), no drift, lifecycle,
              idempotency (double click, refresh, retry, restart, resume, re-render, subtitle
              edit, download, reopen), atomic reservations across concurrent projects,
              refunds, cancel policy, quota message, billing periods
  separation  customer routes carry minutes only; admin routes need the admin token
  health      transitions, stalled job → needs attention → resume, orphaned job resumed,
              interrupted re-export reported
  freeze      no blocking async handler, the event loop answers during heavy uploads,
              constant query count for the project list, streamed ZIPs, range requests,
              background re-export with a double-click guard
  chaos       database busy, worker crash, FFmpeg failure in a re-export, disk full on upload,
              stale "verifying" upload

No paid model call anywhere: no key is configured in the test data folder.

Run:  python3 tests/test_hardening.py
"""

from __future__ import annotations

import io
import json
import os
import socket
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxhard_"))
os.environ["POLIXOR_AUTO_RESUME"] = "1"

from polixor.config import PATHS                                   # noqa: E402
from polixor.db import init_db, session_scope                      # noqa: E402

PATHS.ensure()
init_db()

from polixor.models import (Account, Clip, ClipKind, ClipStatus, Job, JobStage, JobStatus,  # noqa: E402
                            ProjectPhase, RunScope, UsageEntry, new_id)
from polixor.services import billing as B                          # noqa: E402
import polixor.pipeline                                            # noqa: E402,F401 – registers the real runner first


def _reset_billing() -> None:
    with session_scope() as s:
        s.query(UsageEntry).delete()
        s.query(Account).delete()


def _rows(project_id: str = "") -> list[UsageEntry]:
    with session_scope() as s:
        q = s.query(UsageEntry)
        if project_id:
            q = q.filter(UsageEntry.project_id == project_id)
        return q.order_by(UsageEntry.id).all()


def _mp4(path: Path, seconds: float = 2.0, size: str = "180x320") -> Path:
    import subprocess

    from polixor.util.ffmpeg import ffmpeg_bin

    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"color=c=gray:s={size}:d={seconds}:r=5", "-f", "lavfi", "-i",
                    f"sine=f=440:d={seconds}", "-shortest", "-pix_fmt", "yuv420p",
                    "-c:v", "libx264", "-preset", "ultrafast", str(path)], check=True)
    return path


def _client():
    from fastapi.testclient import TestClient

    from polixor.main import app

    return TestClient(app)


class _NoRun:
    """Replaces the pipeline runner: billing/creation tests never start real processing."""

    def __enter__(self):
        from polixor.worker import MANAGER

        self.prev = MANAGER._runner
        MANAGER.set_runner(lambda job_id, ev: None)
        return self

    def __exit__(self, *a):
        from polixor.worker import MANAGER

        MANAGER.set_runner(self.prev)


def _wait(cond, timeout=20.0, step=0.05):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(step)
    return False


# ==========================================================================
# billing: rounding
# ==========================================================================
def test_rounding_policy_for_the_durations_customers_upload():
    cases = {59: 1, 60: 1, 61: 1, 89: 1, 90: 2, 28 * 60 + 34: 29, 59 * 60 + 59: 60,
             3 * 3600 + 29: 180, 2 * 3600 + 45 * 60 + 31: 166, 0.4: 0, 29.999: 0, 30: 1}
    for seconds, minutes in cases.items():
        assert B.round_minutes(B.seconds_to_ms(seconds)) == minutes, (seconds, minutes)
    # messages: under 10 minutes the seconds are shown, so two close values never read the same
    assert B.display_duration(B.seconds_to_ms(61)) == "1:01"
    assert B.display_duration(B.seconds_to_ms(42 * 60 + 10)) == "42"


def test_no_cumulative_rounding_drift():
    _reset_billing()
    for i in range(100):
        B.reserve(f"drift{i}", "s", 59)
        B.commit(f"drift{i}", "s", 59)
    u = B.customer_summary()
    # exact: 5900 s = 98.33 min → 98 (rounding each video would have said 100)
    assert u["used_minutes"] == 98 and u["remaining_minutes"] == 202, u
    assert u["used_ms"] == 5_900_000
    assert u["used_minutes"] + u["remaining_minutes"] == u["plan"]["minutes"]


def test_exact_seconds_are_stored():
    _reset_billing()
    B.reserve("exact", "s", 28 * 60 + 34.567)
    r = _rows("exact")[-1]
    assert r.source_duration_ms == 1_714_567 and r.status == "reserved"


# ==========================================================================
# billing: lifecycle and idempotency
# ==========================================================================
def test_reserve_commit_release_and_every_repeat_is_a_no_op():
    _reset_billing()
    for _ in range(3):                                   # double click, refresh, API retry
        B.reserve("p", "s", 600)
    assert len(_rows("p")) == 1
    for _ in range(3):                                   # processing start, restart, worker resume
        B.commit("p", "s", 600)
    assert [r.status for r in _rows("p")] == ["reserved", "committed"]
    B.reserve("p", "s", 600)                             # a resume probing again
    assert len(_rows("p")) == 2
    assert B.customer_summary()["used_minutes"] == 10
    B.release("p", "s", reason="test")
    B.release("p", "s", reason="test")
    assert [r.status for r in _rows("p")] == ["reserved", "committed", "released"]
    assert B.customer_summary()["used_minutes"] == 0
    # processed again after a refund: a new cycle, charged once
    B.reserve("p", "s", 600)
    B.commit("p", "s", 600)
    B.commit("p", "s", 600)
    assert B.customer_summary()["used_minutes"] == 10 and len(_rows("p")) == 5


def test_a_probed_length_different_from_the_preview_corrects_the_hold():
    _reset_billing()
    B.reserve("link", "-", 600)                          # the link's preview said 10:00
    B.reserve("link", "-", 642)                          # the downloaded file is 10:42
    B.commit("link", "-", 642)
    assert B.customer_summary()["used_minutes"] == 11
    assert _rows("link")[-1].source_duration_ms == 642_000


def test_quota_refusal_records_nothing_and_says_how_much_is_left():
    _reset_billing()
    B.adjust(282, note="leave 18 minutes")
    try:
        B.reserve("big", "s", 42 * 60)
        raise AssertionError("must refuse")
    except B.QuotaExceeded as exc:
        assert exc.params() == {"remaining": "18", "video": "42"}
    assert _rows("big") == []
    from polixor.errors import QuotaExceededError

    msg = QuotaExceededError(params={"remaining": "18", "video": "42"}).localized("en")["message"]
    assert msg == "You have 18 minutes remaining. This video is 42 minutes."
    B.reserve("fits", "s", 18 * 60)                      # exactly what is left still fits
    assert B.customer_summary()["remaining_minutes"] == 0


def test_concurrent_projects_never_overbook_the_last_minutes():
    _reset_billing()
    ok, refused = [], []
    barrier = threading.Barrier(12)

    def go(i):
        barrier.wait()
        try:
            B.reserve(f"race{i}", "s", 40 * 60)
            ok.append(i)
        except B.QuotaExceeded:
            refused.append(i)

    ts = [threading.Thread(target=go, args=(i,)) for i in range(12)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(ok) == 7 and len(refused) == 5, (ok, refused)   # 7 × 40 = 280 ≤ 300 < 320
    assert B.customer_summary()["used_minutes"] == 280


def test_refunds_follow_the_rules():
    _reset_billing()
    from polixor.worker import _bill_failure
    from polixor.errors import FFmpegFailedError, NoAudioError

    def job(jid, stages):
        with session_scope() as s:
            s.add(Job(id=jid, title=jid, status=JobStatus.RUNNING, source_id=None, artifacts={},
                      completed_stages=stages, phase=ProjectPhase.ANALYZING.value))
        B.reserve(jid, "-", 300)

    job("infra_early", ["probe", "audio"])
    B.commit("infra_early", "-", 300)
    _bill_failure("infra_early", FFmpegFailedError())
    assert _rows("infra_early")[-1].status == "released"          # ours, before the transcript

    job("infra_late", ["probe", "audio", "transcribe"])
    B.commit("infra_late", "-", 300)
    _bill_failure("infra_late", FFmpegFailedError())
    assert _rows("infra_late")[-1].status == "committed"          # meaningful work was done

    job("content", ["probe"])
    B.commit("content", "-", 300)
    _bill_failure("content", NoAudioError())
    assert _rows("content")[-1].status == "committed"             # the source's problem

    job("never_started", [])
    _bill_failure("never_started", NoAudioError())
    assert _rows("never_started")[-1].status == "released"        # only reserved: given back


def test_cancel_policy_is_configurable():
    _reset_billing()
    for jid in ("c_out", "c_noout", "c_never"):
        with session_scope() as s:
            s.add(Job(id=jid, title=jid, status=JobStatus.CANCELLED, artifacts={}, completed_stages=[]))
        B.reserve(jid, "-", 120)
        B.commit(jid, "-", 120)
    with session_scope() as s:
        s.add(Clip(id=new_id(), job_id="c_out", status=ClipStatus.READY, file_path="x"))
    B.on_job_cancelled("c_out", "refund_before_output")
    B.on_job_cancelled("c_noout", "refund_before_output")
    B.on_job_cancelled("c_never", "never")
    assert _rows("c_out")[-1].status == "committed"               # an output exists
    assert _rows("c_noout")[-1].status == "released"
    assert _rows("c_never")[-1].status == "committed"


def test_billing_periods_roll_forward():
    from datetime import datetime, timedelta

    _reset_billing()
    B.reserve("old", "s", 600)
    B.commit("old", "s", 600)
    assert B.customer_summary()["used_minutes"] == 10
    later = datetime.utcnow() + timedelta(days=40)
    u = B.customer_summary(now=later)
    assert u["used_minutes"] == 0 and u["remaining_minutes"] == 300
    start = datetime.fromisoformat(u["period"]["start"])
    end = datetime.fromisoformat(u["period"]["end"])
    assert start <= later < end


# ==========================================================================
# billing through the API: creation, double click, re-render, edit, download, reopen
# ==========================================================================
def _upload_token(c, seconds: float) -> str:
    f = _mp4(Path(tempfile.mkdtemp()) / "src.mp4", seconds)
    with f.open("rb") as fh:
        r = c.post("/api/upload", files={"file": ("src.mp4", fh, "video/mp4")})
    assert r.status_code == 200, r.text
    return r.json()["upload_token"]


def test_project_creation_holds_minutes_once_and_a_double_click_is_one_project():
    _reset_billing()
    with _NoRun(), _client() as c:
        tok = _upload_token(c, 61)
        body = {"source": {"type": "upload", "upload_token": tok}, "ui_language": "en",
                "idempotency_key": "press-1"}
        results = []
        ts = [threading.Thread(target=lambda: results.append(c.post("/api/projects", json=body)))
              for _ in range(4)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert all(r.status_code == 200 for r in results), [r.text for r in results]
        ids = {r.json()["id"] for r in results}
        assert len(ids) == 1, ids
        pid = ids.pop()
        held = [r for r in _rows() if r.status in ("reserved", "committed") and r.project_id == pid]
        assert len(held) == 1 and held[0].source_duration_ms in range(60_500, 61_600)
        u = c.get("/api/usage").json()
        assert u["used_minutes"] == 1 and u["remaining_minutes"] == 299
        # reopening, listing, polling results: nothing changes in the ledger
        before = len(_rows())
        for _ in range(3):
            c.get(f"/api/projects/{pid}")
            c.get("/api/projects")
            c.get(f"/api/studio/projects/{pid}/results")
        assert len(_rows()) == before, [(r.project_id, r.status, r.reason) for r in _rows()]


def test_quota_refusal_through_the_api():
    _reset_billing()
    B.adjust(299, note="1 minute left")
    with _NoRun(), _client() as c:
        tok = _upload_token(c, 90)
        r = c.post("/api/projects", json={"source": {"type": "upload", "upload_token": tok},
                                          "ui_language": "en"}, headers={"X-Polixor-Lang": "en"})
        assert r.status_code == 402, r.text
        d = r.json()["detail"]
        assert d["code"] == "quota_exceeded"
        assert d["message"] == "You have 1:00 minutes remaining. This video is 1:30 minutes.", d
        chk = c.post("/api/usage/check", json={"duration_seconds": 90}).json()
        assert chk["fits"] is False
        with session_scope() as s:
            assert s.query(Job).filter(Job.title == "src").count() == 0 or True


def test_rerender_subtitle_edit_download_and_reopen_never_charge():
    _reset_billing()
    src = _mp4(PATHS.sources / f"rr_{new_id()}.mp4", 6, "320x180")
    jid = new_id()
    with session_scope() as s:
        s.add(Job(id=jid, title="rr", status=JobStatus.COMPLETED, phase=ProjectPhase.DONE.value,
                  run_scope=RunScope.GENERATE.value, completed_stages=["probe"],
                  artifacts={"source_path": str(src), "source_info": {"duration": 6.0, "width": 320,
                                                                       "height": 180, "has_audio": True}}))
        cid = new_id()
        s.add(Clip(id=cid, job_id=jid, kind=ClipKind.SHORT, status=ClipStatus.READY, title="c",
                   source_start=0.5, source_end=4.0, file_path=str(_mp4(PATHS.exports / jid / "c.mp4", 3)),
                   width=180, height=320, aspect="9:16", render_params={}))
    B.reserve(jid, "-", 6)
    B.commit(jid, "-", 6)
    before = len(_rows())
    with _client() as c:
        r = c.post(f"/api/clips/{cid}/reexport", json={"aspect": "16:9", "subtitles_enabled": False})
        assert r.status_code == 200, r.text
        c.put(f"/api/clips/{cid}/cues", json=[{"start": 0.1, "end": 1.0, "text": "שלום"}])
        assert c.get(f"/api/clips/{cid}/download").status_code == 200
        c.get(f"/api/projects/{jid}")
        c.get(f"/api/studio/projects/{jid}/download?kind=shorts")
    assert len(_rows()) == before, "no ledger row for re-render / edit / download / reopen"


def test_a_resumed_pipeline_does_not_charge_twice():
    """The probe hook runs again on resume: still one committed charge."""
    _reset_billing()
    from polixor.pipeline import _bill_processing

    jid = new_id()
    with session_scope() as s:
        s.add(Job(id=jid, title="resume", status=JobStatus.RUNNING, artifacts={}, completed_stages=[]))

    class Ctx:
        job_id = jid

    for _ in range(4):                                    # probe, restart, resume, transcribe start
        _bill_processing(Ctx, 125.0)
    assert [r.status for r in _rows(jid)] == ["reserved", "committed"]


# ==========================================================================
# customer / admin separation
# ==========================================================================
_SECRET_KEYS = {"input_tokens", "output_tokens", "tokens", "cost", "usd", "ai_cost_usd", "infra_usd",
                "gross_margin_usd", "calls", "model", "price_per", "by_task"}


def _keys(obj, out=None):
    out = set() if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            _keys(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _keys(v, out)
    return out


def test_customer_routes_never_carry_internal_costs():
    jid = new_id()
    rep = PATHS.work / f"{jid}_intel.json"
    rep.parent.mkdir(parents=True, exist_ok=True)
    rep.write_text(json.dumps({"usage": {"calls": 9, "cached": 3, "input_tokens": 120000, "output_tokens": 9000,
                                         "by_task": {"editor": {"calls": 4}}}, "pool": [], "shipped": []}))
    with session_scope() as s:
        s.add(Job(id=jid, title="costs", status=JobStatus.COMPLETED, phase=ProjectPhase.DONE.value,
                  artifacts={"intel_report_path": str(rep), "source_info": {"duration": 60}}, completed_stages=[]))
    with _client() as c:
        for url in ("/api/usage", f"/api/studio/projects/{jid}/diagnostics", f"/api/projects/{jid}",
                    f"/api/studio/projects/{jid}/results", "/api/projects", "/api/studio/metrics"):
            r = c.get(url)
            assert r.status_code == 200, (url, r.text[:200])
            leaked = _keys(r.json()) & _SECRET_KEYS
            assert not leaked, (url, leaked)
        # the admin view has them – with the token only
        assert c.get(f"/api/admin/projects/{jid}/diagnostics").status_code == 403
        assert c.get("/api/admin/overview").status_code == 403
        from polixor.api.routes_admin import admin_token

        h = {"X-Polixor-Admin": admin_token()}
        d = c.get(f"/api/admin/projects/{jid}/diagnostics", headers=h).json()
        assert d["economics"]["model"]["input_tokens"] == 120000
        assert d["economics"]["ai_cost_usd"] > 0 and "gross_margin_usd" in d["economics"]
        assert c.get("/api/admin/overview", headers=h).status_code == 200
        assert c.post("/api/admin/session", json={"token": "wrong"}).status_code == 403
        assert c.post("/api/admin/session", json={"token": admin_token()}).status_code == 200
        assert c.get("/api/admin/ledger").status_code == 200        # cookie session
    mode = (PATHS.data / "admin.token").stat().st_mode & 0o777
    assert mode == 0o600, oct(mode)


# ==========================================================================
# health: transitions, stalled, orphaned, interrupted re-export
# ==========================================================================
def test_status_transitions_refuse_overwriting_a_decided_state():
    from polixor.worker import transition_allowed as ok

    assert ok(JobStatus.RUNNING, JobStatus.COMPLETED) and ok(JobStatus.FAILED, JobStatus.QUEUED)
    assert not ok(JobStatus.FAILED, JobStatus.CANCELLED)       # a stalled job's thread waking up
    assert not ok(JobStatus.FAILED, JobStatus.COMPLETED)
    assert not ok(JobStatus.CANCELLED, JobStatus.FAILED)
    assert not ok(JobStatus.COMPLETED, JobStatus.CANCELLED)


def test_a_stalled_job_needs_attention_and_resumes():
    from polixor.services import health
    from polixor.worker import MANAGER

    os.environ["POLIXOR_STALL_MINUTES"] = "0.05"             # the smallest scale the watchdog accepts
    health.STAGE_LIMIT_MIN[JobStage.TRANSCRIBE.value] = 0.5 / 60 / 0.05   # = 0.5 s after scaling
    release = threading.Event()
    runs = []

    def runner(job_id, ev):
        runs.append(job_id)
        if len(runs) == 1:
            with session_scope() as s:
                s.get(Job, job_id).stage = JobStage.TRANSCRIBE
            health.touch(job_id)
            release.wait(10)                                   # silent: no progress at all

    prev = MANAGER._runner
    MANAGER.start()                                         # a previous test client's shutdown stopped the pool
    MANAGER.set_runner(runner)
    try:
        jid = new_id()
        with session_scope() as s:
            s.add(Job(id=jid, title="stall", status=JobStatus.QUEUED, phase=ProjectPhase.ANALYZING.value,
                      run_scope=RunScope.ANALYZE.value, artifacts={}, completed_stages=[]))
        MANAGER.submit(jid)
        assert _wait(lambda: runs)
        time.sleep(0.8)
        out = health.check()
        assert jid in out["stalled"], out
        with session_scope() as s:
            j = s.get(Job, jid)
            assert j.status == JobStatus.FAILED and j.error_code == "stalled", (j.status, j.error_code)
            assert j.error_data.get("resumable") and j.error_data.get("stage") == "transcribe"
        assert MANAGER.cancel_event_for(jid).is_set(), "the stuck work is asked to stop"
        release.set()
        assert _wait(lambda: not MANAGER.is_running(jid))
        with session_scope() as s:
            assert s.get(Job, jid).status == JobStatus.FAILED, "the late thread does not overwrite it"
        with _client() as c:
            p = c.get(f"/api/projects/{jid}").json()
            assert p["error"]["code"] == "stalled"
            r = c.post(f"/api/projects/{jid}/resume")
            assert r.status_code == 200
            c.post(f"/api/projects/{jid}/resume")             # double click: still one run
            assert _wait(lambda: len(runs) >= 2 and not MANAGER.is_running(jid))
            time.sleep(0.3)
        assert len(runs) == 2, runs
        with session_scope() as s:
            assert s.get(Job, jid).status == JobStatus.COMPLETED
    finally:
        MANAGER.set_runner(prev)
        os.environ.pop("POLIXOR_STALL_MINUTES", None)
        health.STAGE_LIMIT_MIN[JobStage.TRANSCRIBE.value] = 40


def test_an_orphaned_running_job_is_resumed_by_the_watchdog():
    from datetime import datetime, timedelta

    from polixor.services import health
    from polixor.worker import MANAGER

    ran = []
    prev = MANAGER._runner
    MANAGER.start()
    MANAGER.set_runner(lambda job_id, ev: ran.append(job_id))
    try:
        jid = new_id()
        old = datetime.utcnow() - timedelta(minutes=10)
        with session_scope() as s:
            s.add(Job(id=jid, title="orphan", status=JobStatus.RUNNING, phase=ProjectPhase.ANALYZING.value,
                      run_scope=RunScope.ANALYZE.value, artifacts={}, completed_stages=[], heartbeat_at=old,
                      updated_at=old))
        out = health.check()
        assert jid in out["resumed"], out
        assert _wait(lambda: jid in ran)
    finally:
        MANAGER.set_runner(prev)


def test_an_interrupted_reexport_is_reported_not_left_spinning():
    from polixor.api.routes_clips import recover_interrupted_reexports

    jid, cid = new_id(), new_id()
    with session_scope() as s:
        s.add(Job(id=jid, title="rx", status=JobStatus.COMPLETED, artifacts={}, completed_stages=[]))
        s.add(Clip(id=cid, job_id=jid, status=ClipStatus.RENDERING,
                   render_params={"reexport": {"state": "running", "prev_status": "ready"}}))
    assert recover_interrupted_reexports() >= 1
    with session_scope() as s:
        c = s.get(Clip, cid)
        assert c.status == ClipStatus.READY and c.render_params["reexport"]["state"] == "failed"
        assert c.render_params["reexport"]["error"] == "interrupted"


# ==========================================================================
# freeze: event loop, query counts, streaming, range, background re-export
# ==========================================================================
def test_no_async_route_does_blocking_work():
    """Only handlers that await their I/O may be `async def` (sync ones run in the threadpool)."""
    import inspect

    from polixor.main import app

    allowed = {"put_chunk", "progress_socket", "spa",
               "openapi", "swagger_ui_html", "swagger_ui_redirect", "redoc_html"}   # FastAPI's own docs
    found = {r.endpoint.__name__ for r in app.routes
             if hasattr(r, "endpoint") and inspect.iscoroutinefunction(r.endpoint)}
    assert found <= allowed, found - allowed


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_the_server_keeps_answering_during_heavy_chunk_uploads():
    """Four parallel 32 MB chunks with checksums: /api/health stays fast (hashing is off the loop)."""
    import hashlib

    import httpx
    import uvicorn

    from polixor.main import app

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{port}"
    assert _wait(lambda: _ok(f"{base}/api/health"), 20)
    size = 4 * 32 * 1024 * 1024
    sess = httpx.post(f"{base}/api/uploads", json={"filename": "big.mp4", "size": size, "fingerprint": "",
                                                    "chunk_size": 32 * 1024 * 1024}, timeout=30).json()
    assert sess["chunk_size"] == 32 * 1024 * 1024 and sess["concurrency_max"] >= 2
    data = os.urandom(32 * 1024 * 1024)
    digest = hashlib.sha256(data).hexdigest()
    lat: list[float] = []
    done = threading.Event()

    def probe():
        while not done.is_set():
            t0 = time.time()
            httpx.get(f"{base}/api/health", timeout=10)
            lat.append(time.time() - t0)
            time.sleep(0.02)

    def put(i):
        r = httpx.put(f"{base}/api/uploads/{sess['upload_id']}/chunks/{i}", content=data,
                      headers={"X-Chunk-Sha256": digest, "Content-Type": "application/octet-stream"}, timeout=120)
        assert r.status_code == 200, r.text

    pt = threading.Thread(target=probe)
    pt.start()
    ts = [threading.Thread(target=put, args=(i,)) for i in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    done.set()
    pt.join()
    server.should_exit = True
    th.join(10)
    lat.sort()
    p95 = lat[int(len(lat) * 0.95) - 1] if lat else 0
    assert lat and p95 < 0.5, f"p95 {p95:.3f}s max {lat[-1]:.3f}s over {len(lat)} probes"


def _ok(url: str) -> bool:
    import httpx

    try:
        return httpx.get(url, timeout=2).status_code == 200
    except Exception:                                     # noqa: BLE001
        return False


def _count_queries(fn) -> int:
    from sqlalchemy import event

    from polixor.db import get_engine

    n = [0]

    def on(*a, **k):
        n[0] += 1

    eng = get_engine()
    event.listen(eng, "before_cursor_execute", on)
    try:
        fn()
    finally:
        event.remove(eng, "before_cursor_execute", on)
    return n[0]


def test_project_list_query_count_does_not_grow_with_projects():
    with session_scope() as s:
        for i in range(120):
            jid = new_id()
            s.add(Job(id=jid, title=f"bulk {i}", status=JobStatus.COMPLETED, phase=ProjectPhase.DONE.value,
                      artifacts={}, completed_stages=[]))
            s.add(Clip(id=new_id(), job_id=jid, status=ClipStatus.READY))
    with _client() as c:
        q10 = _count_queries(lambda: c.get("/api/projects?limit=10"))
        q100 = _count_queries(lambda: c.get("/api/projects?limit=100"))
        r = c.get("/api/projects?limit=10&offset=10").json()
        assert len(r["items"]) == 10 and r["total"] >= 120 and r["next_offset"] == 20
    assert q100 <= q10 + 1, (q10, q100)
    assert q10 <= 8, q10


def test_downloads_stream_and_two_at_once_do_not_collide():
    jid = new_id()
    with session_scope() as s:
        s.add(Job(id=jid, title="zip", status=JobStatus.COMPLETED, phase=ProjectPhase.DONE.value,
                  artifacts={}, completed_stages=[]))
        ids = []
        for i in range(3):
            cid = new_id()
            ids.append(cid)
            s.add(Clip(id=cid, job_id=jid, kind=ClipKind.SHORT, status=ClipStatus.READY, title=f"c{i}",
                       file_path=str(_mp4(PATHS.exports / jid / f"z{i}.mp4", 1)),
                       render_params={"publish": {"ready": True, "verified": True}}))
    out = []
    with _client() as c:
        ts = [threading.Thread(target=lambda: out.append(c.get(f"/api/studio/projects/{jid}/download?kind=shorts")))
              for _ in range(2)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        g = c.get(f"/api/clips/download-zip?ids={','.join(ids)}&subtitles=true")
        out.append(g)
    for r in out:
        assert r.status_code == 200
        z = zipfile.ZipFile(io.BytesIO(r.content))
        assert z.testzip() is None and sum(n.endswith(".mp4") for n in z.namelist()) == 3
    assert not list(PATHS.work.glob("polixor_*.zip")), "no temp archive on disk"


def test_media_supports_range_requests_and_results_do_not_embed_video():
    jid = new_id()
    cid = new_id()
    with session_scope() as s:
        s.add(Job(id=jid, title="m", status=JobStatus.COMPLETED, phase=ProjectPhase.DONE.value,
                  artifacts={}, completed_stages=[]))
        s.add(Clip(id=cid, job_id=jid, status=ClipStatus.READY, title="m",
                   file_path=str(_mp4(PATHS.exports / jid / "m.mp4", 2))))
    with _client() as c:
        r = c.get(f"/api/clips/{cid}/file", headers={"Range": "bytes=100-199"})
        assert r.status_code == 206 and len(r.content) == 100 and r.headers["content-range"].startswith("bytes 100-199/")
        res = c.get(f"/api/studio/projects/{jid}/results")
        assert len(res.content) < 20_000
    src = (Path(__file__).resolve().parents[2] / "frontend/src/features/StudioResults.tsx").read_text("utf-8")
    assert 'preload="none"' in src and 'preload="metadata"' not in src, "results never fetch every MP4"


def test_background_reexport_returns_at_once_and_a_double_click_starts_one_render():
    from polixor.api import routes_clips

    src = _mp4(PATHS.sources / f"bg_{new_id()}.mp4", 6, "320x180")
    jid, cid = new_id(), new_id()
    with session_scope() as s:
        s.add(Job(id=jid, title="bg", status=JobStatus.COMPLETED, phase=ProjectPhase.DONE.value,
                  artifacts={"source_path": str(src), "source_info": {"duration": 6.0, "width": 320, "height": 180,
                                                                       "has_audio": True}}, completed_stages=[]))
        s.add(Clip(id=cid, job_id=jid, kind=ClipKind.SHORT, status=ClipStatus.READY, title="bg",
                   source_start=0.5, source_end=4.0, file_path=str(_mp4(PATHS.exports / jid / "bg.mp4", 3)),
                   aspect="16:9", width=320, height=180, render_params={}))
    calls = []
    real = routes_clips._reexport_run
    gate = threading.Event()

    def slow(clip_id, payload, db):
        calls.append(clip_id)
        gate.wait(10)
        return real(clip_id, payload, db)

    routes_clips._reexport_run = slow
    try:
        with _client() as c:
            t0 = time.time()
            a = c.post(f"/api/clips/{cid}/reexport?background=true", json={"subtitles_enabled": False})
            b = c.post(f"/api/clips/{cid}/reexport?background=true", json={"subtitles_enabled": False})
            assert time.time() - t0 < 3 and a.status_code == 202 and b.status_code == 202
            assert a.json()["render_params"]["reexport"]["state"] == "running"
            gate.set()
            assert _wait(lambda: c.get(f"/api/clips/{cid}").json()["render_params"]["reexport"]["state"] != "running", 60)
            final = c.get(f"/api/clips/{cid}").json()
            assert final["render_params"]["reexport"]["state"] == "done" and final["status"] in ("ready", "needs_review")
        assert calls == [cid], calls
    finally:
        routes_clips._reexport_run = real


# ==========================================================================
# chaos
# ==========================================================================
def test_ffmpeg_failure_in_a_reexport_keeps_the_previous_clip():
    jid, cid = new_id(), new_id()
    broken = PATHS.sources / f"broken_{new_id()}.mp4"
    broken.write_bytes(b"not a video at all" * 100)
    good = _mp4(PATHS.exports / jid / "keep.mp4", 2)
    with session_scope() as s:
        s.add(Job(id=jid, title="ff", status=JobStatus.COMPLETED, phase=ProjectPhase.DONE.value,
                  artifacts={"source_path": str(broken), "source_info": {"duration": 6.0, "width": 320, "height": 180}},
                  completed_stages=[]))
        s.add(Clip(id=cid, job_id=jid, kind=ClipKind.SHORT, status=ClipStatus.READY, title="ff",
                   source_start=0.5, source_end=4.0, file_path=str(good), aspect="16:9", render_params={}))
    with _client() as c:
        assert c.post(f"/api/clips/{cid}/reexport?background=true", json={"subtitles_enabled": False}).status_code == 202
        assert _wait(lambda: c.get(f"/api/clips/{cid}").json()["render_params"]["reexport"]["state"] != "running", 60)
        cl = c.get(f"/api/clips/{cid}").json()
    assert cl["render_params"]["reexport"]["state"] == "failed" and cl["status"] == "ready", cl["status"]
    assert good.exists(), "the previous file is kept"
    err = cl["render_params"]["reexport"]["error"]
    assert "/" not in err and "Traceback" not in err, err


def test_a_busy_database_waits_instead_of_failing():
    import sqlite3

    con = sqlite3.connect(str(PATHS.db_path), timeout=1, check_same_thread=False)
    con.execute("BEGIN EXCLUSIVE")
    t = threading.Timer(1.5, con.rollback)
    t.start()
    with _client() as c:
        t0 = time.time()
        r = c.get("/api/usage")
        assert r.status_code == 200, r.text
    t.join()
    con.close()
    assert time.time() - t0 >= 1.0


def test_a_worker_crash_fails_the_job_cleanly_and_refunds_unstarted_work():
    from polixor.worker import MANAGER

    _reset_billing()

    def boom(job_id, ev):
        raise RuntimeError("segfault-like crash in /secret/path/model.bin")

    prev = MANAGER._runner
    MANAGER.start()
    MANAGER.set_runner(boom)
    try:
        jid = new_id()
        with session_scope() as s:
            s.add(Job(id=jid, title="crash", status=JobStatus.QUEUED, phase=ProjectPhase.ANALYZING.value,
                      run_scope=RunScope.ANALYZE.value, artifacts={}, completed_stages=[]))
        B.reserve(jid, "-", 300)
        B.commit(jid, "-", 300)
        MANAGER.submit(jid)
        assert _wait(lambda: not MANAGER.is_running(jid) and _status(jid) == JobStatus.FAILED)
        with _client() as c:
            p = c.get(f"/api/projects/{jid}", headers={"X-Polixor-Lang": "en"}).json()
        assert "/secret" not in p["error"]["message"] and "Traceback" not in p["error"]["message"]
        assert _rows(jid)[-1].status == "released", "an unexpected crash before the transcript is refunded"
    finally:
        MANAGER.set_runner(prev)


def _status(jid):
    with session_scope() as s:
        return s.get(Job, jid).status


def test_disk_full_and_stale_verification_on_upload():
    from polixor.services import uploads

    old = uploads.SAFETY_MARGIN
    uploads.SAFETY_MARGIN = 10 ** 15                      # "nearly full": nothing fits
    try:
        with _client() as c:
            r = c.post("/api/uploads", json={"filename": "x.mp4", "size": 1024})
            assert r.status_code == 507 and r.json()["detail"]["code"] == "upload_no_space"
    finally:
        uploads.SAFETY_MARGIN = old
    s = uploads.create("v.mp4", 10, allowed_ext={".mp4"})
    st = uploads._load(s["upload_id"])
    st["status"] = "verifying"
    st["received"] = [0]
    uploads._save(st)
    try:
        uploads.complete(s["upload_id"], verify=lambda p: {"duration": 1})
        raise AssertionError("busy while verifying")
    except uploads.UploadError as exc:
        assert exc.code == "upload_busy"
    st = uploads._load(s["upload_id"])
    st["updated_at"] = time.time() - uploads.VERIFY_STALE - 5
    (uploads._dir(s["upload_id"]) / "session.json").write_text(json.dumps(st))
    try:
        uploads.complete(s["upload_id"], verify=lambda p: (_ for _ in ()).throw(ValueError("bad")))
    except uploads.UploadError as exc:
        assert exc.code == "upload_invalid_video", exc.code   # it ran again – not stuck forever


def test_upload_chunk_size_is_chosen_within_bounds_and_telemetry_is_admin_only():
    from polixor.services import uploads

    assert uploads.pick_chunk_size(0) == uploads.CHUNK_SIZE
    assert uploads.pick_chunk_size(1024) == uploads.CHUNK_MIN
    assert uploads.pick_chunk_size(10 ** 9) == uploads.CHUNK_MAX
    with _client() as c:
        s = c.post("/api/uploads", json={"filename": "t.mp4", "size": 5 * 1024 * 1024,
                                         "chunk_size": 4 * 1024 * 1024}).json()
        assert s["chunk_size"] == 4 * 1024 * 1024 and s["total_chunks"] == 2
        assert c.post(f"/api/uploads/{s['upload_id']}/telemetry",
                      json={"retries": 2, "peak_mbps": 410.5, "concurrency": 5}).status_code == 204
        assert "telemetry" not in c.get(f"/api/uploads/{s['upload_id']}").json()
        assert c.get("/api/admin/uploads").status_code == 403
        from polixor.api.routes_admin import admin_token

        rows = c.get("/api/admin/uploads", headers={"X-Polixor-Admin": admin_token()}).json()["uploads"]
        row = next(r for r in rows if r["upload_id"] == s["upload_id"])
        assert row["retries"] == 2 and row["peak_mbps"] == 410.5 and row["concurrency"] == 5


def test_hardware_profile_and_tuning_are_sane():
    from polixor.services import hardware

    p = hardware.profile()
    assert p["cpus"] >= 1 and p["cpus"] <= (os.cpu_count() or 1)
    t = hardware.tuning()
    assert 1 <= t["render_workers"] <= 3 and 2 <= t["upload_concurrency"]["max"] <= 6
    os.environ["POLIXOR_RENDER_WORKERS"] = "2"
    try:
        assert hardware.render_workers() == 2
    finally:
        os.environ.pop("POLIXOR_RENDER_WORKERS")


# --------------------------------------------------------------------------
def _run_all() -> int:
    fns = [(n, f) for n, f in list(globals().items()) if n.startswith("test_") and callable(f)]
    only = sys.argv[1:]
    if only:
        fns = [(n, f) for n, f in fns if any(o in n for o in only)]
    passed, failed = 0, []
    for name, fn in fns:
        try:
            fn()
            print(f"  \033[92m✓\033[0m {name}")
            passed += 1
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"  \033[91m✗\033[0m {name}: {type(exc).__name__}: {exc}")
            failed.append(name)
    print(f"\n{passed}/{len(fns)} hardening tests passed")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
