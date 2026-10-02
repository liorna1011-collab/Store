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
# משקלות הוו לפי סוג – סיבה אמיתית להמשיך לצפות (לא "יש סימן שאלה" ולא "רועש")
HOOK_CATEGORY_WEIGHTS = {
    "conflict": 0.42, "opinion": 0.36, "meaningful_question": 0.34, "story": 0.30,
    "claim": 0.28, "comparison": 0.26, "hook_words": 0.22, "emotion": 0.14,
}
# תוכן מינימלי כדי שסימן פאנץ' („בסוף…") ייחשב פאנץ' ולא זנב
PAYOFF_MARKER_MIN_TOKENS = 3
# אותות קוליים (תגובה/עוצמה) בלי תוכן במילים – נשאר מהם רק החלק הזה
ACOUSTIC_ONLY_FACTOR = 0.35
SEMANTIC_PAYOFF = frozenset({"verdict", "opinion", "conflict", "comparison", "payoff_marker",
                             "reaction_words", "emotion", "strong_words", "answers_question"})


def semantic_hooks(u: Unit) -> list[str]:
    """
    הסיבות *בתוכן* שבגללן משפט יכול לפתוח קליפ. משפט לא ברור (זיהוי לא
    בטוח) או פנייה פרטית אינם וו, גם אם הם רועשים.
    """
    if u.garbled or u.private:
        return []
    cats: list[str] = []
    if u.has("conflict_markers"):
        cats.append("conflict")
    if u.has("stance_markers"):
        cats.append("opinion")
    if u.question_kind == "meaningful":
        cats.append("meaningful_question")
    if u.has("story_openers"):
        cats.append("story")
    if u.has("claim"):
        cats.append("claim")
    if u.has("comparison_markers"):
        cats.append("comparison")
    if u.hook >= 0.45:
        cats.append("hook_words")
    if u.has("emotion") and u.content_count >= 3:
        cats.append("emotion")
    return cats


def payoff_potential(u: Unit, nxt: Optional[Unit] = None) -> tuple[float, list[str]]:
    """
    כמה המשפט נראה כמו פאנץ'/שיא/הכרעה. מחזיר ציון ורשימת סיבות.

    התוכן קובע: הכרעה, עמדה, ויכוח, השוואה, סימן פאנץ' עם תוכן, צחוק/תגובה
    במילים. תגובה קולית ושיא עוצמה מחזקים פאנץ' כזה – אבל לבדם (צעקה,
    קריאה בשם) הם כמעט לא שווים כלום: בשידור רועש יש כאלה כל הזמן.
    """
    reasons: list[str] = []
    sem = 0.0
    if u.has("verdict_markers") and u.content_count >= 3:
        sem += 0.34
        reasons.append("verdict")
    if u.has("stance_markers"):
        sem += 0.22
        reasons.append("opinion")
    if u.has("conflict_markers"):
        sem += 0.16
        reasons.append("conflict")
    if u.has("comparison_markers"):
        sem += 0.12
        reasons.append("comparison")
    if u.has("reaction_tokens"):
        sem += 0.22
        reasons.append("reaction_words")
    if u.has("payoff_markers") and u.content_count >= PAYOFF_MARKER_MIN_TOKENS \
            and not u.has("trailing_tag"):
        # כמה סימנים יחד („ממנו למדתי את השיעור") – פאנץ' מילולי ברור יותר
        sem += 0.26 + 0.08 * min(2, u.flags.get("payoff_markers", 1) - 1)
        reasons.append("payoff_marker")
    if u.has("emotion") and u.content_count >= 2:
        sem += 0.14
        reasons.append("emotion")
    if u.lexical >= 0.35 and u.content_count >= 3:
        sem += 0.18 * u.lexical
        reasons.append("strong_words")
    acoustic = 0.0
    if u.reaction_after >= 0.35:
        acoustic += 0.38 * u.reaction_after
        reasons.append("reaction_after")
    if u.peak >= 0.6:
        acoustic += 0.16 * u.peak
        reasons.append("energy_peak")
    if u.exclaim:
        acoustic += 0.08
        reasons.append("exclamation")
    if nxt is not None and nxt.pause_before < 1.5 and (
            nxt.has("reaction_tokens") or nxt.has("closure_markers")) and nxt.duration <= TAIL_REACTION_MAX:
        sem += 0.10
        reasons.append("followed_by_reaction")
    if not any(r in SEMANTIC_PAYOFF for r in reasons):
        acoustic *= ACOUSTIC_ONLY_FACTOR
        if acoustic > 0:
            reasons.append("acoustic_only")
    v = sem + acoustic
    # שאלה בלי תשובה אינה פאנץ'
    if u.is_question and not (u.has("payoff_markers") or u.has("verdict_markers")):
        v *= 0.6
    if u.private:
        v *= 0.3
    if u.garbled:
        v *= 0.5
    return float(min(1.0, v)), reasons


def has_semantic_payoff(reasons: Sequence[str]) -> bool:
    return any(r in SEMANTIC_PAYOFF for r in reasons)


def opening_quality(u: Unit, prev: Optional[Unit]) -> tuple[float, list[str], list[str]]:
    """
    כמה טוב המשפט כנקודת פתיחה של קליפ. מחזיר (ציון, סיבות חיוביות, בעיות).
    הוו נמדד לפי *סוג* הסיבה להמשיך לצפות; שאלה שגרתית, פנייה פרטית או
    טקסט לא ברור לא פותחים קליפ.
    """
    good: list[str] = []
    bad: list[str] = []
    cats = semantic_hooks(u)
    v = 0.25 * u.hook
    if cats:
        weights = sorted((HOOK_CATEGORY_WEIGHTS.get(c, 0.1) for c in cats), reverse=True)
        v += weights[0] + 0.08 * min(2, len(weights) - 1)
        good.extend(cats)
    if prev is None or u.pause_before >= 0.6 or u.has("topic_shift"):
        v += 0.10
        good.append("clean_start")
    if u.energy >= 0.5 and cats:
        v += 0.05
        good.append("energetic")
    if u.question_kind in ("tag", "trivial"):
        v -= 0.22
        bad.append("trivial_question")
    if u.private:
        v -= 0.35
        bad.append("private_talk")
    if u.garbled:
        v -= 0.30
        bad.append("unclear_opening")
    if u.has("continuation"):
        v -= 0.32
        bad.append("starts_mid_thought")
    if u.has("backrefs"):
        v -= 0.30
        bad.append("needs_earlier_context")
    elif u.has("pronoun_start") and not u.has("story_openers"):
        # „הוא אמר לי…" בפתיחה – הצופה לא יודע מי „הוא"
        v -= 0.18
        bad.append("unresolved_reference")
    if prev is not None and prev.question_kind == "meaningful" and u.pause_before < 2.5 \
            and not u.is_question:
        # הקליפ נפתח בתשובה, והשאלה נשארה בחוץ
        v -= 0.20
        bad.append("misses_the_question")
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


def is_setup(u: Unit) -> bool:
    """משפט שמבטיח המשך: בקשה להסביר („תספר להם למה…") או שאלה עם תוכן."""
    return bool(u.has("explain_requests") or (u.question_kind == "meaningful"
                                              and not u.has("verdict_markers")))


def is_tail(u: Unit) -> bool:
    """זנב אחרי הפאנץ' שאינו מוסיף: „אתה מבין?", „כאילו", שאלת-תג."""
    return bool(u.has("trailing_tag") or u.question_kind == "tag"
                or (u.content_count <= 1 and u.filler_ratio >= 0.5))


def weak_tail(u: Unit) -> bool:
    """
    זנב שנראה כמו המשך אבל לא מוסיף: „כאילו בסוף תחשוב", „בקיצור כן".
    משפט עם עמדה, הכרעה, ויכוח או תגובה במילים – אינו זנב.
    """
    if (u.has("stance_markers") or u.has("verdict_markers") or u.has("conflict_markers")
            or u.has("reaction_tokens") or u.has("comparison_markers")):
        return False
    if is_tail(u):
        return True
    return u.content_count <= 3 and (_opens_with(u, "filler_phrases") or _opens_with(u, "trailing_tags"))


def unanswered(units: Sequence[Unit], k: int, lo: int = 0) -> bool:
    """
    הקליפ נגמר בהכנה שמחכה לתשובה: בקשה להסביר („תספר להם למה…"), או
    שאלה אמיתית שלא באה אחרי עמדה. שאלה רטורית שסוגרת טיעון („אז מה הטעם?")
    אחרי עמדה/הכרעה – אינה הכנה.
    """
    u = units[k]
    if u.has("explain_requests"):
        return True
    if u.question_kind != "meaningful" or u.has("verdict_markers"):
        return False
    before = units[max(lo, k - 3):k]
    return not any(x.has("stance_markers") or x.has("verdict_markers") or x.has("conflict_markers")
                   for x in before)


def _not_tail(units: Sequence[Unit], j: int, lo: int) -> int:
    """הפאנץ' לא יכול להיות זנב: חוזרים למשפט עם התוכן שלפניו (אם הוא קרוב)."""
    k = j
    while k > lo and weak_tail(units[k]) and units[k].pause_before < 3.0:
        k -= 1
    if k != j:
        pv, why = payoff_potential(units[k], units[k + 1] if k + 1 < len(units) else None)
        if not has_semantic_payoff(why):
            return j
    return k


# --------------------------------------------------------------------------
def propose(units: Sequence[Unit], tl: Timeline, *, min_d: float, max_d: float,
            extra_seeds: Sequence[dict[str, Any]] = (),
            peak_times: Sequence[float] = (),
            chat_times: Sequence[float] = (),
            lo: int = 0, hi: Optional[int] = None,
            topic_seeds: Sequence[int] = ()) -> list[Proposal]:
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
        j = _not_tail(units, _payoff_of(units, i), lo)
        if lo <= j < hi:
            payoff_src.setdefault(j, []).append(source)

    for i in range(lo, hi):
        u = units[i]
        pv, _ = payoff_potential(u, units[i + 1] if i + 1 < n else None)
        if pv >= PAYOFF_MIN:
            add(i, "payoff_signal")

    # ויכוח/דיון: כמה משפטי עמדה/ויכוח/שאלה אמיתית ברצף – גם בלי פאנץ' "קלאסי".
    # כאן נמצאים רגעים כמו ויכוח על הרכב קבוצה, שאין בהם צחוק או צעקה.
    for i in argument_seeds(units, lo, hi):
        add(i, "argument")
    for i in topic_seeds:
        if lo <= i < hi:
            add(i, "topic")
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
        # הקליפ נגמר בהכנה („תספר להם למה…", שאלה עם תוכן) – ממשיכים עד התשובה
        if unanswered(units, end_idx, lo):
            ans = _answer_after(units, end_idx, max_d)
            if ans is not None:
                end_idx, end, _ = _end_boundary(units, ans, tl.duration)
                end_reason = "answer_included"
        # מיד אחרי הפאנץ' מגיעה השורה התחתונה („בסופו של דבר…") – היא חלק מהסיפור
        ver = _verdict_after(units, end_idx)
        if ver is not None:
            end_idx, end, _ = _end_boundary(units, ver, tl.duration)
            end_reason = "verdict_included"
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
    p0 = p
    p, budget = _glue(units, p, GLUE_MAX_SECONDS)
    # זנב שנדבק („אתה מבין? כאילו…") אינו חלק מהסיום
    while p > p0 and weak_tail(units[p]):
        p -= 1
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


# כמה משפטים "עם עמדה" בחלון קצר הופכים קטע לדיון ששווה לבדוק
ARGUMENT_WINDOW = 45.0
ARGUMENT_MIN_UNITS = 3
ANSWER_SEARCH_SECONDS = 20.0
# הכרעה שמגיעה אחרי הפסקה ארוכה מזו כבר לא "המשך ישיר" של הפאנץ'
VERDICT_GAP = 1.5


def _arguing(u: Unit) -> bool:
    return bool(not u.private and not u.garbled and (
        u.has("stance_markers") or u.has("conflict_markers") or u.has("comparison_markers")
        or u.has("verdict_markers") or u.question_kind == "meaningful"))


def argument_seeds(units: Sequence[Unit], lo: int = 0, hi: Optional[int] = None) -> list[int]:
    """
    בכל רצף של דיון (לפחות 3 משפטי עמדה/ויכוח/השוואה/שאלה אמיתית בתוך 45
    שניות) – המשפט החזק האחרון בעמדה/הכרעה הוא פאנץ' אפשרי.
    """
    hi = len(units) if hi is None else min(len(units), hi)
    out: list[int] = []
    for i in range(lo, hi):
        u = units[i]
        if not (u.has("stance_markers") or u.has("verdict_markers") or u.has("comparison_markers")):
            continue
        window = [x for x in units[max(0, i - 12): i + 3]
                  if u.start - ARGUMENT_WINDOW <= x.start <= u.end + 15.0]
        if sum(1 for x in window if _arguing(x)) >= ARGUMENT_MIN_UNITS:
            out.append(i)
    return out


def _answer_after(units: Sequence[Unit], k: int, max_d: float) -> Optional[int]:
    """המשפט שעונה על ההכנה ביחידה k (עמדה/הכרעה/פאנץ' עם תוכן), אם הוא קרוב."""
    base = units[k]
    for j in range(k + 1, len(units)):
        u = units[j]
        if u.start - base.end > ANSWER_SEARCH_SECONDS:
            break
        pv, why = payoff_potential(u, units[j + 1] if j + 1 < len(units) else None)
        if has_semantic_payoff(why) and pv >= PAYOFF_MIN * 0.8:
            return j
    return None


def _verdict_after(units: Sequence[Unit], k: int) -> Optional[int]:
    """הכרעה עם תוכן שבאה ברצף מיד אחרי היחידה k (עד שני משפטים, בלי הפסקה ארוכה)."""
    for j in range(k + 1, min(len(units), k + 3)):
        u = units[j]
        if u.pause_before > VERDICT_GAP or u.private or u.garbled:
            return None
        if u.has("verdict_markers") and u.content_count >= PAYOFF_MARKER_MIN_TOKENS:
            return j
    return None


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
