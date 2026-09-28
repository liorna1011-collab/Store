"""
Hook Engine — שלוש השניות הראשונות.

שלושה דברים נפרדים, ובכוונה:

1. **מדידה** — כמה חזק הוו שכבר קיים, ולמה.
2. **גיזום** — הסרת גרירת רגליים בפתיחה („אז… אה… אוקיי, אז היום…").
3. **הצעה** — אם יש בסרטון משפט חזק יותר, להציע להעביר אותו לפתיחה.

ההצעה **אינה מבוצעת אוטומטית**. העברת משפט משנה את סדר הדברים שהדובר
אמר, וזו החלטה עריכתית שהמשתמש צריך לאשר. המנוע מחזיר הצעה עם נימוק,
והמשתמש מחליט.

מה שהמנוע לעולם לא עושה: לשנות את הטקסט עצמו, לחבר חצאי משפטים, או
לייצר משמעות שלא נאמרה.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from . import lang as _lang
from .semantics import (
    HOOK_EN, HOOK_HE, Sentence, _hits, _norm, _pack_hits, is_hebrew,
)

log = logging.getLogger("polixor.hook")

# מעל זה הוו נחשב חזק ואין טעם להציע חלופה
STRONG_HOOK = 0.62
# מתחת לזה הוו חלש ושווה להציע משהו אחר
WEAK_HOOK = 0.38
# כמה שניות מתחילת הסרטון נחשבות „הוו"
HOOK_WINDOW = 3.5
# מועמד חלופי חייב להיות לפחות כזה הפרש טוב יותר, אחרת לא שווה להזיז
MIN_IMPROVEMENT = 0.18

# הלקסיקונים נמצאים בחבילות השפה; השמות הישנים נשארים לתאימות לאחור
_HE, _EN = _lang.HEBREW, _lang.ENGLISH
THROAT_CLEARING_HE, THROAT_CLEARING_EN = list(_HE.throat_clearing), list(_EN.throat_clearing)
_DIGIT_RE = re.compile(r"\d")
# מספרים במילים. „שלוש טעויות" הוא וו קונקרטי בדיוק כמו „3 טעויות".
NUMBER_WORDS_HE, NUMBER_WORDS_EN = list(_HE.number_words), list(_EN.number_words)
# הבטחת רשימה / תובנה — „N טעויות", „הדבר האחד ש…"
LIST_PROMISE_HE, LIST_PROMISE_EN = list(_HE.list_promises), list(_EN.list_promises)
_CURIOSITY_HE, _CURIOSITY_EN = list(_HE.curiosity), list(_EN.curiosity)


# --------------------------------------------------------------------------
# מדידת חוזק
# --------------------------------------------------------------------------
@dataclass
class HookScore:
    """פירוט הניקוד, כדי שאפשר יהיה להסביר אותו למשתמש."""

    total: float = 0.0
    marker: float = 0.0        # ביטוי פתיחה מוכר
    curiosity: float = 0.0     # שאלה או פער ידע
    specificity: float = 0.0   # מספרים, פרטים קונקרטיים
    energy: float = 0.0
    brevity: float = 0.0       # קצר מספיק כדי להיקלט
    penalty: float = 0.0       # גרירת רגליים, מילות מילוי

    def to_dict(self) -> dict[str, Any]:
        return {k: round(v, 3) for k, v in self.__dict__.items()}


def score_sentence(s: Sentence, *, position_seconds: Optional[float] = None,
                   language: Optional[str] = None) -> HookScore:
    """
    מנקד משפט כווו פתיחה אפשרי, ללא קשר למיקומו הנוכחי.

    `position_seconds` הוא המיקום שבו המשפט **ישב בפועל** בפתיחה, אם
    יוזז לשם. משמש רק לעונש על אורך.
    """
    packs = _lang.packs_for(language)
    text = s.text
    sc = HookScore()

    has_number = bool(_DIGIT_RE.search(s.text)) or bool(
        _pack_hits(text, "number_words", packs))
    list_promise = bool(_pack_hits(text, "list_promises", packs))

    sc.marker = min(1.0, (
        0.55 * _pack_hits(text, "hook", packs)
        # „שלוש טעויות ש…" היא הבטחה מפורשת למה שיבוא — וו בפני עצמו
        + (0.65 if has_number and list_promise else 0.0)))
    sc.curiosity = min(1.0, (
        (0.5 if s.is_question else 0.0)
        + 0.35 * _pack_hits(text, "curiosity", packs)
        + (0.3 if list_promise else 0.0)))
    sc.specificity = min(1.0, (
        (0.45 if has_number else 0.0)
        + (0.2 if list_promise else 0.0)
        + 0.5 * s.lexical))
    sc.energy = min(1.0, s.energy)

    dur = position_seconds if position_seconds is not None else s.duration
    # וו אידיאלי הוא 1.5–4 שניות. קצר מדי לא מספיק, ארוך מדי מאבד.
    if dur <= 0.6:
        sc.brevity = 0.15
    elif dur <= 4.2:
        sc.brevity = 1.0
    elif dur <= 6.0:
        sc.brevity = 0.55
    else:
        sc.brevity = 0.2

    sc.penalty = min(1.0, 1.3 * s.filler_ratio
                     + 0.25 * _pack_hits(text, "throat_clearing", packs))

    sc.total = float(max(0.0, min(1.0,
        0.26 * sc.marker + 0.22 * sc.curiosity + 0.20 * sc.specificity
        + 0.14 * sc.energy + 0.18 * sc.brevity - 0.45 * sc.penalty)))
    return sc


# --------------------------------------------------------------------------
# תוצאת הניתוח
# --------------------------------------------------------------------------
@dataclass
class HookMove:
    """הצעה להזיז משפט לפתיחה. דורשת אישור מפורש."""

    sentence_index: int
    source_start: float
    source_end: float
    text: str
    score: float
    gain: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"sentence_index": self.sentence_index,
                "start": round(self.source_start, 3),
                "end": round(self.source_end, 3),
                "text": self.text,
                "score": round(self.score, 3),
                "gain": round(self.gain, 3),
                "reason": self.reason,
                "requires_approval": True}


@dataclass
class HookAnalysis:
    """מה שה-Director מקבל מהמנוע הזה."""

    strength: float = 0.0
    verdict: str = "missing"        # strong | ok | weak | missing
    reason: str = ""
    breakdown: HookScore = field(default_factory=HookScore)
    hook_start: float = 0.0
    hook_end: float = 0.0
    hook_text: str = ""
    # קטע בתחילת הסרטון שאפשר לגזום בלי לאבד תוכן
    trim_start: float = 0.0
    trim_reason: str = ""
    # הצעה להעברת משפט אחר לפתיחה — לא מבוצעת אוטומטית
    suggested_move: Optional[HookMove] = None
    notes: list[str] = field(default_factory=list)

    @property
    def has_trim(self) -> bool:
        return self.trim_start > 0.05

    def to_dict(self) -> dict[str, Any]:
        return {
            "strength": round(self.strength, 3),
            "verdict": self.verdict,
            "reason": self.reason,
            "breakdown": self.breakdown.to_dict(),
            "hook": {"start": round(self.hook_start, 3),
                     "end": round(self.hook_end, 3),
                     "text": self.hook_text},
            "trim_start": round(self.trim_start, 3),
            "trim_reason": self.trim_reason,
            "suggested_move": (self.suggested_move.to_dict()
                               if self.suggested_move else None),
            "notes": self.notes,
        }


VERDICT_LABELS_HE = {
    "strong": "וו פתיחה חזק",
    "ok": "וו פתיחה סביר",
    "weak": "וו פתיחה חלש",
    "missing": "אין וו פתיחה",
}


# --------------------------------------------------------------------------
# גיזום הפתיחה
# --------------------------------------------------------------------------
def _leading_trim(sentences: Sequence[Sentence],
                  language: Optional[str] = None) -> tuple[float, str]:
    """
    כמה שניות אפשר לגזום מתחילת הסרטון בלי לאבד תוכן.

    גוזמים רק משפטים שהם כולם מילוי או גרירת רגליים, ורק כל עוד לא
    הגענו למשפט בעל תוכן. עוצרים מיד כשמגיעים לתוכן — עדיף להשאיר
    חצי שנייה מיותרת מאשר לחתוך את תחילת המשפט הראשון.
    """
    if not sentences:
        return 0.0, ""
    cut_until = 0.0
    removed: list[str] = []
    packs = _lang.packs_for(language)
    for s in sentences:
        throat = _pack_hits(s.text, "throat_clearing", packs)
        words = s.word_count
        is_noise = (
            s.role == "filler"
            or s.filler_ratio >= 0.5
            or (throat > 0 and words <= 4 and s.lexical < 0.25)
        )
        if not is_noise:
            break
        cut_until = s.end
        removed.append(s.text)
    if cut_until <= 0.05:
        return 0.0, ""
    joined = " / ".join(t[:28] for t in removed[:3])
    return cut_until, f"פתיחה בלי תוכן: „{joined}”"


# --------------------------------------------------------------------------
# נקודת כניסה
# --------------------------------------------------------------------------
def analyze_hook(sentences: Sequence[Sentence], *,
                 allow_move: bool = True,
                 language: Optional[str] = None) -> HookAnalysis:
    """
    מנתח את פתיחת הסרטון ומחזיר מדידה, גיזום מוצע והצעת החלפה.

    אף אחת מההצעות אינה מבוצעת כאן. המנוע מחזיר מידע בלבד.
    """
    out = HookAnalysis()
    usable = [s for s in sentences if s.text.strip()]
    if not usable:
        out.reason = "אין תמלול, ולכן אי אפשר להעריך את הפתיחה."
        out.notes.append("הערכת הוו דורשת תמלול.")
        return out

    # --- גיזום קודם, מדידה אחר כך ---
    # אם הפתיחה היא „אז… אה… אוקיי", אין טעם למדוד אותה: הוו האמיתי
    # הוא המשפט שאחריה, וזה מה שהצופה יראה אחרי הגיזום.
    trim, trim_reason = _leading_trim(usable, language)
    after_trim = [s for s in usable if s.end > trim + 0.05] or usable

    current = next((s for s in after_trim if s.role == "hook"), after_trim[0])
    out.hook_start, out.hook_end = current.start, current.end
    out.hook_text = current.text
    out.breakdown = score_sentence(current, language=language)
    out.strength = out.breakdown.total

    if out.strength >= STRONG_HOOK:
        out.verdict = "strong"
    elif out.strength >= WEAK_HOOK:
        out.verdict = "ok"
    else:
        out.verdict = "weak"
    out.reason = _explain(out.breakdown, current)

    # --- רישום הגיזום ---
    # מותר לגזום רק עד תחילת הוו. אם החישוב חרג מעבר לזה, סימן
    # שמילות המילוי הן חלק מהמשפט עצמו ואי אפשר להפריד אותן.
    if trim > 0.05 and trim <= current.start + 0.05:
        out.trim_start, out.trim_reason = trim, trim_reason
        out.notes.append(
            f"גיזום {trim:.1f} שניות מהפתיחה מקרב את הצופה לעניין.")
    elif trim > 0.05:
        out.notes.append(
            "הפתיחה מכילה מילות מילוי, אבל הן חלק מהמשפט הראשון בעל "
            "התוכן — גיזום היה חותך גם אותו.")

    # --- מועמד חלופי ---
    if allow_move and out.verdict != "strong":
        best: Optional[Sentence] = None
        best_score = out.strength
        for s in usable:
            if s is current or s.start <= HOOK_WINDOW:
                continue
            if s.role in ("filler", "cta"):
                continue
            sc = score_sentence(s, position_seconds=s.duration, language=language).total
            if sc > best_score + MIN_IMPROVEMENT:
                best, best_score = s, sc
        if best is not None:
            out.suggested_move = HookMove(
                sentence_index=best.index,
                source_start=best.start, source_end=best.end,
                text=best.text, score=best_score,
                gain=best_score - out.strength,
                reason=(f"משפט זה מנקד {best_score:.0%} כוו פתיחה מול "
                        f"{out.strength:.0%} של הפתיחה הנוכחית. "
                        "העברה משנה את סדר הדברים שנאמרו, ולכן דורשת אישור."),
            )

    if out.verdict == "weak" and out.suggested_move is None:
        out.notes.append(
            "הפתיחה חלשה ולא נמצא בסרטון משפט חזק יותר להעביר לתחילתו.")
    return out


def _explain(sc: HookScore, s: Sentence) -> str:
    """משפט אחד בעברית שמסביר את הניקוד. מוצג למשתמש."""
    parts: list[str] = []
    if sc.marker > 0.3:
        parts.append("פותח בניסוח שמושך תשומת לב")
    if sc.curiosity > 0.3:
        parts.append("מייצר שאלה בראש הצופה")
    if sc.specificity > 0.35:
        parts.append("קונקרטי ולא כללי")
    if sc.brevity >= 1.0:
        parts.append(f"באורך טוב ({s.duration:.1f} שניות)")
    elif sc.brevity <= 0.3:
        parts.append(f"ארוך מדי לפתיחה ({s.duration:.1f} שניות)"
                     if s.duration > 4.2 else "קצר מכדי להיקלט")
    if sc.penalty > 0.25:
        parts.append("מתחיל בגרירת רגליים או במילות מילוי")
    if not parts:
        parts.append("פתיחה ניטרלית — לא מזיקה, אבל גם לא מושכת")
    return " · ".join(parts)
