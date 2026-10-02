"""
בדיקות לתמלול החזק של חלונות המועמדים (services/strong_windows) ולבחירה
מחדש על הטקסט הנקי (pipeline._rescore_with_strong).

המודל החזק מיוצג כאן בתמלול חלופי קבוע (אין הורדת מודלים בסביבת הבדיקה);
הלוגיקה – אילו חלונות, מתי מחליפים, מה נדחה, מטמון, ואיך הבחירה משתנה –
היא הקוד האמיתי.

הרצה:  python3 tests/test_strong_windows.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxstrong_"))

from polixor import i18n                                             # noqa: E402
from polixor.services import clip_intel                              # noqa: E402
from polixor.services import strong_windows as sw                    # noqa: E402
from polixor.services.transcribe import Segment, TranscriptResult, Word  # noqa: E402
from test_clip_intel import settings, timeline                       # noqa: E402


def seg(start: float, end: float, text: str, p: float = 0.92) -> Segment:
    toks = text.split()
    step = (end - start) / max(1, len(toks))
    return Segment(start=start, end=end, text=text, language="he",
                   words=[Word(round(start + i * step, 3), round(start + (i + 1) * step - 0.02, 3), t, p)
                          for i, t in enumerate(toks)])


def tr(segs) -> TranscriptResult:
    return TranscriptResult(segments=list(segs), language="he", duration=segs[-1].end + 5,
                            provider="faster-whisper", model="small")


class FakeStrong:
    """מחזיר את התמלול "הנכון" של כל חלון שמבקשים."""
    model_name = "strong-test"
    status = ""
    usable = True

    def __init__(self, truth):
        self.truth = truth
        self.calls: list[tuple[float, float]] = []
        self.vocab: list[str] = []

    def add_vocabulary(self, terms):
        self.vocab += list(terms)

    def __call__(self, a, b):
        self.calls.append((a, b))
        return [s for s in self.truth if s.end > a and s.start < b]


# שיחה אמיתית (מה שנאמר) מול מה שהמודל המהיר "שמע"
TRUTH = [
    seg(98.0, 102.0, "טוב אז מה עם הנבחרת בסוף", 0.95),
    seg(103.0, 107.0, "למה דווקא את החלוץ הזה הכניסו להרכב?", 0.93),
    seg(107.5, 112.0, "לדעתי הוא לא מעניין, הייתי מחליף אותו בקשר הצעיר", 0.94),
    seg(112.5, 117.0, "מה פתאום, אני לא מסכים, הוא יותר טוב מכל הקשרים", 0.92),
    seg(117.5, 122.0, "אין מצב, הקשר הצעיר עדיף עליו בכל פרמטר", 0.93),
    seg(122.5, 127.0, "בסופו של דבר המאמן היה צריך להחליף אותו בדקה השישים", 0.94),
]
FAST = [
    seg(10.0, 14.0, "שלום לכולם ברוכים הבאים לשידור", 0.9),
    seg(98.0, 102.0, "טוב עז מעם הנבחרות בסוף", 0.4),
    seg(103.0, 107.0, "למדה דווה קט החלוס הזה הכניסו להרחב?", 0.3),
    seg(107.5, 112.0, "לדתי הו לא מענין הייתי מכליף אותו בקשר הציר", 0.3),
    seg(112.5, 117.0, "מפתאום אני לו מסקים הו יותר תוב מכל הקשרים", 0.3),
    seg(117.5, 122.0, "אין מצב הקשר הציר אדיף אליו בכל פרמטר", 0.35),
    seg(122.5, 127.0, "בסופו שלד בר המאמן היה צריך לכליף אותו בדקה השישים", 0.35),
    seg(140.0, 144.0, "טוב נמשיך לשחק את המשחק שלנו עכשיו", 0.9),
]


# --------------------------------------------------------------------------
def test_windows_replace_only_inside_and_keep_strong_timing():
    base = tr(FAST)
    fake = FakeStrong(TRUTH)
    data = sw.transcribe_windows(base, [(97.0, 128.0)], fake, model="strong-test")
    assert data["windows"][0]["accepted"], data["windows"][0]["reason"]
    merged = sw.apply(base, data)
    texts = [s.text for s in merged.segments]
    assert texts[0] == FAST[0].text and texts[-1] == FAST[-1].text, "outside the window untouched"
    assert "לדעתי הוא לא מעניין, הייתי מחליף אותו בקשר הצעיר" in texts
    w = merged.words_between(107.5, 112.0)
    assert w and all(x.asr == "strong" for x in w)
    assert abs(w[0].start - TRUTH[2].words[0].start) < 1e-6
    # המקור לא השתנה
    assert base.segments[2].text == FAST[2].text
    # מטמון: חלון שכבר תומלל לא נשלח שוב
    again = sw.transcribe_windows(base, [(97.0, 128.0)], fake, previous=data)
    assert len(fake.calls) == 1 and len(again["windows"]) == 1


def test_bad_strong_output_is_not_used():
    base = tr(FAST)
    loop = [seg(103.0, 127.0, " ".join(["קבודלת לי,"] * 12), 0.9)]
    hallu = [seg(103.0, 127.0, "תודה שצפיתם הירשמו לערוץ", 0.9)]
    short = [seg(103.0, 127.0, "כן", 0.9)]
    for alt, reason in ((loop, "loop"), (hallu, "hallucination"), (short, "length")):
        data = sw.transcribe_windows(base, [(102.5, 127.5)], FakeStrong(alt))
        assert not data["windows"][0]["accepted"] and data["windows"][0]["reason"] == reason, data
        assert sw.apply(base, data) is base


def test_plan_prefers_promising_and_respects_budget():
    S = lambda st, en, final, rej="", passed=False: SimpleNamespace(  # noqa: E731
        start=st, end=en, final=final, rejection=rej, passed=passed, low_confidence=0.0)
    stories = [S(100, 130, 0.62, passed=True), S(300, 330, 0.45, "weak_hook"),
               S(500, 530, 0.45, "private_talk"), S(700, 730, 0.1, "no_payoff")]
    wins = sw.plan_windows(stories, duration=3600, threshold=0.5, budget=130)
    assert wins[0][0] <= 100 and wins[0][1] >= 130
    assert any(a <= 300 <= b for a, b in wins), wins
    assert not any(a <= 700 <= b for a, b in wins)
    assert sum(b - a for a, b in wins) <= 130 and not any(a <= 500 <= b for a, b in wins)


def test_learned_vocabulary_names_heard_twice_with_confidence():
    segs = [seg(0, 3, "מבאפה עשה את זה שוב", 0.95), seg(4, 7, "מבאפה הכי טוב בעולם Kick", 0.95),
            seg(8, 10, "Kick", 0.95)]
    data = {"version": sw.VERSION, "windows": [{"start": 0, "end": 10, "accepted": True,
                                                 "segments": [sw.seg_to_dict(s) for s in segs]}]}
    vocab = sw.learned_vocabulary(data)
    assert "מבאפה" in vocab and "Kick" in vocab and "הכי" not in vocab


def test_rescoring_on_clean_text_recovers_a_quiet_debate():
    """על התמלול המשובש אין קליפ; אחרי תמלול חזק של המועמדים – הוויכוח נבחר."""
    from polixor import pipeline

    base = tr(FAST)
    tl = timeline(base.duration)
    st = settings(short_min_seconds=12, short_max_seconds=45)
    with i18n.use_lang("en"):
        an = clip_intel.analyze_stories(tl, base, settings=st, language="he")
        before = clip_intel.finalize(an, limit=3)
        assert not before.selected, [(c.start, c.end) for c in before.selected]
        work = Path(tempfile.mkdtemp())
        notes: list[str] = []
        fake = FakeStrong(TRUTH)
        ctx = SimpleNamespace(settings=st, transcript=base, transcript_original=None, audio_path=None,
                              work_dir=work, artifacts={}, language="he", note=notes.append,
                              cancel_event=None, _strong_retr=fake)
        orig = pipeline._strong_retranscriber
        pipeline._strong_retranscriber = lambda c: c._strong_retr
        try:
            an2 = pipeline._rescore_with_strong(ctx, an, tl, seeds=[], time_offset=0.0)
        finally:
            pipeline._strong_retranscriber = orig
        after = clip_intel.finalize(an2, limit=3)
    assert after.selected, [r["rejection"] for r in after.review["near_misses"]]
    c = after.selected[0]
    assert c.start <= 103.0 and c.end >= 126.5, (c.start, c.end)
    assert ctx.transcript is not base and "strong_windows_path" in ctx.artifacts
    assert fake.calls and all(b - a < 60 for a, b in fake.calls), "only candidate windows"
    assert any("re-transcribed" in n for n in notes), notes
    # מונחים שהמודל החזק שמע בביטחון נלמדים לפרויקט ומשמשים את המעברים הבאים
    assert ctx.artifacts.get("project_vocabulary") and fake.vocab, (ctx.artifacts, fake.vocab)
    # טעינה מחדש (יצירה/ייצוא מחדש) מקבלת אותו טקסט – בלי לתמלל שוב
    tpath = work / "transcript.json"
    import json
    tpath.write_text(json.dumps({"segments": [{"start": s.start, "end": s.end, "text": s.text, "words": [
        w.to_dict() for w in s.words]} for s in base.segments], "language": "he",
        "provider": "faster-whisper"}), "utf-8")
    job = SimpleNamespace(artifacts={"transcript_path": str(tpath),
                                     "strong_windows_path": ctx.artifacts["strong_windows_path"]})
    loaded = pipeline.load_transcript_for_job(job)
    assert [s.text for s in loaded.segments] == [s.text for s in ctx.transcript.segments]



def test_cached_windows_apply_even_without_the_strong_model():
    from polixor import pipeline

    base = tr(FAST)
    work = Path(tempfile.mkdtemp())
    data = sw.transcribe_windows(base, [(97.0, 128.0)], FakeStrong(TRUTH), model="strong-test")
    sw.save(work / "transcript.strong.json", data)
    tl = timeline(base.duration)
    st = settings(short_min_seconds=12, short_max_seconds=45)
    with i18n.use_lang("en"):
        an = clip_intel.analyze_stories(tl, base, settings=st, language="he")
        ctx = SimpleNamespace(settings=st, transcript=base, transcript_original=None, audio_path=None,
                              work_dir=work, artifacts={}, language="he", note=lambda n: None,
                              cancel_event=None)
        orig = pipeline._strong_retranscriber
        pipeline._strong_retranscriber = lambda c: None
        try:
            an2 = pipeline._rescore_with_strong(ctx, an, tl, seeds=[], time_offset=0.0)
        finally:
            pipeline._strong_retranscriber = orig
        assert clip_intel.finalize(an2, limit=3).selected
    assert any("הייתי מחליף" in s.text for s in ctx.transcript.segments)

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
    print(f"\n{passed}/{len(fns)} בדיקות תמלול חזק של מועמדים עברו")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
