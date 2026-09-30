"""
הסרת כפילויות: לפי זמן, ולפי תוכן.

שני קליפים חופפים בזמן – נשאר החזק. שני קליפים שמספרים את אותו הדבר
(סטרימר שחוזר על אותה בדיחה אחרי עשרים דקות, או מסביר שוב את אותו
הדבר) – נשאר החזק, גם אם הם רחוקים זה מזה בשידור.

דמיון תוכן: TF-IDF על מילות התוכן (בלי מילות עצירה ומילוי, בלי
תחיליות דבוקות בעברית), קוסינוס בין הקליפים.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Optional, Sequence

from .score import Scored, rank_key
from .units import Unit

# חפיפה בזמן שמעליה שני קליפים נחשבים אותו רגע (שניות)
MAX_TIME_OVERLAP = 1.0
# דמיון תוכן שמעליו שני קליפים נחשבים אותו סיפור
SEMANTIC_DUPLICATE = 0.50
# פחות מילות תוכן מזה – אין מספיק טקסט כדי לקבוע דמיון
MIN_TOKENS = 8


@dataclass
class Removal:
    removed: Scored
    kept: Scored
    kind: str             # time_overlap | same_content
    similarity: float = 0.0


class TfIdf:
    def __init__(self, docs: Sequence[Sequence[str]]) -> None:
        self.n = max(1, len(docs))
        df: Counter[str] = Counter()
        for d in docs:
            df.update(set(d))
        self.idf = {t: math.log((1 + self.n) / (1 + c)) + 1.0 for t, c in df.items()}

    def vec(self, tokens: Sequence[str]) -> dict[str, float]:
        tf = Counter(tokens)
        v = {t: c * self.idf.get(t, 1.0) for t, c in tf.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {t: x / norm for t, x in v.items()}


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return float(sum(v * b.get(t, 0.0) for t, v in a.items()))


def tokens_of(sc: Scored, units: Sequence[Unit]) -> list[str]:
    p = sc.proposal
    return [t for u in units[p.hook_idx: p.end_idx + 1] for t in u.tokens]


def dedupe(items: Sequence[Scored], units: Sequence[Unit], *,
           unit_docs: Optional[Sequence[Sequence[str]]] = None
           ) -> tuple[list[Scored], list[Removal]]:
    """מחזיר (הנשארים לפי סדר איכות, רשימת ההסרות עם הסיבה)."""
    ordered = sorted(items, key=rank_key, reverse=True)
    tfidf = TfIdf(unit_docs if unit_docs is not None else [u.tokens for u in units])
    toks = {id(s): tokens_of(s, units) for s in ordered}
    vecs = {id(s): tfidf.vec(toks[id(s)]) for s in ordered}

    kept: list[Scored] = []
    removed: list[Removal] = []
    for s in ordered:
        hit: Optional[Removal] = None
        for k in kept:
            inter = min(s.end, k.end) - max(s.start, k.start)
            if inter > MAX_TIME_OVERLAP:
                hit = Removal(removed=s, kept=k, kind="time_overlap")
                break
            if len(toks[id(s)]) >= MIN_TOKENS and len(toks[id(k)]) >= MIN_TOKENS:
                sim = cosine(vecs[id(s)], vecs[id(k)])
                if sim >= SEMANTIC_DUPLICATE:
                    hit = Removal(removed=s, kept=k, kind="same_content", similarity=round(sim, 3))
                    break
        if hit is None:
            kept.append(s)
        else:
            removed.append(hit)
    return kept, removed
