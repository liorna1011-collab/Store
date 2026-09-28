"""
מיפוי זמנים בשלוש שכבות: מקור → עריכה → רינדור.

```
Source Timeline    הקובץ המקורי. לא משתנה לעולם.
       ↓  segments (חיתוכים + שינויי מהירות)
Edit Timeline      אחרי העריכה. זה מה שמנוע העריכה מפיק.
       ↓  insertions (פתיח, סיום, הכנסות, קטעים שנוצרו)
Render Timeline    הקובץ הסופי. זה מה שהצופה רואה.
```

למה זה מודול נפרד
------------------
הבאג הכי יקר במערכת כזו הוא **זחילת זמנים**: מזיזים כתובית לפי תמונה
שהוכנסה, כותבים את הזמן החדש ל-DB, ובייצוא הבא מזיזים שוב. אחרי שלושה
ייצואים הכתובית רחוקה שניות מהדיבור.

ההגנה היא מבנית, לא זהירות: **ב-DB נשמרים אך ורק זמני מקור.** המיפוי
נבנה מחדש בכל רינדור מתוך (segments, insertions), ולכן אין מה שיצטבר.
מיפוי הוא חישוב, לא מצב.

כל הפונקציות כאן טהורות ואינן נוגעות ב-DB, בדיסק או ב-FFmpeg.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from .. import i18n

# מתחת לסף הזה שני זמנים נחשבים זהים. גודל פריים ב-60fps הוא 16ms,
# ולכן 5ms בטוח מתחת לרזולוציה שאפשר לראות.
EPS = 0.005

# קטע קצר מזה אינו שווה תפר
MIN_SEGMENT = 0.04


@dataclass(frozen=True)
class Segment:
    """
    קטע רציף מהמקור ששרד את העריכה.

    הזמנים הם **מוחלטים בקובץ המקור**. `speed` גדול מ-1 מקצר את הקטע
    בפלט (האצה); קטן מ-1 מאריך אותו.
    """

    source_start: float
    source_end: float
    speed: float = 1.0
    reason: str = ""

    @property
    def source_duration(self) -> float:
        return max(0.0, self.source_end - self.source_start)

    @property
    def edit_duration(self) -> float:
        return self.source_duration / max(0.05, self.speed)

    def to_dict(self) -> dict[str, Any]:
        return {"source_start": round(self.source_start, 4),
                "source_end": round(self.source_end, 4),
                "speed": round(self.speed, 4), "reason": self.reason}


@dataclass(frozen=True)
class Insertion:
    """
    משהו שנוסף על ציר העריכה ולא הגיע מהמקור: תמונה, קטע שנוצר, כרטיס.

    `at_edit_time` הוא הנקודה **על ציר העריכה** שבה ההכנסה נדחפת פנימה.
    זה מה שהמשתמש רואה בנגן, ולכן זה גם מה שנשמר.
    """

    at_edit_time: float
    duration: float
    kind: str = "image"        # image | video | title
    ref: str = ""              # מזהה הנכס
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"at": round(self.at_edit_time, 4),
                "duration": round(self.duration, 4),
                "kind": self.kind, "ref": self.ref, "reason": self.reason}


class TimelineMap:
    """
    ממיר זמנים בין שלוש השכבות.

    נבנה מחדש בכל שימוש מתוך segments ו-insertions. אין לו מצב פנימי
    שנשמר בין קריאות, ולכן אי אפשר "להזיז" אותו פעמיים בטעות.
    """

    __slots__ = ("segments", "insertions", "_seg_source_starts",
                 "_seg_edit_starts", "_ins_sorted", "_ins_edit_starts",
                 "_edit_duration", "_render_duration")

    def __init__(self, segments: Iterable[Segment] = (),
                 insertions: Iterable[Insertion] = ()) -> None:
        segs = [s for s in segments if s.source_duration > MIN_SEGMENT]
        segs.sort(key=lambda s: s.source_start)
        self.segments: list[Segment] = segs

        # מצטברים: נקודת ההתחלה של כל קטע על ציר העריכה
        self._seg_source_starts: list[float] = []
        self._seg_edit_starts: list[float] = []
        acc = 0.0
        for s in segs:
            self._seg_source_starts.append(s.source_start)
            self._seg_edit_starts.append(acc)
            acc += s.edit_duration
        self._edit_duration = acc

        ins = [i for i in insertions if i.duration > EPS]
        ins.sort(key=lambda i: (i.at_edit_time, i.kind))
        self.insertions: list[Insertion] = ins
        self._ins_edit_starts: list[float] = [i.at_edit_time for i in ins]
        self._render_duration = acc + sum(i.duration for i in ins)

    # ------------------------------------------------------------------
    # מידות
    # ------------------------------------------------------------------
    @property
    def source_span(self) -> float:
        """סך השניות מהמקור ששרדו, לפני שינויי מהירות."""
        return sum(s.source_duration for s in self.segments)

    @property
    def edit_duration(self) -> float:
        return self._edit_duration

    @property
    def render_duration(self) -> float:
        return self._render_duration

    @property
    def inserted_seconds(self) -> float:
        return sum(i.duration for i in self.insertions)

    @property
    def removed_seconds(self) -> float:
        """כמה הוסר מהמקור — רק אם יש יותר מקטע אחד להשוות אליו."""
        if not self.segments:
            return 0.0
        covered = self.segments[-1].source_end - self.segments[0].source_start
        return max(0.0, covered - self.source_span)

    @property
    def cut_count(self) -> int:
        return max(0, len(self.segments) - 1)

    # ------------------------------------------------------------------
    # מקור ↔ עריכה
    # ------------------------------------------------------------------
    def source_to_edit(self, t: float) -> Optional[float]:
        """
        זמן מקור → זמן בעריכה. מחזיר None אם הרגע נחתך החוצה.

        זמן שנפל בדיוק בתוך חיתוך מוחזר כ-None, ולא מוצמד לתפר: מי
        שצריך הצמדה מקבל אותה ב-`source_to_edit_clamped`.
        """
        if not self.segments:
            return None
        i = bisect.bisect_right(self._seg_source_starts, t + EPS) - 1
        if i < 0:
            return None
        seg = self.segments[i]
        if t > seg.source_end + EPS:
            return None
        offset = max(0.0, min(seg.source_duration, t - seg.source_start))
        return self._seg_edit_starts[i] + offset / max(0.05, seg.speed)

    def source_to_edit_clamped(self, t: float) -> float:
        """כמו למעלה, אבל זמן שנחתך נצמד לתפר הקרוב אחריו."""
        if not self.segments:
            return 0.0
        exact = self.source_to_edit(t)
        if exact is not None:
            return exact
        if t <= self.segments[0].source_start:
            return 0.0
        for i, seg in enumerate(self.segments):
            if t < seg.source_start:
                return self._seg_edit_starts[i]
        return self._edit_duration

    def edit_to_source(self, t: float) -> Optional[float]:
        """זמן בעריכה → זמן מקור."""
        if not self.segments:
            return None
        t = max(0.0, t)
        i = bisect.bisect_right(self._seg_edit_starts, t + EPS) - 1
        if i < 0:
            return None
        seg = self.segments[i]
        local = t - self._seg_edit_starts[i]
        if local > seg.edit_duration + EPS:
            return None
        return seg.source_start + min(seg.source_duration,
                                      local * max(0.05, seg.speed))

    # ------------------------------------------------------------------
    # עריכה ↔ רינדור
    # ------------------------------------------------------------------
    def shift_at(self, edit_time: float) -> float:
        """סך ההכנסות שנדחפו לפני הנקודה הזו על ציר העריכה."""
        total = 0.0
        for ins in self.insertions:
            if ins.at_edit_time <= edit_time + EPS:
                total += ins.duration
            else:
                break
        return total

    def edit_to_render(self, t: float) -> float:
        """זמן בעריכה → זמן בפלט. תמיד מוגדר: הכנסות רק דוחפות קדימה."""
        return max(0.0, t) + self.shift_at(t)

    def render_to_edit(self, t: float) -> Optional[float]:
        """
        זמן בפלט → זמן בעריכה. מחזיר None אם הנקודה נמצאת **בתוך**
        הכנסה, כלומר מציגה תמונה ולא וידאו מקור.
        """
        t = max(0.0, t)
        shift = 0.0
        for ins in self.insertions:
            start = ins.at_edit_time + shift
            if t < start - EPS:
                break
            if t < start + ins.duration - EPS:
                return None          # בתוך ההכנסה עצמה
            shift += ins.duration
        return t - shift

    def insertion_at_render(self, t: float) -> Optional[Insertion]:
        """איזו הכנסה מוצגת בנקודה הזו בפלט, אם בכלל."""
        shift = 0.0
        for ins in self.insertions:
            start = ins.at_edit_time + shift
            if t < start - EPS:
                return None
            if t < start + ins.duration - EPS:
                return ins
            shift += ins.duration
        return None

    # ------------------------------------------------------------------
    # מקור ↔ רינדור (הרכבה של השתיים)
    # ------------------------------------------------------------------
    def source_to_render(self, t: float) -> Optional[float]:
        e = self.source_to_edit(t)
        return None if e is None else self.edit_to_render(e)

    def render_to_source(self, t: float) -> Optional[float]:
        e = self.render_to_edit(t)
        return None if e is None else self.edit_to_source(e)

    # ------------------------------------------------------------------
    # טווחים
    # ------------------------------------------------------------------
    def map_source_span(self, start: float, end: float
                        ) -> Optional[tuple[float, float]]:
        """
        ממפה טווח מקור (כתובית, למשל) לזמני הפלט.

        טווח שנחתך כולו מוחזר כ-None; טווח שנחתך חלקית מתכווץ לחלק
        ששרד. הקצוות נצמדים לתפרים כדי שכתובית לא תיעלם רק משום
        שתחילתה נפלה בשבריר שנייה שנחתך.
        """
        if end <= start or not self.segments:
            return None
        if not self._overlaps_any_segment(start, end):
            return None
        s = self.source_to_edit_clamped(start)
        e = self.source_to_edit_clamped(end)
        if e <= s + EPS:
            return None
        return (round(self.edit_to_render(s), 3),
                round(self.edit_to_render(e), 3))

    def _overlaps_any_segment(self, start: float, end: float) -> bool:
        for seg in self.segments:
            if seg.source_start < end - EPS and start < seg.source_end - EPS:
                return True
        return False

    # ------------------------------------------------------------------
    # בנייה ואימות
    # ------------------------------------------------------------------
    def with_insertions(self, insertions: Iterable[Insertion]) -> "TimelineMap":
        """
        מפה חדשה עם קבוצת הכנסות **מחליפה** — לא נוספת.

        זו הדרך היחידה להוסיף הכנסות, וזו הסיבה שאי אפשר לצבור אותן
        בטעות: כל בנייה מתחילה מאותם segments המקוריים.
        """
        return TimelineMap(self.segments, insertions)

    def validate(self) -> list[str]:
        """בדיקות שפיות. רשימה ריקה = המפה תקינה."""
        problems: list[str] = []
        prev_end = None
        for i, seg in enumerate(self.segments):
            if seg.source_end <= seg.source_start:
                problems.append(i18n.tr("processing.timeline.end_before_start", index=i))
            if seg.speed <= 0.05:
                problems.append(i18n.tr("processing.timeline.bad_speed", index=i, speed=seg.speed))
            if prev_end is not None and seg.source_start < prev_end - EPS:
                problems.append(i18n.tr("processing.timeline.overlap", index=i))
            prev_end = seg.source_end

        for i, ins in enumerate(self.insertions):
            if ins.duration <= 0:
                problems.append(i18n.tr("processing.timeline.bad_duration", index=i))
            if ins.at_edit_time < -EPS:
                problems.append(i18n.tr("processing.timeline.negative_time", index=i))
            if ins.at_edit_time > self._edit_duration + EPS:
                problems.append(i18n.tr("processing.timeline.past_end", index=i,
                                        at=f"{ins.at_edit_time:.2f}",
                                        end=f"{self._edit_duration:.2f}"))
        return problems

    def to_dict(self) -> dict[str, Any]:
        return {
            "segments": [s.to_dict() for s in self.segments],
            "insertions": [i.to_dict() for i in self.insertions],
            "source_span": round(self.source_span, 3),
            "edit_duration": round(self.edit_duration, 3),
            "render_duration": round(self.render_duration, 3),
            "removed_seconds": round(self.removed_seconds, 3),
            "inserted_seconds": round(self.inserted_seconds, 3),
            "cuts": self.cut_count,
        }

    def __repr__(self) -> str:       # pragma: no cover - עזר לניפוי
        return (f"<TimelineMap {len(self.segments)} segments "
                f"{len(self.insertions)} insertions "
                f"src {self.source_span:.1f}s → edit {self.edit_duration:.1f}s "
                f"→ render {self.render_duration:.1f}s>")


# --------------------------------------------------------------------------
# גשרים למבנים הקיימים
# --------------------------------------------------------------------------
def from_edit_plan(plan: Any, *, window_start: float = 0.0,
                   insertions: Iterable[Insertion] = ()) -> TimelineMap:
    """
    בונה מפה מ-`EditPlan` של מנוע העריכה.

    זמני ה-`Beat` הם יחסיים לתחילת חלון הקליפ (כך FFmpeg מקבל אותם
    אחרי ‎-ss), ולכן מוסיפים כאן את `window_start` כדי לעבור לזמני
    מקור מוחלטים.
    """
    segments = [
        Segment(source_start=window_start + b.src_start,
                source_end=window_start + b.src_end,
                speed=getattr(b, "speed", 1.0) or 1.0,
                reason=getattr(b, "reason", ""))
        for b in getattr(plan, "beats", [])
    ]
    return TimelineMap(segments, insertions)


def insertions_from_placements(placements: Iterable[dict[str, Any]]
                               ) -> list[Insertion]:
    """
    ממיר שיבוצי תמונות (התפקידים שמאריכים את ציר הזמן) להכנסות.

    בי-רול, שכבה ורקע אינם מופיעים כאן: הם מצוירים מעל הווידאו ואינם
    משנים את אורכו, ולכן אינם נוגעים במיפוי הזמנים כלל.
    """
    out: list[Insertion] = []
    for p in placements:
        role = p.get("role")
        if role not in ("intro", "outro", "insert"):
            continue
        out.append(Insertion(
            at_edit_time=float(p.get("at_time") or 0.0),
            duration=float(p.get("duration") or 0.0),
            kind=str(p.get("kind") or "image"),
            ref=str(p.get("image_id") or p.get("id") or ""),
            reason=role,
        ))
    return out
