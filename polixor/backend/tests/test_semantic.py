"""
בדיקות לשכבת ההבנה הסמנטית (services/semantic) ולתמלול הסופי (asr_ensemble).

כל הקלט סינתטי (אין כאן תמלול אמיתי). "מודל השפה" הוא פונקציה שמתנהגת כמו
מודל – כולל תשובות עוינות (ציטוט מומצא, ראיות הפוכות, מזהה לא קיים, טענה מי
דיבר) – כדי לבדוק שהקוד דוחה כל מה שאינו מבוסס, ושהמנגנון (מפה, מועמדים,
דירוג, גבולות, עורך, המשך אחרי נפילה) עובד. איכות המודל האמיתי נמדדת רק
במדיה אמיתית מול רפרנס זהב (scripts/gold_eval.py).

הרצה:  python3 tests/test_semantic.py
"""

from __future__ import annotations

import ast
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxsem_"))

from polixor.config import AppSettings                                # noqa: E402
from polixor.services import asr_ensemble as E                        # noqa: E402
from polixor.services import asr_loops                                # noqa: E402
from polixor.services.semantic import (boundaries, editor, hooks, provider as P,  # noqa: E402
                                       ranking, run as R, sentences as S, validate as V)
from polixor.services.semantic.candidates import Cand                 # noqa: E402
from polixor.services.transcribe import Segment, TranscriptResult, Word  # noqa: E402

# --------------------------------------------------------------------------
# a synthetic conversation: greetings, a quiet question→answer about a coach,
# a loud but empty stretch, a story with a turn, logistics
# --------------------------------------------------------------------------
LINES = [
    (2.0, "שלום לכולם וברוכים הבאים לתוכנית"),
    (6.0, "היום יש לנו אורח מיוחד מאוד"),
    (10.0, "תודה שהזמנתם אותי לכאן"),
    (40.0, "בוא נדבר על העונה של הקבוצה"),
    (44.0, "למה לדעתך המאמן החליט לוותר על החלוץ הוותיק?"),
    (49.0, "תראה, זאת שאלה שכולם שואלים"),
    (53.0, "האמת היא שהחלוץ לא עמד בעומס של המשחקים"),
    (58.0, "ובסוף המאמן בחר בצעירים כי הם רצים יותר"),
    (63.0, "וזה מה שהציל את העונה של הקבוצה"),
    (68.0, "מעניין מאוד, נעבור הלאה"),
    (100.0, "יאללה יאללה יאללה"),
    (102.0, "וואו וואו וואו איזה רעש"),
    (104.0, "כן כן כן"),
    (150.0, "אני אספר לכם משהו שקרה לי בשנה שעברה"),
    (155.0, "נסעתי למשחק חוץ בלי כרטיס בכלל"),
    (160.0, "השומר בכניסה זיהה אותי מהטלוויזיה"),
    (165.0, "והוא הכניס אותי ישר ליציע הכבוד"),
    (170.0, "מאז אני תמיד מגיע בלי כרטיס"),
    (210.0, "טוב, תזכירו לי לשלוח את הקישור בסוף"),
    (214.0, "ההפסקה תהיה בעוד חמש דקות"),
]


def transcript(lines=LINES, asr: str = "strong") -> TranscriptResult:
    segs = []
    for t, text in lines:
        toks = text.split()
        if text.endswith("?") is False and not text.endswith("."):
            toks[-1] = toks[-1] + "."
        step = 0.42
        words = [Word(round(t + i * step, 3), round(t + i * step + 0.36, 3), w, 0.95, asr=asr)
                 for i, w in enumerate(toks)]
        segs.append(Segment(start=words[0].start, end=words[-1].end, text=" ".join(toks), words=words,
                            language="he"))
    return TranscriptResult(segments=segs, language="he", duration=230.0, provider="faster-whisper",
                            model="strong", meta={"discovery": "strong"})


SENTS = S.build_sentences(transcript())


def sid(fragment: str) -> str:
    return next(s.id for s in SENTS if fragment in s.text)


def ids_in(text: str) -> list[str]:
    return re.findall(r"\[(s\d{4}) ", text)


def rubric(n: int) -> dict:
    return {k: {"score": n, "reason": "r"} for k in ("hook", "clarity", "payoff", "interest", "feasibility")}


GOOD_QA = {"type": "question_answer", "start_id": sid("למה לדעתך"), "end_id": sid("מה שהציל"),
           "evidence": [{"role": "question", "sentence_id": sid("למה לדעתך"), "quote": "למה לדעתך המאמן החליט"},
                        {"role": "answer", "sentence_id": sid("בחר בצעירים"), "quote": "המאמן בחר בצעירים כי הם רצים יותר"}],
           "rubric": rubric(2), "standalone": "", "cut_ids": [], "title": "הצעירים הצילו את העונה"}
GOOD_STORY = {"type": "story", "start_id": sid("אני אספר"), "end_id": sid("מאז אני"),
              "evidence": [{"role": "story", "sentence_id": sid("נסעתי"), "quote": "נסעתי למשחק חוץ בלי כרטיס"},
                           {"role": "turn", "sentence_id": sid("השומר"), "quote": "השומר בכניסה זיהה אותי"},
                           {"role": "conclusion", "sentence_id": sid("מאז אני"), "quote": "מאז אני תמיד מגיע בלי כרטיס"}],
              "rubric": rubric(1), "standalone": "", "cut_ids": [], "title": "בלי כרטיס"}
ADVERSARIAL = [
    # an invented quote
    {**GOOD_QA, "evidence": [{"role": "question", "sentence_id": sid("למה לדעתך"), "quote": "למה פיטרו את המאמן"},
                             GOOD_QA["evidence"][1]]},
    # evidence out of order (answer cited before the question)
    {**GOOD_QA, "evidence": list(reversed(GOOD_QA["evidence"]))},
    # an ID that does not exist
    {**GOOD_QA, "start_id": "s9999"},
    # a question with no answer
    {**GOOD_QA, "evidence": GOOD_QA["evidence"][:1]},
    # who said it – there is no speaker diarization
    {**GOOD_QA, "evidence": [GOOD_QA["evidence"][0], {**GOOD_QA["evidence"][1], "speaker": "המאמן"}]},
    # times that are not the cited sentences' times
    {**GOOD_QA, "start": 3.0},
    # the loud empty stretch: no closing evidence
    {"type": "emotional", "start_id": sid("יאללה"), "end_id": sid("כן כן"), "evidence": [],
     "rubric": rubric(2), "standalone": "", "cut_ids": [], "title": "רעש"},
]


def oracle(task: str, system: str, user: str, schema: dict) -> dict:
    """A scripted 'model': right about the content, plus adversarial answers that must be rejected."""
    if task == "topic_map":
        ids = ids_in(user.split("TRANSCRIPT TO MAP:")[1])
        topics = [{"start_id": sid("בוא נדבר"), "end_id": sid("נעבור הלאה"), "title": "העונה של הקבוצה",
                   "summary": "", "central": "", "continues_previous": False, "short_potential": "high",
                   "long_form_value": "medium"},
                  {"start_id": sid("אני אספר"), "end_id": sid("מאז אני"), "title": "בלי כרטיס", "summary": "",
                   "central": "", "continues_previous": False, "short_potential": "high", "long_form_value": "low"}]
        topics = [t for t in topics if t["start_id"] in ids]
        junk = [{"start_id": sid("שלום לכולם"), "end_id": sid("תודה שהזמנתם"), "kind": "greeting"},
                {"start_id": sid("טוב, תזכירו"), "end_id": sid("ההפסקה"), "kind": "logistics"}]
        names = [{"name": "המאמן", "heard_as": "המאמן"},          # in the transcript: a hint
                 {"name": "ליונל מסי", "heard_as": "מסי"}]           # never said: rejected
        return {"topics": topics, "junk": [j for j in junk if j["start_id"] in ids], "names": names}
    if task == "topic_merge":
        pieces = re.findall(r"^(P\d+) ", user, re.M)
        return {"topics": [{"pieces": [p], "title": p, "summary": "", "long_form_value": "medium"} for p in pieces]}
    if task == "candidates":
        ids = set(ids_in(user))
        out = [m for m in [GOOD_QA, GOOD_STORY] + ADVERSARIAL if m["start_id"] in ids]
        return {"moments": out}
    if task == "rank":
        keys = re.findall(r"=== (C\d+) ", user)
        return {"ranking": keys, "verdicts": [{"key": k, "verdict": "ship", "reason": ""} for k in keys]}
    if task == "boundaries":
        st = re.findall(r"^(s\d{4}) \(", user.split("ALLOWED STARTS:")[1].split("ALLOWED ENDS:")[0], re.M)
        en = re.findall(r"^(s\d{4}) \(", user.split("ALLOWED ENDS:")[1], re.M)
        return {"start_id": st[-1], "end_id": en[0], "cut_ids": [], "start_reason": "starts on the question",
                "end_reason": "ends on the answer", "cut_reason": ""}
    if task == "hooks":
        clip = user.split("CLIP TRANSCRIPT:\n")[1]
        if "המאמן" in clip:
            return {"hooks": [
                {"text": "למה המאמן בחר בצעירים?", "support": ["המאמן בחר בצעירים"],
                 "scores": {k: 5 for k in hooks.WEIGHTS}},
                {"text": "לא תאמינו מה קרה", "support": ["המאמן בחר"], "scores": {k: 5 for k in hooks.WEIGHTS}},
                {"text": "3 סיבות שהמאמן בחר בצעירים", "support": ["המאמן בחר בצעירים"],
                 "scores": {k: 5 for k in hooks.WEIGHTS}}],
                "titles": ["הצעירים הצילו את העונה"]}
        return {"hooks": [{"text": "נסע למשחק בלי כרטיס", "support": ["נסעתי למשחק חוץ בלי כרטיס"],
                           "scores": {k: 4 for k in hooks.WEIGHTS}}], "titles": ["בלי כרטיס ליציע הכבוד"]}
    if task == "editor":
        return {"verdict": "ship", "scores": rubric(2), "fixes": [], "reason": "ok"}
    if task == "adjudicate":
        return {"decisions": []}
    if task == "longform":
        ids = ids_in(user)
        return {"keep": [{"start_id": ids[0], "end_id": ids[-1], "purpose": "all"}], "title": "העונה", "description": ""}
    raise AssertionError(task)


def inputs(work: Path, prov, **kw) -> R.Inputs:
    return R.Inputs(transcript=transcript(), settings=AppSettings(short_min_seconds=10, short_max_seconds=60),
                    work_dir=work, language="he", duration=230.0, limit=3, provider=prov, engines={}, **kw)


# --------------------------------------------------------------------------
def test_sentences_have_ids_times_and_turns():
    assert SENTS[0].id == "s0001" and all(s.start < s.end for s in SENTS)
    q = next(s for s in SENTS if "למה לדעתך" in s.text)
    assert q.question
    # a long pause opens a new turn; no speaker is invented
    assert len({s.turn for s in SENTS}) >= 4 and all(s.speaker is None for s in SENTS)
    line = S.render([q])
    assert line.startswith(f"[{q.id} ") and q.text in line


def test_validator_rejects_every_ungrounded_answer():
    v = V.Validator(SENTS)
    assert v.candidate(GOOD_QA).ok and v.candidate(GOOD_STORY).ok
    reasons = [v.candidate(m).reasons for m in ADVERSARIAL]
    assert "invented_quote" in reasons[0]
    assert "reordered_evidence" in reasons[1]
    assert reasons[2] == ["missing_or_unknown_ids"]
    assert "missing_answer" in reasons[3]
    assert "unsupported_speaker_attribution" in reasons[4]
    assert "impossible_timestamps" in reasons[5]
    assert "no_evidence" in reasons[6]


def test_quotes_tolerate_one_prefix_letter_and_point_at_the_right_sentence():
    v = V.Validator(SENTS)
    c = v.candidate({**GOOD_QA, "evidence": [GOOD_QA["evidence"][0],
                                              {"role": "answer", "sentence_id": sid("האמת היא"),   # cites the line before
                                               "quote": "בסוף המאמן בחר בצעירים"}]})          # and drops the ו
    assert c.ok, c.reasons
    ans = c.evidence[1]
    assert SENTS[ans["idx"]].text.startswith("ובסוף"), "evidence moved to the sentence that holds the words"


def test_no_phrase_lists_or_audio_peaks_in_the_semantic_layer():
    """Lint: the semantic decision layer must not import the lexicon engine or signal scoring."""
    banned = ("clip_intel.story", "clip_intel.score", "clip_intel.units", "clip_intel.topics", "services.lang",
              "..lang", "scoring", "story", "score", "units")
    root = Path(__file__).resolve().parents[1] / "polixor" / "services" / "semantic"
    for p in root.glob("*.py"):
        tree = ast.parse(p.read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = (node.module or "")
                full = "." * node.level + mod
                assert not any(full.endswith(b) or full == b for b in banned), f"{p.name} imports {full}"
                if mod.endswith("clip_intel"):
                    assert all(a.name == "editorial" for a in node.names), f"{p.name}: {[a.name for a in node.names]}"


def test_full_run_finds_the_quiet_moments_and_rejects_the_rest():
    work = Path(tempfile.mkdtemp())
    notes: list[str] = []
    out = R.run(inputs(work, P.FunctionProvider(oracle), note=notes.append, want_longform=True))
    assert out.mode == "semantic"
    starts = sorted(round(c.start) for c in out.shorts)
    assert len(out.shorts) == 2, starts
    qa = next(c for c in out.shorts if c.quality["type"] == "question_answer")
    # the clip opens on the question and ends on the answer (the payoff is never cut)
    assert qa.start <= SENTS[V.Validator(SENTS).idx[sid("למה לדעתך")]].start + 0.01
    assert qa.end >= SENTS[V.Validator(SENTS).idx[sid("בחר בצעירים")]].end - 0.01
    # nothing from greetings, logistics or the loud empty stretch
    assert all(c.start > 30 and not (95 < c.start < 110) and c.end < 205 for c in out.shorts)
    # grounded hook chosen; clickbait and the invented number rejected
    ed = qa.quality["editorial"]
    assert ed["hook"] == "למה המאמן בחר בצעירים?"
    why = {r["text"]: r["reason"] for r in ed["rejected"]}
    assert why["לא תאמינו מה קרה"] == "generic_clickbait"
    assert why["3 סיבות שהמאמן בחר בצעירים"] in ("unverified_number", "ungrounded_name_or_number")
    rep = out.report
    assert rep["rejected_proposal_count"] >= len(ADVERSARIAL) - 1
    assert rep["mode"] == "semantic" and rep["usage"]["calls"] > 0 and rep["strong_asr_seconds"] == 230.0
    assert rep["names"] == ["המאמן"], rep["names"]
    assert out.review["mode"] == "semantic" and out.review["selected"]
    assert out.longforms == [], "a 30-second topic is not a long video"
    assert any("2" in n for n in notes)


def test_topic_video_keeps_question_and_answer_together():
    from polixor.services.semantic import longform_plan as LF

    orig = LF.MIN_TOPIC_SECONDS, LF.MIN_OUTPUT_SECONDS
    LF.MIN_TOPIC_SECONDS, LF.MIN_OUTPUT_SECONDS = 20.0, 10.0
    try:
        def cuts_the_answer(task, system, user, schema):
            if task == "longform":        # keeps the question, drops the answer
                return {"keep": [{"start_id": sid("בוא נדבר"), "end_id": sid("למה לדעתך"), "purpose": "q"}],
                        "title": "העונה", "description": ""}
            return oracle(task, system, user, schema)

        out = R.run(inputs(Path(tempfile.mkdtemp()), P.FunctionProvider(cuts_the_answer), want_longform=True))
    finally:
        LF.MIN_TOPIC_SECONDS, LF.MIN_OUTPUT_SECONDS = orig
    lf = next(x for x in out.longforms if x["title"] == "העונה")
    assert lf["repaired_pairs"] >= 1
    segs = lf["plan"]["segments"]
    ans = next(s for s in SENTS if "בחר בצעירים" in s.text)
    assert any(a <= ans.start and ans.end <= b + 0.3 for a, b in segs), "the answer was put back"
    assert lf["shorts"], "the Shorts of the topic are listed with its long video"


def test_resume_after_a_crash_does_not_repeat_finished_work():
    work = Path(tempfile.mkdtemp())
    calls = {"editor": 0}

    def crashing(task, system, user, schema):
        if task == "editor":
            calls["editor"] += 1
            if calls["editor"] == 2:
                raise RuntimeError("power cut")
        return oracle(task, system, user, schema)

    try:
        R.run(inputs(work, P.FunctionProvider(crashing)))
        raise AssertionError("the crash should propagate")
    except RuntimeError:
        pass
    seen: list[str] = []

    def counting(task, system, user, schema):
        seen.append(task)
        return oracle(task, system, user, schema)

    out = R.run(inputs(work, P.FunctionProvider(counting)))
    assert len(out.shorts) == 2
    # topic map, candidates, ranking and the first Short came from checkpoints
    assert "topic_map" not in seen and "candidates" not in seen and "rank" not in seen, seen
    assert seen.count("editor") == 1, seen
    assert out.report["cache"]["resumed_stages"]


def test_a_saved_and_reloaded_transcript_keeps_its_checkpoints():
    from polixor import pipeline

    tr = transcript()
    for s in tr.segments:                       # times with more precision than the file keeps
        for w in s.words:
            w.start += 0.000449
            w.end += 0.000449
    path = Path(tempfile.mkdtemp()) / "t.json"
    pipeline._save_transcript(tr, path)
    assert S.fingerprint(pipeline._load_transcript(path)) == S.fingerprint(tr)


def test_without_a_model_the_run_is_degraded_and_says_why():
    work = Path(tempfile.mkdtemp())
    inp = inputs(work, None)
    inp.settings = AppSettings(ai_mode="heuristic")
    out = R.run(inp)
    assert out.mode == "degraded" and out.reason == "ai_off" and out.sentences
    inp.settings = AppSettings(ai_mode="auto")
    assert R.run(inp).reason == "no_key"


def test_ranking_is_fair_to_position_in_the_source_and_in_the_prompt():
    """A judge that always prefers whatever it reads first must not decide the order."""
    pool = []
    for k in range(12):
        c = Cand(key=f"C{k + 1:03d}", type="strong_quote", start_idx=0, end_idx=0,
                 evidence=[{"role": "quote", "idx": 0, "id": "s0001", "quote": "x"}], rubric=rubric(1),
                 title="", standalone="", cut_idx=[], topic="T1", start=k * 600.0, end=k * 600.0 + 30)
        pool.append(c)
    work = Path(tempfile.mkdtemp())
    from polixor.services.semantic.checkpoint import StageStore

    def first_wins(task, system, user, schema):
        keys = re.findall(r"=== (C\d+) ", user)
        return {"ranking": keys, "verdicts": [{"key": k, "verdict": "maybe", "reason": ""} for k in keys]}

    ranked = ranking.rank_pool(P.FunctionProvider(first_wins), pool, SENTS, store=StageStore(work), fingerprint="x")
    spread = max(c.scores["rank"] for c in ranked) - min(c.scores["rank"] for c in ranked)
    assert spread < 0.8, [c.scores["rank"] for c in ranked]

    def truth(task, system, user, schema):          # prefers higher keys = later in the source
        keys = sorted(re.findall(r"=== (C\d+) ", user), reverse=True)
        return {"ranking": keys, "verdicts": [{"key": k, "verdict": "ship", "reason": ""} for k in keys]}

    ranked = ranking.rank_pool(P.FunctionProvider(truth), pool, SENTS, store=StageStore(Path(tempfile.mkdtemp())),
                               fingerprint="y")
    order = [c.key for c in ranked]
    assert order[:3] == ["C012", "C011", "C010"] and order[-1] == "C001", order


def test_boundary_choices_are_validated():
    v = V.Validator(SENTS)
    ch = v.candidate(GOOD_QA)
    c = Cand(key="C001", type="question_answer", start_idx=ch.start_idx, end_idx=ch.end_idx, evidence=ch.evidence,
             rubric=rubric(2), title="", standalone="", cut_idx=[], topic="T1",
             start=SENTS[ch.start_idx].start, end=SENTS[ch.end_idx].end)
    bad = P.FunctionProvider(lambda *a: {"start_id": sid("שלום לכולם"), "end_id": sid("ההפסקה"),
                                         "cut_ids": [], "start_reason": "", "end_reason": "", "cut_reason": ""})
    out = boundaries.optimise(bad, c, SENTS, lo=0, hi=len(SENTS) - 1, min_s=10, max_s=60)
    assert out["source"] == "proposal" and out["start_idx"] <= ch.start_idx
    cut_ev = P.FunctionProvider(lambda *a: {"start_id": GOOD_QA["start_id"], "end_id": GOOD_QA["end_id"],
                                            "cut_ids": [sid("בחר בצעירים")], "start_reason": "", "end_reason": "",
                                            "cut_reason": ""})
    out = boundaries.optimise(cut_ev, c, SENTS, lo=0, hi=len(SENTS) - 1, min_s=10, max_s=60)
    assert V.Validator(SENTS).idx[sid("בחר בצעירים")] not in out["cut_idx"], "evidence is never cut"
    starts, ends = boundaries.options(c, SENTS, 0, len(SENTS) - 1)
    assert max(starts) <= c.opening_idx and min(ends) >= c.closing_idx


# --------------------------------------------------------------------------
# final transcript ensemble
# --------------------------------------------------------------------------
def _engine(words: list[tuple[float, float, str]]):
    def f(a, b, prompt=None, hotwords=None):
        ws = [Word(x, y, t, 0.99) for x, y, t in words if a <= (x + y) / 2 <= b]
        return [Segment(start=ws[0].start, end=ws[-1].end, text="", words=ws)] if ws else []
    return f


TRUE = [(0.0, 0.4, "הוא"), (0.5, 0.9, "שחרר"), (1.0, 1.4, "1026"), (1.5, 1.9, "מחבלים"), (2.0, 2.4, "ולא"),
        (2.5, 2.9, "התחרט")]


def test_ensemble_votes_by_model_family_and_never_invents():
    a_hyp = [(0.0, 0.4, "הוא"), (0.5, 0.9, "שחרר"), (1.0, 1.4, "1027"), (1.5, 1.9, "מחבלים"), (2.0, 2.4, "ולא"),
             (2.5, 2.9, "התחרט")]
    b_hyp = TRUE
    disc = TranscriptResult(segments=[Segment(start=0, end=3, text="", words=[Word(x, y, t, 0.9)
                                                                               for x, y, t in a_hyp])],
                            language="he", duration=3.0)
    # A, C (discovery) and D (re-hearing) are the same strong family; B is independent
    rec = E.build_clip([(0.0, 3.0)], strong=_engine(a_hyp), second=_engine(b_hyp), discovery=disc,
                       rehear=_engine(a_hyp), adjudicate=None)
    num = next(w for w in rec["words"] if w["text"] in ("1026", "1027"))
    assert num["status"] == "unresolved" and num["critical"] == "number", num
    assert set(num["alts"]) == {"1026", "1027"} and rec["stats"]["critical_unresolved"] == 1
    # a judge may only choose a heard variant – an invented word is ignored
    rec2 = E.build_clip([(0.0, 3.0)], strong=_engine(a_hyp), second=_engine(b_hyp), discovery=disc,
                        rehear=_engine(a_hyp),
                        adjudicate=lambda items: {it["id"]: {"choice": "1025", "unresolved": False} for it in items})
    assert next(w for w in rec2["words"] if w["text"].startswith("10"))["status"] == "unresolved"
    rec3 = E.build_clip([(0.0, 3.0)], strong=_engine(a_hyp), second=_engine(b_hyp), discovery=disc,
                        rehear=_engine(a_hyp),
                        adjudicate=lambda items: {it["id"]: {"choice": "1026", "unresolved": False} for it in items})
    w = next(w for w in rec3["words"] if w["text"].startswith("10"))
    assert w["text"] == "1026" and w["status"] == "adjudicated"
    # two families agree → majority without a judge
    rec4 = E.build_clip([(0.0, 3.0)], strong=_engine(TRUE), second=_engine(TRUE), discovery=disc,
                        rehear=_engine(TRUE), adjudicate=None)
    assert next(w for w in rec4["words"] if w["text"] == "1026")["status"] == "majority"
    # the final words replace the discovery words in the effective transcript
    tr = E.apply(disc, {"version": E.VERSION, "clips": {E.span_key(rec4["spans"]): rec4}})
    assert "1026" in tr.text_between(0, 3) and "1027" not in tr.text_between(0, 3)


def test_focused_rehearing_uses_the_independent_model_and_stops_when_nothing_changes():
    rec = {"words": [{"start": 1.0, "end": 1.4, "text": "1027", "status": "unresolved", "critical": "number",
                      "alts": ["1027", "1026"]}, {"start": 1.5, "end": 1.9, "text": "מחבלים", "status": "agreed"}],
           "stats": {"unresolved": 1, "critical_unresolved": 1}}
    heard = _engine([(1.0, 1.4, "1026"), (1.5, 1.9, "מחבלים")])
    out, n = E.refine(rec, [[0.5, 2.0]], heard)
    assert n == 1 and out["words"][0]["text"] == "1026" and out["words"][0]["status"] == "majority"
    assert out["stats"]["critical_unresolved"] == 0
    # an engine that hears something else resolves nothing – and invents nothing
    out, n = E.refine(rec, [[0.5, 2.0]], _engine([(1.0, 1.4, "1025")]))
    assert n == 0 and out is rec
    # the editor does not loop on a re-hearing that changed nothing
    v = V.Validator(SENTS)
    ch = v.candidate(GOOD_QA)
    c = Cand(key="C001", type="question_answer", start_idx=ch.start_idx, end_idx=ch.end_idx, evidence=ch.evidence,
             rubric=rubric(2), title="", standalone="", cut_idx=[], topic="T1",
             start=SENTS[ch.start_idx].start, end=SENTS[ch.end_idx].end)
    ans = SENTS[ch.evidence[1]["idx"]]
    bad = {"words": [{"start": ans.start, "end": ans.start + 0.3, "text": "לא", "status": "unresolved",
                      "critical": "negation", "alts": ["לא", "כן"]}], "stats": {"words": 1}, "loops": []}
    calls = []
    model = P.FunctionProvider(lambda *a: (calls.append(1), {"verdict": "ship", "scores": rubric(2), "fixes": [],
                                                             "reason": ""})[1])
    choice = {"start_idx": ch.start_idx, "end_idx": ch.end_idx, "cut_idx": [],
              "spans": [[SENTS[ch.start_idx].start, SENTS[ch.end_idx].end]], "duration": 20.0}
    plan = editor.review(editor.Plan(c, choice, bad, {"hook": "x"}), SENTS, model, lo=0, hi=len(SENTS) - 1,
                         retranscribe=lambda spans, focus=None: bad, rebuild_hook=lambda p: {"hook": "x"})
    assert plan.verdict == "reject" and len(plan.history) == 1, plan.history


def test_loops_are_found_and_repaired():
    loop = [Segment(start=0, end=12, text="", words=[Word(i * 0.5, i * 0.5 + 0.4, t, 0.99)
                                                     for i, t in enumerate(("איך אתה מסביר " * 6).split())])]
    lp = asr_loops.find_loops(loop)
    assert lp and lp[0].repeats >= 4
    fixed, rep = asr_loops.repair(loop, lambda a, b: [Segment(start=0, end=2, text="", words=[
        Word(0.0, 0.4, "איך", 0.9), Word(0.5, 0.9, "אתה", 0.9), Word(1.0, 1.4, "מסביר", 0.9)])])
    assert rep[0]["action"] == "re-heard" and len([w for s in fixed for w in s.words]) == 3
    fixed, rep = asr_loops.repair(loop, lambda a, b: None)
    assert rep[0]["action"] == "collapsed" and all(w.flag == "loop" for s in fixed for w in s.words)
    assert not asr_loops.find_loops([Segment(start=0, end=3, text="", words=[
        Word(0, .3, "לא", .9), Word(.4, .7, "לא", .9), Word(.8, 1.1, "לא", .9)])]), "speech is not a loop"


def test_editor_rehears_a_critical_word_and_rejects_only_after_repair_fails():
    v = V.Validator(SENTS)
    ch = v.candidate(GOOD_QA)
    c = Cand(key="C001", type="question_answer", start_idx=ch.start_idx, end_idx=ch.end_idx, evidence=ch.evidence,
             rubric=rubric(2), title="", standalone="", cut_idx=[], topic="T1",
             start=SENTS[ch.start_idx].start, end=SENTS[ch.end_idx].end)
    ans = SENTS[ch.evidence[1]["idx"]]
    bad = {"words": [{"start": ans.start, "end": ans.start + 0.3, "text": "לא", "status": "unresolved",
                      "critical": "negation", "alts": ["לא", "כן"]}], "stats": {"words": 1}, "loops": []}
    good = {"words": [{"start": ans.start, "end": ans.start + 0.3, "text": "לא", "status": "majority"}],
            "stats": {"words": 1}, "loops": []}
    choice = {"start_idx": ch.start_idx, "end_idx": ch.end_idx, "cut_idx": [],
              "spans": [[SENTS[ch.start_idx].start, SENTS[ch.end_idx].end]], "duration": 20.0}
    ok_model = P.FunctionProvider(lambda *a: {"verdict": "ship", "scores": rubric(2), "fixes": [], "reason": ""})
    plan = editor.review(editor.Plan(c, dict(choice), bad, {"hook": "x"}), SENTS, ok_model, lo=0, hi=len(SENTS) - 1,
                         retranscribe=lambda spans, focus=None: good, rebuild_hook=lambda p: {"hook": "x"})
    assert plan.verdict == "ship" and plan.history[0]["fixes"][0]["kind"] == "rehear"
    plan = editor.review(editor.Plan(c, dict(choice), bad, {"hook": "x"}), SENTS, ok_model, lo=0, hi=len(SENTS) - 1,
                         retranscribe=lambda spans, focus=None: bad, rebuild_hook=lambda p: {"hook": "x"})
    assert plan.verdict == "reject" and "critical_unresolved" in plan.reason
    # a better start the model asks for is applied only from the allowed options
    asks = P.FunctionProvider(lambda *a: {"verdict": "repair", "scores": rubric(1), "reason": "",
                                          "fixes": [{"kind": "better_start", "sentence_ids": [sid("שלום לכולם")],
                                                     "note": ""}]})
    plan = editor.review(editor.Plan(c, dict(choice), good, {"hook": "x"}), SENTS, asks, lo=0, hi=len(SENTS) - 1,
                         retranscribe=lambda spans, focus=None: good, rebuild_hook=lambda p: {"hook": "x"})
    assert plan.choice["start_idx"] == ch.start_idx and plan.verdict == "ship"


def test_hooks_use_only_verified_words():
    words = [{"text": "שחרר", "status": "agreed"}, {"text": "1026", "status": "unresolved", "critical": "number"}]
    clip = "הוא שחרר 1026 מחבלים ולא התחרט"
    assert hooks.check("הוא שחרר 1026 מחבלים", ["שחרר 1026 מחבלים"], clip, words, "he") == "uses_unverified_word"
    assert hooks.check("שחרר מחבלים ולא התחרט", ["ולא התחרט"], clip, words, "he") == ""
    assert hooks.check("שחרר מחבלים ולא התחרט", ["משהו שלא נאמר"], clip, words, "he") == "unsupported"


# --------------------------------------------------------------------------
# provider: the Anthropic request (no network)
# --------------------------------------------------------------------------
class _FakeStream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.msg


def test_anthropic_request_shape_cache_and_refusal():
    prov = P.AnthropicProvider("claude-opus-5-5", "sk-test", effort="high", cache_dir=Path(tempfile.mkdtemp()))
    sent: list[dict] = []

    def msg(stop="end_turn", text='{"ok": true}'):
        return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="thinking", thinking=""),
                                                          SimpleNamespace(type="text", text=text)],
                               usage=SimpleNamespace(input_tokens=100, output_tokens=20, cache_read_input_tokens=0,
                                                     cache_creation_input_tokens=50))

    replies = [msg()]
    prov.client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(
        stream=lambda **kw: (sent.append(kw), _FakeStream(replies.pop(0)))[1])))
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"],
              "additionalProperties": False}
    assert prov.complete_json("t", "SYSTEM", "USER", schema) == {"ok": True}
    req = sent[0]
    assert req["model"] == "claude-opus-5-5" and req["fallbacks"] == "default"
    assert req["betas"] == [P.FALLBACK_BETA] and req["thinking"] == {"type": "adaptive"}
    assert req["output_config"] == {"effort": "high", "format": {"type": "json_schema", "schema": schema}}
    assert req["system"][0]["cache_control"] == {"type": "ephemeral"}
    # the same question again is answered from the disk cache
    assert prov.complete_json("t", "SYSTEM", "USER", schema) == {"ok": True} and len(sent) == 1
    assert prov.usage.cached == 1 and prov.usage.calls == 1 and prov.usage.cache_write_tokens == 50
    replies.append(msg(stop="refusal", text=""))
    try:
        prov.complete_json("t", "SYSTEM", "OTHER", schema)
        raise AssertionError("refusal must raise")
    except P.SemanticError:
        pass
    assert prov.usage.failures == 1
    # the key never appears in what is cached or reported
    blob = json.dumps(prov.usage.to_dict()) + "".join(p.read_text() for p in prov.cache_dir.glob("*.json"))
    assert "sk-test" not in blob


# --------------------------------------------------------------------------
# pipeline: degraded mode is labelled everywhere
# --------------------------------------------------------------------------
def test_pipeline_labels_degraded_mode():
    from polixor import pipeline
    from polixor.services.clip_intel.review import localize
    from polixor.services import analysis_store, selection

    work = Path(tempfile.mkdtemp())
    notes: list[str] = []
    rev = analysis_store.save_clip_review(work, {"selected": [], "near_misses": [], "duplicates": []})
    ctx = SimpleNamespace(settings=AppSettings(ai_mode="heuristic"), transcript=transcript(), work_dir=work,
                          artifacts={"clip_review_path": str(rev)}, language="he", note=notes.append,
                          source_info={"duration": 230.0}, cancel_event=None, audio_path=None,
                          reporter=SimpleNamespace(progress=lambda *a, **k: None))
    out = pipeline._select_semantic(ctx, SimpleNamespace(duration=230.0), want_longform=False)
    assert out.mode == "degraded"
    c = selection.Candidate(start=1, end=20, peak_time=5, score=0.5)
    pipeline._label_degraded(ctx, [c], out.reason)
    assert c.quality["mode"] == "degraded"
    review = localize(analysis_store.load_clip_review(Path(ctx.artifacts["clip_review_path"])))
    assert review["mode"] == "degraded" and review["mode_label"]
    rep = json.loads(Path(ctx.artifacts["intel_report_path"]).read_text("utf-8"))
    assert rep["mode"] == "degraded" and rep["clips_labelled"] and notes


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
    print(f"\n{passed}/{len(fns)} בדיקות שכבה סמנטית עברו")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
