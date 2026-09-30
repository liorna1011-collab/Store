"""
בדיקה חזותית של מועמד – אחרי שהסיפור כבר נבחר לפי תמלול ואודיו.

החזותי *תומך* באיכות, הוא לא מחליף את הסיפור:
  * מסך שחור או תמונה קפואה בלי פנים ברוב הקליפ → קנס (קליפ כזה לא
    יעבוד ברשת, גם אם הדיבור טוב)
  * פעילות על המסך (תנועה, מעברי סצנה) → תוספת קטנה
  * נוכחות פנים → נרשמת (לפריסה ולדוח), בלי השפעה על הציון

הבדיקה רצה רק על חלונות שנותחו חזותית בפועל. מועמד מחוץ להם נשאר
בלי שינוי (ואין "עונש" על כך שלא נבדק).
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

import numpy as np

# פריים "שחור": בהירות ממוצעת מתחת לזה (0..1)
BLACK_BRIGHTNESS = 0.04
# פריים "קפוא": תנועה מנורמלת מתחת לזה
FROZEN_MOTION = 0.02
# חלק הפריימים הבעייתיים שמעליו הקליפ נחשב בעייתי חזותית
BAD_FRACTION = 0.6
BAD_VIDEO_PENALTY = 0.15
ACTIVITY_WEIGHT = 0.04


def covered(start: float, end: float,
            windows: Optional[Sequence[tuple[float, float]]]) -> bool:
    if windows is None:
        return True
    need = end - start
    got = sum(max(0.0, min(end, b) - max(start, a)) for a, b in windows)
    return got >= need * 0.9


def measure(visual: Any, start: float, end: float) -> Optional[dict[str, float]]:
    if visual is None or not getattr(visual, "analyzed", False) or visual.n == 0:
        return None
    i0 = visual.index_at(start)
    i1 = min(visual.n, max(i0 + 1, visual.index_at(end) + 1))
    bright = np.asarray(visual.brightness[i0:i1], dtype=np.float32)
    motion = np.asarray(visual.motion[i0:i1], dtype=np.float32)
    scene = np.asarray(visual.scene[i0:i1], dtype=np.float32)
    if bright.size == 0:
        return None
    faces = visual.faces[i0:i1] if visual.faces else []
    face_frac = (sum(1 for f in faces if f) / len(faces)) if faces else 0.0
    black = float(np.mean(bright < BLACK_BRIGHTNESS))
    frozen = float(np.mean((motion < FROZEN_MOTION) & (scene < FROZEN_MOTION)))
    activity = float(np.clip(0.6 * motion.mean() + 0.4 * scene.mean(), 0.0, 1.0))
    return {"black": round(black, 3), "frozen": round(frozen, 3),
            "activity": round(activity, 3), "faces": round(face_frac, 3)}


def apply_visual(s: Any, visual: Any, windows: Optional[Sequence[tuple[float, float]]],
                 threshold: Optional[float] = None) -> bool:
    """מעדכן את ציון המועמד לפי הניתוח החזותי. מחזיר True אם נבדק."""
    if not covered(s.start, s.end, windows):
        return False
    m = measure(visual, s.start, s.end)
    if m is None:
        return False
    # אידמפוטנטי: הציון מחושב תמיד מהציון שלפני הבדיקה החזותית
    base = getattr(s, "base_final", None)
    if base is None:
        s.base_final = base = s.final
        s.base_passed = s.passed
    s.visual = m
    bonus = ACTIVITY_WEIGHT * m["activity"]
    s.components["visual"] = round(m["activity"], 3)
    bad = m["black"] >= BAD_FRACTION or (m["frozen"] >= BAD_FRACTION and m["faces"] < 0.2)
    s.penalties.pop("black_or_frozen_video", None)
    if bad:
        s.penalties["black_or_frozen_video"] = BAD_VIDEO_PENALTY
    s.final = round(float(np.clip(base + bonus - (BAD_VIDEO_PENALTY if bad else 0.0), 0, 1)), 4)
    thr = threshold if threshold is not None else getattr(s, "threshold", None)
    s.passed = bool(s.base_passed) and not (thr is not None and s.final < thr)
    if s.base_passed and not s.passed:
        s.rejection = "below_quality_bar"
    return True
