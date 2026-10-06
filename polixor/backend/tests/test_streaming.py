"""
RC1 streaming: publishable Shorts while the source is still being transcribed.

  ranking     a moment an early window already shipped is a duplicate; a candidate that ends after
              a window's final transcript is left to the full pass ("beyond_window")
  end to end  a 12-minute source with a chunked ASR at a set speed (fixture): the first Short is
              rendered BEFORE the transcription has finished, the full pass keeps it (same clip,
              counted toward the limit, its moment not repeated), the run report carries the early
              windows and their model use; a later re-edit starts clean
  policy      a source under 10 minutes does not stream; POLIXOR_STREAMING=off disables it

No paid model call (scripted editor, POLIXOR_PAID_AI=off).
Run:  python3 tests/test_streaming.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxstream_"))
os.environ["POLIXOR_PAID_AI"] = "off"

from polixor.config import PATHS                                   # noqa: E402
from polixor.db import init_db, session_scope                      # noqa: E402

PATHS.ensure()
init_db()

from polixor.services.semantic import forensics, ranking           # noqa: E402
from polixor.services.semantic.candidates import Cand              # noqa: E402
from polixor.services.semantic.sentences import Sentence          # noqa: E402

TEST_VIDEO = Path(os.environ.get("POLIXOR_TEST_VIDEO", "/home/claude/testdata/polixor_test_stream.mp4"))


def _sents(n: int = 60) -> list[Sentence]:
    return [Sentence(id=f"s{i:04d}", start=i * 4.0, end=i * 4.0 + 3.6, text=f"משפט {i}", turn=i // 3,
                     words=[(i * 4.0, i * 4.0 + 3.6, f"משפט{i}")], pause_before=0.4, question=False)
            for i in range(n)]


def _cand(key: str, a: int, b: int, final: float) -> Cand:
    c = Cand(key=key, type="story", start_idx=a, end_idx=b, cut_idx=[], evidence=[
        {"role": "story", "idx": a + 1, "id": f"s{a + 1:04d}", "quote": "x"},
        {"role": "conclusion", "idx": b - 1, "id": f"s{b - 1:04d}", "quote": "y"}],
        rubric={}, standalone="", title=key, topic="t1", start=a * 4.0, end=b * 4.0 + 3.6)
    c.scores, c.verdicts = {"final": final, "no_share": 0.0}, [{"verdict": "ship", "reason": ""}]
    return c


def test_early_moments_are_duplicates_and_late_ends_wait_for_the_full_pass():
    s = _sents()
    pool = [_cand("A", 2, 9, 0.9), _cand("B", 20, 27, 0.8), _cand("C", 50, 58, 0.7)]
    chosen, dec = ranking.select(pool, s, limit=10, exclude=[(8.0, 37.0, "early:w0:x")], horizon=200.0)
    why = {d["key"]: d["decision"] for d in dec}
    assert [c.key for c in chosen] == ["B"], why
    assert why["A"] == "duplicate_of:early:w0:x" and why["C"] == "beyond_window"
    rows = {r["key"]: r["category"] for r in forensics.classify({"decisions": dec})["rows"]}
    assert rows["A"] == "duplicate" and rows["C"] == "not_reached"


def _bench(minutes: float, rtf: float, *, label: str):
    from polixor.bench import make_long_source, run

    os.environ["POLIXOR_SEMANTIC_SCRIPTED"] = "1"
    os.environ["POLIXOR_FIXTURE_ASR_RTF"] = str(rtf)
    try:
        src, fx = make_long_source(minutes, PATHS.data / "bench_media")
        return run(src, fixture=fx, shorts=4, mode="short", label=label)
    finally:
        os.environ.pop("POLIXOR_FIXTURE_ASR_RTF", None)


def test_first_short_is_ready_before_the_transcription_ends_and_the_full_pass_keeps_it():
    if not TEST_VIDEO.exists():
        print("   (skipped: no test video)")
        return
    from polixor import streaming
    from polixor.models import Clip, ClipStatus, Job

    out = _bench(12.0, 0.08, label="stream")
    st = out["streaming"]
    assert st and st["windows"] and st["early_shorts"] >= 1, out.get("streaming")
    assert out["time_to_first_short"] < out["time_to_transcribed"], out          # the KPI
    jid = out["project_id"]
    state = streaming.load_state(PATHS.job_work_dir(jid))
    early_ids = {x["clip_id"] for x in state["shorts"] if x.get("clip_id")}
    assert state.get("consumed") and state.get("usage_reported")
    with session_scope() as s:
        job = s.get(Job, jid)
        clips = [c for c in s.query(Clip).filter(Clip.job_id == jid).all() if c.status != ClipStatus.FAILED]
        rep = json.loads(Path(job.artifacts["intel_report_path"]).read_text("utf-8"))
        spans = [(c.source_start, c.source_end) for c in clips]
        ids = {c.id for c in clips}
        ms = list((job.artifacts or {}).get("milestones") or [])
    assert early_ids <= ids, "the generation removed a Short the early window made"
    assert len(clips) <= 4                                                         # the limit counts them
    for i, (a0, a1) in enumerate(spans):                                           # no moment twice
        for b0, b1 in spans[i + 1:]:
            inter = max(0.0, min(a1, b1) - max(a0, b0))
            assert inter < 0.5 * min(a1 - a0, b1 - b0), spans
    assert any(x["key"].startswith("early:") for x in rep["shipped"])
    assert rep["streaming"]["usage"]["calls"] > 0 and rep["usage"]["calls"] >= rep["streaming"]["usage"]["calls"]
    assert any(m["key"] == "candidates" for m in ms) and any(m["key"] == "short_ready" for m in ms)
    # a re-edit later is a fresh generation: the early Shorts are not "kept" again
    assert streaming.preshipped(PATHS.job_work_dir(jid), _settings(jid)) == []


def _settings(jid: str):
    from polixor.models import Job
    from polixor.pipeline import settings_for_job

    with session_scope() as s:
        return settings_for_job(s.get(Job, jid))


def test_a_short_source_or_the_switch_does_not_stream():
    from polixor import streaming

    class Ctx:
        is_live = False
        scope = "all"
        config: dict = {}
        source_info = {"duration": 300.0}
        job_id = "x"

        class settings:
            transcript_provider = "faster-whisper"
            short_enabled = True
            short_count = 5
    os.environ["POLIXOR_SEMANTIC_SCRIPTED"] = "1"
    assert streaming.stream_settings(Ctx()) is None                                # 5 minutes
    Ctx.source_info = {"duration": 1800.0}
    assert streaming.stream_settings(Ctx()) is not None
    os.environ["POLIXOR_STREAMING"] = "off"
    try:
        assert streaming.stream_settings(Ctx()) is None
    finally:
        os.environ.pop("POLIXOR_STREAMING", None)


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
    print(f"{len(tests) - failed}/{len(tests)} streaming tests passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
