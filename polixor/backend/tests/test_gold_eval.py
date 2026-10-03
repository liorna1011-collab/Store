"""
בדיקות להערכה מול רפרנס זהב אנושי (polixor/evaluation/gold.py, scripts/gold_eval.py).

הרפרנס כאן סינתטי. הבדיקות מוודאות שהמדדים מודדים את מה שהם מתיימרים:
אפס קליפים = ריקול 0; קליפ שחותך את הפאנץ' לא נחשב; קליפ על אזור אסור
נספר; שמות ומספרים לא מאומתים לא נספרים; עותק מוסתר (hash) נותן אותן
תוצאות כמו העותק הקריא; ומצב מנוון בלי תווית נכשל בנראות.

הרצה:  python3 tests/test_gold_eval.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from polixor.evaluation import gold as G                             # noqa: E402
import gold_eval                                                     # noqa: E402

GOLD = {
    "schema": G.SCHEMA, "id": "synthetic", "status": "engineering_reference", "coverage": "full",
    "source": {"duration": 300.0, "language": "he", "match": {"name_contains": ["synthetic_debate"]}},
    "moments": [
        {"id": "M1", "start": 20.0, "end": 50.0, "label": "ship", "kind": "question_answer",
         "anchors": [{"role": "hook", "t": 20.2, "t_end": 22.0, "text": "למה הקבוצה הפסידה"},
                     {"role": "payoff", "t": 47.0, "t_end": 49.5, "text": "המאמן אשם"}]},
        {"id": "M2", "start": 100.0, "end": 140.0, "label": "ship",
         "anchors": [{"role": "hook", "t": 100.5, "t_end": 102.0, "text": "יש לי סיפור"},
                     {"role": "payoff", "t": 137.0, "t_end": 139.0, "text": "וככה זכיתי"}]},
        {"id": "M3", "start": 200.0, "end": 215.0, "label": "maybe"},
        {"id": "M4", "start": 250.0, "end": 280.0, "label": "no"},
    ],
    "negatives": [{"id": "X1", "start": 0.0, "end": 12.0, "reason": "greetings"}],
    "topics": [{"id": "T1", "start": 15.0, "end": 150.0}],
    "terms": [{"kind": "number", "text": "2016", "t": [130.0], "status": "verified"},
              {"kind": "name", "text": "מסי", "accept": ["מסִי"], "t": [30.0], "status": "verified"},
              {"kind": "name", "text": "רונאלדו", "t": [45.0], "status": "unresolved"}],
    "unresolved": [{"id": "U1", "t": 45.0, "kind": "name"}],
}
TEXT_M1 = "למה הקבוצה הפסידה אתמול? מסי לא שיחק טוב. אבל בסוף המאמן אשם בכל"
TEXT_M2 = "יש לי סיפור מטורף. ב-2016 נסעתי לטורניר וככה זכיתי"


def _gold() -> G.Gold:
    return G.parse_gold(GOLD)


def test_zero_outputs_score_zero_recall():
    r = G.evaluate(_gold(), G.Run("after", clips=[]))
    assert r["recall_ship"] == 0.0 and r["recall_pool"] == 0.0 and r["precision"] is None
    assert r["ship_missed"] == ["M1", "M2"]


def test_complete_clips_score_and_text_checks():
    run = G.Run("after", clips=[G.Clip(19.8, 50.2, TEXT_M1, hook="למה הקבוצה הפסידה?"),
                                G.Clip(100.2, 139.5, TEXT_M2, hook="הסיפור של 2017")],
                mode="semantic", strong_seconds=300.0, semantic_seconds=300.0, source_seconds=300.0)
    r = G.evaluate(_gold(), run)
    assert r["precision"] == 1.0 and r["recall_ship"] == 1.0
    assert r["transcript"]["phrase_accuracy"] == 1.0
    assert r["names_numbers"]["number"]["accuracy"] == 1.0 and r["names_numbers"]["name"]["accuracy"] == 1.0
    assert r["unresolved_in_clips"] == 1                     # the unresolved name is counted, not scored
    # a hook with a number the clip never says is not grounded
    assert r["hook_grounding"]["hooks"] == 2 and r["hook_grounding"]["grounded"] == 1
    assert r["source_coverage"] == {"strong_asr": 1.0, "semantic": 1.0} and r["mode"]["visible"]


def test_cut_payoff_negative_and_duplicates():
    run = G.Run("after", clips=[G.Clip(20.0, 40.0, TEXT_M1),           # ends before the payoff
                                G.Clip(1.0, 11.0, "שלום לכולם"),       # greetings
                                G.Clip(200.0, 215.0, "x"),            # maybe
                                G.Clip(201.0, 214.0, "x")])           # the same moment again
    r = G.evaluate(_gold(), run)
    assert r["recall_ship"] == 0.0 and "M1" in r["ship_missed"]
    assert r["negative_hits"] == 1
    assert r["precision"] == round((0.5 + 0.5) / 4, 3)
    assert r["duplicates"]["pairs"] >= 1


def test_pool_recall_counts_candidates_that_were_not_shipped():
    run = G.Run("after", clips=[], pool=[(18.0, 52.0), (120.0, 135.0)], pool_known=True)
    r = G.evaluate(_gold(), run)
    assert r["recall_pool"] == 0.5 and r["recall_ship"] == 0.0     # M2's pool candidate misses its payoff


def test_redacted_copy_scores_identically_and_hides_text():
    red = G.redact(GOLD)
    dump = json.dumps(red, ensure_ascii=False)
    assert "המאמן" not in dump and "מסי" not in dump and red["redacted"]
    run = G.Run("after", clips=[G.Clip(19.8, 50.2, TEXT_M1), G.Clip(100.2, 139.5, "יש לי סיפור וככה זכיתי ב-2017")])
    a, b = G.evaluate(_gold(), run), G.evaluate(G.parse_gold(red), run)
    for k in ("precision", "recall_ship", "transcript", "names_numbers"):
        assert a[k] == b[k], k
    assert a["names_numbers"]["number"]["accuracy"] == 0.0          # 2017 ≠ 2016


def test_degraded_mode_must_be_labelled():
    clips = [G.Clip(19.8, 50.2, TEXT_M1)]
    hidden = G.evaluate(_gold(), G.Run("after", clips=clips, mode="degraded", clips_labelled=False))
    shown = G.evaluate(_gold(), G.Run("after", clips=clips, mode="degraded", clips_labelled=True))
    silent = G.evaluate(_gold(), G.Run("after", clips=clips))
    assert not hidden["mode"]["visible"] and shown["mode"]["visible"] and not silent["mode"]["visible"]


def test_partial_map_reports_unrated_clips():
    g = G.parse_gold({**GOLD, "coverage": "partial", "negatives": []})
    r = G.evaluate(g, G.Run("after", clips=[G.Clip(19.8, 50.2, TEXT_M1), G.Clip(160.0, 180.0, "x")]))
    assert r["precision"] == 1.0 and "1 unrated" in r["precision_basis"]


def test_longform_topics():
    run = G.Run("after", clips=[], longs=[G.Clip(14.0, 150.0, kind="long", segments=[(14.0, 60.0), (95.0, 150.0)])])
    assert G.evaluate(_gold(), run)["longform"]["recall"] == 1.0


def test_kit_run_and_auto_found_gold():
    d = Path(tempfile.mkdtemp(prefix="pxgold_"))
    (d / "gold").mkdir()
    (d / "gold" / "synthetic.gold.json").write_text(json.dumps(G.redact(GOLD), ensure_ascii=False), "utf-8")
    after = {"label": "after", "media": "/x/synthetic_debate.mp4", "media_seconds": 300.0,
             "clips": [{"id": "c1", "start": 19.8, "end": 50.2, "cues": [{"text": TEXT_M1}]}],
             "long_clips": [], "artifacts": {},
             "diagnostics": {"intelligence": {"mode": "semantic", "strong_asr_seconds": 300.0,
                                              "semantic_seconds": 300.0,
                                              "pool": [{"start": 19.8, "end": 50.2}, {"start": 99, "end": 140}]},
                             "editorial_hooks": [{"clip_id": "c1", "text": "למה הקבוצה הפסידה", "rendered": True}]}}
    (d / "after.json").write_text(json.dumps(after, ensure_ascii=False), "utf-8")
    orig = gold_eval.GOLD_DIRS
    gold_eval.GOLD_DIRS = (d / "gold",)
    try:
        out = gold_eval.score_acceptance(d)
    finally:
        gold_eval.GOLD_DIRS = orig
    r = out["results"]["after"]
    assert r["recall_ship"] == 0.5 and r["recall_pool"] == 1.0 and r["recall_pool_known"]
    assert r["hook_grounding"]["grounded"] == 1 and r["mode"]["declared"] == "semantic"
    assert (d / "gold_eval.md").exists()


def test_draft_gold_is_written_for_review_and_never_scored():
    d = Path(tempfile.mkdtemp(prefix="pxdraft_"))
    rep = d / "intel_report.json"
    rep.write_text(json.dumps({"pool": [{"key": "C001", "start": 20.0, "end": 50.0, "type": "question_answer",
                                         "topic": "T1", "title": "t", "evidence": [
                                             {"role": "question", "t": 20.2, "quote": "q"},
                                             {"role": "answer", "t": 47.0, "quote": "a"}]}],
                               "topics": [{"id": "T1", "start": 10.0, "end": 60.0, "title": "x"}],
                               "junk": [{"start": 0.0, "end": 5.0, "kind": "greeting"}]}), "utf-8")
    (d / "after.json").write_text(json.dumps({"media": "/v/synthetic_debate.mp4", "media_seconds": 300.0,
                                              "artifacts": {"intel_report_path": str(rep)}}), "utf-8")
    path = gold_eval.draft(d, d / "gold")
    g = json.loads(path.read_text("utf-8"))
    assert g["status"] == "draft_unreviewed" and g["moments"][0]["label"] == "hold"
    assert path.with_suffix(".md").exists()
    assert G.find_gold(Path("/v/synthetic_debate.mp4"), 300.0, [d / "gold"]) is None, "drafts never count"


def test_committed_gold_files_are_valid_and_redacted():
    for p in sorted((ROOT / "gold").glob("*.gold.json")):
        g = G.load_gold(p)
        assert g.raw.get("redacted"), p
        assert not any("֐" <= ch <= "׿" for ch in p.read_text("utf-8")), f"Hebrew text in {p}"
        assert gold_eval.check(p) == 0


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
    print(f"\n{passed}/{len(fns)} בדיקות הערכה מול זהב עברו")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
