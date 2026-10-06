"""
RC1 editorial funnel: strong moments are not lost to their own first cut, weak ones still are.

  ranking     "maybe" (good moment, rough raw cut) reaches the editor; a majority of "no" does not;
              duplicates are skipped; the editing budget grows with the source
  rebuild     the reconstruction pass offers a wide range, never cuts the evidence, refuses ids
              outside its options, and says "cannot" instead of guessing
  end to end  with a strict stand-in editor (raw cuts stop before the payoff, the payoff lies two
              sentences later): the pre-RC1 funnel ships 0 Shorts, the RC1 funnel ships them after
              ONE reconstruction each – and every weak moment is still rejected
  forensics   an old report's candidates get their EARLIEST real failure (strict gate vs weak)
  salvage     when nothing ships, further strong candidates are tried before accepting zero

No paid model call (scripted editor, POLIXOR_PAID_AI=off).
Run:  python3 tests/test_funnel.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxfun_"))
os.environ["POLIXOR_PAID_AI"] = "off"

from polixor.config import PATHS                                   # noqa: E402
from polixor.db import init_db, session_scope                      # noqa: E402

PATHS.ensure()
init_db()

from polixor.services.semantic import boundaries, forensics, ranking   # noqa: E402
from polixor.services.semantic.candidates import Cand              # noqa: E402
from polixor.services.semantic.sentences import Sentence          # noqa: E402

TEST_VIDEO = Path(os.environ.get("POLIXOR_TEST_VIDEO", "/home/claude/testdata/polixor_test_stream.mp4"))


def _sents(n: int = 60) -> list[Sentence]:
    out = []
    for i in range(n):
        out.append(Sentence(id=f"s{i:04d}", start=i * 4.0, end=i * 4.0 + 3.6, text=f"משפט {i}",
                            turn=i // 3, words=[(i * 4.0, i * 4.0 + 3.6, f"משפט{i}")],
                            pause_before=0.4, question=(i % 7 == 0)))
    return out


def _cand(key: str, a: int, b: int, *, final: float, verdicts: list[str], topic: str = "t1") -> Cand:
    c = Cand(key=key, type="story", start_idx=a, end_idx=b, cut_idx=[], evidence=[
        {"role": "story", "idx": a + 1, "id": f"s{a + 1:04d}", "quote": "x"},
        {"role": "conclusion", "idx": b - 1, "id": f"s{b - 1:04d}", "quote": "y"}],
        rubric={}, standalone="", title=key, topic=topic, start=a * 4.0, end=b * 4.0 + 3.6)
    no = sum(v == "no" for v in verdicts) / len(verdicts)
    c.scores = {"final": final, "no_share": no}
    c.verdicts = [{"verdict": v, "reason": ""} for v in verdicts]
    return c


def test_maybe_reaches_the_editor_and_a_no_majority_does_not():
    s = _sents()
    pool = [_cand("C1", 2, 9, final=0.52, verdicts=["maybe", "maybe", "maybe"]),
            _cand("C2", 20, 27, final=0.70, verdicts=["no", "no", "maybe"]),
            _cand("C3", 3, 10, final=0.50, verdicts=["ship", "maybe", "maybe"]),     # duplicate of C1
            _cand("C4", 40, 47, final=0.20, verdicts=["maybe", "maybe", "ship"])]    # under the floor
    chosen, dec = ranking.select(sorted(pool, key=lambda c: -c.scores["final"]), s, limit=10)
    why = {d["key"]: d["decision"] for d in dec}
    assert [c.key for c in chosen] == ["C1"], why
    assert why["C2"] == "judges_rejected" and why["C3"].startswith("duplicate_of:") and why["C4"] == "below_bar"


def test_the_editing_budget_grows_with_the_source():
    assert ranking.edit_budget(5, 15 * 60, 100) == 5 + 4 + 1
    assert ranking.edit_budget(30, 4 * 3600, 400) == 30 + 15 + 16
    assert ranking.edit_budget(5, 600, 3) == 3                       # never more than the pool


def test_reconstruction_range_is_wide_and_validated():
    s = _sents()
    c = _cand("C1", 20, 26, final=0.6, verdicts=["maybe"])
    st, en = boundaries.options(c, s, 0, 59)
    wst, wen = boundaries.wide_options(c, s, 0, 59)
    assert min(wst) < min(st) and max(wen) > max(en)
    assert max(wst) > c.opening_idx                                  # a weak opening may be dropped
    assert min(wen) >= c.closing_idx                                 # the payoff is never cut off

    class P:
        name, model = "p", "m"

        def __init__(self, answer):
            self.answer = answer

        def complete_json(self, *a, **k):
            return self.answer
    cur = {"start_idx": 20, "end_idx": 26, "cut_idx": [], "duration": 25.0}
    ok = boundaries.reconstruct(P({"fixable": True, "start_id": "s0012", "end_id": "s0030", "cut_ids": ["s0025", "s0021"],
                                   "start_reason": "", "end_reason": "", "cut_reason": ""}),
                                c, s, cur, "payoff missing", lo=0, hi=59, min_s=20, max_s=60)
    assert ok is not None and ok["start_idx"] == 12 and ok["end_idx"] == 30
    assert 21 not in ok["cut_idx"] and 25 not in ok["cut_idx"] or ok["cut_idx"] == []   # evidence never cut
    assert boundaries.reconstruct(P({"fixable": True, "start_id": "s0001", "end_id": "s0030", "cut_ids": [],
                                     "start_reason": "", "end_reason": "", "cut_reason": ""}),
                                  c, s, cur, "x", lo=0, hi=59, min_s=20, max_s=60) is None
    assert boundaries.reconstruct(P({"fixable": False, "start_id": "s0012", "end_id": "s0030", "cut_ids": [],
                                     "start_reason": "", "end_reason": "", "cut_reason": ""}),
                                  c, s, cur, "x", lo=0, hi=59, min_s=20, max_s=60) is None


def test_forensics_of_a_pre_rc1_report():
    rep = {"pool": [{"key": f"C{i}", "title": f"t{i}", "start": i, "end": i + 30} for i in range(6)],
           "decisions": [
               {"key": "C0", "decision": "judges_rejected", "scores": {"final": 0.6, "no_share": 0.0}},
               {"key": "C1", "decision": "judges_rejected", "scores": {"final": 0.3, "no_share": 0.67}},
               {"key": "C2", "decision": "below_bar", "scores": {"final": 0.5}},
               {"key": "C3", "decision": "duplicate_of:C0", "scores": {"final": 0.6}},
               {"key": "C4", "decision": "selected", "scores": {"final": 0.8}},
               {"key": "C5", "decision": "selected", "scores": {"final": 0.7}}],
           "editor_rejected": [{"key": "C4", "category": "repair_failed:missing_payoff", "reason": "stops early"},
                               {"key": "C5", "category": "weak_moment", "reason": "nothing happens"}],
           "editor_unreviewed": [], "shipped": []}
    f = forensics.classify(rep)
    cat = {r["key"]: r["category"] for r in f["rows"]}
    assert cat == {"C0": "strict_gate", "C1": "weak_moment", "C2": "strict_gate", "C3": "duplicate",
                   "C4": "repair_failed", "C5": "weak_moment"}, cat
    assert f["by_bucket"] == {"overly_strict_gate": 2, "genuinely_weak": 2, "duplicate": 1, "construction": 1}
    assert f["headline"].startswith("0 of 6")


def _strict_run(old: bool) -> dict:
    """A 6-minute looped source, the strict stand-in editor; old=True replays the pre-RC1 funnel."""
    from polixor.bench import make_long_source, run
    from polixor.models import Job

    os.environ["POLIXOR_SEMANTIC_SCRIPTED"] = "1"
    os.environ["POLIXOR_SCRIPTED_STRICT"] = "1"
    saved = (ranking.select, ranking.edit_budget, boundaries.reconstruct)
    if old:
        def old_select(ranked, sentences, *, limit, threshold=0.55, **_streaming):
            chosen, log_ = [], []
            for c in ranked:
                vs = c.verdicts or []
                share = sum(v.get("verdict") == "ship" for v in vs) / len(vs) if vs else 1.0
                why = "limit" if len(chosen) >= limit else (
                    "judges_rejected" if c.scores.get("no_share", 0) > 0.5 or share < 0.5 else (
                        "below_bar" if c.scores.get("final", 0) < threshold else ""))
                log_.append({"key": c.key, "start": c.start, "end": c.end, "type": c.type, "topic": c.topic,
                             "title": c.title, "scores": c.scores, "decision": why or "selected"})
                if not why:
                    chosen.append(c)
            return chosen, log_
        ranking.select, ranking.edit_budget = old_select, (lambda limit, d, n: limit + 4)
        boundaries.reconstruct = lambda *a, **k: None
    try:
        src, fx = make_long_source(6.0, PATHS.data / "bench_media")
        out = run(src, fixture=fx, shorts=3, mode="short", label="funnel-" + ("old" if old else "new"))
    finally:
        ranking.select, ranking.edit_budget, boundaries.reconstruct = saved
        os.environ.pop("POLIXOR_SCRIPTED_STRICT", None)
        os.environ.pop("POLIXOR_SEMANTIC_SCRIPTED", None)
    with session_scope() as s:
        arts = s.get(Job, out["project_id"]).artifacts or {}
    out["report"] = json.loads(Path(arts["intel_report_path"]).read_text("utf-8"))
    return out


def test_strong_moments_with_rough_cuts_ship_after_one_reconstruction():
    if not TEST_VIDEO.exists():
        print("   (skipped: no test video)")
        return
    old = _strict_run(old=True)
    assert old["outputs"]["shorts"] == 0, old["outputs"]                  # the RC1 bug, reproduced
    fo = forensics.classify(old["report"])
    assert fo["by_bucket"].get("overly_strict_gate", 0) >= 1, fo["by_bucket"]
    new = _strict_run(old=False)
    assert new["outputs"]["shorts"] >= 1, new["outputs"]
    rep = new["report"]
    shipped = rep["shipped"]
    assert all(any(h.get("reconstructed") for h in x["editor"]) for x in shipped), "shipped without the rebuild?"
    # weak moments are still rejected: nothing the editor called weak ships
    cats = forensics.classify(rep)["by_category"]
    assert cats.get("shipped", 0) == len(shipped) and "strict_gate" not in cats, cats
    assert all(x["category"] in ("weak_moment", "repair_failed:weak_moment") or x.get("kind") != "content"
               for x in rep["editor_rejected"])


def _run_all() -> int:
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            t0 = time.time()
            fn()
            print(f"✓ {name} ({time.time() - t0:.1f}s)")
        except Exception as exc:                                        # noqa: BLE001
            import traceback

            failed += 1
            print(f"✗ {name}: {exc}")
            traceback.print_exc()
    print(f"{len(tests) - failed}/{len(tests)} funnel tests passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
