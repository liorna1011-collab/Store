"""
כותרת לקליפ מהמילים של היוצר עצמו – הרעיון החזק בקליפ, קצר וספציפי.

סדר העדפה: וו עם דעה/ויכוח/שאלה אמיתית/טענה, ואחריו פאנץ' עם הכרעה או עמדה.
מהמשפט שנבחר מסירים פתיח ריק („טוב אז", „תקשיבו,", „אחי,"), זנב („אתה
מבין?") והיסוס, ומקצרים בגבול ביטוי. אין כאן ניסוח חדש: כל מילה בכותרת
נאמרה בקליפ, באותו סדר.
"""

from __future__ import annotations

import re
from typing import Optional, Sequence

from .. import lang as _lang
from .score import Scored
from .story import HOOK_CATEGORY_WEIGHTS, has_semantic_payoff, payoff_potential, semantic_hooks
from .units import Unit

MAX_CHARS = 52
MIN_WORDS = 3
_EDGE = re.compile(r"^[\s,.;:–—\-…]+|[\s,;:–—\-…]+$")


def _strip_lead(text: str, packs: Sequence[object]) -> str:
    changed = True
    while changed:
        changed = False
        for p in packs:
            phrases = tuple(getattr(p, "throat_clearing", ())) + tuple(getattr(p, "hook", ())) + \
                tuple(getattr(p, "filler_phrases", ())) + ("אחי", "אחשלי", "bro", "dude")
            for ph in sorted(phrases, key=len, reverse=True):
                m = re.match(rf"^\s*{re.escape(ph)}(?=[\s,.!?…]|$)[\s,.!…]*", text, re.IGNORECASE)
                if m and len(text[m.end():].split()) >= MIN_WORDS:
                    text, changed = text[m.end():], True
                    break
            words = text.split()
            if words and words[0].strip(",.") in getattr(p, "hesitation_tokens", frozenset()):
                text, changed = " ".join(words[1:]), True
    return text


def _strip_tail(text: str, packs: Sequence[object]) -> str:
    for p in packs:
        for ph in sorted(tuple(getattr(p, "trailing_tags", ())) + tuple(getattr(p, "tag_questions", ())),
                         key=len, reverse=True):
            m = re.search(rf"[\s,]+{re.escape(ph)}\s*[?!.…]*\s*$", text, re.IGNORECASE)
            if m and len(text[:m.start()].split()) >= MIN_WORDS:
                text = text[:m.start()]
    return text


def _shorten(text: str) -> str:
    if len(text) <= MAX_CHARS:
        return text
    # הביטוי הראשון שעומד בפני עצמו (עד פסיק/נקודה)
    parts = [x.strip() for x in re.split(r"(?<=[,.;!?])\s+", text) if x.strip()]
    acc = ""
    for part in parts:
        cand = f"{acc} {part}".strip()
        if len(cand) > MAX_CHARS:
            break
        acc = cand
    if len(acc.split()) >= MIN_WORDS:
        return acc
    cut = text[:MAX_CHARS]
    return cut[:cut.rfind(" ")] + "…" if " " in cut else cut + "…"


def clean_line(text: str, language: Optional[str], *, shorten: bool = True) -> str:
    packs = _lang.packs_for(language)
    t = re.sub(r"\s+", " ", (text or "").strip())
    t = _strip_tail(_strip_lead(t, packs), packs)
    t = _EDGE.sub("", t)
    if shorten:
        t = _shorten(t)
    t = re.sub(r"\.+$", "", t)
    return _EDGE.sub("", t) if not t.endswith(("?", "!", "…")) else t


def _strength(u: Unit, nxt: Optional[Unit]) -> float:
    cats = semantic_hooks(u)
    v = max((HOOK_CATEGORY_WEIGHTS.get(c, 0.1) for c in cats), default=0.0)
    pv, why = payoff_potential(u, nxt)
    if has_semantic_payoff(why) and any(r in ("verdict", "opinion", "conflict", "comparison") for r in why):
        v = max(v, 0.8 * pv)
    return v


def clip_title(sc: Scored, units: Sequence[Unit], language: Optional[str]) -> str:
    """הכותרת, או "" כשאין משפט מתאים (ואז נשארת הכותרת הקיימת)."""
    p = sc.proposal
    best, best_v = None, 0.0
    for k in range(p.hook_idx, p.end_idx + 1):
        u = units[k]
        if u.private or u.garbled or len(u.text.split()) < MIN_WORDS:
            continue
        v = _strength(u, units[k + 1] if k + 1 < len(units) else None)
        if k == p.hook_idx:
            v += 0.05          # הוו הוא מה שהצופה שומע ראשון
        if v > best_v:
            best, best_v = u, v
    if best is None or best_v <= 0.0:
        return ""
    title = clean_line(best.text, language)
    return title if len(title.split()) >= MIN_WORDS else ""
