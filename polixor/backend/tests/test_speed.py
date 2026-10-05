"""
Speed + editorial-gate behaviour on a small real source (90 s test video, fixture transcript),
with a scripted language model standing in for the editor (no network).

  * only clips the editor ships are rendered – a rejected candidate never is
  * the first Short is rendered while the editor is still judging the others
  * a content package renders every Short before long-form planning starts
  * regenerating with other subtitle settings re-runs no ASR and no model call (all cached)
  * an unavailable editor is "not evaluated", never a quality rejection – and nothing is cached for it
  * a strong moment gets exactly one repair; the diagnostics classify every rejection

Run:  python3 tests/test_speed.py
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxspeed_"))

VIDEO = Path("/home/claude/testdata/polixor_test_stream.mp4")
FIXTURE = VIDEO.with_suffix(".transcript.json")
LINE = re.compile(r"\[(s\d{4}) ([\d.]+)-([\d.]+) t\d+\] (.*)")
EVENTS: list[tuple[float, str]] = []
_LOCK = threading.Lock()


def ev(name: str) -> None:
    with _LOCK:
        EVENTS.append((time.time(), name))


def rubric(n: int) -> dict:
    return {k: {"score": n, "reason": "r"} for k in ("hook", "clarity", "payoff", "interest", "feasibility")}


CHECKS_OK = {k: True for k in ("opening_hooks", "standalone", "payoff", "clean_ending", "pacing")}


class Oracle:
    """A scripted editor: candidate 1 ships, 2 is a weak moment, 3 needs a longer ending then ships."""

    def __init__(self, editor_down: bool = False) -> None:
        self.editor_down = editor_down
        self.plan: dict[str, str] = {}
        self.calls: dict[str, int] = {}

    def __call__(self, task: str, system: str, user: str, schema: dict) -> dict:
        from polixor.services.semantic.provider import SemanticError

        self.calls[task] = self.calls.get(task, 0) + 1
        ev(f"model:{task}")
        lines = [(m.group(1), float(m.group(2)), float(m.group(3)), m.group(4)) for m in LINE.finditer(user)]
        if task == "topic_map":
            return {"topics": [{"start_id": lines[0][0], "end_id": lines[-1][0], "title": "השידור", "summary": "",
                                "central": "", "continues_previous": False, "short_potential": "high",
                                "long_form_value": "high"}], "junk": [], "names": []}
        if task == "topic_merge":
            pieces = re.findall(r"^(P\d+) ", user, re.M)
            return {"topics": [{"pieces": pieces, "title": "השידור", "summary": "", "long_form_value": "high"}]}
        if task == "profile":
            return {"profile": "livestream", "confidence": "high", "reason": "streamer"}
        if task == "candidates":
            body = [x for x in lines]
            out, i = [], 0
            while i < len(body) and len(out) < 3:
                j = i
                while j + 1 < len(body) and body[j][2] - body[i][1] < 16:
                    j += 1
                if body[j][2] - body[i][1] >= 10:
                    self.plan[body[i][0]] = ("ship", "weak", "repair")[len(out)]
                    out.append({"type": "story", "start_id": body[i][0], "end_id": body[j][0],
                                "evidence": [{"role": "story", "sentence_id": body[i][0], "quote": body[i][3]},
                                             {"role": "conclusion", "sentence_id": body[j][0], "quote": body[j][3]}],
                                "rubric": rubric(2), "standalone": "", "cut_ids": [], "title": f"רגע {len(out) + 1}"})
                i = j + 1
            return {"moments": out}
        if task == "rank":
            keys = re.findall(r"=== (C\d+) ", user)
            return {"ranking": keys, "verdicts": [{"key": k, "verdict": "ship", "reason": ""} for k in keys]}
        if task == "boundaries":
            st = re.findall(r"^(s\d{4}) \(", user.split("ALLOWED STARTS:")[1].split("ALLOWED ENDS:")[0], re.M)
            en = re.findall(r"^(s\d{4}) \(", user.split("ALLOWED ENDS:")[1], re.M)
            return {"start_id": st[-1], "end_id": en[0], "cut_ids": [], "start_reason": "", "end_reason": "",
                    "cut_reason": ""}
        if task == "editor":
            if self.editor_down:
                raise SemanticError("provider overloaded")
            time.sleep(1.5)                       # model latency: renders overlap with judging
            ev("done:editor")
            start = re.search(r"CUT: (s\d{4})", user).group(1)
            kind = next((v for k, v in self.plan.items() if k <= start), "ship")
            kind = self.plan.get(start, kind)
            if kind == "weak":
                return {"verdict": "reject", "checks": CHECKS_OK, "fixes": [], "reason": "nothing happens",
                        "scores": {**rubric(1), "interest": {"score": 0, "reason": "dull"}}}
            if kind == "repair" and self.calls["editor"] and "repaired:" + start not in self.plan:
                self.plan["repaired:" + start] = "1"
                return {"verdict": "repair", "checks": CHECKS_OK | {"payoff": False}, "fixes": [],
                        "reason": "stops before the point", "scores": rubric(2)}
            return {"verdict": "ship", "checks": CHECKS_OK, "fixes": [], "reason": "ok", "scores": rubric(2)}
        if task == "hooks":
            words = user.split("CLIP TRANSCRIPT:\n")[1].split()[:4]
            return {"hooks": [{"text": " ".join(words), "support": [" ".join(words)],
                               "scores": {k: 4 for k in ("truthfulness", "specificity", "curiosity", "clarity",
                                                         "natural", "relevance")}}], "titles": ["כותרת"]}
        if task == "adjudicate":
            return {"decisions": []}
        if task == "longform":
            return {"keep": [{"start_id": lines[0][0], "end_id": lines[-1][0], "purpose": "all"}],
                    "title": "השידור", "description": ""}
        raise AssertionError(task)


def _project(oracle: Oracle, *, mode: str = "package"):
    import polixor.services.semantic.run as R
    from polixor import clip_factory
    from polixor.config import AppSettings, PATHS
    from polixor.db import init_db, session_scope
    from polixor.models import Job, JobStatus, ProjectPhase, RunScope, new_id
    from polixor.pipeline import run_job
    from polixor.project_config import clamp_config
    from polixor.services.semantic import provider as P

    os.environ["POLIXOR_FIXTURE_TRANSCRIPT"] = str(FIXTURE)
    PATHS.ensure()
    init_db()
    prov = P.FunctionProvider(oracle)
    R.resolve = lambda settings, cache_dir=None: (_cached(prov, cache_dir), "")
    orig = clip_factory.render_candidate

    def render(ctx, cand, **kw):
        ev(f"render:{'short' if kw.get('short') else 'long'}:{cand.title}")
        return orig(ctx, cand, **kw)
    clip_factory.render_candidate = render
    base = AppSettings().to_dict()
    base.update({"transcript_provider": "fixture", "ai_mode": "cloud", "video_quality": "low",
                 "performance_profile": "fast", "final_asr_ensemble": False})
    cfg = clamp_config({"mode": mode, "clip_count": 3, "clip_min_seconds": 10, "clip_max_seconds": 40,
                        "longform_target_seconds": 60, "studio": {"quality": "fast"}})
    jid = new_id()
    with session_scope() as s:
        s.add(Job(id=jid, title="speed", input_url="", status=JobStatus.QUEUED, phase=ProjectPhase.ANALYZING.value,
                  run_scope=RunScope.ANALYZE.value, settings_snapshot=AppSettings.from_dict(base).to_dict(),
                  artifacts={"source_path": str(VIDEO)}, completed_stages=[], project_config=cfg, mode=mode))
    run_job(jid, threading.Event())
    return jid, render, orig


_PROVIDERS: dict = {}


def _cached(prov, cache_dir):
    """The same provider object per cache dir, with that project's disk cache (like resolve())."""
    prov.cache_dir = cache_dir
    return prov


def _generate(jid: str) -> None:
    from polixor.db import session_scope
    from polixor.models import Job, JobStatus, ProjectPhase, RunScope
    from polixor.pipeline import run_job

    with session_scope() as s:
        j = s.get(Job, jid)
        j.run_scope, j.phase, j.status = RunScope.GENERATE.value, ProjectPhase.GENERATING.value, JobStatus.QUEUED
    run_job(jid, threading.Event())


def test_package_ships_early_renders_only_shipped_and_long_form_waits_for_shorts():
    if not (VIDEO.exists() and FIXTURE.exists()):
        print("    (skipped: no test video)")
        return
    from polixor import clip_factory
    from polixor.db import session_scope
    from polixor.models import Clip, ClipKind, Job, StageTiming
    from polixor.services import diagnostics

    oracle = Oracle()
    jid, render, orig = _project(oracle)
    try:
        from polixor import pipeline
        orig_lf = pipeline._render_package_longforms

        def lf(ctx):
            ev("longform:start")
            return orig_lf(ctx)
        pipeline._render_package_longforms = lf
        EVENTS.clear()
        t0 = time.time()
        try:
            _generate(jid)
        finally:
            pipeline._render_package_longforms = orig_lf
        took = time.time() - t0
        names = [n for _, n in EVENTS]
        renders = [n for n in names if n.startswith("render:short")]
        with session_scope() as s:
            job = s.get(Job, jid)
            shorts = s.query(Clip).filter(Clip.job_id == jid, Clip.kind == ClipKind.SHORT).all()
            longs = s.query(Clip).filter(Clip.job_id == jid, Clip.kind == ClipKind.LONG).all()
            g = diagnostics.gate(job)
            perf = diagnostics.profile(job)
            arts = dict(job.artifacts or {})
        print(f"    package generation: {took:.0f} s · {len(shorts)} Shorts, {len(longs)} long · events {len(names)}")
        # only shipped clips are rendered: 2 ship (one after a repair), the weak one never
        assert len(shorts) == 2 and len(renders) == 2, (renders, [c.title for c in shorts])
        assert not any("רגע 2" in n for n in renders), "a rejected candidate is never rendered"
        assert g["shipped"] == 2 and g["rejected_by_category"] == {"weak_moment": 1}, g
        assert g["repaired"] >= 1, g
        # the first Short was rendered while the editor was still judging (early renderer)
        first_render = names.index(renders[0])
        last_editor = max(i for i, n in enumerate(names) if n == "done:editor")
        assert first_render < last_editor, names
        # Shorts first, then long-form planning and rendering
        first_long_task = names.index("longform:start")
        assert max(names.index(r) for r in renders) < first_long_task, names
        # an 86-second source has no topic long enough for a long-form video: none is forced
        assert len(longs) == 0
        m = arts.get("run_metrics") or {}
        assert m["first_short_at"] <= m["all_shorts_at"] <= m["longform_at"], m
        assert perf["milestones"]["time_to_first_short"] is not None
        # regenerate after a subtitle-style change: no ASR stage, no new model call (cache)
        with session_scope() as s:
            n_tr = s.query(StageTiming).filter(StageTiming.job_id == jid, StageTiming.stage == "transcribe").count()
            j = s.get(Job, jid)
            cfg = dict(j.project_config)
            cfg["subtitles"] = {**cfg["subtitles"], "style": {**cfg["subtitles"]["style"], "size": 6.0}}
            j.project_config = cfg
        before = dict(oracle.calls)
        _generate(jid)
        with session_scope() as s:
            n_tr2 = s.query(StageTiming).filter(StageTiming.job_id == jid, StageTiming.stage == "transcribe").count()
            again = s.query(Clip).filter(Clip.job_id == jid, Clip.kind == ClipKind.SHORT).count()
        assert n_tr2 == n_tr, "the source is not transcribed again"
        assert oracle.calls == before, ("no model call is repeated", before, oracle.calls)
        # and the long-form planning after the Shorts reused the selection's stages (no 2nd topic map)
        assert names.count("model:topic_map") == 1 and names.count("model:candidates") == 1, names
        assert again == 2
    finally:
        clip_factory.render_candidate = orig


def test_an_unavailable_editor_is_not_a_rejection_and_is_not_cached():
    if not (VIDEO.exists() and FIXTURE.exists()):
        print("    (skipped: no test video)")
        return
    from polixor import clip_factory
    from polixor.db import session_scope
    from polixor.models import Clip, Job
    from polixor.services import diagnostics
    from polixor.services.semantic import editor

    editor.RETRY_PAUSE = 0.0
    oracle = Oracle(editor_down=True)
    jid, render, orig = _project(oracle, mode="short")
    try:
        EVENTS.clear()
        _generate(jid)
        with session_scope() as s:
            job = s.get(Job, jid)
            g = diagnostics.gate(job)
            clips = s.query(Clip).filter(Clip.job_id == jid).count()
            notes = list((job.artifacts or {}).get("notes") or [])
        assert clips == 0 and not any(n.startswith("render") for _, n in EVENTS), "nothing judged, nothing rendered"
        assert g["rejected"] == 0 and g["not_evaluated"] >= 1, g
        assert g["verdict"] in ("editor_unavailable", "no_candidates"), g
        # the editor comes back: generating again judges them (nothing was cached as a rejection)
        oracle.editor_down = False
        _generate(jid)
        with session_scope() as s:
            g2 = diagnostics.gate(s.get(Job, jid))
        assert g2["shipped"] >= 1 and g2["not_evaluated"] == 0, g2
    finally:
        clip_factory.render_candidate = orig


def test_old_reports_are_classified_for_the_diagnostics():
    from polixor.services.diagnostics import classify_record

    no_payoff = {"verdict": "repair", "checks": CHECKS_OK | {"payoff": False}, "scores": rubric(2), "problems": []}
    assert classify_record({"history": [no_payoff], "reason": "x | the requested repair was not possible"}) == \
        {"category": "missing_payoff", "failed_checks": ["payoff"], "repaired": False, "near_pass": True}
    assert classify_record({"history": [{"verdict": "reject"}], "reason": "editor unavailable: 529"})["category"] \
        == "editor_unavailable"
    weak = {"verdict": "reject", "checks": CHECKS_OK, "scores": {"interest": {"score": 0}}, "problems": []}
    assert classify_record({"history": [weak]})["category"] == "weak_moment"
    assert classify_record({"history": [no_payoff, no_payoff]})["category"] == "repair_failed:missing_payoff"


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
    print(f"\n{passed}/{len(fns)} speed/gate tests passed")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
