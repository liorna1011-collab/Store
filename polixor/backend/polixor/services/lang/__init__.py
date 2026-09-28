"""
רישום חבילות השפה וזיהוי שפת התוכן.

    get_pack("he")            חבילה לפי קוד, או None
    packs_for(code)           החבילה של השפה, או כל החבילות כשהשפה
                              לא ידועה (התנהגות קודמת: איחוד לקסיקונים)
    detect_language(text)     זיהוי לפי הכתב. ערבית ופרסית אינן עברית.
    resolve_language(t)       שפת הרוב בתמלול (לפי אורך הטקסט)
    segment_languages(t)      שפה לכל מקטע – לזרם מעורב
    register_pack(pack)       הוספת שפה

שפה חדשה: קובץ `xx.py` שמגדיר LanguagePack ונרשם כאן. המנועים עצמם
אינם צריכים שינוי.
"""

from __future__ import annotations

import threading
import unicodedata
from typing import Any, Iterable, Optional

from .base import LanguagePack, default_normalizer
from .en import ENGLISH
from .he import HEBREW

_PACKS: dict[str, LanguagePack] = {}
_LOCK = threading.RLock()

# כתבים שאינם עברית גם כשהם מימין לשמאל
_ARABIC_RANGES = ((0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF),
                  (0xFB50, 0xFDFF), (0xFE70, 0xFEFF))
_PERSIAN_LETTERS = set("پچژگکی")
_ALIASES = {"iw": "he", "heb": "he", "hebrew": "he", "english": "en", "eng": "en"}


def register_pack(pack: LanguagePack) -> None:
    with _LOCK:
        _PACKS[pack.code] = pack


def _canonical(code: Optional[str]) -> str:
    c = (code or "").strip().lower().replace("_", "-").split("-", 1)[0]
    return _ALIASES.get(c, c)


def get_pack(code: Optional[str]) -> Optional[LanguagePack]:
    return _PACKS.get(_canonical(code))


def available_languages() -> list[dict[str, str]]:
    return [{"code": p.code, "name": p.name, "direction": p.direction}
            for p in _PACKS.values()]


def packs_for(code: Optional[str]) -> list[LanguagePack]:
    """
    החבילה לשפה נתונה. בלי שפה, או לשפה בלי חבילה – כל החבילות
    (כך שקוד שלא מעביר שפה מתנהג בדיוק כמו קודם).
    """
    pack = get_pack(code)
    return [pack] if pack is not None else list(_PACKS.values())


def _ratio(text: str, ranges: Iterable[tuple[int, int]]) -> float:
    letters = [c for c in text or "" if unicodedata.category(c).startswith("L")]
    if not letters:
        return 0.0
    ranges = tuple(ranges)
    return sum(1 for c in letters
               if any(lo <= ord(c) <= hi for lo, hi in ranges)) / len(letters)


def detect_language(text: str) -> Optional[str]:
    """
    זיהוי שפה לפי הכתב בלבד. מחזיר קוד (he / en / ar / fa / ...) או None.

    ערבית ופרסית נכתבות גם הן מימין לשמאל, אבל **אינן** עברית – הן
    מזוהות בנפרד ולא מקבלות את חבילת העברית.
    """
    if not (text or "").strip():
        return None
    arabic = _ratio(text, _ARABIC_RANGES)
    if arabic >= 0.3:
        return "fa" if any(c in _PERSIAN_LETTERS for c in text) else "ar"
    best, best_ratio = None, 0.0
    for pack in _PACKS.values():
        r = pack.script_ratio(text)
        if r > best_ratio:
            best, best_ratio = pack.code, r
    if best is None or best_ratio < 0.3:
        return None
    return best


def _segment_language(seg: Any) -> Optional[str]:
    text = getattr(seg, "text", "") or ""
    detected = detect_language(text)
    declared = _canonical(getattr(seg, "language", "") or "")
    # הכתב בפועל גובר על הצהרת המודל: מודל שמסמן „he" לטקסט בלטינית
    # טועה, ולהפך.
    return detected or (declared or None)


def resolve_language(transcript: Any) -> Optional[str]:
    """
    שפת הרוב בתמלול, לפי כמות הטקסט בכל שפה.

    מחזיר None כשאין תמלול או טקסט. בשפה שאין לה חבילה מוחזר הקוד
    שלה (למשל "ar") – והמנועים משתמשים אז באיחוד כל החבילות.
    """
    if transcript is None:
        return None
    weights: dict[str, float] = {}
    for seg in getattr(transcript, "segments", None) or []:
        code = _segment_language(seg)
        if not code:
            continue
        weights[code] = weights.get(code, 0.0) + len((seg.text or "").strip())
    if not weights:
        declared = _canonical(getattr(transcript, "language", "") or "")
        return declared or None
    return max(weights.items(), key=lambda kv: kv[1])[0]


def segment_languages(transcript: Any) -> list[Optional[str]]:
    """שפה לכל מקטע בתמלול (לזרמים שמחליפים שפה באמצע)."""
    return [_segment_language(s) for s in (getattr(transcript, "segments", None) or [])]


def normalize(text: str, code: Optional[str] = None) -> str:
    pack = get_pack(code)
    return (pack.normalizer if pack else default_normalizer)(text)


register_pack(HEBREW)
register_pack(ENGLISH)

__all__ = [
    "LanguagePack", "register_pack", "get_pack", "packs_for", "available_languages",
    "detect_language", "resolve_language", "segment_languages", "normalize",
    "HEBREW", "ENGLISH",
]
