"""
בדיקות להגהת התמלול (services/transcript_correct): איתור, תמלול חוזר
ממוקד, החלטה לפי ראיות בלבד, שמירת מקור+תיקון, וסימון לבדיקה ידנית.

המודל החזק מיוצג כאן בתמלול חלופי קבוע של אותם זמנים (בסביבת הפיתוח אין
גישה להורדת מודלים); הלוגיקה שנבדקת – מה נחשב ראיה, מתי מתקנים ומתי רק
מסמנים – היא הקוד האמיתי.

הרצה:  python3 tests/test_transcript_correct.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxcorr_"))

from polixor import i18n                                              # noqa: E402
from polixor.services import subtitles                                # noqa: E402
from polixor.services import transcript_correct as tc                 # noqa: E402
from polixor.services.transcribe import Segment, TranscriptResult, Word  # noqa: E402


def seg(start: float, end: float, text: str, probs: list[float]) -> Segment:
    toks = text.split()
    assert len(toks) == len(probs)
    step = (end - start) / len(toks)
    return Segment(start=start, end=end, text=text, language="he",
                   words=[Word(start + i * step, start + (i + 1) * step - 0.02, t, p)
                          for i, (t, p) in enumerate(zip(toks, probs))])


def transcript(*segs: Segment) -> TranscriptResult:
    return TranscriptResult(segments=list(segs), language="he",
                            duration=max(s.end for s in segs) + 5, provider="faster-whisper",
                            model="small")


GOOD = seg(0.0, 3.0, "שלום לכולם וברוכים הבאים", [0.95, 0.93, 0.9, 0.92])
BAD = seg(4.0, 7.0, "שיחקנו בולו רנט אתמול", [0.9, 0.3, 0.25, 0.85])


def fixed_alt(*alt: Segment):
    """"המודל החזק": מחזיר את החלופה הקבועה לחלון שנשאל."""
    calls = []

    def run(a: float, b: float):
        calls.append((a, b))
        return [s for s in alt if s.end > a and s.start < b]
    run.calls = calls
    return run


# --------------------------------------------------------------------------
def test_detects_only_uncertain_sentences():
    assert tc.suspicious(GOOD)[0] is False
    sus, low = tc.suspicious(BAD)
    assert sus and [w["text"] for w in low] == ["בולו", "רנט"]


def test_windows_merge_neighbours_and_are_capped():
    segs = [seg(i * 5.0, i * 5.0 + 4.0, "א ב", [0.3, 0.3]) for i in range(12)]
    wins = tc.windows_for(segs, list(range(12)), 70.0)
    assert all(b - a <= tc.MAX_WINDOW for a, b, _ in wins)
    assert sum(len(ix) for _, _, ix in wins) == 12 and len(wins) >= 3


def test_stronger_retranscription_corrects_with_audio_timestamps():
    alt = seg(4.0, 7.0, "שיחקנו ולורנט אתמול", [0.92, 0.88, 0.9])
    rv = tc.decide(BAD, 1, [alt], strong_model="strong")
    assert rv.status == "corrected" and rv.source == "retranscription"
    assert rv.corrected == "שיחקנו ולורנט אתמול"
    # מילים נוספו/הוסרו – מותר, כי הראיה מהאודיו חזקה; הזמנים מהחלופה
    assert [w["text"] for w in rv.corrected_words] == ["שיחקנו", "ולורנט", "אתמול"]
    assert rv.original == "שיחקנו בולו רנט אתמול"
    assert rv.evidence[0]["kind"] == "retranscription" and rv.evidence[0]["confidence"] > 0.85


def test_weak_alternative_is_only_flagged():
    alt = seg(4.0, 7.0, "שיחקנו פולו רנט אתמול", [0.7, 0.45, 0.4, 0.8])
    rv = tc.decide(BAD, 1, [alt], strong_model="strong")
    assert rv.status == "flagged" and rv.corrected is None
    assert rv.evidence[0]["text"] == "שיחקנו פולו רנט אתמול"   # מוצע בעורך, לא מוחל


def test_same_text_confirms_the_original():
    alt = seg(4.0, 7.0, "שיחקנו בולו רנט אתמול", [0.9, 0.8, 0.8, 0.9])
    rv = tc.decide(BAD, 1, [alt])
    assert rv.status == "confirmed" and rv.corrected is None


def test_never_accepts_hallucinated_or_inflated_text():
    halluc = seg(4.0, 7.0, "תודה שצפיתם הירשמו לערוץ", [0.95, 0.95, 0.95, 0.95])
    assert tc.decide(BAD, 1, [halluc]).status == "flagged"
    long_alt = seg(4.0, 7.0, " ".join(["מילה"] * 12), [0.95] * 12)
    assert tc.decide(BAD, 1, [long_alt]).status == "flagged"
    assert tc.decide(BAD, 1, []).status == "flagged"          # לא נשמע כלום


def test_vocabulary_term_counts_as_evidence():
    alt = seg(4.0, 7.0, "שיחקנו ולורנט אתמול", [0.7, 0.65, 0.7])
    # שיפור קטן בביטחון, אבל החלופה מכילה מונח מהרשימה שלא היה במקור
    assert tc.decide(BAD, 1, [alt]).status == "flagged"
    rv = tc.decide(BAD, 1, [alt], vocabulary=["ולורנט"])
    assert rv.status == "corrected" and rv.reason["key"] == "reason.retranscription_vocab"


def test_vocabulary_fix_only_touches_uncertain_words():
    s = seg(0.0, 3.0, "אוהת ניצח שוב", [0.3, 0.9, 0.9])
    rv = tc.decide(s, 0, None, vocabulary=["אוהד"])
    assert rv.status == "corrected" and rv.source == "vocabulary"
    assert rv.corrected == "אוהד ניצח שוב"
    confident = seg(0.0, 3.0, "אוהת ניצח שוב", [0.95, 0.9, 0.9])
    assert tc.decide(confident, 0, None, vocabulary=["אוהד"]).status != "corrected"
    far = seg(0.0, 3.0, "משהו ניצח שוב", [0.3, 0.9, 0.9])
    assert tc.decide(far, 0, None, vocabulary=["אוהד"]).status == "flagged"


def test_review_checks_only_the_chosen_spans_and_reuses_results():
    t = transcript(GOOD, BAD, seg(40.0, 43.0, "משהו לא ברור כאן", [0.2, 0.3, 0.3, 0.4]))
    run = fixed_alt(seg(4.0, 7.0, "שיחקנו ולורנט אתמול", [0.92, 0.88, 0.9]))
    data = tc.review_transcript(t, spans=[(0.0, 10.0)], retranscribe=run, strong_model="s")
    assert [r["index"] for r in data["segments"]] == [1]       # רק מה שבטווח ורק החשוד
    assert data["stats"]["corrected"] == 1 and len(run.calls) == 1
    run2 = fixed_alt()
    again = tc.review_transcript(t, spans=[(0.0, 10.0)], retranscribe=run2, previous=data)
    assert run2.calls == [] and again["segments"][0]["status"] == "corrected"


def test_budget_limits_retranscription_and_flags_the_rest():
    segs = [seg(i * 40.0, i * 40.0 + 20.0, "א ב ג", [0.2, 0.2, 0.2]) for i in range(5)]
    run = fixed_alt()
    data = tc.review_transcript(transcript(*segs), spans=None, retranscribe=run,
                                budget_seconds=50.0)
    assert len(run.calls) == 2 and data["budget_skipped_windows"] == 3
    assert data["stats"]["flagged"] == 5


def test_apply_keeps_the_original_and_marks_words():
    t = transcript(GOOD, BAD, seg(10.0, 12.0, "לא ברור", [0.2, 0.3]))
    run = fixed_alt(seg(4.0, 7.0, "שיחקנו ולורנט אתמול", [0.92, 0.88, 0.9]))
    data = tc.review_transcript(t, spans=None, retranscribe=run)
    eff = tc.apply(t, data)
    assert t.segments[1].text == "שיחקנו בולו רנט אתמול"         # המקור לא השתנה
    assert eff.segments[1].text == "שיחקנו ולורנט אתמול"
    assert all(w.flag == "corrected" for w in eff.segments[1].words)
    assert eff.segments[1].words[0].asr == "שיחקנו בולו רנט אתמול"
    assert [w.flag for w in eff.segments[2].words] == ["low", "low"]
    assert eff.meta["corrections"]["corrected"] == 1
    # הסימונים מגיעים עד לכתוביות – העורך יכול להדגיש אותם
    cues = subtitles.build_cues(eff, clip_start=0.0, clip_end=13.0, max_chars=40)
    words = [w for c in cues for w in c.words]
    assert any(w.get("flag") == "corrected" and w.get("asr") for w in words)
    assert any(w.get("flag") == "low" for w in words)
    assert all("p" in w for w in words)


def test_saved_corrections_roundtrip_and_user_edits():
    d = Path(tempfile.mkdtemp())
    path = d / "transcript.corrections.json"
    t = transcript(GOOD, BAD)
    data = tc.review_transcript(t, spans=None, retranscribe=None)
    tc.save(path, data)
    tc.record_user_edit(path, clip_id="c1", cue_id=3, source_start=4.0, source_end=7.0,
                        before="שיחקנו בולו רנט אתמול", after="שיחקנו ולורנט אתמול",
                        asr_text="שיחקנו בולו רנט אתמול")
    back = tc.load(path)
    assert back["segments"][0]["status"] == "flagged"
    ue = back["user_edits"][0]
    assert ue["after"] == "שיחקנו ולורנט אתמול" and ue["asr_text"] and ue["at"]


def test_language_model_is_not_used_when_disabled():
    from polixor.config import AppSettings

    t = transcript(GOOD, BAD)
    data = tc.review_transcript(t, spans=None,
                                retranscribe=fixed_alt(seg(4.0, 7.0, "שיחקנו פולו רנט אתמול",
                                                           [0.7, 0.45, 0.4, 0.8])))
    assert tc.llm_choose(data, t, AppSettings.from_dict({"ai_mode": "heuristic"})) == 0
    assert data["segments"][0]["status"] == "flagged"


def test_reasons_are_translated():
    alt = seg(4.0, 7.0, "שיחקנו ולורנט אתמול", [0.92, 0.88, 0.9])
    with i18n.use_lang("en"):
        rv = tc.decide(BAD, 1, [alt])
    assert "stronger model" in rv.reason["text"]
    with i18n.use_lang("he"):
        rv = tc.decide(BAD, 1, [alt])
    assert "מודל חזק" in rv.reason["text"]


# --------------------------------------------------------------------------
def _run_all() -> int:
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
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
    print(f"\n{passed}/{len(fns)} בדיקות הגהה עברו")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
