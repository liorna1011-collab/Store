"""
בדיקות למנוע החומר הנלווה.

השאלה שנבדקת כאן אינה „האם נבחרה תמונה" אלא **האם ההכרעה נכונה**:
משפט מופשט לא אמור לקבל תמונה, שיא רגשי לא אמור לאבד את הפנים של
הדובר, וחומר שכבר קיים עדיף על יצירה חדשה.

הרצה:  python3 tests/test_broll.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxbr_"))

from polixor.config import PATHS, AppSettings                    # noqa: E402

PATHS.ensure()

from polixor.services import broll_engine as be                  # noqa: E402
from polixor.services import pacing_engine as pe                 # noqa: E402
from polixor.services import semantics as sem                    # noqa: E402
from polixor.services.transcribe import (                        # noqa: E402
    Segment, TranscriptResult, Word,
)


def build(lines: list[tuple[str, float]], gap: float = 0.4
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


SCRIPT = [
    ("שלוש טעויות שעשיתי בשנה הראשונה שלי", 3.2),
    ("נסעתי לבד בלילה בכביש ריק לכיוון הים", 3.6),
    ("אני חושב שזה בעצם הרעיון הכי חשוב", 3.4),
    ("המכונית נעצרה ופתחתי את הדלת בגשם", 3.5),
    ("זה היה הרגע הכי קשה שעברתי בחיים", 3.3),
    ("בסרטון הזה אני אסביר לכם בדיוק איך", 3.1),
    ("בניתי את המשרד הראשון שלי בחדר קטן", 3.4),
    ("אם זה עזר לכם תעקבו לעוד טיפים", 2.9),
]


def analyse(lines=None):
    tr = build(lines or SCRIPT)
    return sem.analyze(tr, None, settings=AppSettings(), use_llm=False), tr


# ==========================================================================
# מוחשיות
# ==========================================================================
def test_visual_sentence_scores_higher_than_an_opinion():
    visual, _ = be.concreteness("נסעתי לבד בלילה בכביש ריק לכיוון הים")
    opinion, _ = be.concreteness("אני חושב שזה בעצם הרעיון הכי חשוב")
    assert visual > be.CONCRETE_ENOUGH, visual
    assert opinion < be.CONCRETE_ENOUGH, opinion
    assert visual > opinion + 0.3


def test_categories_are_identified():
    cases = {
        "המכונית נעצרה ופתחתי את הדלת": ("object", "action"),
        "הגעתי לחוף בבוקר": ("place", "nature", "action"),
        "ירד גשם חזק כל הלילה": ("nature",),
    }
    for text, allowed in cases.items():
        score, cat = be.concreteness(text)
        assert cat in allowed, (text, cat, score)
        assert score > 0.0, text


def test_talking_about_the_video_itself_is_not_visual():
    score, cat = be.concreteness("בסרטון הזה אני אסביר לכם בדיוק איך")
    assert cat == "meta", (score, cat)
    assert score < be.CONCRETE_ENOUGH


def test_word_boundaries_are_respected():
    """
    רגרסיה: התאמת תת-מחרוזת סיווגה „אני רוצה לבדוק את זה" כרגע
    רגשי נמוך, כי „לבד" נמצא בתוך „לבדוק" — והפיקה לו תמונה
    חשוכה ובודדה.
    """
    score, cat = be.concreteness("אני רוצה לבדוק את זה")
    assert cat != "low", (score, cat)
    assert score < be.CONCRETE_ENOUGH, score


def test_numbers_with_units_add_concreteness():
    plain, _ = be.concreteness("נסעתי הרבה")
    exact, _ = be.concreteness("נסעתי 300 קמ")
    assert exact > plain, (plain, exact)


def test_empty_text_is_zero():
    assert be.concreteness("") == (0.0, "")


# ==========================================================================
# ההכרעה
# ==========================================================================
def plan_for(lines=None, budget=4, assets=None, pacing=None):
    a, tr = analyse(lines)
    return be.plan_broll(a, pacing, assets=assets, budget=budget), a


def test_direct_address_always_keeps_the_speaker_on_screen():
    """פתיח וקריאה לפעולה הם פנייה ישירה — הפנים תמיד מנצחות."""
    plan, a = plan_for()
    direct = [d for d in plan.decisions if d.role in be.FACE_ALWAYS]
    assert direct, [d.role for d in plan.decisions]
    for d in direct:
        assert d.verdict == "talking_head", d.to_dict()
        assert "פנים" in d.reason or "ישירה" in d.reason, d.reason


def test_emotional_line_without_a_scene_keeps_the_face():
    """
    רגע רגשי שנישא בהבעה ובקול — בלי תיאור שאפשר לצלם — נשאר
    על הדובר.
    """
    plan, _ = plan_for([
        ("שלוש טעויות שעשיתי בשנה הראשונה", 3.0),
        ("זה היה הרגע הכי קשה שעברתי בחיים", 3.4),
        ("ואז הבנתי מה באמת חשוב לי", 3.0),
    ], budget=4)
    hard = [d for d in plan.decisions if "הכי קשה" in d.text]
    assert hard, [d.text for d in plan.decisions]
    assert hard[0].verdict == "talking_head", hard[0].to_dict()
    assert hard[0].concreteness < be.HIGH_CONCRETE


def test_emotional_line_with_a_concrete_scene_can_get_broll():
    """
    הדוגמה שבמפרט עצמו: „נסעתי לבד בלילה" הוא רגע רגשי **וגם**
    סצנה שאפשר לצלם. שם החומר הנלווה משרת את הרגש.
    """
    plan, _ = plan_for([
        ("שלוש טעויות שעשיתי בשנה הראשונה", 3.0),
        ("נסעתי לבד בלילה בכביש ריק לכיוון הים", 3.6),
        ("ואז הבנתי מה באמת חשוב לי", 3.0),
    ], budget=4)
    ride = [d for d in plan.decisions if "כביש ריק" in d.text]
    assert ride, [d.text for d in plan.decisions]
    assert ride[0].concreteness >= be.HIGH_CONCRETE, ride[0].to_dict()
    assert ride[0].verdict == "broll", ride[0].to_dict()


def test_abstract_sentence_gets_no_image():
    plan, _ = plan_for()
    abstract = [d for d in plan.decisions if "אני חושב" in d.text]
    assert abstract, [d.text for d in plan.decisions]
    assert abstract[0].verdict == "talking_head"
    assert "גנרית" in abstract[0].reason


def test_visual_sentence_gets_broll():
    plan, _ = plan_for()
    visual = [d for d in plan.decisions if "המכונית" in d.text]
    assert visual, [d.text for d in plan.decisions]
    assert visual[0].verdict == "broll", visual[0].to_dict()
    assert visual[0].prompt or visual[0].asset_id


def test_not_every_sentence_gets_an_image():
    """הדרישה: „לא ליצור תמונה לכל משפט"."""
    plan, _ = plan_for(budget=99)
    assert plan.inserts, "אף הכנסה לא נבחרה"
    assert len(plan.inserts) < len(plan.decisions) / 2, (
        len(plan.inserts), len(plan.decisions))


def test_every_sentence_gets_an_explicit_verdict_and_reason():
    plan, a = plan_for()
    assert len(plan.decisions) == len(a.sentences)
    for d in plan.decisions:
        assert d.verdict in ("broll", "talking_head"), d.to_dict()
        assert d.reason, d.to_dict()
        assert 0.0 <= d.confidence <= 1.0
        assert 0.0 <= d.concreteness <= 1.0


def test_budget_is_respected():
    plan, _ = plan_for(budget=1)
    assert len(plan.inserts) <= 1, [d.text for d in plan.inserts]
    rejected = [d for d in plan.decisions
                if d.verdict == "talking_head" and "מכסה" in d.reason]
    assert rejected or len(plan.inserts) == 1


def test_zero_budget_means_no_inserts_but_says_why():
    plan, _ = plan_for(budget=0)
    assert plan.inserts == []
    assert any("מקצה" in n or "סגנון" in n for n in plan.notes), plan.notes


def test_inserts_keep_a_minimum_gap():
    lines = [(f"נסעתי לכביש ולים ופתחתי את הדלת מספר {i}", 3.0)
             for i in range(8)]
    plan, _ = plan_for(lines, budget=8)
    starts = sorted(d.start for d in plan.inserts)
    for a, b in zip(starts, starts[1:]):
        assert b - a >= be.MIN_GAP_SECONDS - 1e-6, (a, b)


def test_short_sentence_is_skipped():
    """משפט של שנייה אחת לא מחזיק הכנסה — היא תיראה כמו הבהוב."""
    plan, _ = plan_for([
        ("שלוש טעויות שעשיתי בשנה הראשונה", 3.0),
        ("ואז פתחתי את הדלת של המכונית בגשם", 3.4),
        ("נסעתי לים בכביש", 1.0),
        ("וזה מה שלמדתי מהסיפור הזה", 3.0),
    ], budget=6)
    short = [d for d in plan.decisions if d.end - d.start < be.MIN_SENTENCE_SECONDS]
    assert short, [(d.text, round(d.end - d.start, 2)) for d in plan.decisions]
    assert short[0].verdict == "talking_head", short[0].to_dict()
    assert "קצר" in short[0].reason, short[0].reason


def test_no_transcript_says_so():
    plan = be.plan_broll(sem.SemanticAnalysis(), None, budget=3)
    assert plan.decisions == []
    assert any("תמלול" in n for n in plan.notes), plan.notes


def test_plan_is_json_serialisable():
    plan, _ = plan_for()
    blob = json.dumps(plan.to_dict(), ensure_ascii=False)
    assert "decisions" in blob and "verdict" in blob


# ==========================================================================
# העדפת חומר קיים
# ==========================================================================
def test_existing_media_is_preferred_over_generation():
    """הדרישה: „יש להעדיף Media קיים לפני generation"."""
    assets = [be.MediaAsset(id="a1", description="כביש לילה ריק",
                            tags=["לילה", "כביש"])]
    plan, _ = plan_for(assets=assets)
    road = [d for d in plan.inserts if "כביש" in d.text or "המכונית" in d.text]
    assert road, [d.text for d in plan.inserts]
    assert road[0].source == "existing", road[0].to_dict()
    assert road[0].asset_id == "a1"
    assert not road[0].prompt, "לא אמור להיווצר פרומפט כשיש חומר קיים"


def test_unrelated_asset_is_not_reused():
    assets = [be.MediaAsset(id="a9", description="עוגת יום הולדת ורודה",
                            tags=["מסיבה"])]
    plan, _ = plan_for(assets=assets)
    for d in plan.inserts:
        assert d.source == "generate", d.to_dict()
        assert d.asset_id == ""


def test_match_requires_more_than_one_stray_word():
    asset = be.MediaAsset(id="x", description="ים")
    got, score = be.match_existing(
        "נסעתי לבד בלילה בכביש ריק לכיוון הים והמשכתי הלאה", [asset])
    assert got is None, (got, score)


def test_matching_respects_word_boundaries():
    asset = be.MediaAsset(id="x", description="לבדוק מכשירים במעבדה")
    got, _ = be.match_existing("הרגשתי לבד", [asset])
    assert got is None


# ==========================================================================
# פרומפט
# ==========================================================================
def test_prompt_is_english_and_describes_a_shot():
    p = be.build_prompt("נסעתי לבד בלילה בכביש ריק", "place", aspect="9:16")
    assert "vertical" in p
    assert "no text" in p
    assert not any("֐" <= ch <= "׿" for ch in p), p


def test_prompt_avoids_real_people_and_brands():
    p = be.build_prompt("הגעתי לחוף", "place")
    assert "no recognisable faces" in p
    assert "no watermark" in p


def test_aspect_changes_the_prompt():
    v = be.build_prompt("הגעתי לחוף", "place", aspect="9:16")
    h = be.build_prompt("הגעתי לחוף", "place", aspect="16:9")
    assert v != h and "horizontal" in h


# ==========================================================================
# שילוב עם תכנית הקצב
# ==========================================================================
def test_pacing_blocks_broll_except_for_a_strong_visual():
    """
    קטע שהקצב חוסם נשאר חסום — אלא אם המשפט מצייר סצנה מוחשית
    במיוחד, וזה חריג מתועד ולא עקיפה שקטה.
    """
    a, tr = analyse()
    plan_pace = pe.plan_pacing(a.beats, profile="viral_short",
                               total_duration=tr.duration)
    plan = be.plan_broll(a, plan_pace, budget=None)
    assert plan.budget == plan_pace.total_broll_slots
    for d in plan.inserts:
        section = plan_pace.at(d.start)
        if section is not None and not section.allow_broll:
            assert d.concreteness >= be.HIGH_CONCRETE, d.to_dict()


def test_hebrew_prefix_does_not_block_asset_matching():
    """„בכביש" ו„כביש" הן אותה מילה לצורך התאמה לנכס קיים."""
    asset = be.MediaAsset(id="a1", description="כביש לילה ריק")
    got, score = be.match_existing("נסעתי בכביש ריק בלילה", [asset])
    assert got is not None and got.id == "a1", (got, score)


def test_budget_comes_from_pacing_when_not_given():
    a, tr = analyse()
    plan_pace = pe.plan_pacing(a.beats, profile="educational",
                               total_duration=tr.duration)
    plan = be.plan_broll(a, plan_pace)
    assert plan.budget == plan_pace.total_broll_slots


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
    print(f"\n{passed}/{len(fns)} בדיקות חומר נלווה עברו")
    if failed:
        print("נכשלו: " + ", ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
