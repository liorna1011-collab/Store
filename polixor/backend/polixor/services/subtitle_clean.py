"""
ניקוי כתוביות מגמגום והיסוס – רק במקומות בטוחים.

  * היסוס: קולות בלבד („אה", „אממ", "uh", "um") – יורדים מהכתובית.
  * גמגום: אותה מילת קישור/כינוי פעמיים ברצף ומהר („אני אני חושב",
    "the the") – הראשונה יורדת. חזרה מכוונת על מילה עם תוכן („לא לא לא",
    „יאללה יאללה") נשארת, וכך גם מילים עם אופי כמו „אחי".
  * התחלה קטועה: „ש- שלום" – החלק הקטוע יורד.

שום מילה לא מוזזת ואין טקסט חדש: המילים שנשארות שומרות בדיוק על הזמנים
שלהן, כך שהדגשת המילה הפעילה נשארת מסונכרנת לדיבור. התמלול המקורי לא
משתנה – הפונקציה מחזירה עותק.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Optional, Sequence

from . import lang as _lang
from .transcribe import Segment, TranscriptResult, Word

# גמגום = שתי מילים זהות ברצף עם רווח קטן מזה
STUTTER_GAP = 0.5
_BARE = re.compile(r"[\"'“”„«»\(\)\[\]\.,!?;:—–…׃\-]+")


def _bare(text: str) -> str:
    return _BARE.sub("", (text or "").strip()).lower()


def _packs(language: Optional[str]) -> Sequence[object]:
    return _lang.packs_for(language)


def is_hesitation(text: str, language: Optional[str]) -> bool:
    b = _bare(text)
    return bool(b) and any(b in getattr(p, "hesitation_tokens", frozenset()) for p in _packs(language))


def _stutter_word(b: str, language: Optional[str]) -> bool:
    """מילת קישור/כינוי – חזרה עליה היא גמגום, לא הדגשה."""
    for p in _packs(language):
        if b in getattr(p, "hanging_words", frozenset()) or b in getattr(p, "stop_words", frozenset()):
            return b not in getattr(p, "emphatic_repeats", frozenset())
    return False


def clean_words(words: Sequence[Word], language: Optional[str]) -> list[Word]:
    out: list[Word] = []
    n = len(words)
    bare = [_bare(w.text) for w in words]
    for i, w in enumerate(words):
        t = (w.text or "").strip()
        if not t:
            continue
        if is_hesitation(t, language):
            continue
        nxt = words[i + 1] if i + 1 < n else None
        if nxt is not None and nxt.start - w.end <= STUTTER_GAP:
            b, nb = bare[i], bare[i + 1]
            # התחלה קטועה: „ש-" לפני „שלום"
            if t.endswith(("-", "–")) and b and nb.startswith(b) and len(b) < len(nb):
                continue
            # מילה כפולה בדיוק פעמיים ברצף (שלוש = הדגשה), בלי סוף משפט ביניהן
            run2 = (b and b == nb and (i == 0 or bare[i - 1] != b)
                    and (i + 2 >= n or bare[i + 2] != b))
            if run2 and not re.search(r"[.!?…]$", t) and _stutter_word(b, language):
                continue
        out.append(w)
    return out


def cleaned(transcript: Optional[TranscriptResult], start: float, end: float) -> Optional[TranscriptResult]:
    """עותק של התמלול שבו המשפטים בטווח נוקו (המילים והטקסט)."""
    if transcript is None or not transcript.segments:
        return transcript
    lang = transcript.language or None
    segs: list[Segment] = []
    changed = False
    for s in transcript.segments:
        if s.end < start or s.start > end or not s.words:
            segs.append(s)
            continue
        kept = clean_words(s.words, s.language or lang)
        if len(kept) == len(s.words):
            segs.append(s)
            continue
        changed = True
        if not kept:
            continue
        segs.append(replace(s, words=kept, text=" ".join(w.text.strip() for w in kept)))
    return replace(transcript, segments=segs) if changed else transcript
