"""
Core recovery pass: the evidence a real project carries about itself (no model call, nothing re-run).

  time breakdown  wall seconds by the categories a person thinks in, biggest first; per-Short editor
                  work that ran a few at a time is scaled to the stage's wall time (and marked);
                  early renders that the editor waited for count as render
  cost ledger     every model run of a project is kept – a re-edit does not erase the first run's cost
  replay estimate what "Re-edit with the improved editor" is expected to cost, from the project's own
                  tokens per call, before anyone presses it

Run:  python3 tests/test_recovery.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxrec_"))
os.environ["POLIXOR_PAID_AI"] = "off"

from polixor.config import PATHS                                   # noqa: E402
from polixor.db import init_db, session_scope                      # noqa: E402

PATHS.ensure()
init_db()


def test_time_breakdown_adds_up_and_scales_parallel_editor_work():
    from polixor.services.diagnostics import time_breakdown

    stages = {"probe": {"seconds": 2}, "audio": {"seconds": 8}, "transcribe": {"seconds": 240},
              "analyze": {"seconds": 30}, "select": {"seconds": 400}, "render_short": {"seconds": 90}}
    subs = {"analyze.audio": {"seconds": 5}, "analyze.visual": {"seconds": 20}, "select.semantic": {"seconds": 300},
            "select.render_wait": {"seconds": 60}, "render.qa": {"seconds": 10}}
    # topic map / candidates / ranking ran one after another (120 s); the per-Short steps summed to
    # 540 s with 3 at a time inside the remaining 180 s of the editor stage
    sem = {"topic_map": 40, "candidates": 50, "ranking": 30, "editor": 300, "boundaries": 60, "reconstruct": 90,
           "final_transcript": 60, "hooks": 30}
    rows = time_breakdown(stages, subs, sem, queue=4)
    by = {r["category"]: r for r in rows}
    assert rows[0]["category"] == "asr" and rows == sorted(rows, key=lambda r: -r["seconds"])
    assert by["editor"]["shared"] and abs(by["editor"]["seconds"] - 360 / 540 * 180) < 0.2
    assert abs(by["repairs"]["seconds"] - 90 / 540 * 180) < 0.2
    assert by["render"]["seconds"] == 80 + 60                       # render stage − QC + early-render wait
    total = sum(r["seconds"] for r in rows)
    assert abs(total - (2 + 8 + 240 + 30 + 400 + 90 + 4)) < 1.0, total   # every wall second once
    assert abs(by["other"]["seconds"] - (5 + 40)) < 0.2               # analysis and editor stage leftovers


def _job(intel: dict, arts: dict | None = None) -> str:
    from polixor.models import Job, JobStatus, new_id

    d = Path(tempfile.mkdtemp())
    p = d / "intel_report.json"
    p.write_text(json.dumps(intel), "utf-8")
    jid = new_id()
    with session_scope() as s:
        s.add(Job(id=jid, title="t", input_url="", status=JobStatus.COMPLETED, phase="done", run_scope="generate",
                  mode="short", project_config={"mode": "short", "clip_count": 5},
                  artifacts={"intel_report_path": str(p), "source_info": {"duration": 973.0}, **(arts or {})},
                  completed_stages=[]))
    return jid


def test_every_run_is_in_the_cost_ledger():
    from polixor.models import Job
    from polixor.services import costs

    jid = _job({"usage": {"calls": 40, "input_tokens": 400000, "output_tokens": 50000}},
               {"ai_runs": [costs.run_record({"calls": 40, "input_tokens": 400000, "output_tokens": 50000},
                                             kind="earlier"),
                            costs.run_record({"calls": 20, "input_tokens": 200000, "output_tokens": 30000},
                                             kind="generation")]})
    with session_scope() as s:
        e = costs.project(s.get(Job, jid), 973000, {"price": 99, "currency": "USD", "minutes": 600})
    first = 0.4 * 4 + 0.05 * 20
    second = 0.2 * 4 + 0.03 * 20
    assert abs(e["ai_cost_usd"] - (first + second)) < 0.01 and len(e["ai_runs"]) == 2


def test_replay_estimate_uses_the_projects_own_tokens_and_keeps_cached_steps_free():
    from polixor.models import Job
    from polixor.services import costs

    by_task = {"topic_map": {"calls": 3, "input_tokens": 90000, "output_tokens": 9000},
               "candidates": {"calls": 6, "input_tokens": 120000, "output_tokens": 30000},
               "rank": {"calls": 4, "input_tokens": 80000, "output_tokens": 8000},
               "boundaries": {"calls": 10, "input_tokens": 40000, "output_tokens": 5000},
               "editor": {"calls": 10, "input_tokens": 80000, "output_tokens": 20000}}
    jid = _job({"usage": {"by_task": by_task}, "pool": [{"key": f"C{i}"} for i in range(30)]})
    with session_scope() as s:
        est = costs.replay_estimate(s.get(Job, jid))
    assert est["available"] and est["candidates_judged"] >= 5
    assert set(est["cached_free"]) == {"topic_map", "candidates", "rank"}
    lo, hi = est["usd_range"]
    assert 0 < lo < est["usd"] < hi < 20, est


def test_upload_bench_is_admin_only_and_the_transport_starts_from_its_best_setting():
    from fastapi.testclient import TestClient

    from polixor.api.routes_admin import admin_token
    from polixor.main import app

    adm = {"x-polixor-admin": admin_token()}
    with TestClient(app) as c:
        assert c.put("/api/admin/upload-bench/sink", content=b"x" * 1000).status_code == 403
        r = c.put("/api/admin/upload-bench/sink", content=b"x" * 300000, headers=adm).json()
        assert r["bytes"] == 300000                                   # read and dropped
        before = c.get("/api/uploads/transport").json()
        best = {"request_bytes": 8 * 1024 * 1024, "concurrency": 3, "polixor_MBps": 12.5,
                "raw_MBps_same_setting": 13.0, "raw_MBps_ceiling": 14.0, "overhead_pct": 4}
        assert c.post("/api/admin/upload-bench/result", json={"rows": [], "best": best}).status_code == 403
        assert c.post("/api/admin/upload-bench/result", json={"rows": [], "best": best}, headers=adm).json()["saved"]
        after = c.get("/api/uploads/transport").json()
        assert c.get("/api/admin/upload-bench/result", headers=adm).json()["best"]["concurrency"] == 3
    assert after["profile"].endswith("+measured") and after["concurrency_start"] == min(3, after["concurrency_max"])
    assert after["start_bytes"] == min(after["max_bytes"], 8 * 1024 * 1024) and before["profile"] != after["profile"]


def test_hebrew_detected_in_auto_mode_is_transcribed_by_the_hebrew_model():
    """742e3ca0 / 7f89de1c: content language 'auto' left the whole discovery transcript to the
    generic large-v3-turbo (weak on Hebrew) – the editor then judged garbled Hebrew."""
    import subprocess

    from polixor.config import AppSettings
    from polixor.profiles import GENERAL_STRONG_MODEL, HEBREW_MODEL
    from polixor.services import transcribe as T
    from polixor.util.ffmpeg import ffmpeg_bin

    wav = Path(tempfile.mkdtemp()) / "a.wav"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-f", "lavfi", "-i", "sine=f=300:d=3", "-ar", "16000", "-ac", "1",
                    str(wav)], check=True)
    loaded: list[str] = []
    used: list[str] = []

    class Model:
        def __init__(self, name):
            self.name = name

    prov = T.FasterWhisperProvider()
    orig = (prov._load_model, T._detect_language, prov._run_chunk)
    prov._load_model = lambda settings, plan=None: (loaded.append(plan.model), Model(plan.model))[1]
    T._detect_language = lambda model, *a, **k: ("he", 0.97)

    def run_chunk(runner, batched, plan, *a, **k):
        used.append(getattr(runner, "name", getattr(getattr(runner, "model", None), "name", "?")))
        return []
    prov._run_chunk = run_chunk
    try:
        s = AppSettings(transcribe_language="auto", asr_batched=False)
        prov.transcribe(wav, settings=s, media_duration=3.0)
    finally:
        T._detect_language = orig[1]
    assert loaded[0] == GENERAL_STRONG_MODEL and HEBREW_MODEL in loaded, loaded
    assert used and all(u == HEBREW_MODEL for u in used), used
    # the default discovery beam is the full beam again – also for settings saved with the greedy default
    from polixor.profiles import asr_plan

    assert asr_plan(AppSettings()).discovery_beam == 5
    from polixor.config import migrate_settings

    assert migrate_settings({"settings_version": 3, "discovery_beam_size": 1})["discovery_beam_size"] == 5
    assert migrate_settings({"settings_version": 4, "discovery_beam_size": 1})["discovery_beam_size"] == 1


def test_candidate_recall_with_a_rough_transcript_stays_grounded():
    """742e3ca0: 1 candidate from a 2.5-minute source – a model quoting a rough Hebrew transcript
    fixes its spelling, and one such quote used to reject the whole candidate."""
    import sys as _s
    _s.path.insert(0, str(Path(__file__).resolve().parent))
    import test_semantic as TS
    from polixor.services.semantic import validate as V

    v = V.Validator(TS.SENTS)
    q, a = TS.GOOD_QA["evidence"]
    # the answer quoted with one letter fixed (an ASR typo) – accepted, stored as the transcript's words
    words = a["quote"].split()
    typo = " ".join(words[:-1] + [words[-1][:-1] + ("א" if words[-1][-1] != "א" else "ב")])
    c = v.candidate({**TS.GOOD_QA, "evidence": [q, {**a, "quote": typo}]})
    assert c.ok, c.reasons
    assert c.evidence[1]["quote"] != typo and V.tokens(c.evidence[1]["quote"]) == V.tokens(a["quote"])
    # a filler word the model added inside the quote does not cost the words after it
    padded = a["quote"].replace(" כי ", " כי פשוט ")
    c = v.candidate({**TS.GOOD_QA, "evidence": [q, {**a, "quote": padded}]})
    assert c.ok, c.reasons
    assert "פשוט" not in c.evidence[1]["quote"] and V.tokens(c.evidence[1]["quote"]) == V.tokens(a["quote"])
    # an extra quote the transcript does not hold is dropped – the grounded candidate stands
    extra = {"role": "answer", "sentence_id": q["sentence_id"], "quote": "משהו שאף אחד לא אמר בכלל"}
    c = v.candidate({**TS.GOOD_QA, "evidence": [q, a, extra]})
    assert c.ok and len(c.evidence) == 2, c.reasons
    # but a required role carried only by an invented quote still fails, with the cause named
    c = v.candidate({**TS.GOOD_QA, "evidence": [{**q, "quote": "למה פיטרו את המאמן"}, a]})
    assert not c.ok and "invented_quote" in c.reasons


def test_a_re_edit_re_checks_cached_candidates_without_asking_the_model_again():
    """Re-edit of an existing project: the candidate verdicts the OLD validator cached must be checked
    again (else the re-edit still sees 1 candidate), from the model's cached answers – no new call."""
    import sys as _s
    _s.path.insert(0, str(Path(__file__).resolve().parent))
    import test_semantic as TS
    from polixor.services.semantic import candidates as CD
    from polixor.services.semantic.checkpoint import StageStore
    from polixor.services.semantic.provider import SemanticProvider
    from polixor.services.semantic.topics import Topic, TopicMap

    words = TS.GOOD_QA["evidence"][1]["quote"].split()
    typo = " ".join(words[:-1] + [words[-1][:-1] + "א"])
    answer = {"moments": [{**TS.GOOD_QA, "evidence": [TS.GOOD_QA["evidence"][0],
                                                      {**TS.GOOD_QA["evidence"][1], "quote": typo}]}]}

    class Model(SemanticProvider):
        name = "stub"
        paid = 0

        def _complete(self, task, system, user, schema, max_tokens):
            Model.paid += 1
            return answer, {"input_tokens": 100, "output_tokens": 50}

    t = Topic(id="t1", title="כדורגל", summary="", spans=[(0, len(TS.SENTS) - 1)], short_potential="high",
              long_form_value="none")
    tmap = TopicMap(topics=[t], junk=[])
    windows = list(CD.topic_windows(t, TS.SENTS))
    with tempfile.TemporaryDirectory() as d:
        model = Model("stub-1", cache_dir=Path(d) / "answers")
        kw = dict(language="he", min_s=20.0, max_s=60.0, fingerprint="fp")
        # the first run: the model answered (this is what the project's answer cache holds)
        CD.discover(model, TS.SENTS, tmap, store=StageStore(Path(d) / "run1"), **kw)
        paid = Model.paid
        assert paid == len(windows)
        # the project as the old code left it: these verdicts cached under the old key, moment rejected
        old_store = StageStore(Path(d) / "project")
        for k, (lo, hi) in enumerate(windows):
            key = CD.key_of("cands", "fp", t.id, t.title, lo, hi, 20.0, 60.0, "stub", "stub-1")
            old_store.put(f"cands_{k:03d}", key, {"ok": [], "rejected": [{"reasons": ["invented_quote"]}]})
        ok, _ = CD.discover(model, TS.SENTS, tmap, store=old_store, **kw)
    assert ok, "the re-edit reused the old validator's verdicts"
    assert Model.paid == paid, "the re-edit asked the model again instead of using its cached answers"


def test_the_same_question_asked_twice_at_once_is_paid_once_and_counted():
    import threading

    from polixor.services.semantic.provider import SemanticError, SemanticProvider

    class Slow(SemanticProvider):
        name = "stub"
        paid = 0
        fail = False

        def _complete(self, task, system, user, schema, max_tokens):
            Slow.paid += 1
            time.sleep(0.3)
            if Slow.fail:
                raise SemanticError("network")
            return {"ok": True}, {"input_tokens": 1000, "output_tokens": 100}

    p = Slow("m")                                   # no disk cache: only the in-flight wait can help
    out: list = []
    ts = [threading.Thread(target=lambda: out.append(p.complete_json("editor", "s", "u", {}))) for _ in range(3)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    u = p.usage.to_dict()
    assert out == [{"ok": True}] * 3 and Slow.paid == 1, (out, Slow.paid)
    assert u["calls"] == 1 and u["duplicates"] == 2 and u["input_tokens"] == 1000, u
    # a failed ask is not an answer: the next ask goes to the model again (an editor retry still works)
    Slow.fail, Slow.paid = True, 0
    for _ in range(2):
        try:
            p.complete_json("editor", "s", "other", {})
        except SemanticError:
            pass
    assert Slow.paid == 2 and p.usage.failures == 2


def test_a_paid_validation_run_stops_at_its_spend_cap_and_prices_prompt_caching():
    from polixor.services import costs, paid_guard
    from polixor.services.semantic.provider import SemanticError, SemanticProvider

    # Claude prompt caching: written tokens 1.25×, read tokens 0.1× the input price, on top of input
    assert abs(costs.call_usd("claude-sonnet-5-5", {"cache_write_tokens": 1e6}) - 2.5) < 1e-9
    assert abs(costs.call_usd("claude-sonnet-5-5", {"cache_read_tokens": 1e6}) - 0.2) < 1e-9
    assert costs.call_usd("gpt-4o", {"cache_read_tokens": 1e6}) == 0.0   # a part of OpenAI's prompt tokens

    class Paid(SemanticProvider):
        name = "anthropic"
        paid = 0

        def _complete(self, task, system, user, schema, max_tokens):
            Paid.paid += 1                       # $0.003 per call at the editor tier's price
            return {"ok": True}, {"input_tokens": 1000, "output_tokens": 100}

    old = {k: os.environ.get(k) for k in ("POLIXOR_PAID_AI", "POLIXOR_PAID_AI_CAP_USD")}
    os.environ["POLIXOR_PAID_AI"] = "on"
    os.environ["POLIXOR_PAID_AI_CAP_USD"] = "0.01"
    paid_guard._spent = 0.0
    try:
        p = Paid("claude-sonnet-5-5")
        refused = 0
        for k in range(8):
            try:
                p.complete_json("editor", "s", f"question {k}", {})
            except SemanticError:
                refused += 1
        assert Paid.paid == 4 and refused == 4, (Paid.paid, refused)          # 4 × $0.003 ≥ $0.01
        assert 0.01 <= paid_guard.spent_usd() < 0.0125
        # a paid service that cannot report its cost is not called while a cap is set
        try:
            paid_guard.check("openai:images")
            raise AssertionError("an unmetered paid call was allowed under a cap")
        except paid_guard.PaidAIDisabled:
            pass
        os.environ["POLIXOR_PAID_AI_CAP_USD"] = "not a number"
        try:
            paid_guard.check("anthropic:editor", metered=True)
            raise AssertionError("an unreadable cap allowed spending")
        except paid_guard.PaidAIDisabled:
            pass
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        paid_guard._spent = 0.0


def test_the_editor_judges_the_cut_that_will_be_delivered():
    """742e3ca0: "~21 s dead air" – the metric counted the jump over a removed sentence, and the
    editor judged untrimmed cuts. Long pauses inside a piece are now cut before the judgement."""
    from polixor.services.semantic import editor as E
    from polixor.services.semantic.run import low_confidence

    def w(t0: float, t1: float, p: float = 0.9) -> dict:
        return {"start": t0, "end": t1, "text": "מילה", "p": p}

    # two pieces 20 s apart in the source; inside them only short pauses
    spans = [[10.0, 14.0], [34.0, 38.0]]
    words = [w(10.0, 11.0), w(11.3, 12.5), w(12.8, 14.0), w(34.0, 35.0), w(35.2, 36.5), w(36.8, 38.0)]
    m = E.pacing({"words": words}, spans)
    assert m["dead_air_seconds"] == 0.0 and m["longest_pause"] < 1.0, m

    # a 2 s pause inside talk is cut (0.3 s of air kept on each side) …
    spans = [[0.0, 8.0]]
    words = [w(0.0, 1.5), w(1.6, 3.0), w(5.0, 6.5), w(6.6, 8.0)]
    choice = {"spans": spans, "duration": 8.0}
    tight = E.auto_tighten(choice, {"words": words}, "insight")
    assert tight["spans"] == [[0.0, 3.3], [4.7, 8.0]], tight["spans"]
    assert tight["duration"] == 6.6 and tight["tightened"] == 1.4
    assert E.pacing({"words": words}, tight["spans"])["dead_air_seconds"] == 0.0
    # … but the same 2 s after a punchline is the reaction – kept for a payoff/emotional beat
    assert E.auto_tighten(choice, {"words": words}, "setup_payoff") is choice
    assert E.auto_tighten(choice, {"words": words}, "emotional") is choice
    # nothing long: the choice is returned untouched (no needless re-transcription)
    calm = [w(0.0, 1.5), w(1.8, 3.0), w(3.4, 6.5), w(6.6, 8.0)]
    assert E.auto_tighten(choice, {"words": calm}, "insight") is choice

    # targeted re-hearing: only a clip the discovery model was unsure about
    assert not low_confidence({"words": [w(0, 1)] * 10})
    assert low_confidence({"words": [w(0, 1, 0.3)] * 4 + [w(0, 1)] * 6})
    assert not low_confidence({"words": [w(0, 1, 0.3)] * 3})       # too few words to judge


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
    print(f"{len(tests) - failed}/{len(tests)} recovery tests passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
