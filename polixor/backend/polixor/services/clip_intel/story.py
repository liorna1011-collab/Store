"""
הצעות לקליפים: לכל פאנץ' אפשרי – פתיחות טבעיות אפשריות לפניו.

איך נוצרת הצעה
--------------
1. **פאנץ'** (payoff) – משפט שיש בו סימן לסיום/תפנית/תגובה: תגובה קולית
   מיד אחריו, צחוק, „ובסוף…", „התברר ש…", רגש חזק, שיא עוצמה מקומי.
   מקורות נוספים: שיאי הציון המשולב הקיים (כולל חזותי), קפיצות צ'אט,
   ורגעים שמודל שפה הציע (אם מופעל).
2. **וו** (hook) – משפט *לפני* הפאנץ' שממנו אפשר להתחיל: המנוע בודק
   כמה נקודות התחלה אפשריות (תחילת משפט בלבד) וכל אחת נבחנת כסיפור שלם.
3. **גבולות** – ההתחלה תמיד בתחילת משפט, הסיום תמיד בסוף משפט; אחרי
   הפאנץ' נכללת התגובה (צחוק/קריאה קצרה) אם יש.

מה שלא קורה כאן, בכוונה: שום משפט לא זז ממקומו. אם הוו הטבעי נמצא
*אחרי* ההקשר, הקליפ פשוט מתחיל מאוחר יותר או מוקדם יותר – הוא לעולם
לא מורכב מחדש.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import numpy as np

from ..scoring import Timeline
from .units import Unit

# כמה נקודות התחלה לבדוק לכל פאנץ' (הטובות ביותר לפי איכות פתיחה)
MAX_OPENINGS_PER_PAYOFF = 6
# מתחת לזה משפט אינו מועמד לפאנץ'
PAYOFF_MIN = 0.30
# תגובה אחרי הפאנץ' שנכללת בקליפ (צחוק, „אין מצב")
TAIL_REACTION_MAX = 3.2
# הפסקה קצרה מזה בין משפטים = הדובר לא עצר
CONTINUOUS_GAP = 0.35
# כמה שניות של דיבור רציף אחרי הפאנץ' מצרפים לסיום לכל היותר
GLUE_MAX_SECONDS = 6.0
# קליפ שלם וטוב יכול להיות קצר מהמינימום המבוקש עד היחס הזה (עם קנס)
MIN_DURATION_RATIO = 0.6


@dataclass
class Proposal:
    hook_idx: int
    payoff_idx: int
    end_idx: int                     # היחידה האחרונה בקליפ (פאנץ' או תגובה אחריו)
    start: float
    end: float
    sources: list[str] = field(default_factory=list)
    start_reason: str = ""
    end_reason: str = ""

    @property
    def duration(self) -> float:
        return self.end - self.start


# --------------------------------------------------------------------------
def payoff_potential(u: Unit, nxt: Optional[Unit] = None) -> tuple[float, list[str]]:
    """כמה המשפט נראה כמו פאנץ'/שיא/פתרון. מחזיר ציון ורשימת סיבות."""
    reasons: list[str] = []
    v = 0.0
    if u.reaction_after >= 0.35:
        v += 0.38 * u.reaction_after
        reasons.append("reaction_after")
    if u.has("reaction_tokens"):
        v += 0.22
        reasons.append("reaction_words")
    if u.has("payoff_markers"):
        # כמה סימנים יחד („ממנו למדתי את השיעור") – פאנץ' מילולי ברור יותר
        v += 0.26 + 0.08 * min(2, u.flags.get("payoff_markers", 1) - 1)
        reasons.append("payoff_marker")
    if u.has("emotion"):
        v += 0.14
        reasons.append("emotion")
    if u.peak >= 0.6:
        v += 0.16 * u.peak
        reasons.append("energy_peak")
    if u.exclaim:
        v += 0.08
        reasons.append("exclamation")
    if u.lexical >= 0.35:
        v += 0.18 * u.lexical
        reasons.append("strong_words")
    if nxt is not None and nxt.pause_before < 1.5 and (
            nxt.has("reaction_tokens") or nxt.has("closure_markers")) and nxt.duration <= TAIL_REACTION_MAX:
        v += 0.10
        reasons.append("followed_by_reaction")
    # שאלה בלי תשובה אינה פאנץ'
    if u.is_question and not u.has("payoff_markers"):
        v *= 0.6
    return float(min(1.0, v)), reasons


def opening_quality(u: Unit, prev: Optional[Unit]) -> tuple[float, list[str], list[str]]:
    """
    כמה טוב המשפט כנקודת פתיחה של קליפ. מחזיר (ציון, סיבות חיוביות, בעיות).
    """
    good: list[str] = []
    bad: list[str] = []
    v = 0.45 * u.hook
    if u.hook >= 0.45:
        good.append("hook_words")
    if u.has("story_openers"):
        v += 0.22
        good.append("story_opener")
    if u.is_question or u.has("curiosity"):
        v += 0.10
        good.append("question")
    if prev is None or u.pause_before >= 0.6 or u.has("topic_shift"):
        v += 0.14
        good.append("clean_start")
    if u.energy >= 0.5:
        v += 0.08
        good.append("energetic")
    if u.has("continuation"):
        v -= 0.32
        bad.append("starts_mid_thought")
    if u.has("backrefs"):
        v -= 0.30
        bad.append("needs_earlier_context")
    if prev is not None and not prev.ends_sentence and u.pause_before < 0.35:
        v -= 0.25
        bad.append("previous_sentence_unfinished")
    if u.filler_ratio > 0.3:
        v -= 0.15
        bad.append("filler_opening")
    if u.has("payoff_markers") and _opens_with(u, "payoff_markers"):
        # „ובסוף התברר ש…" בפתיחה – זה הסוף של סיפור שהתחיל קודם
        v -= 0.25
        bad.append("starts_with_conclusion")
    return float(np.clip(v, 0.0, 1.0)), good, bad


# --------------------------------------------------------------------------
def propose(units: Sequence[Unit], tl: Timeline, *, min_d: float, max_d: float,
            extra_seeds: Sequence[dict[str, Any]] = (),
            peak_times: Sequence[float] = (),
            chat_times: Sequence[float] = (),
            lo: int = 0, hi: Optional[int] = None) -> list[Proposal]:
    """
    כל ההצעות: לכל פאנץ' אפשרי, כמה פתיחות אפשריות לפניו.

    `lo`/`hi` מגבילים את הפאנץ' והפתיחה לאזור ניתוח (units[lo:hi]); השכנים
    (המשפט שלפני הפתיחה, התגובה שאחרי הפאנץ') נלקחים מהרשימה המלאה, כך
    שגבול אזור אינו נראה כמו "התחלה נקייה" ואינו חותך סיום.
    """
    if not units:
        return []
    n = len(units)
    hi = n if hi is None else min(n, hi)
    lo = max(0, lo)
    payoff_src: dict[int, list[str]] = {}

    def add(i: int, source: str) -> None:
        j = _payoff_of(units, i)
        if lo <= j < hi:
            payoff_src.setdefault(j, []).append(source)

    for i in range(lo, hi):
        u = units[i]
        pv, _ = payoff_potential(u, units[i + 1] if i + 1 < n else None)
        if pv >= PAYOFF_MIN:
            add(i, "payoff_signal")

    # שיאי הציון המשולב הקיים (עוצמה, חזותי, צ'אט) – כהצעות בלבד
    for t in peak_times:
        i = _unit_at(units, t)
        if i is not None:
            add(i, "signal_peak")
    # צ'אט מגיב באיחור של כמה שניות – הפאנץ' הוא המשפט שלפני הקפיצה
    for t in chat_times:
        i = _unit_before(units, t, within=12.0)
        if i is not None:
            add(i, "chat_spike")
    # רגעים שמודל שפה הציע: הפאנץ' הוא המשפט האחרון בטווח
    for seed in extra_seeds:
        s0, s1 = float(seed.get("start", 0.0)), float(seed.get("end", 0.0))
        inside = [k for k in range(lo, hi) if units[k].start >= s0 - 1.0 and units[k].end <= s1 + 1.0]
        if inside:
            payoff_src.setdefault(inside[-1], []).append("llm")

    out: list[Proposal] = []
    for p_idx, sources in sorted(payoff_src.items()):
        # משפט שנחתך באמצע (תמלול ארוך בלי פיסוק) – ממשיכים עד סוף המשפט
        q = p_idx
        while (not units[q].ends_sentence and q + 1 < n and q - p_idx < 2
               and units[q + 1].pause_before < 0.6):
            q += 1
        end_idx, end, end_reason = _end_boundary(units, q, tl.duration)
        # פתיחות אפשריות: כל תחילת משפט שהקליפ ממנה נכנס בטווח האורך
        openings: list[tuple[float, int]] = []
        for h in range(p_idx, lo - 1, -1):
            start = _start_boundary(units, h)
            dur = end - start
            if dur > max_d:
                break
            if dur < max(4.0, min_d * MIN_DURATION_RATIO):
                continue
            q, _, _ = opening_quality(units[h], units[h - 1] if h else None)
            openings.append((q, h))
        if not openings:
            continue
        openings.sort(reverse=True)
        for _, h in openings[:MAX_OPENINGS_PER_PAYOFF]:
            start = _start_boundary(units, h)
            out.append(Proposal(
                hook_idx=h, payoff_idx=p_idx, end_idx=end_idx,
                start=round(start, 3), end=round(end, 3), sources=list(dict.fromkeys(sources)),
                start_reason=_start_reason(units, h), end_reason=end_reason))
    return out


def _opens_with(u: Unit, field_name: str) -> bool:
    from .. import lang as _lang
    from .units import _starts_with

    first = " ".join(u.text.split()[:4])
    return any(_starts_with(first, getattr(p, field_name, ()), p) for p in _lang.packs_for(None))


def is_reaction_line(u: Unit) -> bool:
    """משפט שכולו תגובה קצרה („חחחח אין מצב") – לא טענה בפני עצמה."""
    return u.has("reaction_tokens") and u.duration <= TAIL_REACTION_MAX and len(u.text.split()) <= 5


def _payoff_of(units: Sequence[Unit], i: int) -> int:
    """
    תגובה קצרה מיד אחרי משפט של ממש היא *התגובה לפאנץ'*, לא הפאנץ' עצמו:
    הפאנץ' הוא המשפט שלפניה, והתגובה נכנסת לסיום הקליפ.
    """
    u = units[i]
    if i == 0 or not is_reaction_line(u) or u.pause_before >= 1.5:
        return i
    prev = units[i - 1]
    if prev.duration < 2.0 or prev.is_question or is_reaction_line(prev):
        return i
    pv, _ = payoff_potential(prev, u)
    return i - 1 if pv >= PAYOFF_MIN else i


def _start_boundary(units: Sequence[Unit], h: int) -> float:
    """תחילת המשפט, עם מרווח קטן לפני המילה הראשונה (לא לתוך המשפט הקודם)."""
    u = units[h]
    lead = min(0.25, max(0.0, u.pause_before) * 0.5) if h else min(0.25, u.start)
    return max(0.0, u.start - lead)


def _start_reason(units: Sequence[Unit], h: int) -> str:
    u = units[h]
    if h == 0:
        return "source_start"
    if u.has("topic_shift"):
        return "topic_shift"
    if u.pause_before >= 0.6:
        return "after_pause"
    return "sentence_start"


def _glue(units: Sequence[Unit], p: int, budget: float) -> tuple[int, float]:
    """
    הדובר לא עצר: משפטים שנאמרים ברצף (פרץ קריאות: „וואו! בדיוק עכשיו?
    ניצחתי! עשיתי את זה!") הם יחידה אחת – לא חותכים באמצעה.
    """
    while (p + 1 < len(units) and units[p + 1].pause_before < CONTINUOUS_GAP
           and units[p + 1].duration <= budget):
        budget -= units[p + 1].duration
        p += 1
    return p, budget


def _end_boundary(units: Sequence[Unit], p: int, duration: float) -> tuple[int, float, str]:
    """
    סוף הקליפ: אחרי הפאנץ', כולל דיבור רציף שממשיך אותו, ותגובה קצרה
    שמיד אחריו (משפט תגובה/סגירה, או צחוק/קריאה בלי מילים). הסיום לעולם
    לא נכנס למילה הראשונה של המשפט הבא.
    """
    n = len(units)
    p, budget = _glue(units, p, GLUE_MAX_SECONDS)
    reason = "sentence_end"
    nxt = units[p + 1] if p + 1 < n else None
    if nxt is not None and nxt.pause_before < 1.5 and nxt.duration <= TAIL_REACTION_MAX and (
            nxt.has("reaction_tokens") or nxt.has("closure_markers")):
        p, _ = _glue(units, p + 1, budget)
        reason = "closing_line"
    u = units[p]
    nxt = units[p + 1] if p + 1 < n else None
    limit = (nxt.start - 0.05) if nxt is not None else duration
    if nxt is not None and limit <= u.end:
        limit = u.end + max(0.0, (nxt.start - u.end) / 2.0)
    if reason == "closing_line":
        end = min(u.end + 0.5, limit)
    elif u.reaction_after >= 0.35:
        reason = "after_reaction"
        end = min(u.end + 2.2, limit)
    else:
        end = min(u.end + min(0.6, max(0.15, u.pause_after * 0.6)), limit)
    return p, min(duration, max(u.end, end)), reason


def _unit_at(units: Sequence[Unit], t: float) -> Optional[int]:
    for i, u in enumerate(units):
        if u.start - 0.5 <= t <= u.end + 0.5:
            return i
    return None


def _unit_before(units: Sequence[Unit], t: float, within: float) -> Optional[int]:
    best = None
    for i, u in enumerate(units):
        if u.end <= t + 0.5 and t - u.end <= within:
            best = i
    return best
