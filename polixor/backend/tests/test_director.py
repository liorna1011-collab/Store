"""
בדיקות ל-AI Video Director: התכנית, ההצדקות ומגבלת הזום.

הרצה:  python3 tests/test_director.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxdir_"))

from polixor.config import PATHS, AppSettings                    # noqa: E402

PATHS.ensure()

import numpy as np                                                # noqa: E402

from polixor.services.audio import AudioFeatures                 # noqa: E402
from polixor.services import semantics as sem                    # noqa: E402
from polixor.services import video_director as vd                # noqa: E402
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


STORY = [
    ("אז אה אוקיי", 1.4),
    ("אמ בדיקה אחד שתיים", 1.6),
    ("שלוש טעויות שעשיתי בשנה הראשונה שלי", 3.2),
    ("הראשונה הייתה שלא ביקשתי עזרה", 3.0),
    ("ואז ואז הגעתי למקום הכי נמוך בחיים שלי", 3.4),
    ("הסיבה שזה קרה היא שלא הקשבתי לעצמי", 3.6),
    ("נסעתי לים בשלוש לפנות בוקר בלי להגיד לאף אחד", 4.0),
    ("היום אני יודע שזה היה הדבר הכי טוב שקרה לי", 3.8),
    ("עקבו אחריי לעוד סיפורים כאלה", 2.4),
]


def make_plan(lines=None, *, style="viral_short", width=3840, height=2160,
              out_width=1080, out_height=1920) -> vd.VideoEditPlan:
    tr = build(lines or STORY)
    a = sem.analyze(tr, None, settings=AppSettings(), use_llm=False)
    return vd.direct(semantics=a, transcript=tr, source_start=0.0,
                     source_end=tr.duration, width=width, height=height,
                     out_width=out_width, out_height=out_height,
                     fps=30.0, style=style)


def make_audio(duration: float, silent_spans: list[tuple[float, float]],
               *, hop: float = 0.1) -> AudioFeatures:
    """
    AudioFeatures עם מסכת שקט ידועה מראש.

    הבמאי מזהה אוויר מת מהמסכה הזו בלבד, ולכן בדיקה עם שקט מוגדר
    היא הדרך לוודא שהחיתוך באמת קורה — ולא רק שהקוד לא מתפוצץ.
    """
    n = max(1, int(duration / hop))
    times = np.arange(n, dtype=np.float32) * hop
    silence = np.zeros(n, dtype=bool)
    for a, b in silent_spans:
        silence[int(a / hop):int(b / hop)] = True
    rms = np.where(silence, -55.0, -20.0).astype(np.float32)
    energy = np.where(silence, 0.02, 0.8).astype(np.float32)
    return AudioFeatures(
        sample_rate=16000, hop=hop, duration=duration, times=times,
        rms_db=rms, energy=energy,
        flux=np.zeros(n, dtype=np.float32),
        centroid=np.zeros(n, dtype=np.float32),
        zcr=np.zeros(n, dtype=np.float32),
        jump=np.zeros(n, dtype=np.float32),
        silence=silence,
        laughter=np.zeros(n, dtype=np.float32),
        noise_floor_db=-55.0, peak_db=-8.0,
        speech_ratio=float(1.0 - silence.mean()))


# ==========================================================================
# אוויר מת
# ==========================================================================
def test_dead_air_is_actually_cut():
    """
    החיתוך הכי שימושי במוצר. בלי אודיו הבמאי לא מזהה שקט כלל,
    ולכן הבדיקה חייבת לספק מסכת שקט אמיתית.
    """
    tr = build(STORY)
    a = sem.analyze(tr, None, settings=AppSettings(), use_llm=False)
    audio = make_audio(tr.duration, [(6.0, 9.5)])
    plan = vd.direct(semantics=a, audio=audio, transcript=tr,
                     source_start=0.0, source_end=tr.duration,
                     width=1920, height=1080, out_width=1080, out_height=1920,
                     fps=30.0, style="clean_creator")
    dead = [d for d in plan.cuts
            if d.enabled and d.params.get("kind") == "dead_air"]
    assert dead, [d.to_dict() for d in plan.cuts]
    cut = max(dead, key=lambda d: d.duration)
    assert cut.start >= 5.8 and cut.end <= 9.7, cut.to_dict()
    assert cut.duration > 2.0, cut.to_dict()
    assert "אוויר מת" in cut.reason


def test_dead_air_shortens_the_rendered_clip():
    from polixor.services import director_bridge as br

    tr = build(STORY)
    a = sem.analyze(tr, None, settings=AppSettings(), use_llm=False)
    audio = make_audio(tr.duration, [(6.0, 9.5)])
    plan = vd.direct(semantics=a, audio=audio, transcript=tr,
                     source_start=0.0, source_end=tr.duration,
                     width=1920, height=1080, out_width=1080, out_height=1920,
                     fps=30.0, style="clean_creator")
    ep = br.to_edit_plan(plan)
    assert ep.removed_seconds > 2.0, ep.stats()
    assert ep.out_duration < tr.duration - 2.0


def test_silence_without_audio_is_not_invented():
    """בלי פס קול אין זיהוי שקט — והתכנית אומרת זאת במקום לנחש."""
    tr = build(STORY)
    a = sem.analyze(tr, None, settings=AppSettings(), use_llm=False)
    plan = vd.direct(semantics=a, audio=None, transcript=tr,
                     source_start=0.0, source_end=tr.duration,
                     width=1920, height=1080, out_width=1080, out_height=1920,
                     fps=30.0, style="clean_creator")
    assert not [d for d in plan.cuts
                if d.params.get("kind") == "dead_air"]


# ==========================================================================
# מגבלת הזום
# ==========================================================================
def test_zoom_limit_scales_with_source_resolution():
    """
    4K סובל זום; 720p לחיתוך אנכי כבר מתוח מדי ולא מקבל זום בכלל.
    זו הדרישה „אסור להגיע לתמונה רכה רק בשביל Zoom".
    """
    z4k = vd.safe_zoom_limit(3840, 2160, 1080, 1920)
    z1080 = vd.safe_zoom_limit(1920, 1080, 1080, 1920)
    z720 = vd.safe_zoom_limit(1280, 720, 1080, 1920)

    assert z4k > z1080 > z720, (z4k, z1080, z720)
    assert z720 == 1.0, z720
    assert z4k <= vd.ABSOLUTE_MAX_ZOOM


def test_total_upscale_never_exceeds_the_cap():
    for sw, sh in ((1920, 1080), (3840, 2160), (2560, 1440)):
        base = vd.baseline_upscale(sw, sh, 1080, 1920)
        z = vd.safe_zoom_limit(sw, sh, 1080, 1920)
        total = base * z
        # מותר לחרוג רק כשהבסיס לבדו כבר מעל הסף, ואז הזום הוא 1.0
        assert total <= vd.MAX_TOTAL_UPSCALE + 1e-6 or z == 1.0, (
            sw, sh, base, z, total)


def test_no_zoom_when_source_too_small():
    plan = make_plan(width=1280, height=720)
    zooms = [z for z in plan.zoom_events
             if z.action != vd.Action.HOLD_FRAME.value]
    assert zooms == [], [z.to_dict() for z in zooms]
    assert any("מותח" in n for n in plan.notes), plan.notes


def test_every_zoom_respects_the_limit():
    plan = make_plan()
    limit = plan.export_settings["safe_zoom_limit"]
    for z in plan.zoom_events:
        if "zoom" in z.params:
            assert z.params["zoom"] <= limit + 1e-6, z.to_dict()


def test_degenerate_sizes_do_not_crash():
    assert vd.safe_zoom_limit(0, 0, 0, 0) == 1.0
    assert vd.baseline_upscale(0, 100, 100, 100) == 1.0


# ==========================================================================
# מבנה ההחלטות
# ==========================================================================
def test_every_decision_has_the_required_fields():
    """הדרישה: start, end, reason, confidence, action, priority לכל החלטה."""
    plan = make_plan()
    assert plan.all_decisions
    for d in plan.all_decisions:
        row = d.to_dict()
        for key in ("start", "end", "action", "reason", "confidence",
                    "priority"):
            assert key in row, (key, row)
        assert row["reason"], row
        assert 0.0 <= row["confidence"] <= 1.0, row
        assert row["priority"] in (1, 2, 3), row
        assert row["end"] >= row["start"], row
        assert row["id"], row


def test_plan_is_json_serialisable():
    import json

    plan = make_plan()
    blob = json.dumps(plan.to_dict(), ensure_ascii=False)
    back = json.loads(blob)
    for key in ("hook", "beats", "cuts", "zoom_events", "captions", "broll",
                "generated_visuals", "sound_events", "music", "transitions",
                "color", "ending", "export_settings"):
        assert key in back, key
    assert back["summary"]["cuts"] >= 1


def test_unimplemented_categories_are_declared_not_faked():
    """
    מוזיקה, סאונד ומעברים אינם מתוכננים בשלב הזה — והתכנית אומרת
    זאת במפורש במקום להחזיר רשימות ריקות שנראות כמו החלטה.
    """
    plan = make_plan()
    for key in ("music", "sound_events", "transitions", "color", "ending"):
        assert key in plan.unimplemented, plan.unimplemented
    assert plan.music == {} and plan.sound_events == []
    assert any("אינם מתוכננים" in n for n in plan.notes), plan.notes


# ==========================================================================
# חיתוכים
# ==========================================================================
def test_throat_clearing_is_trimmed_as_one_decision():
    """
    רגרסיה: הגיזום הפסיד במיון למילת מילוי בעלת ביטחון גבוה יותר,
    נדחה כחופף, וכל הפתיחה נשארה.
    """
    plan = make_plan()
    heads = [c for c in plan.cuts
             if c.action == vd.Action.TRIM_HEAD.value and c.enabled]
    assert len(heads) == 1, [c.to_dict() for c in plan.cuts]
    assert heads[0].start == 0.0
    assert heads[0].end > 2.5, heads[0].to_dict()
    # ולא נשארו חיתוכים כפולים בתוך האזור שנגזם
    inside = [c for c in plan.cuts
              if c.enabled and c is not heads[0] and c.end <= heads[0].end]
    assert not inside, [c.to_dict() for c in inside]


def test_false_start_is_cut():
    plan = make_plan()
    reasons = " ".join(c.reason for c in plan.cuts if c.enabled)
    assert "התחלה כושלת" in reasons, reasons


def test_removal_stays_under_the_style_budget():
    for style in ("cinematic_story", "clean_creator", "viral_short"):
        plan = make_plan(style=style)
        from polixor.services.pacing_engine import get_profile
        cap = get_profile(style).max_removed_ratio
        ratio = plan.removed_seconds / max(0.01, plan.source_duration)
        assert ratio <= cap + 0.02, (style, ratio, cap)


def test_disabled_cut_explains_why():
    """הסרה שנדחתה בגלל התקרה נשארת בתכנית עם ההסבר, ולא נעלמת."""
    plan = make_plan(style="cinematic_story")
    skipped = [c for c in plan.cuts if not c.enabled]
    for c in skipped:
        assert "מכסת ההסרה" in c.reason, c.to_dict()


def test_cuts_do_not_overlap():
    plan = make_plan()
    enabled = sorted(plan.enabled_cuts(), key=lambda d: d.start)
    for a, b in zip(enabled, enabled[1:]):
        assert b.start >= a.end - 0.03, (a.to_dict(), b.to_dict())


# ==========================================================================
# מעבר ל-timeline
# ==========================================================================
def test_segments_are_the_complement_of_the_cuts():
    plan = make_plan()
    tm = plan.timeline()
    assert tm.validate() == [], tm.validate()
    expected = plan.source_duration - plan.removed_seconds
    assert abs(tm.source_span - expected) < 0.05, (tm.source_span, expected)


def test_disabling_a_cut_restores_the_material():
    """
    התכנית מנוסחת כרשימת הסרות, ולכן ביטול החלטה פשוט מחזיר את
    החומר — בלי לבנות מחדש שום דבר.
    """
    plan = make_plan()
    before = plan.timeline().source_span
    target = next(c for c in plan.cuts if c.enabled)
    restored = target.duration
    target.enabled = False
    after = plan.timeline().source_span
    assert abs((after - before) - restored) < 0.05, (before, after, restored)


def test_no_decision_lands_on_removed_material():
    """
    רגרסיה: תוכנן זום בשנייה הראשונה של סרטון שהשנייה הראשונה שלו
    נגזמה — החלטה שלא תתבצע לעולם אבל מוצגת למשתמש.
    """
    plan = make_plan()
    removed = [(c.start, c.end) for c in plan.enabled_cuts()]
    for d in [*plan.zoom_events, *plan.captions, *plan.broll]:
        mid = (d.start + d.end) / 2.0
        assert not any(a - 0.02 <= mid <= b + 0.02 for a, b in removed), \
            d.to_dict()


# ==========================================================================
# הדגשות
# ==========================================================================
def test_emphasis_is_limited_and_spaced():
    plan = make_plan()
    minutes = plan.source_duration / 60.0
    assert len(plan.captions) <= vd.MAX_EMPHASIS_PER_MINUTE * minutes + 1
    starts = sorted(d.start for d in plan.captions)
    for a, b in zip(starts, starts[1:]):
        assert b - a >= vd.MIN_EMPHASIS_GAP - 0.01, (a, b)


def test_emphasis_picks_content_words_not_filler():
    plan = make_plan()
    words = [d.params["word"] for d in plan.captions]
    assert words, plan.to_dict()["summary"]
    for w in words:
        assert w.lower() not in vd._STOP_EMPHASIS, w
        assert len(w) >= 3, w
    # „אוקיי" מהפתיחה שנגזמה לא אמור להופיע
    assert "אוקיי" not in words, words


# ==========================================================================
# הצעות שדורשות אישור
# ==========================================================================
def test_broll_suggestions_are_never_auto_applied():
    plan = make_plan()
    for d in plan.broll:
        assert d.enabled is False, d.to_dict()
        assert d.requires_approval is True, d.to_dict()
        assert d.params.get("prompt"), d.to_dict()
    if plan.broll:
        assert any("לא נוצרה ולא" in n for n in plan.notes), plan.notes


def test_hook_move_requires_approval():
    weak = [("אז אה אוקיי", 1.4), ("אמ כן", 1.0),
            ("היום נדבר על משהו", 2.4),
            ("חמישה כללים ששינו לי את העסק", 3.0),
            ("הראשון הוא לא לענות למיילים בבוקר", 3.2)]
    plan = make_plan(weak)
    moves = [d for d in plan.pending
             if d.action == vd.Action.MOVE_HOOK.value]
    if moves:
        for m in moves:
            assert m.requires_approval and not m.enabled, m.to_dict()
            assert "אישור" in m.reason


# ==========================================================================
# בלי תמלול
# ==========================================================================
def test_works_without_transcript_and_says_so():
    empty = sem.SemanticAnalysis(note="אין תמלול")
    plan = vd.direct(semantics=empty, source_start=0.0, source_end=20.0,
                     width=1920, height=1080, out_width=1920, out_height=1080,
                     fps=30.0, style="clean_creator")
    assert plan.beats == []
    assert plan.captions == [] and plan.broll == []
    assert any("אין תמלול" in n for n in plan.notes), plan.notes
    # עדיין מחזיר תכנית שמישה
    assert plan.timeline().validate() == []


def test_style_changes_the_plan():
    fast = make_plan(style="viral_short")
    slow = make_plan(style="cinematic_story")
    fast_zooms = len([z for z in fast.zoom_events if "zoom" in z.params])
    slow_zooms = len([z for z in slow.zoom_events if "zoom" in z.params])
    assert fast_zooms >= slow_zooms, (fast_zooms, slow_zooms)
    assert fast.style != slow.style


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
    print(f"\n{passed}/{len(fns)} בדיקות Director עברו")
    if failed:
        print("נכשלו: " + ", ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
