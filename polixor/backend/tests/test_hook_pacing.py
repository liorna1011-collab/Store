"""
בדיקות ל-Hook Engine ול-Pacing Engine.

הרצה:  python3 tests/test_hook_pacing.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxhook_"))

from polixor.config import PATHS, AppSettings                    # noqa: E402

PATHS.ensure()

from polixor.services import hook_engine as hk                   # noqa: E402
from polixor.services import pacing_engine as pe                 # noqa: E402
from polixor.services import semantics as sem                    # noqa: E402
from polixor.services.transcribe import (                        # noqa: E402
    Segment, TranscriptResult, Word,
)


def build(lines: list[tuple[str, float]], gap: float = 0.35
          ) -> TranscriptResult:
    segs, t = [], 0.0
    for text, dur in lines:
        toks = text.split()
        per = dur / max(1, len(toks))
        words, wt = [], t
        for tok in toks:
            words.append(Word(start=round(wt, 3),
                              end=round(wt + per * 0.92, 3), text=tok))
            wt += per
        segs.append(Segment(start=t, end=t + dur, text=text, words=words,
                            language="he"))
        t += dur + gap
    return TranscriptResult(segments=segs, language="he", duration=t)


def analyse(lines):
    return sem.analyze(build(lines), None, settings=AppSettings(),
                       use_llm=False)


WEAK_OPENING = [
    ("אז אה אוקיי", 1.4),
    ("אמ בדיקה אחד שתיים", 1.6),
    ("היום אני רוצה לדבר על משהו", 2.6),
    ("שלוש טעויות שעשיתי בשנה הראשונה שלי", 3.2),
    ("הראשונה הייתה שלא ביקשתי עזרה", 3.0),
    ("וזה עלה לי בחצי שנה של עבודה מיותרת", 3.4),
]

STRONG_OPENING = [
    ("שלוש טעויות שעשיתי בשנה הראשונה שלי", 3.2),
    ("הראשונה הייתה שלא ביקשתי עזרה", 3.0),
    ("וזה עלה לי בחצי שנה", 2.4),
]


# ==========================================================================
# ניקוד הוו
# ==========================================================================
def test_hebrew_number_words_count_as_specific():
    """
    רגרסיה: „שלוש טעויות" נוקד 18% כי חיפשנו רק ספרות. בעברית מספר
    במילים הוא הצורה הנפוצה, ובלעדיו כל הבטחת רשימה נראית כללית.
    """
    a = analyse(STRONG_OPENING)
    h = hk.analyze_hook(a.sentences)
    assert h.strength >= 0.45, h.to_dict()
    assert h.breakdown.specificity > 0.3, h.breakdown.to_dict()
    assert h.breakdown.marker > 0.3, h.breakdown.to_dict()


def test_strong_hook_gets_no_move_suggestion():
    a = analyse(STRONG_OPENING)
    h = hk.analyze_hook(a.sentences)
    assert h.verdict in ("ok", "strong"), h.to_dict()
    assert h.suggested_move is None


def test_weak_opening_is_trimmed_and_measured_after_the_trim():
    """
    הוו נמדד אחרי הגיזום, לא לפניו: אחרת מודדים את „אז… אה… אוקיי"
    ומקבלים אפס, במקום למדוד את מה שהצופה באמת יראה.
    """
    a = analyse(WEAK_OPENING)
    h = hk.analyze_hook(a.sentences)
    assert h.has_trim, h.to_dict()
    assert h.trim_start > 2.5, h.trim_start
    # הוו שנמדד אינו המשפט שנגזם
    assert "אוקיי" not in h.hook_text
    assert h.hook_start >= h.trim_start - 0.6


def test_move_suggestion_requires_approval_and_explains_itself():
    a = analyse(WEAK_OPENING)
    h = hk.analyze_hook(a.sentences)
    assert h.suggested_move is not None, h.to_dict()
    m = h.suggested_move
    assert "טעויות" in m.text
    assert m.gain >= hk.MIN_IMPROVEMENT
    assert m.reason and "אישור" in m.reason
    assert m.to_dict()["requires_approval"] is True


def test_move_is_never_applied_automatically():
    """המנוע מחזיר הצעה בלבד — הוא לא נוגע במשפטים."""
    a = analyse(WEAK_OPENING)
    before = [(s.start, s.end, s.text) for s in a.sentences]
    hk.analyze_hook(a.sentences)
    after = [(s.start, s.end, s.text) for s in a.sentences]
    assert before == after


def test_trim_never_cuts_into_the_hook():
    a = analyse(STRONG_OPENING)
    h = hk.analyze_hook(a.sentences)
    # אין מה לגזום כאן, והמנוע לא ממציא גיזום
    assert h.trim_start <= h.hook_start + 0.05


def test_no_transcript_is_reported_not_scored():
    h = hk.analyze_hook([])
    assert h.verdict == "missing"
    assert h.strength == 0.0
    assert "תמלול" in h.reason


def test_disable_move_suggestions():
    a = analyse(WEAK_OPENING)
    h = hk.analyze_hook(a.sentences, allow_move=False)
    assert h.suggested_move is None


def test_question_hook_scores_curiosity():
    a = analyse([("למה אף אחד לא מדבר על זה?", 2.6),
                 ("הסיבה פשוטה יותר ממה שנדמה", 2.8)])
    h = hk.analyze_hook(a.sentences)
    assert h.breakdown.curiosity > 0.4, h.breakdown.to_dict()


# ==========================================================================
# קצב
# ==========================================================================
def test_styles_produce_different_pacing():
    a = analyse(WEAK_OPENING)
    total = a.sentences[-1].end
    fast = pe.plan_pacing(a.beats, profile="viral_short", total_duration=total)
    slow = pe.plan_pacing(a.beats, profile="cinematic_story",
                          total_duration=total)
    assert fast.total_visual_changes > slow.total_visual_changes, (
        fast.total_visual_changes, slow.total_visual_changes)
    assert fast.profile.visual_interval[1] < slow.profile.visual_interval[0]


def test_emotional_peak_gets_more_air_than_hook():
    """גם בסגנון מהיר, השיא הרגשי מקבל מרווח גדול יותר מהפתיחה."""
    assert pe.ROLE_TEMPO["emotional_peak"] > pe.ROLE_TEMPO["hook"]
    beats = [
        sem.NarrativeBeat(0.0, 6.0, "hook", 0.9),
        sem.NarrativeBeat(6.0, 12.0, "emotional_peak", 0.9),
    ]
    plan = pe.plan_pacing(beats, profile="viral_short", total_duration=12.0)
    hook_sec = plan.at(1.0)
    peak_sec = plan.at(8.0)
    assert hook_sec and peak_sec
    assert peak_sec.interval > hook_sec.interval
    assert peak_sec.visual_changes < hook_sec.visual_changes


def test_no_broll_over_the_speaker_at_emotional_moments():
    beats = [sem.NarrativeBeat(0.0, 30.0, "emotional_peak", 0.9)]
    plan = pe.plan_pacing(beats, profile="educational", total_duration=30.0)
    sec = plan.at(10.0)
    assert sec is not None
    assert sec.allow_broll is False and sec.broll_slots == 0
    assert "הדובר" in sec.reason


def test_hook_never_gets_broll():
    beats = [sem.NarrativeBeat(0.0, 20.0, "hook", 0.9)]
    plan = pe.plan_pacing(beats, profile="product", total_duration=20.0)
    assert plan.total_broll_slots == 0


def test_video_is_never_completely_static():
    """סגנון איטי על סרטון קצר עדיין מקבל שינוי אחד, עם הסבר."""
    beats = [
        sem.NarrativeBeat(0.0, 5.0, "setup", 0.8),
        sem.NarrativeBeat(5.0, 11.0, "emotional_peak", 0.8),
    ]
    plan = pe.plan_pacing(beats, profile="cinematic_story",
                          total_duration=11.0)
    assert plan.total_visual_changes >= 1, plan.to_dict()
    assert any("סטטי" in n for n in plan.notes), plan.notes
    # השינוי ניתן לקטע החזק ביותר
    peak = plan.at(7.0)
    assert peak is not None and peak.visual_changes == 1


def test_very_short_video_stays_a_single_take():
    beats = [sem.NarrativeBeat(0.0, 2.0, "hook", 0.9)]
    plan = pe.plan_pacing(beats, profile="cinematic_story", total_duration=2.0)
    assert plan.total_visual_changes == 0
    assert any("קצר" in n for n in plan.notes), plan.notes


def test_no_transcript_gives_uniform_pacing_with_a_note():
    plan = pe.plan_pacing([], profile="clean_creator", total_duration=40.0)
    assert len(plan.sections) == 1
    assert plan.sections[0].role == "unknown"
    assert plan.total_broll_slots == 0
    assert any("תמלול" in n for n in plan.notes), plan.notes


def test_legacy_style_names_still_resolve():
    for old, expected in (("hype", "viral_short"), ("clean", "clean_creator"),
                          ("dynamic", "podcast_clip"), ("raw", "clean_creator")):
        assert pe.get_profile(old).name == expected
    # שם לא מוכר נופל לברירת המחדל ולא מתפוצץ
    assert pe.get_profile("nonsense").name == pe.DEFAULT_PROFILE


def test_every_section_explains_itself():
    a = analyse(WEAK_OPENING)
    plan = pe.plan_pacing(a.beats, profile="educational",
                          total_duration=a.sentences[-1].end)
    for s in plan.sections:
        assert s.reason, s.to_dict()
        assert s.interval > 0
        assert s.zoom_changes <= s.visual_changes


def test_catalog_is_serialisable():
    import json

    blob = json.dumps(pe.profile_catalog(), ensure_ascii=False)
    assert "viral_short" in blob and "cinematic_story" in blob
    assert len(pe.profile_catalog()) == len(pe.PROFILES)


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
    print(f"\n{passed}/{len(fns)} בדיקות hook + pacing עברו")
    if failed:
        print("נכשלו: " + ", ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
