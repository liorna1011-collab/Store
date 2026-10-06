"""
The performance engine: what makes it fast must never cost quality, money or correctness.

  encoders     a listed-but-broken hardware encoder is never chosen (a test encode decides);
               "none" is always the CPU; a hardware failure during a real render falls back
               to the CPU for that render and the rest of the process; settings v3 migration
  render pool  Shorts render in parallel up to the configured limit, each with its own index;
               a failing render never stops the others
  long-form    in a worker, with more urgent work queued, the long-form becomes its own task at
               long-form priority (the Shorts are kept); with nothing waiting it runs at once
  profiler     every process start is counted; a full decode of the source is detected, a seek
               or a time limit is not
  ASR          the thread count never exceeds the container's CPU quota
  paid AI      with POLIXOR_PAID_AI=off and API keys configured, a complete project (analysis +
               generation) opens no connection to a paid provider – checked at the socket
  scripted     the stand-in editor is deterministic (benchmarks compare like with like)

Run:  python3 tests/test_engine.py
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxeng_"))
os.environ["POLIXOR_PAID_AI"] = "off"

from polixor.config import PATHS                                   # noqa: E402
from polixor.db import init_db, session_scope                      # noqa: E402

PATHS.ensure()
init_db()

import polixor.pipeline                                            # noqa: E402,F401

TEST_VIDEO = Path(os.environ.get("POLIXOR_TEST_VIDEO", "/home/claude/testdata/polixor_test_stream.mp4"))


# --------------------------------------------------------------------------
# encoders
# --------------------------------------------------------------------------
from polixor.services import encoders as _E                     # noqa: E402

_REAL = (_E._listed, _E._test_encode)


def _restore_encoders() -> None:
    _E._listed, _E._test_encode = _REAL                             # type: ignore[assignment]
    _E._probe = None
    _E._broken.clear()


def _fresh_encoders(listed: set[str], works: dict[str, bool]):
    from polixor.services import encoders as E

    E._probe = None
    E._broken.clear()
    E._listed = lambda: set(listed)                                # type: ignore[assignment]
    E._test_encode = lambda kind: (works.get(kind, False), 0.1, "" if works.get(kind) else "no device")  # type: ignore
    try:
        E._cache_path().unlink()
    except OSError:
        pass
    return E


def test_a_listed_but_broken_hardware_encoder_is_never_chosen():
    E = _fresh_encoders({"nvenc", "qsv", "cpu"}, {"nvenc": False, "qsv": False, "cpu": True})
    assert E.choose("auto") == "cpu"
    assert E.choose("nvenc") == "cpu"
    assert E.choose("none") == "cpu"
    assert E.probe()["tests"]["nvenc"]["works"] is False
    _restore_encoders()


def test_a_working_hardware_encoder_is_chosen_by_auto_and_none_stays_cpu():
    E = _fresh_encoders({"nvenc", "cpu"}, {"nvenc": True, "cpu": True})
    assert E.choose("auto") == "nvenc"
    assert E.choose("none") == "cpu"
    assert E.choose("qsv") == "cpu"                                 # asked for one this machine lacks
    args = E.codec_args("nvenc", 18, "medium")
    assert "h264_nvenc" in args and "-cq" in args and args[args.index("-cq") + 1] == "19"
    E.mark_broken("nvenc", "driver crashed")
    assert E.choose("auto") == "cpu"
    _restore_encoders()


def test_a_hardware_failure_during_a_render_is_redone_on_the_cpu():
    if not TEST_VIDEO.exists():
        print("   (skipped: no test video)")
        return
    from polixor.services import render as R
    from polixor.util.ffmpeg import probe

    E = _fresh_encoders({"nvenc", "cpu"}, {"nvenc": True, "cpu": True})
    real = E.codec_args
    used: list[str] = []

    def fake_args(kind, crf, preset):
        used.append(kind)
        if kind == "nvenc":                       # a "hardware" encoder that fails on real work
            return ["-c:v", "polixor_no_such_encoder"]
        return real(kind, crf, preset)

    E.codec_args = fake_args                                        # type: ignore[assignment]
    try:
        out = Path(tempfile.mkdtemp(prefix="pxenc_")) / "c.mp4"
        info = probe(TEST_VIDEO)
        req = R.RenderRequest(source=TEST_VIDEO, output=out, segments=[(10.0, 13.0)], width=640, height=360,
                              aspect="16:9", quality="low", hw_accel="auto",
                              source_info={"width": info.width, "height": info.height, "fps": 25, "has_audio": True})
        res = R.render_clip(req)
        assert res.path.exists() and res.duration > 2.5
        assert used[0] == "nvenc" and used[-1] == "cpu", used
        assert "nvenc" in E._broken
    finally:
        E.codec_args = real                                         # type: ignore[assignment]
        _restore_encoders()


def test_settings_v3_moves_the_old_default_to_auto_and_keeps_a_later_choice():
    from polixor.config import migrate_settings

    assert migrate_settings({"settings_version": 2, "hw_accel": "none"})["hw_accel"] == "auto"
    assert migrate_settings({"settings_version": 2, "hw_accel": "nvenc"})["hw_accel"] == "nvenc"
    assert migrate_settings({"settings_version": 3, "hw_accel": "none"})["hw_accel"] == "none"


def test_mastering_at_the_peak_edge_uses_gain_and_limiter_not_fragile_linear_loudnorm():
    """Found by the A/B of the single-pass mastering: a 3.3 dB gain with exactly 3.3 dB of peak
    headroom went to linear loudnorm, which fell back to dynamic mode and ended 2.9 LU short."""
    from polixor.services.audio_mastering import AudioMeasurement, plan_loudness

    m = AudioMeasurement(ok=True, duration=60.0, sample_rate=48000, lufs=-17.3, true_peak=-4.3, lra=1.2,
                         threshold=-27.4)
    plan = plan_loudness(m, "youtube")
    norm = next(s for s in plan.steps if s.action == "normalize")
    assert norm.params.get("mode") == "gain_limited", norm.filter
    assert any(s.action == "limit" and s.applied for s in plan.steps)
    # plenty of headroom: the non-destructive linear pass is still used
    m2 = AudioMeasurement(ok=True, duration=60.0, sample_rate=48000, lufs=-17.3, true_peak=-9.0, lra=4.0,
                          threshold=-27.4)
    norm2 = next(s for s in plan_loudness(m2, "youtube").steps if s.action == "normalize")
    assert norm2.params.get("mode") == "linear", norm2.filter


# --------------------------------------------------------------------------
# render pool
# --------------------------------------------------------------------------
def test_early_renders_run_in_parallel_with_their_own_index_and_a_failure_is_isolated():
    from polixor import clip_factory, pipeline
    from polixor.services import hardware

    os.environ["POLIXOR_RENDER_PARALLEL"] = "3"
    try:
        assert hardware.render_parallel() == 3
        active, peak, seen = [0], [0], []
        lock = threading.Lock()

        def fake_render(view, cand, *, index, total, short):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.3)
            with lock:
                active[0] -= 1
                seen.append(index)
            if index == 1:
                raise RuntimeError("one bad clip")
            return f"clip{index}"

        class Ctx:
            transcript = None
            transcript_original = None
            artifacts: dict = {}
            cancel_event = threading.Event()
            reporter = None
            job_id = "x"

        real = clip_factory.render_candidate
        clip_factory.render_candidate = fake_render                 # type: ignore[assignment]
        real_repair = pipeline._repair_timing
        pipeline._repair_timing = lambda ctx, spans: None           # type: ignore[assignment]
        real_view = pipeline._CtxView
        pipeline._CtxView = lambda ctx, rep: ctx                    # type: ignore[assignment]
        real_q = pipeline._QuietReporter
        pipeline._QuietReporter = lambda r: r                       # type: ignore[assignment]
        try:
            er = pipeline.EarlyRenderer(Ctx(), 5)

            class C:
                def __init__(self, i):
                    self.start, self.end, self.segments, self.title = i * 10.0, i * 10 + 5.0, None, f"c{i}"
            for i in range(5):
                er.submit(C(i), {})
            done = er.close()
        finally:
            clip_factory.render_candidate = real                    # type: ignore[assignment]
            pipeline._repair_timing = real_repair                   # type: ignore[assignment]
            pipeline._CtxView = real_view                           # type: ignore[assignment]
            pipeline._QuietReporter = real_q                        # type: ignore[assignment]
        assert peak[0] >= 2, peak
        assert sorted(seen) == [0, 1, 2, 3, 4]
        assert sorted(done) == ["clip0", "clip2", "clip3", "clip4"]
    finally:
        os.environ.pop("POLIXOR_RENDER_PARALLEL", None)


def test_render_parallelism_respects_the_machine():
    from polixor.services import hardware

    os.environ["POLIXOR_RENDER_PARALLEL"] = "99"
    try:
        assert hardware.render_parallel() == 6
    finally:
        os.environ.pop("POLIXOR_RENDER_PARALLEL", None)
    assert 1 <= hardware.render_parallel() <= 3


# --------------------------------------------------------------------------
# long-form isolation
# --------------------------------------------------------------------------
def test_longform_is_deferred_only_when_more_urgent_work_waits():
    from sqlalchemy import text

    from polixor import pipeline
    from polixor.db import get_engine
    from polixor.errors import LongformDeferred
    from polixor.models import Job, JobStatus, new_id
    from polixor.services import taskq

    with get_engine().begin() as c:
        c.execute(text("DELETE FROM tasks"))
    jid = new_id()
    with session_scope() as s:
        s.add(Job(id=jid, title="lf", status=JobStatus.RUNNING, artifacts={}, completed_stages=[]))

    class Rep:
        def progress(self, *a, **k):
            pass

    class Ctx:
        job_id = jid
        artifacts: dict = {}
        reporter = Rep()

        def _persist(self):
            pass

    os.environ["POLIXOR_IN_WORKER"] = "1"
    try:
        ctx = Ctx()
        pipeline._maybe_defer_longform(ctx)                         # nothing waiting: continue here
        assert not ctx.artifacts.get("longform_task")
        taskq.enqueue("job", "other", key="job:other", priority=taskq.PRIO_FIRST)
        try:
            pipeline._maybe_defer_longform(ctx)
            raise AssertionError("should have deferred")
        except LongformDeferred:
            pass
        assert ctx.artifacts["longform_task"] and ctx.artifacts["resume_pending"]
        with get_engine().connect() as c:
            row = c.execute(text("SELECT priority, status FROM tasks WHERE idempotency_key=:k"),
                            {"k": f"longform:{jid}"}).first()
        assert row.priority == taskq.PRIO_LONGFORM and row.status == "queued"
        pipeline._maybe_defer_longform(ctx)                         # the long-form task itself: no second deferral
    finally:
        os.environ.pop("POLIXOR_IN_WORKER", None)
    os.environ["POLIXOR_IN_WORKER"] = "0"
    ctx2 = Ctx()
    ctx2.artifacts = {}
    pipeline._maybe_defer_longform(ctx2)                            # the web process / tests: never deferred
    os.environ.pop("POLIXOR_IN_WORKER", None)


# --------------------------------------------------------------------------
# profiler
# --------------------------------------------------------------------------
def test_profiler_counts_processes_and_detects_full_source_decodes():
    from polixor.util import profiler

    src = "/data/source.mp4"
    assert profiler._full_source_decode(["ffmpeg", "-i", src, "-vn", "a.wav"], src) == "audio"
    assert profiler._full_source_decode(["ffmpeg", "-ss", "10", "-i", src, "-t", "5", "o.mp4"], src) == ""
    assert profiler._full_source_decode(["ffmpeg", "-i", src, "-t", "5", "o.mp4"], src) == ""
    assert profiler._full_source_decode(["ffmpeg", "-i", "/other.mp4", "o.mp4"], src) == ""
    assert profiler._full_source_decode(["ffmpeg", "-i", src, "-f", "null", "-"], src) == "analysis"
    d = Path(tempfile.mkdtemp(prefix="pxprof_"))
    with profiler.activate("job1", d, "") as p:
        subprocess.run(["true"], check=False)
        subprocess.run(["true"], check=False)
        with profiler.span("work", media=10.0):
            time.sleep(0.05)
        profiler.cache_event("model_answers", True)
        profiler.cache_event("model_answers", False)
        t = threading.Thread(target=profiler.thread_target(lambda: subprocess.run(["true"], check=False)))
        t.start()
        t.join()
    assert p.procs["true"] == 3, dict(p.procs)
    saved = profiler.load(d)["total"]
    assert saved["subprocesses"]["true"] == 3
    assert saved["cache"]["model_answers"] == {"hit": 1, "miss": 1}
    assert saved["bottlenecks"][0]["name"] == "work" and saved["spans"]["work"]["rtf"] is not None


# --------------------------------------------------------------------------
# ASR threads
# --------------------------------------------------------------------------
def test_asr_threads_never_exceed_the_container_quota():
    from polixor.services import hardware
    from polixor.services.transcribe import cpu_threads

    real = hardware.cpus
    try:
        hardware.cpus = lambda: 2                                   # type: ignore[assignment]
        assert cpu_threads() <= 2
        hardware.cpus = lambda: 32                                  # type: ignore[assignment]
        assert 4 <= cpu_threads() <= 16
    finally:
        hardware.cpus = real                                        # type: ignore[assignment]


# --------------------------------------------------------------------------
# paid AI: nothing leaves the machine
# --------------------------------------------------------------------------
PAID_HOSTS = ("anthropic.com", "openai.com")


def test_a_full_project_with_keys_configured_never_contacts_a_paid_provider():
    if not TEST_VIDEO.exists():
        print("   (skipped: no test video)")
        return
    from polixor.config import SECRETS, SETTINGS, AppSettings
    from polixor.models import Job, JobStatus, ProjectPhase, RunScope, new_id
    from polixor.pipeline import run_job
    from polixor.project_config import clamp_config

    SECRETS.set("anthropic_api_key", "sk-ant-test-not-a-real-key")
    SECRETS.set("openai_api_key", "sk-test-not-a-real-key")
    os.environ["POLIXOR_PAID_AI"] = "off"
    os.environ.pop("POLIXOR_SEMANTIC_SCRIPTED", None)
    os.environ["POLIXOR_FIXTURE_TRANSCRIPT"] = str(TEST_VIDEO.with_suffix(".transcript.json"))
    contacted: list[str] = []
    real_gai, real_cc = socket.getaddrinfo, socket.create_connection

    def gai(host, *a, **k):
        if any(h in str(host) for h in PAID_HOSTS):
            contacted.append(str(host))
            raise OSError("blocked by test")
        return real_gai(host, *a, **k)

    def cc(addr, *a, **k):
        if any(h in str(addr[0]) for h in PAID_HOSTS):
            contacted.append(str(addr[0]))
            raise OSError("blocked by test")
        return real_cc(addr, *a, **k)

    socket.getaddrinfo, socket.create_connection = gai, cc          # type: ignore[assignment]
    try:
        base = SETTINGS.get().to_dict()
        base.update({"transcript_provider": "fixture", "ai_mode": "cloud"})
        cfg = clamp_config({"mode": "package", "clip_count": 3, "studio": {"auto_generate": "package"}})
        jid = new_id()
        with session_scope() as s:
            s.add(Job(id=jid, title="no paid ai", input_url="", status=JobStatus.QUEUED,
                      phase=ProjectPhase.ANALYZING.value, run_scope=RunScope.ANALYZE.value,
                      settings_snapshot=AppSettings.from_dict(base).to_dict(), project_config=cfg, mode="package",
                      artifacts={"source_path": str(TEST_VIDEO)}, completed_stages=[]))
        run_job(jid, threading.Event())
        with session_scope() as s:
            j = s.get(Job, jid)
            j.run_scope, j.phase, j.status = RunScope.GENERATE.value, ProjectPhase.GENERATING.value, JobStatus.QUEUED
        run_job(jid, threading.Event())
        with session_scope() as s:
            intel = (s.get(Job, jid).artifacts or {}).get("intelligence") or {}
    finally:
        socket.getaddrinfo, socket.create_connection = real_gai, real_cc  # type: ignore[assignment]
        SECRETS.set("anthropic_api_key", "")
        SECRETS.set("openai_api_key", "")
    assert contacted == [], contacted
    # and the run says honestly that no language model chose (never a silent "semantic")
    assert intel.get("mode") == "degraded", intel


# --------------------------------------------------------------------------
# pipeline overlap
# --------------------------------------------------------------------------
def test_the_visual_scan_overlaps_the_transcription_and_runs_once():
    if not TEST_VIDEO.exists():
        print("   (skipped: no test video)")
        return
    from polixor.config import SETTINGS, AppSettings
    from polixor.models import Job, JobStatus, ProjectPhase, RunScope, new_id
    from polixor.pipeline import run_job
    from polixor.services import frame_scan

    calls: list[str] = []
    real = frame_scan.scan_visual_and_layouts

    def counted(*a, **k):
        calls.append(threading.current_thread().name)
        return real(*a, **k)

    frame_scan.scan_visual_and_layouts = counted                    # type: ignore[assignment]
    os.environ["POLIXOR_OVERLAP_VISUAL"] = "1"
    os.environ["POLIXOR_FIXTURE_TRANSCRIPT"] = str(TEST_VIDEO.with_suffix(".transcript.json"))
    try:
        base = SETTINGS.get().to_dict()
        base.update({"transcript_provider": "fixture"})
        jid = new_id()
        with session_scope() as s:
            s.add(Job(id=jid, title="overlap", input_url="", status=JobStatus.QUEUED,
                      phase=ProjectPhase.ANALYZING.value, run_scope=RunScope.ANALYZE.value,
                      settings_snapshot=AppSettings.from_dict(base).to_dict(), project_config={}, mode=None,
                      artifacts={"source_path": str(TEST_VIDEO)}, completed_stages=[]))
        run_job(jid, threading.Event())
        with session_scope() as s:
            j = s.get(Job, jid)
            arts = j.artifacts or {}
    finally:
        frame_scan.scan_visual_and_layouts = real                   # type: ignore[assignment]
        os.environ.pop("POLIXOR_OVERLAP_VISUAL", None)
    assert calls == ["polixor-visual-prefetch"], calls              # once, in the overlapping thread
    assert arts.get("visual_path") or arts.get("layouts_path") or arts.get("visual_features_path"), sorted(arts)


# --------------------------------------------------------------------------
# two-tier ASR
# --------------------------------------------------------------------------
def _scripted_project(minutes: float, *, mode: str = "short", shorts: int = 3):
    """A finished project on a looped source, with the stand-in editor (no paid call)."""
    from polixor.bench import make_long_source, run

    os.environ["POLIXOR_SEMANTIC_SCRIPTED"] = "1"
    src, fx = make_long_source(minutes, PATHS.data / "bench_media")
    return run(src, fixture=fx, shorts=shorts, mode=mode, label="engine-test")


def test_finalist_asr_runs_only_on_the_candidates_the_ranking_kept():
    if not TEST_VIDEO.exists():
        print("   (skipped: no test video)")
        return
    import json as _json

    from polixor.services import asr_ensemble
    from polixor.services.semantic import run as srun

    built: list[list] = []
    real_build, real_engines = asr_ensemble.build_clip, srun._engines

    def fake_build(spans, **kw):
        built.append([list(x) for x in spans])
        return srun._discovery_only(kw["discovery"], [list(x) for x in spans], kw.get("vocabulary") or [])

    asr_ensemble.build_clip = fake_build                            # type: ignore[assignment]
    srun._engines = lambda inp: {"strong": object()}                # type: ignore[assignment]
    try:
        out = _scripted_project(6.0, shorts=3)
    finally:
        asr_ensemble.build_clip = real_build                        # type: ignore[assignment]
        srun._engines = real_engines                                # type: ignore[assignment]
        os.environ.pop("POLIXOR_SEMANTIC_SCRIPTED", None)
    from polixor.models import Job

    with session_scope() as s:
        arts = s.get(Job, out["project_id"]).artifacts or {}
    rep = _json.loads(Path(arts["intel_report_path"]).read_text("utf-8"))
    found = int(rep.get("candidates") or rep.get("counts", {}).get("candidates") or 0) or \
        len(rep.get("ranking", {}).get("ranked", []) or [])
    assert built, "the finalist transcript never ran"
    # discovery found more moments than were ever re-transcribed: rejected ones never got final ASR
    assert len({str(b) for b in built}) <= 3 + srun.RESERVES, (len(built), found)
    if found:
        assert len({str(b) for b in built}) < found, (len(built), found)


def test_a_subtitle_style_change_reuses_words_and_never_reruns_asr_or_the_editor():
    if not TEST_VIDEO.exists():
        print("   (skipped: no test video)")
        return
    from fastapi.testclient import TestClient

    from polixor.main import app
    from polixor.models import Clip, ClipKind, ClipStatus
    from polixor.services import asr_ensemble, transcribe
    from polixor.services.semantic import run as srun

    out = _scripted_project(3.0, shorts=2)
    os.environ.pop("POLIXOR_SEMANTIC_SCRIPTED", None)
    with session_scope() as s:
        clip = s.query(Clip).filter(Clip.job_id == out["project_id"], Clip.kind != ClipKind.LONG,
                                    Clip.status.in_([ClipStatus.READY, ClipStatus.NEEDS_REVIEW])).first()
        assert clip is not None
        cid = clip.id

    def boom(*a, **k):
        raise AssertionError("re-ran an expensive stage for a style change")

    saved = (transcribe.transcribe_audio, asr_ensemble.build_clip, srun.run)
    transcribe.transcribe_audio, asr_ensemble.build_clip, srun.run = boom, boom, boom  # type: ignore[assignment]
    try:
        with TestClient(app) as c:
            r = c.post(f"/api/clips/{cid}/reexport", json={"subtitle_style": {"preset": "bold", "color": "#FFEE00"}})
        assert r.status_code == 200, r.text[:300]
    finally:
        transcribe.transcribe_audio, asr_ensemble.build_clip, srun.run = saved  # type: ignore[assignment]


# --------------------------------------------------------------------------
# the stand-in editor
# --------------------------------------------------------------------------
def test_the_scripted_editor_is_deterministic():
    from polixor.services.semantic.scripted import ScriptedEditor

    user = "\n".join(f"[s{i:04d} {i * 4.0:.1f}-{i * 4.0 + 3.5:.1f} t0] line {i}" for i in range(60))
    a = ScriptedEditor(latency=0)("candidates", "", user, {})
    b = ScriptedEditor(latency=0)("candidates", "", user, {})
    assert a == b and a["moments"]
    e1 = ScriptedEditor(latency=0)("editor", "", "CUT: s0007\n", {})
    e2 = ScriptedEditor(latency=0)("editor", "", "CUT: s0007\n", {})
    assert e1 == e2


def _run_all() -> int:
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            t0 = time.time()
            fn()
            print(f"✓ {name} ({time.time() - t0:.1f}s)")
        except Exception as exc:                                    # noqa: BLE001
            import traceback

            failed += 1
            print(f"✗ {name}: {exc}")
            traceback.print_exc()
    print(f"{len(tests) - failed}/{len(tests)} engine tests passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
