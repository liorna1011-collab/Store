"""
אוצר מילים לתמלול: שמות, כינויים, סלנג ומונחי משחק.

המילים משמשות **רק להטיה** של מזהה הדיבור (hotwords של faster-whisper,
שנשלחים עם כל חלון של 30 שניות) – הן לא מחליפות שום מילה בתמלול.
תיקון לפי אוצר המילים (שלב ההגהה) קורה רק כשיש ראיה נוספת מהאודיו, ונשמר
עם המקור והסיבה – ראו services/transcript_correct.py.

שני מקורות: רשימה כללית (הגדרות → תמלול) ורשימה לפרויקט (מסך פרויקט חדש).
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

MAX_TERMS = 100
MAX_TERM_CHARS = 60
# hotwords נכנסים לפרומפט של כל חלון; פרומפט ארוך מדי דוחק את ההקשר
MAX_HOTWORD_CHARS = 320

_SPLIT = re.compile(r"[\n,;،]+")


def normalize_terms(value: Any) -> list[str]:
    """רשימה נקייה: מפצל שורות/פסיקים, מסיר כפילויות (בלי תלות ברישיות)."""
    if value is None:
        return []
    items: Iterable[str]
    if isinstance(value, str):
        items = _SPLIT.split(value)
    elif isinstance(value, (list, tuple)):
        items = [x for v in value for x in (_SPLIT.split(v) if isinstance(v, str) else [])]
    else:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw in items:
        term = re.sub(r"\s+", " ", raw).strip()[:MAX_TERM_CHARS].strip()
        if not term:
            continue
        key = term.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(term)
        if len(out) >= MAX_TERMS:
            break
    return out


def merge_terms(*lists: Any) -> list[str]:
    merged: list[str] = []
    for lst in lists:
        merged += normalize_terms(lst)
    return normalize_terms(merged)


def hotwords(terms: Any) -> Optional[str]:
    """המחרוזת ל-hotwords, בגבול האורך (המונחים הראשונים קודמים)."""
    out: list[str] = []
    size = 0
    for t in normalize_terms(terms):
        add = len(t) + (2 if out else 0)
        if size + add > MAX_HOTWORD_CHARS:
            break
        out.append(t)
        size += add
    return ", ".join(out) if out else None
