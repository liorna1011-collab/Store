"""
בדיקות להבנה הסמנטית: פירוק למשפטים, מילות מילוי וסיווג תפקידים.

הרצה:  python3 tests/test_semantics.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxsem_"))

from polixor.config import PATHS, AppSettings                    # noqa: E402

PATHS.ensure()

from polixor.services import semantics as sem                    # noqa: E402
from polixor.services.transcribe import (                        # noqa: E402
    Segment, TranscriptResult, Word,
)


def build(lines: list[tuple[str, float]], gap: float = 0.35
          ) -> TranscriptResult:
    """בונה תמלול סינתטי עם תזמון ברמת מילה."""
    segs, t = [], 0.0
    for text, dur in lines:
        toks = text.split()
        per = dur / max(1, len(toks))
        words, wt = [], t
        for tok in toks:
            words.append(Word(start=round(wt, 3), end=round(wt + per * 0.92, 3),
                              text=tok))
            wt += per
        segs.append(Segment(start=t, end=t + dur, text=text, words=words,
                            language="he"))
        t += dur + gap
    return TranscriptResult(segments=segs, language="he", duration=t)


STORY = [
    ("תקשיבו אני חייב לספר לכם משהו", 3.0),
    ("אה אמ אז בעצם לפני שנה", 2.4),
    ("עבדתי במקום שממש לא אהבתי", 2.8),
    ("ואז ואז הגעתי למקום הכי נמוך בחיים שלי", 3.4),
    ("הסיבה שזה קרה היא שלא הקשבתי לעצמי", 3.6),
    ("מה הייתם עושים במצב כזה", 2.2),
    ("היום אני יודע שזה היה הדבר הכי טוב שקרה לי", 3.8),
    ("עקבו אחריי לעוד סיפורים כאלה", 2.4),
]


# ==========================================================================
# התאמת ביטויים בגבולות מילה
# ==========================================================================
def test_lexicon_respects_word_boundaries():
    """
    רגרסיה לבאג אמיתי: „כי " הותאם בתוך „ה<b>כי</b> נמוך", ולכן משפט
    רגשי סווג כטענה. התאמת תת-מחרוזת אינה קבילה בעברית.
    """
    n = sem._norm
    assert sem._hits(n("הגעתי למקום הכי נמוך"), sem.CLAIM_HE) == 0
    assert sem._hits(n("זה קרה כי לא הקשבתי"), sem.CLAIM_HE) == 1
    # „לבד" לא מותאם בתוך „לבדוק"
    assert sem._hits(n("צריך לבדוק את זה"), sem.EMOTION_HE) == 0
    assert sem._hits(n("הייתי לבד בבית"), sem.EMOTION_HE) == 1


def test_hebrew_prefix_is_matched():
    """תחילית דבוקה (ו/ה/ב/ל/כ/מ/ש) עדיין נחשבת התאמה."""
    n = sem._norm
    assert sem._hits(n("ולבד זה קשה"), sem.EMOTION_HE) == 1
    assert sem._hits(n("שנשברתי לגמרי"), sem.EMOTION_HE) == 1


def test_english_boundaries():
    n = sem._norm
    assert sem._hits(n("uncomfortable situation"), sem.CTA_EN) == 0
    assert sem._hits(n("comment below if you agree"), sem.CTA_EN) == 1


# ==========================================================================
# פירוק למשפטים
# ==========================================================================
def test_splits_on_segment_boundaries():
    """
    גבולות המקטעים של המתמלל נושאים מידע. בלי לכבד אותם שמונה שורות
    התאחדו לשלושה „משפטים" של תשע שניות.
    """
    sentences = sem.split_sentences(build(STORY))
    assert len(sentences) == len(STORY), [s.text for s in sentences]
    assert all(s.duration <= sem.MAX_SENTENCE_SECONDS + 0.1 for s in sentences)


def test_splits_on_long_pause():
    tr = build([("משפט ראשון כאן", 2.0), ("משפט שני כאן", 2.0)], gap=1.5)
    sentences = sem.split_sentences(tr)
    assert len(sentences) == 2
    assert sentences[1].pause_before > 1.0


def test_no_transcript_is_empty_not_guessed():
    a = sem.analyze(None, None, settings=AppSettings(), use_llm=False)
    assert a.sentences == [] and a.beats == []
    assert not a.has_transcript
    assert "אין תמלול" in a.note


# ==========================================================================
# מילות מילוי והיסוסים
# ==========================================================================
def test_finds_filler_words():
    a = sem.analyze(build(STORY), None, settings=AppSettings(), use_llm=False)
    kinds = {d.kind for d in a.disfluencies}
    assert "filler_word" in kinds, [d.to_dict() for d in a.disfluencies]
    fillers = [d for d in a.disfluencies if d.kind == "filler_word"]
    assert any("אה" in d.text or "אמ" in d.text for d in fillers)


def test_finds_false_start_across_sentence_boundary():
    """
    „ואז… ואז" נשבר לשני משפטים בדיוק בגלל ההיסוס. בלי בדיקת תפרים
    ההיסוסים הבולטים ביותר היו נעלמים.
    """
    a = sem.analyze(build(STORY), None, settings=AppSettings(), use_llm=False)
    starts = [d for d in a.disfluencies if d.kind == "false_start"]
    assert starts, [d.to_dict() for d in a.disfluencies]
    assert any("ואז" in d.text for d in starts)


def test_deliberate_repetition_is_not_a_false_start():
    """הדגשה מכוונת („לא. לא!") עם פער ארוך אינה היסוס."""
    tr = build([("לא", 0.6), ("לא זה לא נכון", 2.0)], gap=1.6)
    a = sem.analyze(tr, None, settings=AppSettings(), use_llm=False)
    assert not [d for d in a.disfluencies if d.kind == "false_start"]


def test_no_word_timings_means_no_word_level_cuts():
    """בלי תזמון ברמת מילה אי אפשר לחתוך מילה — ולא מנחשים."""
    segs = [Segment(start=0.0, end=3.0, text="אה אמ אז בעצם", words=[])]
    tr = TranscriptResult(segments=segs, language="he", duration=3.0)
    a = sem.analyze(tr, None, settings=AppSettings(), use_llm=False)
    assert a.sentences
    assert a.disfluencies == []


def test_adjacent_fillers_merge():
    tr = build([("אה אמ אז נתחיל", 2.0)])
    a = sem.analyze(tr, None, settings=AppSettings(), use_llm=False)
    fillers = [d for d in a.disfluencies if d.kind == "filler_word"]
    # „אה" ו„אמ" צמודות => קטע אחד, לא שניים
    assert len(fillers) == 1, [d.to_dict() for d in fillers]


# ==========================================================================
# סיווג תפקידים
# ==========================================================================
def test_story_structure_is_recognised():
    a = sem.analyze(build(STORY), None, settings=AppSettings(), use_llm=False)
    roles = [s.role for s in a.sentences]

    assert roles[0] == "hook", roles
    assert roles[-1] == "cta", roles
    assert "emotional_peak" in roles, roles
    # השיא הרגשי הוא המשפט עם „הכי נמוך בחיים שלי"
    peak = next(s for s in a.sentences if s.role == "emotional_peak")
    assert "נמוך" in peak.text, peak.text
    # המשפט עם מילות המילוי אינו נושא תפקיד תוכן
    assert a.sentences[1].role == "filler", a.sentences[1].to_dict()


def test_only_one_hook_and_one_peak():
    a = sem.analyze(build(STORY), None, settings=AppSettings(), use_llm=False)
    roles = [s.role for s in a.sentences]
    assert roles.count("hook") == 1, roles
    assert roles.count("emotional_peak") <= 1, roles


def test_cta_only_near_the_end():
    """ניסוח של קריאה לפעולה בתחילת הסרטון אינו CTA."""
    lines = [("עקבו אחריי לעוד סיפורים", 2.5)] + STORY[:5]
    a = sem.analyze(build(lines), None, settings=AppSettings(), use_llm=False)
    assert a.sentences[0].role != "cta", a.sentences[0].to_dict()


def test_every_sentence_has_reason_and_confidence():
    a = sem.analyze(build(STORY), None, settings=AppSettings(), use_llm=False)
    for s in a.sentences:
        assert s.reason, s.to_dict()
        assert 0.0 < s.confidence <= 1.0, s.to_dict()
        assert s.role in {r.value for r in sem.BeatRole}
        assert s.role_source == "heuristic"


def test_reason_does_not_claim_energy_without_audio():
    """
    בלי פס קול אסור לכתוב „עוצמה גבוהה (0%)" — זה נשמע כמו מדידה
    שבוצעה, ולא כך.
    """
    a = sem.analyze(build(STORY), None, settings=AppSettings(), use_llm=False)
    for s in a.sentences:
        assert "(0%)" not in s.reason, s.reason


def test_beats_group_consecutive_same_role():
    a = sem.analyze(build(STORY), None, settings=AppSettings(), use_llm=False)
    assert a.beats
    assert len(a.beats) <= len(a.sentences)
    for b in a.beats:
        assert b.start <= b.end
        assert all(s.role == b.role for s in b.sentences)
    # הביטים מכסים את הסרטון בסדר עולה
    for x, y in zip(a.beats, a.beats[1:]):
        assert y.start >= x.start


def test_role_at_finds_the_beat():
    a = sem.analyze(build(STORY), None, settings=AppSettings(), use_llm=False)
    b = a.role_at(1.0)
    assert b is not None and b.role == "hook"
    assert a.role_at(9999.0) is None


def test_serialisation_is_json_safe():
    import json

    a = sem.analyze(build(STORY), None, settings=AppSettings(), use_llm=False)
    blob = json.dumps(a.to_dict(), ensure_ascii=False)
    assert "hook" in blob and "role_label" in blob
    back = json.loads(blob)
    assert len(back["sentences"]) == len(a.sentences)
    assert back["filler_seconds"] > 0


# ==========================================================================
def _run_all() -> int:
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    passed, failed = 0, []
    for name, fn in fns:
        try:
            fn()
            print(f"  \033[92m✓\033[0m {name}")
            passed += 1
        except AssertionError as exc:
            print(f"  \033[91m✗\033[0m {name}: {exc}")
            failed.append(name)
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"  \033[91m✗\033[0m {name}: {type(exc).__name__}: {exc}")
            failed.append(name)
    print(f"\n{passed}/{len(fns)} בדיקות סמנטיקה עברו")
    if failed:
        print("נכשלו: " + ", ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
