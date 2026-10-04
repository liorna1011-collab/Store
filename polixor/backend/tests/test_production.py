"""
Production-pass tests: what a user of Polixor Studio relies on.

  * the default Short is the video + its subtitles: no on-screen editorial
    hook overlay (and saved settings migrate to OFF)
  * subtitle cue timing: lead-in, no lingering on stretched words, no
    flashes, no overlaps, never across an internal cut; deterministic QA
  * the transcript layers apply in the order they were computed

Run:  python3 tests/test_production.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxprod_"))

from polixor.config import AppSettings, migrate_settings          # noqa: E402
from polixor.services import subtitle_timing as ST                 # noqa: E402
from polixor.services.subtitles import Cue                         # noqa: E402

VIDEO = Path("/home/claude/testdata/polixor_test_stream.mp4")
FIXTURE = VIDEO.with_suffix(".transcript.json")


def _cue(a, b, words):
    return Cue(start=a, end=b, text=" ".join(w[2] for w in words),
               words=[{"start": s, "end": e, "text": t} for s, e, t in words])


# --------------------------------------------------------------------------
# 1. no white editorial text by default
# --------------------------------------------------------------------------
def test_overlay_is_off_by_default_and_old_settings_migrate_to_off():
    assert AppSettings().editorial_hook_enabled is False
    # settings saved before the overlay became optional had it on implicitly
    old = AppSettings.from_dict({"editorial_hook_enabled": True, "short_count": 4})
    assert old.editorial_hook_enabled is False and old.short_count == 4
    # a user who turns it on after the change keeps the choice
    on = AppSettings.from_dict({**AppSettings().to_dict(), "editorial_hook_enabled": True})
    assert on.editorial_hook_enabled is True
    assert migrate_settings({})["settings_version"] >= 2


def test_default_export_contains_no_editorial_overlay():
    """A real job with default settings: no clip draws the hook, no subtitle file carries it."""
    if not (VIDEO.exists() and FIXTURE.exists()):
        print("    (skipped: no test video)")
        return
    from polixor.config import PATHS
    from polixor.db import init_db, session_scope
    from polixor.models import Clip, Job, JobStatus, RunScope, new_id
    from polixor.pipeline import run_job

    os.environ["POLIXOR_FIXTURE_TRANSCRIPT"] = str(FIXTURE)
    PATHS.ensure()
    init_db()
    base = AppSettings().to_dict()
    base.update({"transcript_provider": "fixture", "ai_mode": "heuristic", "video_quality": "low",
                 "performance_profile": "fast", "short_min_seconds": 10, "short_max_seconds": 45,
                 "long_enabled": False, "short_count": 2})
    jid = new_id()
    with session_scope() as s:
        s.add(Job(id=jid, title="no overlay", input_url="", status=JobStatus.QUEUED,
                  settings_snapshot=AppSettings.from_dict(base).to_dict(),
                  artifacts={"source_path": str(VIDEO)}, completed_stages=[], run_scope=RunScope.ALL.value))
    run_job(jid, threading.Event())
    with session_scope() as s:
        clips = s.query(Clip).filter(Clip.job_id == jid).all()
        params = [dict(c.render_params or {}) for c in clips]
    assert clips, "the job made clips"
    assert not any((p.get("editorial_hook") or {}).get("rendered") for p in params), params
    for ass in PATHS.job_work_dir(jid).glob("*.ass"):
        assert "PolixorHook" not in ass.read_text("utf-8"), ass
    # every rendered Short carries its subtitle timing QA
    assert all("subtitle_timing" in p for p in params if p.get("resolution")), params


# --------------------------------------------------------------------------
# 2. subtitle cue timing
# --------------------------------------------------------------------------
def test_a_stretched_last_word_no_longer_holds_the_card():
    # the recogniser stretched "עכשיו" over 2.5 s of silence
    c = _cue(1.0, 4.0, [(1.0, 1.3, "אני"), (1.35, 1.6, "אומר"), (1.65, 4.2, "עכשיו")])
    out = ST.finalize([c], duration=10.0)
    assert out[0].end < 2.9, out[0].end
    assert ST.check(out, duration=10.0)["kinds"].get("linger", 0) == 0


def test_card_leads_its_first_word_and_short_gaps_do_not_blink():
    a = _cue(2.0, 3.0, [(2.0, 2.4, "שלום"), (2.45, 3.0, "לכולם")])
    b = _cue(3.2, 4.0, [(3.2, 3.6, "מה"), (3.65, 4.0, "נשמע")])
    out = ST.finalize([a, b], duration=10.0)
    assert abs(out[0].start - (2.0 - ST.LEAD)) < 1e-6
    assert out[0].end == out[1].start, "a 0.2 s gap is bridged, the card does not blink"
    assert ST.check(out)["ok"], ST.check(out)


def test_flash_cards_are_lengthened_or_merged_and_never_overlap():
    a = _cue(1.0, 1.15, [(1.0, 1.15, "כן")])
    b = _cue(1.2, 1.35, [(1.2, 1.35, "לא")])
    c = _cue(5.0, 6.0, [(5.0, 6.0, "בסדר")])
    out = ST.finalize([a, b, c], duration=8.0)
    for x, y in zip(out, out[1:]):
        assert x.end <= y.start + 1e-9
    assert all(x.end - x.start >= 0.5 for x in out), [(x.start, x.end) for x in out]
    assert "כן" in out[0].text and "לא" in " ".join(x.text for x in out), "no text is lost"


def test_cards_stay_inside_their_segment():
    c = _cue(9.6, 9.95, [(9.6, 9.95, "סוף")])
    out = ST.finalize([c], duration=10.0)
    assert out[-1].end <= 10.0


def test_timing_qa_reports_real_problems():
    bad = [Cue(start=0.0, end=4.5, text="x", words=[{"start": 1.5, "end": 1.9, "text": "x"}]),
           Cue(start=4.0, end=4.2, text="y", words=[{"start": 4.0, "end": 4.2, "text": "y"}])]
    kinds = ST.check(bad, duration=4.1)["kinds"]
    assert {"early", "linger", "overlap", "flash", "outside"} <= set(kinds), kinds


def test_final_words_then_timing_repair_in_the_effective_transcript():
    """The timing repair was computed on the final words: it must be applied after them."""
    from polixor import pipeline
    from polixor.services import asr_ensemble, subtitle_align as sa
    from polixor.services.transcribe import Segment, TranscriptResult, Word

    d = Path(tempfile.mkdtemp())
    tr = TranscriptResult(segments=[Segment(start=0.0, end=3.0, text="אחת שתיים", words=[
        Word(0.0, 1.0, "אחת", 0.9), Word(1.1, 3.0, "שתיים", 0.9)])], language="he", duration=3.0,
        provider="faster-whisper", model="m")
    (d / "t.json").write_text(json.dumps({"language": "he", "duration": 3.0, "provider": "faster-whisper",
                                          "segments": [{"start": 0.0, "end": 3.0, "text": "אחת שתיים", "words": [
                                              {"start": 0.0, "end": 1.0, "text": "אחת", "p": 0.9},
                                              {"start": 1.1, "end": 3.0, "text": "שתיים", "p": 0.9}]}]},
                                         ensure_ascii=False), "utf-8")
    final = {"clips": {"k": {"spans": [[0.0, 3.0]], "words": [
        {"start": 0.0, "end": 1.0, "text": "אחת", "status": "agreed"},
        {"start": 1.1, "end": 3.0, "text": "שתיים", "status": "agreed"}]}}}
    asr_ensemble.save(d / "final.json", final)
    after_final = asr_ensemble.apply(tr, final)
    timing = sa.align_transcript(after_final, energy_fn=lambda a, b: None, spans=[(0.0, 3.0)])
    sa.save(d / "timing.json", timing)
    eff = pipeline.effective_transcript({"transcript_path": str(d / "t.json"),
                                         "final_transcripts_path": str(d / "final.json"),
                                         "timing_path": str(d / "timing.json")})
    want = sa.apply(after_final, timing)
    got = [(w.start, w.end) for s in eff.segments for w in s.words]
    assert got == [(w.start, w.end) for s in want.segments for w in s.words], (got, timing.get("stats"))


# --------------------------------------------------------------------------
# 3. Studio: results, reviews, downloads, resume, cleanup
# --------------------------------------------------------------------------
def _mp4(path: Path, seconds: float = 1.0) -> Path:
    import subprocess

    from polixor.util.ffmpeg import ffmpeg_bin

    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"color=c=gray:s=180x320:d={seconds}:r=10", "-f", "lavfi", "-i",
                    f"sine=f=440:d={seconds}", "-shortest", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def _project(status="completed", clips=(("ready", True), ("attention", False))):
    """A finished project with clips: (group, publish-ready?) – rendered files on disk."""
    from polixor.config import PATHS
    from polixor.db import init_db, session_scope
    from polixor.models import Clip, ClipKind, ClipStatus, Job, JobStatus, ProjectPhase, RunScope, new_id

    PATHS.ensure()
    init_db()
    jid = new_id()
    out = PATHS.exports / jid
    with session_scope() as s:
        s.add(Job(id=jid, title="studio", input_url="", status=JobStatus(status), phase=ProjectPhase.DONE.value,
                  run_scope=RunScope.GENERATE.value, artifacts={}, completed_stages=[]))
        ids = []
        for i, (kind, ready) in enumerate(clips):
            cid = new_id()
            f = _mp4(out / f"clip{i}.mp4")
            s.add(Clip(id=cid, job_id=jid, kind=ClipKind.LONG if kind == "long" else ClipKind.SHORT,
                       status=ClipStatus.READY, title=f"clip {i}", file_path=str(f), duration=1.0,
                       width=180, height=320, render_params={
                           "publish": {"ready": ready, "verified": True, "reasons": [] if ready else ["render:x"]},
                           "quality": {"engine": "semantic", "type": "question_answer",
                                       "editor": {"verdict": "ship", "history": [{"checks": {"payoff": True}}]}}}))
            ids.append(cid)
    return jid, ids


def _client():
    from fastapi.testclient import TestClient

    from polixor.main import app

    return TestClient(app)


def test_results_put_only_publish_ready_clips_up_front_and_media_plays_from_the_backend():
    jid, ids = _project(clips=(("short", True), ("short", False), ("long", True)))
    with _client() as c:
        r = c.get(f"/api/studio/projects/{jid}/results").json()
        groups = {x["id"]: x["group"] for x in r["shorts"] + r["long"]}
        assert groups[ids[0]] == "ready" and groups[ids[1]] == "attention" and groups[ids[2]] == "ready"
        assert r["summary"]["publish_ready"] == 2 and len(r["long"]) == 1
        clip = r["shorts"][0]
        assert clip["media"]["video"] == f"/api/clips/{clip['id']}/file", "backend URL, never a local path"
        v = c.get(clip["media"]["video"], headers={"Range": "bytes=0-99"})
        assert v.status_code == 206 and len(v.content) == 100
        d = c.get(clip["media"]["download"])
        assert d.status_code == 200 and d.content[4:8] == b"ftyp"


def test_reviews_persist_export_and_feed_the_metrics():
    jid, ids = _project(clips=(("short", True), ("short", True)))
    with _client() as c:
        assert c.put(f"/api/clips/{ids[0]}/review", json={"post": "yes", "hook": "yes", "story": "yes",
                                                          "subtitles": "good", "edit": "good"}).status_code == 200
        c.put(f"/api/clips/{ids[1]}/review", json={"post": "no", "story": "no", "subtitles": "timing"})
        c.put(f"/api/clips/{ids[1]}/review", json={"note": "ends before the answer", "decision": "rejected"})
        assert c.put(f"/api/clips/{ids[1]}/review", json={"post": "maybe"}).status_code == 422
        r = c.get(f"/api/studio/projects/{jid}/results").json()
        rev = {x["id"]: x["review"] for x in r["shorts"]}
        assert rev[ids[1]]["post"] == "no" and rev[ids[1]]["note"] == "ends before the answer"
        assert rev[ids[1]]["decision"] == "rejected"
        exp = c.get(f"/api/studio/projects/{jid}/reviews.json")
        assert "attachment" in exp.headers["content-disposition"]
        doc = exp.json()
        assert doc["summary"]["approval_rate"] == 0.5 and doc["summary"]["story_failure_rate"] == 0.5
        feats = {row["clip_id"]: row["features"] for row in doc["reviews"]}
        assert feats[ids[0]]["type"] == "question_answer" and feats[ids[0]]["publish"]["ready"]
        m = c.get("/api/studio/metrics").json()
        row = next(p for p in m["projects"] if p["project_id"] == jid)
        assert row["surfaced"] == 2 and row["approval_rate"] == 0.5
        # deleting a clip removes its review (no orphan rows, no FK failure)
        assert c.delete(f"/api/clips/{ids[1]}").status_code == 200


def test_downloads_all_shorts_long_and_package_as_zips():
    import io
    import zipfile

    jid, ids = _project(clips=(("short", True), ("short", False), ("long", True)))
    with _client() as c:
        for kind, want in (("shorts", 2), ("long", 1), ("package", 3)):
            r = c.get(f"/api/studio/projects/{jid}/download?kind={kind}")
            assert r.status_code == 200, (kind, r.text[:200])
            names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
            assert sum(n.endswith(".mp4") for n in names) == want, (kind, names)
            if kind == "package":
                assert "package.json" in names


def test_a_job_interrupted_by_a_restart_resumes_by_itself_and_a_crash_loop_stops():
    from polixor import pipeline
    from polixor.db import init_db, session_scope
    from polixor.models import Job, JobStatus, RunScope, new_id

    init_db()
    jid = new_id()
    with session_scope() as s:
        s.add(Job(id=jid, title="r", input_url="", status=JobStatus.RUNNING, run_scope=RunScope.GENERATE.value,
                  phase="generating", artifacts={}, completed_stages=["probe", "audio", "transcribe", "analyze"]))
    submitted = []
    orig = pipeline.MANAGER.submit
    pipeline.MANAGER.submit = lambda job_id: submitted.append(job_id)
    try:
        pipeline.resume_interrupted_jobs()
        with session_scope() as s:
            j = s.get(Job, jid)
            assert j.status == JobStatus.QUEUED and j.completed_stages[-1] == "analyze", "checkpoints kept"
            assert j.artifacts["auto_resumes"] == 1
            j.status = JobStatus.RUNNING
            j.artifacts = {**j.artifacts, "auto_resumes": pipeline.AUTO_RESUME_LIMIT}
        assert jid in submitted
        pipeline.resume_interrupted_jobs()
        with session_scope() as s:
            assert s.get(Job, jid).status == JobStatus.FAILED, "a job that keeps dying is stopped"
    finally:
        pipeline.MANAGER.submit = orig


def test_a_project_created_with_a_goal_generates_after_its_analysis():
    from polixor import worker
    from polixor.db import init_db, session_scope
    from polixor.models import Job, JobStatus, RunScope, new_id
    from polixor.project_config import clamp_config

    init_db()
    jid = new_id()
    cfg = clamp_config({"studio": {"auto_generate": "package", "content_profile": "news"}})
    with session_scope() as s:
        s.add(Job(id=jid, title="g", input_url="", status=JobStatus.COMPLETED, run_scope=RunScope.ANALYZE.value,
                  phase="configure", artifacts={}, completed_stages=[], project_config=cfg))
    assert worker._auto_continue(jid)
    with session_scope() as s:
        j = s.get(Job, jid)
        assert j.run_scope == RunScope.GENERATE.value and j.mode == "package" and j.status == JobStatus.QUEUED
        assert j.project_config["studio"]["auto_generate"] is None, "only once"
        j.status, j.run_scope = JobStatus.COMPLETED, RunScope.ANALYZE.value
    assert not worker._auto_continue(jid), "a later re-analysis waits for the user"
    from polixor.config import AppSettings
    from polixor.project_config import settings_for_project
    st = settings_for_project(AppSettings(), cfg)
    assert st.content_profile == "news" and st.editorial_hook_enabled is False and st.discovery_asr == "strong"


def test_cleanup_removes_only_temporary_files():
    from polixor.config import PATHS
    from polixor.services import storage

    jid, ids = _project(clips=(("short", True),))
    work = PATHS.work / jid
    (work / "intel" / "llm").mkdir(parents=True)
    keep = [work / "transcript.json", work / "intel" / "topic_map.json", work / "intel" / "llm" / "a.json"]
    temp = [work / ".clip_parts" / "part_000.mp4", work / "x.tmp", PATHS.exports / jid / "short_old_render.mp4"]
    for p in keep + temp:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * 1000)
    u = storage.usage(jid)
    assert u["reclaimable_bytes"] == 3000 and u["files"]["outputs"] >= 1
    assert storage.cleanup(jid, dry_run=True)["freed_bytes"] == 3000 and all(p.exists() for p in temp)
    out = storage.cleanup(jid)
    assert out["deleted"] == 3 and not any(p.exists() for p in temp)
    assert all(p.exists() for p in keep), "checkpoints and caches are kept"
    from polixor.db import session_scope
    from polixor.models import Clip
    with session_scope() as s:
        assert Path(s.get(Clip, ids[0]).file_path).exists(), "the finished clip is kept"


def test_publish_ready_needs_the_editor_and_a_clean_render():
    from polixor.clip_factory import publish_verdict
    from polixor.models import ClipKind
    from polixor.services.selection import Candidate

    def cand(q):
        return Candidate(start=0, end=20, peak_time=10, score=0.9, kind="short", title="t", quality=q)
    shipped = {"engine": "semantic", "editor": {"verdict": "ship"}, "final_transcript": {"critical_unresolved": 0}}
    assert publish_verdict(cand(shipped), kind=ClipKind.SHORT, qa_report=None, audio_check={},
                           timing_qa={"kinds": {}})["ready"]
    degraded = publish_verdict(cand({"engine": "clip_intel"}), kind=ClipKind.SHORT, qa_report=None,
                               audio_check={}, timing_qa={})
    assert not degraded["ready"] and not degraded["verified"]
    bad_timing = publish_verdict(cand(shipped), kind=ClipKind.SHORT, qa_report=None, audio_check={},
                                 timing_qa={"kinds": {"overlap": 1}})
    assert bad_timing["reasons"] == ["subtitle_timing"]
    uncertain = publish_verdict(cand({**shipped, "final_transcript": {"critical_unresolved": 1}}),
                                kind=ClipKind.SHORT, qa_report=None, audio_check={}, timing_qa={})
    assert "uncertain_critical_word" in uncertain["reasons"]


def test_one_broken_clip_does_not_cost_the_others():
    from polixor import clip_factory

    calls = []

    class Rep:
        def check_cancel(self):
            pass

        def log(self, *a, **k):
            pass

    class Ctx:
        job_id = "nojob"
        reporter = Rep()

    def fake(ctx, cand, *, index, total, short):
        calls.append(index)
        if index == 1:
            raise ValueError("boom")
        return f"clip{index}"
    orig = clip_factory.render_candidate
    clip_factory.render_candidate = fake
    try:
        made = clip_factory.render_group(Ctx(), [object(), object(), object()], short=True)
    finally:
        clip_factory.render_candidate = orig
    assert made == ["clip0", "clip2"] and calls == [0, 1, 2]


# --------------------------------------------------------------------------
def _run_all() -> int:
    fns = [(n, f) for n, f in list(globals().items()) if n.startswith("test_") and callable(f)]
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
    print(f"\n{passed}/{len(fns)} production tests passed")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
