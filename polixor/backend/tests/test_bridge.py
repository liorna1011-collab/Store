"""
בדיקות לגשר בין הבמאי למנוע הרינדור.

הגשר הוא הנקודה שבה „תכנית" הופכת ל„ביצוע". אם הוא טועה, כל
העבודה של הבמאי לא מגיעה לסרטון — או גרוע מכך, מגיעה מעוותת.

הרצה:  python3 tests/test_bridge.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("POLIXOR_DATA_DIR", tempfile.mkdtemp(prefix="pxbr_"))

from polixor.config import PATHS, AppSettings                    # noqa: E402

PATHS.ensure()

from polixor.services import director_bridge as br               # noqa: E402
from polixor.services import semantics as sem                    # noqa: E402
from polixor.services import video_director as vd                # noqa: E402
from polixor.services.transcribe import (                        # noqa: E402
    Segment, TranscriptResult, Word,
)

SCRIPT = [
    ("אז אה אוקיי", 1.4),
    ("אמ בדיקה אחד שתיים", 1.6),
    ("שלוש טעויות שעשיתי בשנה הראשונה שלי", 3.2),
    ("הראשונה הייתה שלא ביקשתי עזרה", 3.0),
    ("וזה עלה לי בחצי שנה של עבודה מיותרת", 3.4),
    ("ואז ואז הבנתי משהו שלא שכחתי מאז", 3.1),
    ("זה היה הרגע הכי קשה שעברתי בעסק", 3.3),
    ("אם זה עזר לכם תעקבו לעוד טיפים", 2.8),
]


def build(lines, gap: float = 0.35, offset: float = 0.0) -> TranscriptResult:
    segs, t = [], offset
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


def make_plan(*, offset: float = 0.0, style: str = "clean_creator",
              width: int = 1920, height: int = 1080) -> vd.VideoEditPlan:
    tr = build(SCRIPT, offset=offset)
    a = sem.analyze(tr, None, settings=AppSettings(), use_llm=False)
    return vd.direct(semantics=a, transcript=tr, source_start=offset,
                     source_end=tr.segments[-1].end,
                     width=width, height=height,
                     out_width=1080, out_height=1920,
                     fps=30.0, style=style)


# ==========================================================================
def test_analysis_does_not_depend_on_the_absolute_timestamp():
    """
    רגרסיה: אותות המיקום חושבו מזמן השידור המוחלט מול אורך החלון.
    קליפ שמתחיל בדקה 2 קיבל „מיקום" 4.95 במקום 0 — חמישה משפטים
    רצופים סווגו כ-payoff באותו ביטחון, והוו איבד חצי מהציון שלו.
    בקליפ אמיתי שמתחיל באמצע השידור זה כל הסיווג.
    """
    import copy

    runs = []
    for offset in (0.0, 120.0, 3600.0):
        a = sem.analyze(build(SCRIPT, offset=offset), None,
                        settings=AppSettings(), use_llm=False)
        runs.append([(round(s.start - offset, 3), s.role,
                      round(s.confidence, 3)) for s in a.sentences])
    assert runs[0] == runs[1] == runs[2], runs


def test_the_whole_clip_is_not_cut_away_at_an_offset():
    """
    רגרסיה: גיזום הפתיחה חושב כ-`source_start + trim_start`, אבל
    `trim_start` כבר מוחלט. בקליפ שמתחיל בדקה 2 הגיזום יצא אחרי
    סוף הקליפ — ומחק אותו כולו.
    """
    for offset in (0.0, 120.0, 3600.0):
        ep = br.to_edit_plan(make_plan(offset=offset))
        assert ep.beats, offset
        assert ep.out_duration > 15.0, (offset, ep.out_duration)


def test_the_edit_is_identical_at_any_offset():
    """
    סובלנות של 2ms: חיסור היסט גדול מזמן קטן מאבד דיוק ב-float,
    וזה הפרש שאינו נראה בפריים אחד של וידאו. מה שנבדק כאן הוא
    שאין הפרש **מבני** — אותם ביטים, אותו זום.
    """
    plans = [br.to_edit_plan(make_plan(offset=o)) for o in (0.0, 120.0)]
    a, b = [[(x.src_start, x.src_end, x.zoom) for x in p.beats]
            for p in plans]
    assert len(a) == len(b), (a, b)
    for (s0, e0, z0), (s1, e1, z1) in zip(a, b):
        assert abs(s0 - s1) <= 0.002 and abs(e0 - e1) <= 0.002, (a, b)
        assert abs(z0 - z1) < 1e-6, (a, b)


def test_planned_visual_changes_actually_happen():
    """
    רגרסיה: `zoom_changes = round(visual_changes * zoom_share)` עיגל
    לאפס בכל קטע עם שינוי אחד. חומר נלווה חסום ברוב התפקידים,
    ולכן התוצאה הייתה תכנית שמבטיחה שינוי — וסרטון סטטי לגמרי.
    """
    plan = make_plan()
    assert plan.pacing is not None
    for s in plan.pacing.sections:
        assert s.zoom_changes + s.broll_slots == s.visual_changes, s.to_dict()
    if plan.pacing.total_visual_changes:
        ep = br.to_edit_plan(plan)
        assert any(abs(b.zoom - 1.0) > 1e-3 for b in ep.beats), [
            b.zoom for b in ep.beats]


def test_beats_are_relative_to_the_window_not_the_broadcast():
    """
    נקודת הכשל הקלאסית: הבמאי עובד בזמני השידור, FFmpeg כבר קיבל
    את החלון חתוך. אי-החסרה של תחילת החלון מזיזה את כל העריכה.
    """
    plan = make_plan(offset=120.0)
    ep = br.to_edit_plan(plan)
    assert ep.beats
    assert ep.beats[0].src_start < 5.0, ep.beats[0].src_start
    assert all(b.src_start >= -1e-6 for b in ep.beats)
    assert ep.window_start == 120.0


def test_cuts_actually_shorten_the_output():
    plan = make_plan()
    ep = br.to_edit_plan(plan)
    assert ep.out_duration < plan.source_duration - 0.5, (
        ep.out_duration, plan.source_duration)
    assert ep.removed_seconds > 0.5
    assert abs(ep.removed_seconds
               - (plan.source_duration - ep.out_duration)) < 0.25


def test_beats_never_overlap_and_stay_in_order():
    plan = make_plan()
    ep = br.to_edit_plan(plan)
    for a, b in zip(ep.beats, ep.beats[1:]):
        assert a.src_end <= b.src_start + 1e-6, (a, b)
        assert a.src_end > a.src_start


def test_disabling_every_cut_restores_the_full_length():
    plan = make_plan()
    for d in plan.cuts:
        d.enabled = False
    ep = br.to_edit_plan(plan)
    assert abs(ep.out_duration - plan.source_duration) < 0.2, ep.out_duration
    assert ep.removed_seconds < 0.2


def test_zoom_decisions_reach_the_beats():
    plan = make_plan()
    ep = br.to_edit_plan(plan)
    zoomed = [b for b in ep.beats if abs(b.zoom - 1.0) > 1e-3]
    assert zoomed, "אף החלטת מסגור לא הגיעה לרנדרר"
    limit = vd.safe_zoom_limit(plan.width, plan.height, 1080, 1920)
    for b in zoomed:
        assert b.zoom <= limit + 1e-6, (b.zoom, limit)
        assert b.reason, b


def test_a_zoom_covering_part_of_a_segment_splits_it():
    """זום על חלק מקטע לא אמור למתוח את כל הקטע."""
    pieces = br._split_by_zoom(0.0, 10.0, [(4.0, 6.0, 1.15, "דחיפה")])
    assert [(round(a, 2), round(b, 2), z) for a, b, z, _ in pieces] == [
        (0.0, 4.0, 1.0), (4.0, 6.0, 1.15), (6.0, 10.0, 1.0)]
    assert sum(b - a for a, b, _, _ in pieces) == 10.0


def test_split_covers_the_segment_exactly():
    for zooms in ([], [(0.0, 10.0, 1.2, "")], [(-5.0, 2.0, 1.1, "")],
                  [(8.0, 20.0, 1.1, "")],
                  [(1.0, 3.0, 1.1, ""), (2.5, 5.0, 1.2, "")]):
        pieces = br._split_by_zoom(0.0, 10.0, list(zooms))
        assert abs(pieces[0][0] - 0.0) < 1e-6, zooms
        assert abs(pieces[-1][1] - 10.0) < 1e-6, zooms
        for a, b in zip(pieces, pieces[1:]):
            assert abs(a[1] - b[0]) < 1e-6, (zooms, pieces)


def test_no_zoom_when_the_source_cannot_take_it():
    """מקור 720p לוורטיקלי כבר נמתח פי 2.67 — אין זום נוסף."""
    plan = make_plan(width=1280, height=720)
    ep = br.to_edit_plan(plan)
    assert all(abs(b.zoom - 1.0) < 1e-3 for b in ep.beats), [
        b.zoom for b in ep.beats]


def test_tiny_beats_are_dropped():
    plan = make_plan()
    ep = br.to_edit_plan(plan)
    assert all(b.src_duration >= br.MIN_BEAT - 1e-6 for b in ep.beats)


def test_map_span_survives_the_conversion():
    """כתובית בתוך חומר ששרד חייבת למצוא מיפוי."""
    plan = make_plan()
    ep = br.to_edit_plan(plan)
    seg = plan.to_segments()[-1]
    mid = (seg.source_start + seg.source_end) / 2.0 - plan.source_start
    span = ep.map_span(mid, mid + 0.4)
    assert span is not None
    assert 0.0 <= span[0] < ep.out_duration


def test_cut_material_has_no_mapping():
    plan = make_plan()
    ep = br.to_edit_plan(plan)
    cut = [d for d in plan.enabled_cuts()]
    assert cut, "התסריט אמור להפיק לפחות חיתוך אחד"
    c = max(cut, key=lambda d: d.duration)
    mid = (c.start + c.end) / 2.0 - plan.source_start
    span = ep.map_span(mid, mid + 0.02)
    assert span is None, span


def test_preset_follows_the_pacing_style():
    assert br.caption_preset_for(make_plan(style="viral_short")) == "viral"
    assert br.caption_preset_for(
        make_plan(style="cinematic_story")) == "cinematic"
    assert br.caption_preset_for(make_plan(style="podcast_clip")) == "podcast"


def test_explanations_cover_every_decision():
    """„למה ה-AI עשה את זה?" — כולל מה שלא בוצע."""
    plan = make_plan()
    rows = br.explain(plan)
    assert rows
    ids = {r["id"] for r in rows}
    for group in (plan.cuts, plan.zoom_events, plan.captions, plan.broll,
                  plan.pending):
        for d in group:
            assert d.id in ids, d.to_dict()
    for r in rows:
        assert r["label"] and r["reason"]
        assert "enabled" in r
    starts = [r["start"] for r in rows]
    assert starts == sorted(starts)


def test_empty_plan_produces_an_empty_edit_not_a_crash():
    empty = sem.analyze(None, None, settings=AppSettings(), use_llm=False)
    plan = vd.direct(semantics=empty, transcript=None, source_start=0.0,
                     source_end=8.0, width=1920, height=1080,
                     out_width=1080, out_height=1920, fps=30.0,
                     style="clean_creator")
    ep = br.to_edit_plan(plan)
    assert ep.out_duration > 0
    assert abs(ep.out_duration - 8.0) < 0.2


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
    print(f"\n{passed}/{len(fns)} בדיקות גשר עברו")
    if failed:
        print("נכשלו: " + ", ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
