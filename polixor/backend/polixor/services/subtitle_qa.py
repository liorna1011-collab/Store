"""
בדיקת כתוביות אחרונה לפני רינדור – "האם צופה רגיל יראה כאן טעות?".

בודק את המילים של הקליפ אחרי התמלול החזק, ההגהה ויישור הזמנים:

  חמור (הקליפ לא יוצא כך):
    * כתב זר באמצע עברית (סינית/קוריאנית/קירילית/ערבית) – תמלול שבור
    * מילה שמערבבת עברית ולטינית („בשביליםcular") – שבר זיהוי
    * לולאת הזיה („קבודלת לי, קבודלת לי, …")
    * יותר מדי מילים לא בטוחות שלא אושרו (SEVERE_LOW_SHARE)
  אזהרה (יוצא, מסומן לבדיקה):
    * מילים לא בטוחות או מסומנות בהגהה (ומפורטות, כדי שהעורך יראה איפה)

אנגלית בתוך עברית, מספרים ו„אחי" – תקינים.
"""

from __future__ import annotations

import re
from typing import Any, Optional, Sequence

from .strong_windows import _looping
from .transcribe import Word

LOW_P = 0.5
WARN_LOW_SHARE = 0.08
SEVERE_LOW_SHARE = 0.30
_FOREIGN = re.compile(r"[Ѐ-ӿ؀-ۿ぀-ヿ㐀-鿿가-힯฀-๿]")
_HEB = re.compile(r"[א-ת]")
_LAT = re.compile(r"[A-Za-z]")


def check(words: Sequence[Word], language: Optional[str] = "he") -> dict[str, Any]:
    severe: list[str] = []
    warnings: list[str] = []
    toks = [w for w in words if (w.text or "").strip()]
    if not toks:
        return {"ok": True, "severe": [], "warnings": [], "low_share": 0.0, "suspicious": []}
    hebrew = (language or "").startswith("he") or sum(bool(_HEB.search(w.text)) for w in toks) > len(toks) / 3
    foreign = [w.text for w in toks if _FOREIGN.search(w.text)]
    if hebrew and foreign:
        severe.append("foreign_script")
    mixed = [w.text for w in toks if _HEB.search(w.text) and _LAT.search(w.text)]
    if mixed:
        severe.append("mixed_script_word")
    if _looping(toks):
        severe.append("hallucination_loop")
    low = [w for w in toks if float(w.probability) < LOW_P
           and getattr(w, "flag", "") not in ("confirmed", "corrected")]
    flagged = [w for w in toks if getattr(w, "flag", "") == "low"]
    share = len(low) / len(toks)
    if share > SEVERE_LOW_SHARE:
        severe.append("too_many_uncertain_words")
    elif share > WARN_LOW_SHARE or flagged:
        warnings.append("uncertain_words")
    suspicious = sorted({(round(w.start, 2), w.text) for w in low + flagged})[:20]
    return {"ok": not severe, "severe": severe, "warnings": warnings, "low_share": round(share, 3),
            "suspicious": [{"t": t, "text": x} for t, x in suspicious],
            "examples": (foreign + mixed)[:5]}
