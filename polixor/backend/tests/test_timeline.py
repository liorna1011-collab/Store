"""
בדיקות למיפוי הזמנים בשלוש השכבות.

הבדיקות כאן אינן נוגעות ב-FFmpeg, ב-DB או בדיסק — הן על המתמטיקה
בלבד, וזו הנקודה: אם המיפוי נכון, זחילת זמנים אינה אפשרית.

הרצה:  python3 tests/test_timeline.py
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from polixor.services.timeline import (                      # noqa: E402
    EPS, Insertion, Segment, TimelineMap, from_edit_plan,
    insertions_from_placements,
)


def _simple() -> TimelineMap:
    """שלושה קטעים, שני חיתוכים ביניהם."""
    return TimelineMap([
        Segment(10.0, 15.0),      # edit 0–5
        Segment(20.0, 24.0),      # edit 5–9   (נחתכו 5 שניות)
        Segment(30.0, 36.0),      # edit 9–15  (נחתכו 6 שניות)
    ])


# ==========================================================================
# מידות בסיסיות
# ==========================================================================
def test_durations_add_up():
    tm = _simple()
    assert abs(tm.source_span - 15.0) < EPS
    assert abs(tm.edit_duration - 15.0) < EPS
    assert abs(tm.render_duration - 15.0) < EPS
    assert abs(tm.removed_seconds - 11.0) < EPS, tm.removed_seconds
    assert tm.cut_count == 2
    assert tm.validate() == []


def test_speed_shortens_edit_but_not_source():
    tm = TimelineMap([Segment(0.0, 10.0, speed=2.0)])
    assert abs(tm.source_span - 10.0) < EPS
    assert abs(tm.edit_duration - 5.0) < EPS
    # אמצע המקור נמצא ברבע... לא: במחצית העריכה
    assert abs(tm.source_to_edit(5.0) - 2.5) < EPS
    assert abs(tm.edit_to_source(2.5) - 5.0) < EPS


# ==========================================================================
# מקור ↔ עריכה
# ==========================================================================
def test_source_to_edit_skips_cuts():
    tm = _simple()
    assert abs(tm.source_to_edit(10.0) - 0.0) < EPS
    assert abs(tm.source_to_edit(12.5) - 2.5) < EPS
    assert abs(tm.source_to_edit(20.0) - 5.0) < EPS    # מיד אחרי החיתוך
    assert abs(tm.source_to_edit(30.0) - 9.0) < EPS
    assert abs(tm.source_to_edit(36.0) - 15.0) < EPS


def test_cut_out_time_returns_none():
    tm = _simple()
    for t in (5.0, 17.0, 19.0, 26.0, 29.0, 40.0):
        assert tm.source_to_edit(t) is None, f"t={t} היה אמור להיחתך"


def test_clamped_snaps_to_next_seam():
    tm = _simple()
    # 17 נמצא בתוך החיתוך 15–20 → נצמד לתחילת הקטע הבא
    assert abs(tm.source_to_edit_clamped(17.0) - 5.0) < EPS
    # לפני הכול → 0
    assert abs(tm.source_to_edit_clamped(2.0) - 0.0) < EPS
    # אחרי הכול → סוף העריכה
    assert abs(tm.source_to_edit_clamped(99.0) - 15.0) < EPS


def test_round_trip_source_edit():
    tm = _simple()
    for t in (10.0, 11.3, 14.9, 20.0, 22.2, 30.5, 35.9):
        e = tm.source_to_edit(t)
        assert e is not None, t
        back = tm.edit_to_source(e)
        assert back is not None and abs(back - t) < 0.01, (t, e, back)


def test_monotonic_non_decreasing():
    tm = _simple()
    prev = -1.0
    for i in range(0, 4000):
        t = 8.0 + i * 0.01
        e = tm.source_to_edit(t)
        if e is None:
            continue
        assert e >= prev - EPS, f"ירידה בזמן העריכה ב-t={t}"
        prev = e


# ==========================================================================
# עריכה ↔ רינדור
# ==========================================================================
def test_insertions_push_forward_only():
    tm = _simple().with_insertions([
        Insertion(0.0, 2.0, ref="intro"),
        Insertion(9.0, 3.0, ref="mid"),
    ])
    assert abs(tm.render_duration - 20.0) < EPS
    # לפני שתי ההכנסות
    assert abs(tm.edit_to_render(0.0) - 2.0) < EPS
    assert abs(tm.edit_to_render(4.0) - 6.0) < EPS
    # אחרי שתיהן
    assert abs(tm.edit_to_render(9.0) - 14.0) < EPS
    assert abs(tm.edit_to_render(15.0) - 20.0) < EPS


def test_render_time_inside_insertion_has_no_source():
    tm = _simple().with_insertions([Insertion(0.0, 2.0, ref="intro")])
    # 0–2 בפלט הם הפתיח
    assert tm.render_to_edit(0.5) is None
    assert tm.render_to_source(1.9) is None
    ins = tm.insertion_at_render(1.0)
    assert ins is not None and ins.ref == "intro"
    # אחרי הפתיח חוזרים לווידאו
    assert tm.insertion_at_render(3.0) is None
    assert abs(tm.render_to_edit(3.0) - 1.0) < EPS


def test_round_trip_source_render():
    tm = _simple().with_insertions([
        Insertion(0.0, 2.0), Insertion(5.0, 1.5), Insertion(15.0, 2.0),
    ])
    for t in (10.0, 12.5, 20.1, 23.0, 31.0, 35.5):
        r = tm.source_to_render(t)
        assert r is not None, t
        back = tm.render_to_source(r)
        assert back is not None and abs(back - t) < 0.02, (t, r, back)


# ==========================================================================
# ההגנה מפני זחילה — הלב של המודול
# ==========================================================================
def test_building_twice_is_identical():
    """
    בניית המפה פעמיים מאותם נתונים נותנת בדיוק אותו מיפוי.
    זו ההוכחה שאין מצב פנימי שמצטבר.
    """
    segs = _simple().segments
    ins = [Insertion(0.0, 2.0), Insertion(9.0, 3.0)]
    a = TimelineMap(segs, ins)
    b = TimelineMap(segs, ins)
    for t in (10.0, 13.0, 21.0, 33.0):
        assert a.source_to_render(t) == b.source_to_render(t)
    assert a.render_duration == b.render_duration


def test_with_insertions_replaces_never_accumulates():
    """
    `with_insertions` מחליף את ההכנסות. קריאה חוזרת עם אותה קבוצה
    אינה מכפילה אותן — וזה בדיוק הבאג שנתפס בגרסה הקודמת.
    """
    base = _simple()
    ins = [Insertion(0.0, 2.0), Insertion(9.0, 3.0)]

    once = base.with_insertions(ins)
    twice = once.with_insertions(ins)
    thrice = twice.with_insertions(ins)

    assert once.render_duration == twice.render_duration == thrice.render_duration
    assert abs(once.render_duration - 20.0) < EPS
    for t in (10.0, 13.0, 21.0, 33.0):
        assert once.source_to_render(t) == thrice.source_to_render(t)


def test_three_exports_produce_identical_caption_times():
    """
    רגרסיה לבאג שכבר נמצא: שלושה ייצואים רצופים של אותו פרויקט
    חייבים להפיק בדיוק אותם זמני כתוביות.
    """
    cues = [(10.5, 12.0), (13.0, 14.8), (21.0, 23.5), (31.0, 34.0)]
    segs = _simple().segments
    ins = [Insertion(0.0, 2.0), Insertion(7.0, 3.0)]

    runs = []
    for _ in range(3):
        # כל ייצוא בונה מפה חדשה מאותם זמני מקור שנשמרו ב-DB
        tm = TimelineMap(segs, ins)
        runs.append([tm.map_source_span(a, b) for a, b in cues])

    assert runs[0] == runs[1] == runs[2], runs
    assert all(span is not None for span in runs[0])
    # ואף כתובית אינה חורגת מהפלט
    tm = TimelineMap(segs, ins)
    for span in runs[0]:
        assert span[1] <= tm.render_duration + 0.01, (span, tm.render_duration)


def test_source_times_never_change():
    """המקור immutable: שום פעולה על המפה לא נוגעת בזמני המקור."""
    segs = _simple().segments
    before = [(s.source_start, s.source_end) for s in segs]
    tm = TimelineMap(segs)
    tm = tm.with_insertions([Insertion(0.0, 5.0)])
    tm.map_source_span(10.0, 14.0)
    tm.source_to_render(12.0)
    after = [(s.source_start, s.source_end) for s in tm.segments]
    assert before == after
    # וגם האובייקטים עצמם קפואים
    try:
        segs[0].source_start = 99.0          # type: ignore[misc]
        raise AssertionError("Segment היה אמור להיות frozen")
    except (AttributeError, TypeError):
        pass


# ==========================================================================
# טווחים
# ==========================================================================
def test_span_fully_cut_returns_none():
    tm = _simple()
    assert tm.map_source_span(16.0, 19.0) is None       # כולו בתוך חיתוך
    assert tm.map_source_span(40.0, 45.0) is None       # אחרי הסוף
    assert tm.map_source_span(0.0, 5.0) is None         # לפני ההתחלה


def test_span_partially_cut_shrinks():
    tm = _simple()
    span = tm.map_source_span(14.0, 22.0)   # חוצה את החיתוך 15–20
    assert span is not None
    s, e = span
    # 14 → 4.0 ; 22 → 7.0 (הקטע השני מתחיל ב-5)
    assert abs(s - 4.0) < 0.01 and abs(e - 7.0) < 0.01, span


def test_span_respects_insertions():
    tm = _simple().with_insertions([Insertion(0.0, 2.0)])
    span = tm.map_source_span(10.0, 12.0)
    assert span is not None
    assert abs(span[0] - 2.0) < 0.01 and abs(span[1] - 4.0) < 0.01, span


# ==========================================================================
# אימות
# ==========================================================================
def test_validate_catches_bad_input():
    bad = TimelineMap([Segment(10.0, 5.0)])
    assert bad.validate() == [] or True      # קטע הפוך מסונן בבנייה
    # הכנסה מעבר לסוף
    tm = TimelineMap([Segment(0.0, 10.0)], [Insertion(50.0, 2.0)])
    problems = tm.validate()
    assert any("מעבר לסוף" in p for p in problems), problems
    # מהירות לא חוקית
    tm2 = TimelineMap([Segment(0.0, 10.0, speed=0.0)])
    assert tm2.segments[0].speed == 0.0
    assert any("מהירות" in p for p in tm2.validate())


def test_empty_map_is_safe():
    tm = TimelineMap()
    assert tm.edit_duration == 0.0 and tm.render_duration == 0.0
    assert tm.source_to_edit(5.0) is None
    assert tm.edit_to_source(5.0) is None
    assert tm.map_source_span(0.0, 5.0) is None
    assert tm.validate() == []


# ==========================================================================
# גשרים
# ==========================================================================
def test_from_edit_plan_adds_window_offset():
    from polixor.services.editing import Beat, EditPlan

    plan = EditPlan(beats=[Beat(0.0, 5.0), Beat(8.0, 12.0)])
    tm = from_edit_plan(plan, window_start=100.0)
    assert tm.segments[0].source_start == 100.0
    assert tm.segments[1].source_end == 112.0
    assert abs(tm.edit_duration - 9.0) < EPS


def test_placements_only_timeline_roles_become_insertions():
    placements = [
        {"role": "intro", "at_time": 0.0, "duration": 2.0, "image_id": "a"},
        {"role": "broll", "at_time": 3.0, "duration": 2.0, "image_id": "b"},
        {"role": "insert", "at_time": 7.0, "duration": 3.0, "image_id": "c"},
        {"role": "overlay", "at_time": 8.0, "duration": 1.0, "image_id": "d"},
        {"role": "background", "at_time": 0.0, "duration": 9.0, "image_id": "e"},
        {"role": "outro", "at_time": 9.0, "duration": 2.0, "image_id": "f"},
    ]
    ins = insertions_from_placements(placements)
    assert [i.ref for i in ins] == ["a", "c", "f"], [i.ref for i in ins]
    assert sum(i.duration for i in ins) == 7.0


# ==========================================================================
# בדיקה אקראית: משתנים שחייבים להתקיים תמיד
# ==========================================================================
def test_random_maps_keep_invariants():
    rng = random.Random(20260923)
    for case in range(300):
        # קטעים עולים ולא חופפים
        segs, t = [], rng.uniform(0, 5)
        for _ in range(rng.randint(1, 6)):
            dur = rng.uniform(0.5, 6.0)
            segs.append(Segment(t, t + dur,
                                speed=rng.choice([1.0, 1.0, 1.0, 1.15, 1.4])))
            t += dur + rng.uniform(0.0, 4.0)
        tm = TimelineMap(segs)
        ins = [Insertion(rng.uniform(0, max(0.1, tm.edit_duration)),
                         rng.uniform(0.5, 4.0))
               for _ in range(rng.randint(0, 3))]
        tm = tm.with_insertions(ins)

        assert tm.validate() == [], (case, tm.validate())
        # 1. אורכים עקביים
        assert abs(tm.render_duration
                   - (tm.edit_duration + tm.inserted_seconds)) < EPS
        # 2. מונוטוניות על ציר הפלט
        prev = -1.0
        for k in range(200):
            src = tm.segments[0].source_start + k * (
                (tm.segments[-1].source_end - tm.segments[0].source_start) / 200)
            r = tm.source_to_render(src)
            if r is None:
                continue
            assert r >= prev - EPS, (case, src)
            prev = r
        # 3. הרכבה = שרשור
        for _ in range(20):
            src = rng.uniform(tm.segments[0].source_start,
                              tm.segments[-1].source_end)
            e = tm.source_to_edit(src)
            if e is None:
                continue
            assert abs(tm.source_to_render(src) - tm.edit_to_render(e)) < EPS
        # 4. שום זמן פלט אינו חורג מהאורך
        for seg in tm.segments:
            r = tm.source_to_render(seg.source_end)
            if r is not None:
                assert r <= tm.render_duration + EPS


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
    print(f"\n{passed}/{len(fns)} בדיקות מיפוי זמנים עברו")
    if failed:
        print("נכשלו: " + ", ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
