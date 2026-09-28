"""
Long-Form: משידור ארוך (למשל 4 שעות) לסרטון של 10–30 דקות.

זה **אלגוריתם אחר** מבחירת Shorts. שורטים מחפשים רגעי שיא בודדים;
כאן בונים סרטון אחד שנשמע כמו סיפור רציף:

  1. יחידות של משפטים מהתמלול.
  2. אוויר מת – שתיקות, קטעים בלי דיבור, AFK/BRB – יוצא תמיד.
  3. יחידות של מילוי בלבד, וחזרות כמעט-מילוליות (shingles + Jaccard),
     יוצאות. נשמר המופע הראשון.
  4. חלוקה לנושאים בשיטת TextTiling: דמיון TF-IDF בין חלונות סמוכים
     ו-depth score בכל גבול.
  5. רלוונטיות לנושא המרכזי: דמיון קוסינוס בין הנושא לבין ה-centroid של
     כל הנושאים. נושא רחוק שיש בו סימני סטייה (חסות, תקלות טכניות, „אני
     מביא מים") יוצא.
  6. עניין: ציון ה-timeline, צפיפות הדיבור וסימנים סמנטיים.
  7. בחירה בתקציב הזמן: תקציב לכל נושא ביחס לגודלו ולערכו, ובתוך הנושא
     החלון הרציף הטוב ביותר, מיושר לגבולות משפט ועם משפט הקשר לפניו.
     סדר כרונולוגי, מיזוג פערים קטנים, השמטת שברים.
  8. פרקים לפי נושא, עם כותרת מתוך מה שנאמר.

אין כאן מודל שפה: הכול דטרמיניסטי, מוסבר ומהיר (תמלול של 4 שעות
מתוכנן בשניות). כשהמקור דל מהיעד, התוצאה קצרה יותר וה-stats אומרים
למה – לא ממלאים בחומר חלש כדי „להגיע למספר".
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Optional, Sequence

from .. import i18n
from ..util.text import format_duration_he
from . import lang as _lang

REASONS = ("dead_air", "off_topic", "repetition", "low_interest", "filler")
_STRONG_END = re.compile(r"[.!?…׃]+[\"'”״»)\]]*$")
_TOKEN = re.compile(r"[\w'֐-׿]+", re.UNICODE)


# --------------------------------------------------------------------------
# נתונים
# --------------------------------------------------------------------------
@dataclass
class LongformSettings:
    target_seconds: float = 900.0
    tolerance: float = 0.12              # עמידה ביעד ± 12%
    min_section_seconds: float = 12.0    # קטע קצר מזה לא נכנס לבד
    merge_gap_seconds: float = 3.0       # פער קטן מזה בין קטעים נבלע
    context_lead_seconds: float = 6.0    # כמה הקשר להוסיף לפני חלון
    dead_air_min_seconds: float = 2.0    # שתיקה מזה ומעלה יוצאת
    keep_pause_seconds: float = 0.35     # שתיקה בתוך חומר שנשמר מקוצרת לזה
    max_sections: int = 40
    language: Optional[str] = None
    # TextTiling
    tiling_window: int = 6               # יחידות בכל צד של גבול
    min_topic_seconds: float = 45.0
    repetition_jaccard: float = 0.6
    shingle_size: int = 3
    off_topic_relevance: float = 0.12    # מתחת לזה (ועם סימני סטייה) – יוצא

    @classmethod
    def from_any(cls, data: Optional[dict[str, Any]] = None, **kw: Any) -> "LongformSettings":
        vals = {k: v for k, v in {**(data or {}), **kw}.items()
                if k in cls.__dataclass_fields__ and v is not None}
        return cls(**vals)


@dataclass
class Unit:
    """משפט אחד (או חלק ממקטע תמלול ארוך)."""

    idx: int
    start: float
    end: float
    text: str
    tokens: list[str] = field(default_factory=list)
    words: int = 0
    filler_ratio: float = 0.0
    removed: str = ""                    # סיבת הסרה, אם הוסר

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class Section:
    start: float
    end: float
    score: float = 0.0
    topic: int = 0
    title: str = ""
    reasons: list[str] = field(default_factory=list)
    lead_in: float = 0.0                 # כמה שניות הקשר נוספו לפני החלון

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["start"], d["end"] = round(self.start, 3), round(self.end, 3)
        d["score"] = round(self.score, 4)
        d["lead_in"] = round(self.lead_in, 3)
        return d


@dataclass
class Chapter:
    start: float                          # זמן בפלט
    title: str
    source_start: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"start": round(self.start, 3), "title": self.title,
                "source_start": round(self.source_start, 3)}


@dataclass
class LongformPlan:
    segments: list[tuple[float, float]] = field(default_factory=list)
    # לכל חלק: טווחי הדיבור בתוכו (זמני מקור), כשהשתיקות ביניהם מקוצרות
    # ל-keep_pause. הרינדור חותך לפיהם בתוך החלק, בלי מעבר (fade) ביניהם.
    beats: list[list[tuple[float, float]]] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    removed: list[dict[str, Any]] = field(default_factory=list)
    chapters: list[Chapter] = field(default_factory=list)
    output_seconds: float = 0.0
    stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "segments": [[round(a, 3), round(b, 3)] for a, b in self.segments],
            "beats": [[[round(a, 3), round(b, 3)] for a, b in seg] for seg in self.beats],
            "sections": [s.to_dict() for s in self.sections],
            "removed": [dict(r) for r in self.removed],
            "chapters": [c.to_dict() for c in self.chapters],
            "output_seconds": round(self.output_seconds, 3),
            "stats": dict(self.stats),
        }

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]]) -> "LongformPlan":
        data = data or {}
        return cls(
            segments=[(float(a), float(b)) for a, b in data.get("segments") or []],
            beats=[[(float(a), float(b)) for a, b in seg] for seg in data.get("beats") or []],
            sections=[Section(**{k: v for k, v in s.items()
                                 if k in Section.__dataclass_fields__})
                      for s in data.get("sections") or []],
            removed=[dict(r) for r in data.get("removed") or []],
            chapters=[Chapter(**{k: v for k, v in c.items()
                                 if k in Chapter.__dataclass_fields__})
                      for c in data.get("chapters") or []],
            output_seconds=float(data.get("output_seconds") or 0.0),
            stats=dict(data.get("stats") or {}),
        )

    def removed_seconds(self) -> dict[str, float]:
        out: dict[str, float] = {r: 0.0 for r in REASONS}
        for r in self.removed:
            out[r["reason"]] = out.get(r["reason"], 0.0) + float(r["end"]) - float(r["start"])
        return {k: round(v, 2) for k, v in out.items()}

    def explain(self, lang: Optional[str] = None) -> list[str]:
        """הסבר קריא של התכנית, בשפת הממשק."""
        st = self.stats
        lines = [i18n.tr("longform.explain.summary", lang,
                         source=format_duration_he(st.get("source_seconds", 0)),
                         output=format_duration_he(self.output_seconds),
                         parts=len(self.segments), chapters=len(self.chapters),
                         target=format_duration_he(st.get("target_seconds", 0)))]
        if st.get("no_transcript"):
            lines.append(i18n.tr("longform.explain.no_transcript", lang))
        if st.get("short_source"):
            lines.append(i18n.tr("longform.explain.short_source", lang,
                                 available=format_duration_he(st.get("available_seconds", 0))))
        for reason, secs in self.removed_seconds().items():
            if secs >= 1.0:
                lines.append(i18n.tr("longform.explain.removed", lang,
                                     reason=i18n.tr(f"longform.reason.{reason}", lang),
                                     seconds=format_duration_he(secs)))
        for s in self.sections:
            lines.append(i18n.tr("longform.explain.section", lang,
                                 start=format_duration_he(s.start),
                                 end=format_duration_he(s.end), title=s.title,
                                 score=f"{s.score:.2f}"))
        usable = [c for i, c in enumerate(self.chapters)
                  if (self.chapters[i + 1].start if i + 1 < len(self.chapters)
                      else self.output_seconds) - c.start >= 10.0]
        if len(usable) < 3:
            lines.append(i18n.tr("longform.explain.chapters_note", lang, n=len(usable)))
        return lines

    def youtube_chapters_text(self) -> str:
        """שורות פרקים לתיאור ב-YouTube: „0:00 כותרת"."""
        rows = []
        for i, c in enumerate(self.chapters):
            t = 0.0 if i == 0 else c.start
            rows.append(f"{format_duration_he(t)} {c.title}")
        return "\n".join(rows)


# --------------------------------------------------------------------------
# יחידות
# --------------------------------------------------------------------------
def _tokens(text: str, packs: Sequence[Any]) -> list[str]:
    """מילות תוכן: בלי מילות עצירה, ובעברית בלי תחיליות דבוקות."""
    out: list[str] = []
    for tok in _TOKEN.findall((text or "").lower()):
        if len(tok) < 2 or tok.isdigit():
            continue
        if any(tok in p.stop_words for p in packs):
            continue
        stem = tok
        for p in packs:
            if p.prefixes:
                s = p.strip_prefix(tok)
                if s and s not in p.stop_words:
                    stem = s
                    break
        out.append(stem)
    return out


def _filler_ratio(text: str, packs: Sequence[Any]) -> float:
    toks = _TOKEN.findall((text or "").lower())
    if not toks:
        return 1.0
    fill = sum(1 for t in toks if any(t in p.filler_tokens for p in packs))
    fill += sum(len(ph.split()) * p.count(text, [ph]) for p in packs for ph in p.filler_phrases)
    return min(1.0, fill / len(toks))


def build_units(transcript, packs: Sequence[Any], *, max_unit_seconds: float = 20.0
                ) -> list[Unit]:
    """משפטים מהתמלול: מקטע ארוך נחתך בסוף משפט (לפי תזמון המילים)."""
    units: list[Unit] = []

    def add(start: float, end: float, text: str) -> None:
        text = " ".join(text.split())
        if not text or end <= start:
            return
        units.append(Unit(idx=len(units), start=start, end=end, text=text,
                          tokens=_tokens(text, packs), words=len(text.split()),
                          filler_ratio=_filler_ratio(text, packs)))

    for seg in getattr(transcript, "segments", None) or []:
        words = [w for w in (seg.words or []) if (w.text or "").strip()]
        if not words or seg.duration <= max_unit_seconds:
            add(float(seg.start), float(seg.end), seg.text)
            continue
        bucket: list[Any] = []
        for w in words:
            bucket.append(w)
            span = bucket[-1].end - bucket[0].start
            if (_STRONG_END.search(w.text.strip()) and span >= 3.0) or span >= max_unit_seconds:
                add(bucket[0].start, bucket[-1].end, " ".join(x.text for x in bucket))
                bucket = []
        if bucket:
            add(bucket[0].start, bucket[-1].end, " ".join(x.text for x in bucket))
    units.sort(key=lambda u: u.start)
    for i, u in enumerate(units):
        u.idx = i
    return units


# --------------------------------------------------------------------------
# TF-IDF
# --------------------------------------------------------------------------
class _Tfidf:
    def __init__(self, docs: Sequence[Sequence[str]]) -> None:
        df: Counter[str] = Counter()
        for d in docs:
            df.update(set(d))
        n = max(1, len(docs))
        self.idf = {t: math.log((1 + n) / (1 + c)) + 1.0 for t, c in df.items()}

    def vec(self, tokens: Iterable[str]) -> dict[str, float]:
        tf = Counter(tokens)
        v = {t: (1.0 + math.log(c)) * self.idf.get(t, 1.0) for t, c in tf.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {t: x / norm for t, x in v.items()}


def _cos(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(t, 0.0) for t, x in a.items())


def _add(acc: dict[str, float], v: dict[str, float], w: float = 1.0) -> None:
    for t, x in v.items():
        acc[t] = acc.get(t, 0.0) + x * w


def _normalize(v: dict[str, float]) -> dict[str, float]:
    norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
    return {t: x / norm for t, x in v.items()}


# --------------------------------------------------------------------------
# שלבי האלגוריתם
# --------------------------------------------------------------------------
def _mark_dead_air(units: list[Unit], packs: Sequence[Any]) -> None:
    for u in units:
        if u.removed:
            continue
        if any(p.count(u.text, p.afk) for p in packs) and u.words <= 14:
            u.removed = "dead_air"


def _mark_filler(units: list[Unit]) -> None:
    for u in units:
        if not u.removed and (u.filler_ratio >= 0.6 or not u.tokens) and u.words <= 12:
            u.removed = "filler"


def _shingles(tokens: Sequence[str], k: int) -> set[tuple[str, ...]]:
    if len(tokens) < k:
        return {tuple(tokens)} if tokens else set()
    return {tuple(tokens[i:i + k]) for i in range(len(tokens) - k + 1)}


def _mark_repetition(units: list[Unit], settings: LongformSettings) -> None:
    """חזרה כמעט-מילולית על יחידה קודמת: נשמר המופע הראשון."""
    index: dict[tuple[str, ...], list[int]] = defaultdict(list)
    shingles: dict[int, set[tuple[str, ...]]] = {}
    for u in units:
        if u.removed or len(u.tokens) < 4:
            continue
        sh = _shingles(u.tokens, settings.shingle_size)
        cands: Counter[int] = Counter()
        for s in sh:
            for j in index.get(s, ()):
                cands[j] += 1
        for j, common in cands.most_common(5):
            other = shingles[j]
            jac = common / max(1, len(sh | other))
            if jac >= settings.repetition_jaccard and u.start - units[j].end > 20.0:
                u.removed = "repetition"
                break
        if not u.removed:
            shingles[u.idx] = sh
            for s in sh:
                index[s].append(u.idx)


def _tile(kept: list[Unit], tfidf: _Tfidf, settings: LongformSettings) -> list[list[Unit]]:
    """TextTiling: גבולות נושא לפי ירידה בדמיון בין חלונות סמוכים."""
    n = len(kept)
    if n < 2 * settings.tiling_window + 1:
        return [kept] if kept else []
    vecs = [tfidf.vec(u.tokens) for u in kept]
    w = settings.tiling_window
    # חלונות מצטברים: סכום וקטורים משמאל ומימין לכל פער
    sims = [0.0] * (n - 1)
    for gap in range(n - 1):
        left: dict[str, float] = {}
        right: dict[str, float] = {}
        for v in vecs[max(0, gap - w + 1):gap + 1]:
            _add(left, v)
        for v in vecs[gap + 1:gap + 1 + w]:
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
    cutoff = mean + 0.5 * sd
    # פער זמן גדול (אוויר מת שהוסר) הוא גבול טבעי
    cands = sorted(((depth[g] + (0.5 if kept[g + 1].start - kept[g].end > 30 else 0.0), g)
                    for g in range(n - 1)), reverse=True)
    # לא יותר נושאים ממה שהתקציב יכול לכסות בצורה משמעותית
    max_topics = max(2, int(settings.target_seconds / (3.0 * settings.min_section_seconds)))
    bounds: list[int] = []
    for d, g in cands:
        if d < cutoff or len(bounds) >= max_topics - 1:
            break
        t = kept[g].end
        if all(abs(t - kept[b].end) >= settings.min_topic_seconds for b in bounds):
            bounds.append(g)
    bounds.sort()
    topics, prev = [], 0
    for g in bounds:
        topics.append(kept[prev:g + 1])
        prev = g + 1
    topics.append(kept[prev:])
    return [t for t in topics if t]


def _interest(units: Sequence[Unit], timeline, packs: Sequence[Any]) -> float:
    """ציון עניין 0..1 לקבוצת יחידות: timeline, צפיפות דיבור וסימנים סמנטיים."""
    if not units:
        return 0.0
    start, end = units[0].start, units[-1].end
    dur = max(1.0, end - start)
    tl = 0.0
    if timeline is not None and getattr(timeline, "n", 0):
        try:
            tl = float(timeline.window_mean(timeline.score, start, end))
        except Exception:                           # noqa: BLE001
            tl = 0.0
    words = sum(u.words for u in units)
    density = min(1.0, (words / dur) / 3.0)          # ~3 מילים בשנייה = מלא
    text = " ".join(u.text for u in units)
    lex = 0.0
    for p in packs:
        hits = sum(1 for ph in p.lexicon if p.pattern(ph).search(p.normalizer(text)))
        hits += p.count(text, p.emotion, skip_negated=True) + p.count(text, p.hook)
        lex = max(lex, min(1.0, hits / max(1.0, dur / 30.0) / 3.0))
    return 0.45 * tl + 0.3 * density + 0.25 * lex


def _best_window(units: list[Unit], budget: float, timeline, packs: Sequence[Any]
                 ) -> tuple[int, int]:
    """החלון הרציף (אינדקסים [i, j)) בתוך הנושא שנכנס לתקציב עם העניין הגבוה."""
    n = len(units)
    best, best_ij, j = -1.0, (0, 1), 0
    scores = [_interest([u], timeline, packs) * max(0.1, u.duration) for u in units]
    acc = 0.0
    for i in range(n):
        if j < i:
            j, acc = i, 0.0
        while j < n and units[j].end - units[i].start <= budget:
            acc += scores[j]
            j += 1
        if j > i:
            # לתת עדיפות לחלון שמסתיים בסוף משפט
            end_bonus = 1.05 if _STRONG_END.search(units[j - 1].text) else 1.0
            val = acc * end_bonus
            if val > best:
                best, best_ij = val, (i, j)
            acc -= scores[i]
        else:
            j = i + 1
    return best_ij


def _title_for(units: Sequence[Unit], tfidf: _Tfidf, n: int, lang: Optional[str],
               packs: Sequence[Any]) -> str:
    """כותרת פרק מתוך מה שנאמר: קטע מהמשפט הכי „נושאי" בפרק."""
    agg: dict[str, float] = {}
    for u in units:
        _add(agg, tfidf.vec(u.tokens))
    top = {t for t, _ in sorted(agg.items(), key=lambda kv: -kv[1])[:6]}
    best, best_score = None, 0.0
    for u in units:
        if not u.tokens or u.filler_ratio > 0.4:
            continue
        hit = len(top & set(u.tokens)) / math.sqrt(len(u.tokens))
        if hit > best_score:
            best, best_score = u, hit
    if best is None or best_score <= 0:
        return i18n.tr("longform.chapter.fallback", lang, n=n)
    words = best.text.split()
    # מדלגים על מילות מילוי ופתיחות בתחילת המשפט
    while words and any(p.is_hanging(words[0]) or words[0].strip(",.").lower() in p.filler_tokens
                        or words[0].strip(",.").lower() in p.stop_words for p in packs):
        words = words[1:]
    title = " ".join(words[:7]).strip(" ,.;:–-")
    if len(words) > 7:
        title += "…"
    return title or i18n.tr("longform.chapter.fallback", lang, n=n)


def _merge_spans(spans: list[tuple[float, float]], gap: float) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for a, b in sorted(spans):
        if out and a - out[-1][1] <= gap:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _removed_spans(units: list[Unit], duration: float, kept_spans: list[tuple[float, float]],
                   dead_air_min: float) -> list[dict[str, Any]]:
    """מה לא נכנס ולמה – כולל רווחים בלי דיבור בכלל (אוויר מת)."""
    out: list[dict[str, Any]] = []
    prev_end = 0.0
    for u in units:
        if u.start - prev_end >= dead_air_min:
            out.append({"start": round(prev_end, 3), "end": round(u.start, 3),
                        "reason": "dead_air"})
        prev_end = max(prev_end, u.end)
        if u.removed:
            out.append({"start": round(u.start, 3), "end": round(u.end, 3),
                        "reason": u.removed})
    if duration - prev_end >= dead_air_min:
        out.append({"start": round(prev_end, 3), "end": round(duration, 3),
                    "reason": "dead_air"})
    # יחידות שלא הוסרו אבל גם לא נבחרו – עניין נמוך
    for u in units:
        if u.removed:
            continue
        mid = (u.start + u.end) / 2
        if not any(a <= mid <= b for a, b in kept_spans):
            out.append({"start": round(u.start, 3), "end": round(u.end, 3),
                        "reason": "low_interest"})
    # מאחדים רצפים סמוכים עם אותה סיבה
    out.sort(key=lambda r: r["start"])
    merged: list[dict[str, Any]] = []
    for r in out:
        if merged and merged[-1]["reason"] == r["reason"] and r["start"] - merged[-1]["end"] < 1.0:
            merged[-1]["end"] = max(merged[-1]["end"], r["end"])
        else:
            merged.append(dict(r))
    return merged


# --------------------------------------------------------------------------
# הכול יחד
# --------------------------------------------------------------------------
def plan_longform(transcript, *, duration: float, silences=None, timeline=None,
                  settings: Optional[LongformSettings] = None,
                  language: Optional[str] = None) -> LongformPlan:
    s = settings or LongformSettings()
    lang = language or s.language or (getattr(transcript, "language", "") or None)
    packs = _lang.packs_for(lang)
    ui_lang = i18n.get_lang()
    target = float(s.target_seconds)
    units = build_units(transcript, packs) if transcript is not None else []

    if not units:
        return _plan_without_transcript(duration, timeline, s)

    _mark_dead_air(units, packs)
    _mark_filler(units)
    _mark_repetition(units, s)
    kept = [u for u in units if not u.removed]
    tfidf = _Tfidf([u.tokens for u in kept] or [[]])
    topics = _tile(kept, tfidf, s)

    # רלוונטיות: centroid משוקלל-זמן של כל הנושאים
    topic_vecs = []
    centroid: dict[str, float] = {}
    for t in topics:
        v: dict[str, float] = {}
        for u in t:
            _add(v, tfidf.vec(u.tokens), max(0.5, u.duration))
        v = _normalize(v)
        topic_vecs.append(v)
        _add(centroid, v, sum(u.duration for u in t))
    centroid = _normalize(centroid)
    info = []
    for ti, (t, v) in enumerate(zip(topics, topic_vecs)):
        rel = _cos(v, centroid)
        text = " ".join(u.text for u in t)
        markers = sum(p.count(text, p.offtopic) for p in packs)
        dur = sum(u.duration for u in t)
        marker_rate = markers / max(1.0, dur / 60.0)
        off = (rel < s.off_topic_relevance and markers > 0) or \
              (marker_rate >= 1.5 and rel < 2.5 * s.off_topic_relevance)
        if off:
            for u in t:
                u.removed = "off_topic"
        info.append({"topic": ti, "units": t, "relevance": rel, "off": off,
                     "interest": _interest(t, timeline, packs), "duration": dur})

    # סטייה קצרה בתוך נושא (חסות, „שומעים אותי?"): משפט עם סימן סטייה
    # שאינו דומה לנושא המרכזי יוצא גם כשהנושא סביבו נשאר
    for x in info:
        if x["off"]:
            continue
        for u in x["units"]:
            if u.removed or not any(p.count(u.text, p.offtopic) for p in packs):
                continue
            if _cos(tfidf.vec(u.tokens), centroid) < 2.0 * s.off_topic_relevance:
                u.removed = "off_topic"
        x["units"] = [u for u in x["units"] if not u.removed]
        x["duration"] = sum(u.duration for u in x["units"])

    live = [x for x in info if not x["off"] and x["units"]]
    available = sum(x["duration"] for x in live)
    # תקציב לכל נושא: לפי גודל × ערך, עם תקרה של אורך הנושא
    budget_total = target
    for x in live:
        x["value"] = x["duration"] * (0.35 + x["interest"]) * (0.5 + min(1.0, x["relevance"]))
    def allocate(pool_in: list[dict[str, Any]]) -> dict[int, float]:
        """תקציב ביחס לערך, עם תקרה של אורך הנושא; עודף עובר לאחרים."""
        out: dict[int, float] = {}
        remaining, pool = budget_total, list(pool_in)
        for _ in range(6):
            total_value = sum(x["value"] for x in pool) or 1.0
            spare, nxt = 0.0, []
            for x in pool:
                want = out.get(x["topic"], 0.0) + remaining * x["value"] / total_value
                if want >= x["duration"]:
                    spare += want - x["duration"]
                    out[x["topic"]] = x["duration"]
                else:
                    out[x["topic"]] = want
                    nxt.append(x)
            remaining, pool = spare, nxt
            if spare < 1.0 or not pool:
                break
        return out

    # נושא שמקבל פחות ממקטע מינימלי – החלש שבהם יוצא והתקציב מתחלק מחדש
    chosen = sorted(live, key=lambda x: -x["value"])
    alloc = allocate(chosen)
    while len(chosen) > 1 and min(alloc[x["topic"]] for x in chosen) < 2 * s.min_section_seconds:
        chosen.pop()
        alloc = allocate(chosen)

    # חלון רציף בכל נושא, עם הקשר, מיושר למשפטים
    windows: list[tuple[float, float, int, float]] = []   # start, end, topic, lead
    for x in live:
        b = alloc.get(x["topic"], 0.0)
        if b < s.min_section_seconds:
            continue
        t = [u for u in x["units"] if not u.removed]
        if not t:
            continue
        i, j = _best_window(t, b, timeline, packs)
        lead = 0.0
        if i > 0 and t[i].start - t[i - 1].start <= s.context_lead_seconds + t[i - 1].duration \
                and t[j - 1].end - t[i - 1].start <= b + s.context_lead_seconds:
            lead = t[i].start - t[i - 1].start
            i -= 1
        a, e = t[i].start, t[j - 1].end
        if e - a >= s.min_section_seconds:
            windows.append((a, e, x["topic"], lead))

    # לא יותר מ-max_sections: משמיטים את החלשים
    if len(windows) > s.max_sections:
        ranked = sorted(windows, key=lambda w: -info[w[2]]["interest"] * (w[1] - w[0]))
        windows = sorted(ranked[:s.max_sections])
    windows.sort()

    # חלקי הפלט: היחידות שנשמרו בתוך החלונות, בלי אוויר מת פנימי
    kept_units = [u for u in units if not u.removed]
    raw_spans: list[tuple[float, float]] = []
    sections: list[Section] = []
    for a, e, ti, lead in windows:
        inside = [u for u in kept_units if u.start >= a - 1e-6 and u.end <= e + 1e-6]
        if not inside:
            continue
        spans = []
        for u in inside:
            ua, ue = u.start - s.keep_pause_seconds / 2, u.end + s.keep_pause_seconds / 2
            spans.append((max(0.0, ua), min(duration, ue)))
        raw_spans.extend(_merge_spans(spans, s.dead_air_min_seconds))
        x = info[ti]
        reasons = []
        if x["interest"] >= 0.5:
            reasons.append("interest")
        if x["relevance"] >= 0.5:
            reasons.append("main_topic")
        sections.append(Section(start=a, end=e, topic=ti, lead_in=lead,
                                score=round(x["interest"] * (0.5 + min(1.0, x["relevance"])), 4),
                                reasons=reasons or ["coverage"],
                                title=_title_for(inside, tfidf, len(sections) + 1, ui_lang
                                                 if not lang else lang, packs)))
    spoken = [(a, b) for a, b in _merge_spans(raw_spans, s.keep_pause_seconds)
              if b - a >= 0.3]
    segments = [(a, b) for a, b in _merge_spans(spoken, s.merge_gap_seconds) if b - a >= 1.0]
    beats = [_capped([(x, y) for x, y in spoken if x >= a - 1e-6 and y <= b + 1e-6])
             for a, b in segments]
    flat = [bt for seg in beats for bt in seg]
    output = sum(b - a for a, b in flat)

    # פרקים: תחילת כל נושא בזמן הפלט
    chapters: list[Chapter] = []
    for sec in sections:
        out_t = _output_time(flat, sec.start)
        if out_t is None:
            continue
        if chapters and out_t - chapters[-1].start < 10.0:
            continue
        chapters.append(Chapter(start=round(out_t, 3), title=sec.title,
                                source_start=sec.start))
    if chapters:
        chapters[0].start = 0.0

    kept_spans = segments
    removed = _removed_spans(units, duration, kept_spans, s.dead_air_min_seconds)
    lo, hi = target * (1 - s.tolerance), target * (1 + s.tolerance)
    stats = {
        "source_seconds": round(duration, 2),
        "speech_seconds": round(sum(u.duration for u in units), 2),
        "available_seconds": round(available, 2),
        "target_seconds": round(target, 2),
        "output_seconds": round(output, 2),
        "within_tolerance": lo <= output <= hi,
        "short_source": output < lo,
        "topics": len(topics),
        "topics_off": sum(1 for x in info if x["off"]),
        "sections": len(sections),
        "units": len(units),
        "removed_seconds": {},
        "language": lang or "",
    }
    plan = LongformPlan(segments=segments, beats=beats, sections=sections, removed=removed,
                        chapters=chapters, output_seconds=round(output, 3), stats=stats)
    plan.stats["removed_seconds"] = plan.removed_seconds()
    return plan


def _capped(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """תקרת ביטים לחלק (באג 15) – כבר בתכנון, כדי שזמני הפרקים יהיו מדויקים."""
    from .editing import Beat, cap_beats

    capped = cap_beats([Beat(src_start=a, src_end=b) for a, b in spans])
    return [(bt.src_start, bt.src_end) for bt in capped]


def _output_time(segments: list[tuple[float, float]], src: float) -> Optional[float]:
    acc = 0.0
    for a, b in segments:
        if src < a:
            return acc
        if src <= b:
            return acc + src - a
        acc += b - a
    return None


def _plan_without_transcript(duration: float, timeline, s: LongformSettings) -> LongformPlan:
    """
    בלי תמלול אין נושאים. בוחרים חלונות לפי ציון ה-timeline בלבד, בסדר
    כרונולוגי, ואומרים זאת במפורש ב-stats.
    """
    segments: list[tuple[float, float]] = []
    if timeline is not None and getattr(timeline, "n", 0) and duration > 0:
        win = max(s.min_section_seconds, 30.0)
        cands = []
        t = 0.0
        while t + win <= duration:
            cands.append((float(timeline.window_mean(timeline.score, t, t + win)), t))
            t += win
        cands.sort(reverse=True)
        chosen, total = [], 0.0
        for score, t0 in cands:
            if total + win > s.target_seconds * (1 + s.tolerance):
                continue
            chosen.append((t0, t0 + win))
            total += win
            if total >= s.target_seconds:
                break
        segments = _merge_spans(chosen, s.merge_gap_seconds)
    output = sum(b - a for a, b in segments)
    return LongformPlan(
        segments=segments, beats=[[seg] for seg in segments], sections=[Section(start=a, end=b, score=0.0, topic=i,
                                             title=i18n.tr("longform.chapter.fallback", n=i + 1),
                                             reasons=["timeline"])
                                     for i, (a, b) in enumerate(segments)],
        removed=[], chapters=[], output_seconds=output,
        stats={"source_seconds": round(duration, 2), "target_seconds": s.target_seconds,
               "output_seconds": round(output, 2), "no_transcript": True,
               "short_source": output < s.target_seconds * (1 - s.tolerance),
               "available_seconds": round(output, 2),
               "within_tolerance": abs(output - s.target_seconds)
               <= s.target_seconds * s.tolerance})
