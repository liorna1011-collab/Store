"""
נושאים בשידור – תשתית לבחירת שורטים, לא מוצר בפני עצמו.

TextTiling על יחידות השיח (אותו אלגוריתם כמו services/longform): גבול נושא
הוא ירידה בדמיון המילים בין החלונות משני צדיו, ומתחזק במעבר נושא מפורש
(„בנושא אחר…") או בהפסקה ארוכה. משמש את בחירת הקליפים כדי:
  * לא לחצות נושאים – רק גבולות *חזקים* נכנסים לשער "נושא אחד", כי גבול
    חלש של TextTiling בדיבור חופשי הוא לעתים קרובות רעש;
  * לזהות קטעי ויכוח ודעות (צפיפות עמדה/ויכוח/הכרעה/שאלה אמיתית);
  * לזרוע מועמדים: הרגע החזק בכל נושא כזה מוצע גם אם לא היה שם שיא
    קולי – כך ויכוח ענייני ושקט יחסית לא נעלם.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

from ..longform import _add, _cos, _normalize, _Tfidf
from .story import _arguing, has_semantic_payoff, payoff_potential
from .units import Unit

TILING_WINDOW = 6                # יחידות בכל צד של גבול
MIN_TOPIC_SECONDS = 60.0
LONG_PAUSE = 4.0
# גבול חזק: עומק מעל ממוצע + STRONG_SD סטיות תקן, או מעבר נושא מפורש
STRONG_SD = 1.0
STRONG_PAUSE = 2.0
STRONG_MIN_UNITS = 4 * TILING_WINDOW
# נושא "מתווכח": לפחות כך הרבה משפטים עם עמדה/ויכוח/הכרעה, ובצפיפות הזו
ARGUMENT_MIN_UNITS = 3
ARGUMENT_MIN_DENSITY = 0.18
SEEDS_PER_TOPIC = 2


@dataclass
class Topic:
    start_idx: int
    end_idx: int                 # כולל
    start: float
    end: float
    strong_start: bool = False   # הגבול שבתחילתו חזק (נכנס לשער "נושא אחד")
    argument: float = 0.0        # צפיפות עמדה/ויכוח/הכרעה/שאלה 0..1
    seeds: list[int] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"start": round(self.start, 2), "end": round(self.end, 2),
                "units": self.end_idx - self.start_idx + 1, "strong_start": self.strong_start,
                "argument": round(self.argument, 3), "seeds": list(self.seeds),
                "terms": list(self.terms)}


def _explicit_shift(u: Unit) -> bool:
    """מעבר נושא מפורש: ביטוי המעבר *פותח* משפט של ממש („בנושא אחר, בואו נדבר על…")."""
    if not u.has("topic_shift") or len(u.text.split()) < 4:
        return False
    from .. import lang as _lang
    from .units import _starts_with

    return any(_starts_with(u.text, getattr(p, "topic_shift", ()), p) for p in _lang.packs_for(None))


def detect_topics(units: Sequence[Unit], *, window: int = TILING_WINDOW,
                  min_seconds: float = MIN_TOPIC_SECONDS) -> list[Topic]:
    n = len(units)
    if n == 0:
        return []
    bounds: list[tuple[int, bool]] = []          # (אינדקס הפער, חזק?)
    if n >= 2 * window + 1:
        tfidf = _Tfidf([u.tokens for u in units])
        vecs = [tfidf.vec(u.tokens) for u in units]
        sims = [0.0] * (n - 1)
        for gap in range(n - 1):
            left: dict[str, float] = {}
            right: dict[str, float] = {}
            for v in vecs[max(0, gap - window + 1):gap + 1]:
                _add(left, v)
            for v in vecs[gap + 1:gap + 1 + window]:
                _add(right, v)
            sims[gap] = _cos(_normalize(left), _normalize(right))
        depth = [0.0] * (n - 1)
        for g in range(n - 1):
            lp = sims[g]
            for x in range(g, -1, -1):
                if sims[x] < lp:
                    break
                lp = sims[x]
            rp = sims[g]
            for x in range(g, n - 1):
                if sims[x] < rp:
                    break
                rp = sims[x]
            depth[g] = (lp - sims[g]) + (rp - sims[g])
        mean = sum(depth) / len(depth)
        sd = math.sqrt(sum((d - mean) ** 2 for d in depth) / len(depth))
        boosted = []
        for g in range(n - 1):
            b = depth[g]
            explicit = _explicit_shift(units[g + 1])
            paused = units[g + 1].start - units[g].end >= STRONG_PAUSE
            if explicit:
                b += 0.3
            if units[g + 1].start - units[g].end >= LONG_PAUSE:
                b += 0.2
            boosted.append((b, g, explicit, paused))
        cutoff = mean + 0.5 * sd
        strong_cut = mean + STRONG_SD * sd
        enough = n >= STRONG_MIN_UNITS
        for b, g, explicit, paused in sorted(boosted, reverse=True):
            if b < cutoff:
                break
            t = units[g].end
            if t - units[0].start < min_seconds * 0.5 or units[-1].end - t < min_seconds * 0.5:
                continue
            if all(abs(t - units[x].end) >= min_seconds for x, _ in bounds):
                # "חזק" (שער "נושא אחד"): מעבר מפורש, או ירידה עמוקה בדמיון וגם
                # הפסקה ממשית – ירידה בדמיון לבדה בדיבור חופשי היא לרוב רעש
                bounds.append((g, explicit or (enough and paused and b >= strong_cut)))
        bounds.sort()

    topics: list[Topic] = []
    prev, strong = 0, False
    for g, st in bounds + [(n - 1, False)]:
        topics.append(Topic(start_idx=prev, end_idx=g, start=units[prev].start,
                            end=units[g].end, strong_start=strong))
        prev, strong = g + 1, st
    for t in topics:
        _describe(t, units)
    return topics


def _describe(t: Topic, units: Sequence[Unit]) -> None:
    part = units[t.start_idx: t.end_idx + 1]
    arguing = [u for u in part if _arguing(u)]
    t.argument = len(arguing) / max(1, len(part))
    counts: dict[str, int] = {}
    for u in part:
        for tok in u.tokens:
            counts[tok] = counts.get(tok, 0) + 1
    t.terms = [w for w, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])) if c >= 2][:5]
    if len(arguing) < ARGUMENT_MIN_UNITS or t.argument < ARGUMENT_MIN_DENSITY:
        return
    # הרגע החזק בנושא: עמדה/הכרעה עם תוכן, לא צעקה
    best: list[tuple[float, int]] = []
    for k in range(t.start_idx, t.end_idx + 1):
        u = units[k]
        if not _arguing(u):
            continue
        v, why = payoff_potential(u, units[k + 1] if k + 1 < len(units) else None)
        if has_semantic_payoff(why):
            best.append((v, k))
    best.sort(reverse=True)
    picked: list[int] = []
    for _, k in best:
        if all(abs(units[k].start - units[j].start) >= 15.0 for j in picked):
            picked.append(k)
        if len(picked) >= SEEDS_PER_TOPIC:
            break
    t.seeds = sorted(picked)


def topic_seeds(topics: Sequence[Topic]) -> list[int]:
    return [k for t in topics for k in t.seeds]


def strong_bounds(topics: Sequence[Topic]) -> list[float]:
    """זמני הגבולות החזקים בלבד – לשער "נושא אחד"."""
    return [t.start for t in topics[1:] if t.strong_start]


def topic_at(topics: Sequence[Topic], t: float) -> Optional[Topic]:
    return next((x for x in topics if x.start <= t <= x.end), None)
