"""
אזורי ניתוח לשידורים ארוכים – עם חפיפה.

שידור של שעות מחולק לאזורים של כ-8 דקות. בין כל שני אזורים יש חפיפה
שגדולה מאורך הקליפ המרבי (ועוד מרווח), ולכן כל סיפור באורך המותר נמצא
*בשלמותו* בתוך אזור אחד לפחות – גם סיפור שמתחיל בסוף אזור אחד ונגמר
בתחילת הבא. הוכחה: אזור k מכסה [k·step, k·step + size], step = size − overlap.
לחלון [a, a+L] עם L ≤ overlap ניקח k = ⌊a/step⌋; אז a ≥ k·step
ו-a+L < (k+1)·step + overlap = k·step + size.

בכל אזור ההצעות נבנות מהמשפטים שבו, ושיאי האותות נמדדים ביחס לאזור
עצמו (לא לשידור כולו) – כך שעה רועשת לא "גונבת" את כל ההצעות.
ההצעות מכל האזורים מתאחדות (הצעה זהה משני אזורים נספרת פעם אחת)
ומדורגות יחד.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# אורך אזור ניתוח
REGION_SECONDS = 480.0
# מתחת לאורך הזה – אזור אחד (אין מה לחלק)
SINGLE_REGION_MAX = 720.0
# מרווח נוסף מעבר לאורך הקליפ המרבי בחפיפה
OVERLAP_MARGIN = 30.0


@dataclass
class Region:
    index: int
    start: float
    end: float
    proposals: int = 0
    stories: int = 0
    passed: int = 0
    selected: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def contains(self, start: float, end: float) -> bool:
        return start >= self.start - 1e-6 and end <= self.end + 1e-6

    def to_dict(self) -> dict[str, Any]:
        return {"index": self.index, "start": round(self.start, 2), "end": round(self.end, 2),
                "proposals": self.proposals, "stories": self.stories,
                "passed": self.passed, "selected": self.selected}


def overlap_for(max_clip_seconds: float) -> float:
    return max(90.0, float(max_clip_seconds) + OVERLAP_MARGIN)


def plan_regions(duration: float, max_clip_seconds: float) -> list[Region]:
    duration = max(0.0, float(duration))
    if duration <= SINGLE_REGION_MAX:
        return [Region(0, 0.0, duration)]
    overlap = overlap_for(max_clip_seconds)
    size = max(REGION_SECONDS, overlap + 240.0)
    step = size - overlap
    out: list[Region] = []
    start = 0.0
    while True:
        end = min(duration, start + size)
        out.append(Region(len(out), start, end))
        if end >= duration:
            break
        start += step
    return out
