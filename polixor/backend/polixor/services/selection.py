"""
בחירת רגעים והרכבת קליפים.

שלושה שלבים:
  1. איתור שיאים בציר הזמן המשולב.
  2. הרחבת כל שיא לחלון עם גבולות טבעיים (סוף משפט / שקט), בטווח
     האורך שהמשתמש ביקש, עם שמירת שניות הקשר לפני ואחרי.
  3. סינון כפילויות וחפיפות (NMS), ודירוג סופי.

לקליפים ארוכים מתבצעת בדיקה נוספת של "קשת סיפורית": התחלה שבונה,
שיא באמצע וסיום – כדי לא לחתוך קטעים אקראיים באורך הנכון.

הכותרות והתיאורים נגזרים מהטקסט שנאמר בפועל. אין המצאת כותרות
שאינן תואמות את התוכן, ואין הבטחות לביצועים.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from .. import i18n
from ..config import AppSettings
from .scoring import Timeline, classify_text, lexical_score
from .transcribe import TranscriptResult
from ..util.text import truncate

log = logging.getLogger("polixor.selection")


@dataclass
class Candidate:
    start: float
    end: float
    peak_time: float
    score: float
    kind: str = "short"                 # short | long | highlights
    title: str = ""
    description: str = ""
    reason: str = ""
    category: str = "moment"
    signals: dict[str, float] = field(default_factory=dict)
    segments: list[tuple[float, float]] = field(default_factory=list)  # ל-highlights
    title_source: str = "heuristic"     # heuristic | transcript | llm
    # פירוט הציון ממנוע clip_intel (רכיבים, קנסות, וו ופאנץ'); ריק במנוע הישן
    quality: dict[str, Any] = field(default_factory=dict)

    @property
    def duration(self) -> float:
        if self.segments:
            return sum(max(0.0, e - s) for s, e in self.segments)
        return max(0.0, self.end - self.start)

    def overlaps(self, other: "Candidate") -> float:
        """IoU על ציר הזמן."""
        inter = max(0.0, min(self.end, other.end) - max(self.start, other.start))
        if inter <= 0:
            return 0.0
        union = (self.end - self.start) + (other.end - other.start) - inter
        return inter / union if union > 0 else 0.0


# --------------------------------------------------------------------------
# איתור שיאים
# --------------------------------------------------------------------------
def find_peaks(tl: Timeline, *, min_distance_seconds: float,
               sensitivity: float, max_peaks: int = 200) -> list[int]:
    """מאתר מקסימומים מקומיים מעל סף שנגזר מהרגישות."""
    score = tl.score
    n = score.size
    if n < 3:
        return []

    # סף: רגישות גבוהה => אחוזון נמוך => יותר מועמדים
    pct = 96.0 - 30.0 * float(np.clip(sensitivity, 0.0, 1.0))   # 96 .. 66
    threshold = float(np.percentile(score, pct))
    floor = float(score.mean() + 0.35 * score.std())
    threshold = max(threshold, floor, 0.06)

    min_dist = max(1, int(round(min_distance_seconds / max(1e-6, tl.hop))))

    # מקסימומים מקומיים
    left = np.concatenate([[score[0]], score[:-1]])
    right = np.concatenate([score[1:], [score[-1]]])
    local = (score >= left) & (score >= right) & (score >= threshold)
    idxs = np.flatnonzero(local)
    if idxs.size == 0:
        # אין שיא מובהק – ניקח את הנקודות החזקות ביותר בכל זאת
        idxs = np.argsort(score)[-max_peaks:]

    # מיון לפי ציון, ודחיית שיאים קרובים מדי
    order = idxs[np.argsort(score[idxs])[::-1]]
    chosen: list[int] = []
    for i in order:
        if len(chosen) >= max_peaks:
            break
        if all(abs(int(i) - c) >= min_dist for c in chosen):
            chosen.append(int(i))
    chosen.sort()
    log.info("found %d peaks (threshold=%.3f, pct=%.0f)", len(chosen), threshold, pct)
    return chosen


# --------------------------------------------------------------------------
# גבולות טבעיים
# --------------------------------------------------------------------------
class BoundaryFinder:
    """
    מאתר נקודות חיתוך טבעיות: תחילת/סוף מקטע תמלול, או גבול שקט.
    עדיפות לגבולות דיבור; בהיעדר תמלול – לשקט.
    """

    def __init__(self, transcript: Optional[TranscriptResult],
                 silences: Optional[list[tuple[float, float]]],
                 duration: float) -> None:
        self.duration = duration
        starts: list[float] = []
        ends: list[float] = []
        if transcript and transcript.segments:
            for s in transcript.segments:
                if s.text.strip():
                    starts.append(float(s.start))
                    ends.append(float(s.end))
        if silences:
            for s0, s1 in silences:
                ends.append(float(s0))     # סוף דיבור = תחילת שקט
                starts.append(float(s1))   # תחילת דיבור = סוף שקט
        self.starts = np.asarray(sorted(set(starts)), dtype=np.float32) \
            if starts else np.zeros(0, dtype=np.float32)
        self.ends = np.asarray(sorted(set(ends)), dtype=np.float32) \
            if ends else np.zeros(0, dtype=np.float32)

    def snap_start(self, t: float, tolerance: float = 2.5) -> float:
        return self._snap(self.starts, t, tolerance, prefer_before=True)

    def snap_end(self, t: float, tolerance: float = 2.5) -> float:
        return self._snap(self.ends, t, tolerance, prefer_before=False)

    def _snap(self, arr: np.ndarray, t: float, tol: float,
              prefer_before: bool) -> float:
        if arr.size == 0:
            return t
        diffs = arr - t
        mask = np.abs(diffs) <= tol
        if not mask.any():
            return t
        cands = arr[mask]
        # מעדיפים כיוון שמרחיב את הקליפ (לא חותך דיבור באמצע)
        directional = cands[cands <= t] if prefer_before else cands[cands >= t]
        pool = directional if directional.size else cands
        best = pool[np.argmin(np.abs(pool - t))]
        return float(max(0.0, min(self.duration, best)))


# --------------------------------------------------------------------------
# בניית מועמדים
# --------------------------------------------------------------------------
def build_short_candidates(
    tl: Timeline,
    *,
    transcript: Optional[TranscriptResult],
    boundaries: BoundaryFinder,
    settings: AppSettings,
    limit: int,
    language: Optional[str] = None,
) -> list[Candidate]:
    """בונה מועמדים לשורטים סביב שיאים."""
    if limit <= 0:
        return []

    min_d = float(settings.short_min_seconds)
    max_d = float(settings.short_max_seconds)
    peaks = find_peaks(tl, min_distance_seconds=max(4.0, min_d * 0.6),
                       sensitivity=settings.sensitivity,
                       max_peaks=max(30, limit * 6))

    out: list[Candidate] = []
    for pi in peaks:
        t_peak = pi * tl.hop
        start, end = _grow_window(tl, pi, min_d, max_d, settings)
        start = boundaries.snap_start(max(0.0, start - settings.context_pad_before))
        end = boundaries.snap_end(min(tl.duration, end + settings.context_pad_after))

        # אכיפת טווח האורך אחרי ההצמדה לגבולות
        start, end = _clamp_duration(start, end, min_d, max_d, t_peak, tl.duration)
        if end - start < min(min_d, 3.0):
            continue

        cand = _make_candidate(tl, transcript, start, end, t_peak, kind="short",
                               language=language)
        out.append(cand)

    out = dedupe(out, iou_threshold=0.30, min_gap=1.0)
    out.sort(key=lambda c: c.score, reverse=True)
    return out[:limit]


def build_long_candidates(
    tl: Timeline,
    *,
    transcript: Optional[TranscriptResult],
    boundaries: BoundaryFinder,
    settings: AppSettings,
    limit: int,
    language: Optional[str] = None,
) -> list[Candidate]:
    """
    בונה קליפים ארוכים רציפים: מחפש חלונות עם צפיפות עניין גבוהה,
    ואז מאמת שיש בהם קשת (עלייה → שיא → סיום).
    """
    if limit <= 0 or tl.n == 0:
        return []

    min_d = float(settings.long_min_seconds)
    max_d = float(settings.long_max_seconds)
    if tl.duration < min_d * 0.8:
        log.info("source too short for long clips (%.1fs < %.1fs)", tl.duration, min_d)
        return []

    # סורקים כמה אורכי חלון בין min ל-max
    lengths = sorted({
        round(min_d),
        round(min_d + (max_d - min_d) * 0.33),
        round(min_d + (max_d - min_d) * 0.66),
        round(min(max_d, tl.duration)),
    })
    lengths = [L for L in lengths if L >= min(min_d, tl.duration) and L > 5]

    cum = np.concatenate([[0.0], np.cumsum(tl.score.astype(np.float64))])
    stride = max(1, int(round(2.0 / max(1e-6, tl.hop))))   # צעד של 2 שניות

    scored: list[tuple[float, float, float, float]] = []   # (ציון, start, end, peak)
    for L in lengths:
        wf = int(round(L / tl.hop))
        if wf <= 1 or wf >= tl.n:
            continue
        for i in range(0, tl.n - wf, stride):
            j = i + wf
            mean_score = float((cum[j] - cum[i]) / wf)
            arc = _arc_quality(tl.score[i:j])
            peak_i = i + int(np.argmax(tl.score[i:j]))
            # העדפה לחלון עם קשת ברורה, לא רק ממוצע גבוה
            total = mean_score * (0.65 + 0.35 * arc)
            scored.append((total, i * tl.hop, j * tl.hop, peak_i * tl.hop))

    if not scored:
        return []
    scored.sort(key=lambda x: x[0], reverse=True)

    out: list[Candidate] = []
    for total, s0, e0, tpeak in scored[: max(40, limit * 12)]:
        start = boundaries.snap_start(s0, tolerance=4.0)
        end = boundaries.snap_end(e0, tolerance=4.0)
        start, end = _clamp_duration(start, end, min_d, max_d, tpeak, tl.duration)
        if end - start < min_d * 0.75:
            continue
        cand = _make_candidate(tl, transcript, start, end, tpeak, kind="long",
                               language=language)
        cand.score = float(np.clip(total / max(1e-6, float(tl.score.max())), 0.0, 1.0))
        out.append(cand)
        if len(out) >= limit * 8:
            break

    out = dedupe(out, iou_threshold=0.22, min_gap=5.0)
    out.sort(key=lambda c: c.score, reverse=True)
    return out[:limit]


def build_highlights_candidate(
    tl: Timeline,
    *,
    transcript: Optional[TranscriptResult],
    boundaries: BoundaryFinder,
    settings: AppSettings,
    shorts: list[Candidate],
    language: Optional[str] = None,
) -> Optional[Candidate]:
    """
    מרכיב סרטון Highlights: כמה רגעים שונים מהשידור, מסודרים כרונולוגית,
    עד לאורך המקסימלי שהוגדר לקליפ ארוך.
    """
    if not shorts:
        return None
    target = float(settings.long_max_seconds)
    min_target = float(settings.long_min_seconds)

    picked: list[Candidate] = []
    total = 0.0
    for c in sorted(shorts, key=lambda x: x.score, reverse=True):
        seg_len = c.end - c.start
        if total + seg_len > target:
            continue
        picked.append(c)
        total += seg_len
        if total >= target * 0.92:
            break

    if not picked or total < min(min_target, 20.0):
        return None

    picked.sort(key=lambda c: c.start)
    segments = [(c.start, c.end) for c in picked]
    avg_score = float(np.mean([c.score for c in picked]))
    cats = [c.category for c in picked]
    top_cat = max(set(cats), key=cats.count) if cats else "moment"

    parts = [truncate(c.title, 40) for c in picked[:4] if c.title]
    desc = i18n.tr("analysis.highlights_desc", n=len(picked))
    if parts:
        desc += ": " + " · ".join(parts)

    return Candidate(
        start=picked[0].start, end=picked[-1].end,
        peak_time=picked[0].peak_time, score=avg_score, kind="highlights",
        # כותרת בשפת הדיבור; תיאור ונימוק בשפת הממשק
        title=i18n.tr("analysis.highlights_title", _content_lang(language), n=len(picked)),
        description=truncate(desc, 300),
        reason=i18n.tr("analysis.highlights_reason", n=len(picked)),
        category=top_cat,
        signals=tl.channel_breakdown(picked[0].start, picked[-1].end),
        segments=segments,
    )


# --------------------------------------------------------------------------
# עזרים לבניית חלון
# --------------------------------------------------------------------------
def _grow_window(tl: Timeline, peak_idx: int, min_d: float, max_d: float,
                 settings: AppSettings) -> tuple[float, float]:
    """
    מרחיב חלון סביב השיא כל עוד הציון השולי נשאר משמעותי,
    עד לאורך המקסימלי. כך אורך הקליפ נקבע מהתוכן, לא שרירותית.
    """
    score = tl.score
    n = score.size
    hop = tl.hop
    peak_val = float(score[peak_idx])
    cutoff = max(0.12 * peak_val, 0.04)

    min_f = int(round(min_d / hop))
    max_f = int(round(max_d / hop))

    i, j = peak_idx, peak_idx + 1
    while (j - i) < max_f:
        left_val = float(score[i - 1]) if i > 0 else -1.0
        right_val = float(score[j]) if j < n else -1.0
        if left_val < 0 and right_val < 0:
            break
        # מרחיבים לכיוון החזק יותר
        if right_val >= left_val:
            if right_val < cutoff and (j - i) >= min_f:
                break
            j += 1
        else:
            if left_val < cutoff and (j - i) >= min_f:
                break
            i -= 1
    # משלימים לאורך המינימלי אם נעצרנו מוקדם
    while (j - i) < min_f:
        if j < n:
            j += 1
        elif i > 0:
            i -= 1
        else:
            break
    return i * hop, j * hop


def _clamp_duration(start: float, end: float, min_d: float, max_d: float,
                    peak: float, duration: float) -> tuple[float, float]:
    """מוודא שהחלון בטווח האורך המבוקש ובתוך גבולות הווידאו."""
    start = max(0.0, start)
    end = min(duration, end)
    length = end - start
    if length > max_d:
        # חותכים סביב השיא
        half = max_d / 2.0
        start = max(0.0, min(peak - half, duration - max_d))
        end = min(duration, start + max_d)
    elif length < min_d:
        need = min_d - length
        start = max(0.0, start - need / 2.0)
        end = min(duration, start + min_d)
        if end - start < min_d:
            start = max(0.0, end - min_d)
    return round(start, 3), round(end, 3)


def _arc_quality(seg: np.ndarray) -> float:
    """
    מודד עד כמה לקטע יש "קשת": בנייה, שיא, סיום.
    מחזיר 0..1. קטע שטוח או שהשיא בו בקצה מקבל ציון נמוך.
    """
    n = seg.size
    if n < 9:
        return 0.3
    thirds = np.array_split(seg, 3)
    a, b, c = (float(t.mean()) for t in thirds)
    peak_pos = float(np.argmax(seg)) / max(1, n - 1)

    # השיא רצוי בשליש האמצעי-מאוחר (0.3..0.8)
    pos_q = 1.0 - min(1.0, abs(peak_pos - 0.55) / 0.55)
    # בנייה: אמצע חזק מההתחלה
    build_q = float(np.clip((b - a) / (abs(a) + 0.08) + 0.5, 0.0, 1.0))
    # סיום: ירידה מסוימת בסוף, אבל לא קריסה מוחלטת
    close_q = float(np.clip(1.0 - abs(c - b * 0.72) / (abs(b) + 0.12), 0.0, 1.0))
    # שונות: קטע שטוח אינו סיפור
    var_q = float(np.clip(seg.std() / (seg.mean() + 0.06), 0.0, 1.2)) / 1.2

    return float(np.clip(0.34 * pos_q + 0.24 * build_q + 0.18 * close_q + 0.24 * var_q,
                         0.0, 1.0))


def _content_lang(language: Optional[str]) -> Optional[str]:
    """שפת הכותרות: שפת הדיבור כשיש לה קטלוג, אחרת שפת הממשק."""
    return i18n.normalize_lang(language) if language else None


def _make_candidate(tl: Timeline, transcript: Optional[TranscriptResult],
                    start: float, end: float, peak: float, *, kind: str,
                    language: Optional[str] = None) -> Candidate:
    signals = tl.channel_breakdown(start, end)
    score = float(np.clip(tl.window_mean(tl.score, start, end) * 0.55 +
                          tl.window_mean(tl.score, max(start, peak - 1.5),
                                         min(end, peak + 1.5)) * 0.45, 0.0, 1.0))

    text = transcript.text_between(start, end) if transcript else ""
    title, title_src = _suggest_title(text, transcript, peak, start, end,
                                      language=language)
    category, cat_label = classify_text(text, language) if text \
        else _visual_category(signals)
    description = _describe(text, signals, category, cat_label)
    reason = _explain(signals, text, language=language)

    return Candidate(
        start=round(start, 3), end=round(end, 3), peak_time=round(peak, 3),
        score=round(score, 4), kind=kind, title=title, description=description,
        reason=reason, category=category, signals=signals, title_source=title_src,
    )


def _visual_category(signals: dict[str, float]) -> tuple[str, str]:
    if signals.get("visual", 0) >= signals.get("vocal", 0):
        return "visual", i18n.tr("analysis.category.visual")
    return "moment", i18n.tr("analysis.category.moment")


_TITLE_CLEAN = re.compile(r"^[\s\-–—,.!?:;]+|[\s\-–—,:;]+$")


def _suggest_title(text: str, transcript: Optional[TranscriptResult],
                   peak: float, start: float, end: float, *,
                   language: Optional[str] = None) -> tuple[str, str]:
    """
    כותרת מוצעת מהטקסט שנאמר בפועל – המשפט בעל ציון העניין הגבוה ביותר
    בתוך החלון. אם אין תמלול, מחזירים תיאור זמן ניטרלי ולא ממציאים כותרת.
    """
    if not text.strip() or transcript is None:
        mm, ss = divmod(int(max(0.0, peak)), 60)
        hh, mm = divmod(mm, 60)
        stamp = f"{hh}:{mm:02d}:{ss:02d}" if hh else f"{mm}:{ss:02d}"
        return i18n.tr("analysis.title_at", _content_lang(language), stamp=stamp), "heuristic"

    best_text, best_val = "", -1.0
    for seg in transcript.segments:
        if seg.end < start or seg.start > end:
            continue
        t = (seg.text or "").strip()
        if len(t) < 6:
            continue
        val = lexical_score(t, seg.language or language)
        # קרבה לשיא מחזקת
        dist = abs((seg.start + seg.end) / 2.0 - peak)
        val += max(0.0, 0.35 - dist / 60.0)
        if "!" in t or "?" in t:
            val += 0.12
        if val > best_val:
            best_val, best_text = val, t

    if not best_text:
        best_text = truncate(text, 70)

    title = _TITLE_CLEAN.sub("", best_text)
    title = re.sub(r"\s+", " ", title)
    if len(title) > 68:
        cut = title[:68]
        if " " in cut:
            cut = cut[:cut.rfind(" ")]
        title = cut + "…"
    return (title or i18n.tr("analysis.title_moment", _content_lang(language))), "transcript"


def _describe(text: str, signals: dict[str, float], category: str,
              cat_label: str) -> str:
    """תיאור עובדתי של מה שקורה בקטע, מבוסס על התוכן והאותות."""
    parts: list[str] = [cat_label]
    if text.strip():
        parts.append(i18n.tr("analysis.said", text=truncate(text, 180)))
    else:
        top = max(signals, key=lambda k: signals.get(k, 0.0)) if signals else ""
        if top in ("vocal", "visual", "pause", "speech", "chat"):
            parts.append(i18n.tr(f"analysis.signal_desc.{top}"))
        parts.append(i18n.tr("analysis.no_transcript_here"))
    return truncate(" · ".join(parts), 400)


def _explain(signals: dict[str, float], text: str, *,
             language: Optional[str] = None) -> str:
    """סיבת הבחירה – שקופה, לפי האותות שהובילו לציון."""
    ranked = sorted(((v, k) for k, v in signals.items() if v > 0.02), reverse=True)
    if not ranked:
        return i18n.tr("analysis.reason_default")
    top = [f"{i18n.tr(f'analysis.signal.{k}', default=k)} ({v:.2f})" for v, k in ranked[:3]]
    base = i18n.tr("analysis.reason_signals", signals=", ".join(top))
    if text.strip() and lexical_score(text, language) > 0.4:
        base += i18n.tr("analysis.reason_lexical")
    return base


# --------------------------------------------------------------------------
# סינון כפילויות
# --------------------------------------------------------------------------
def dedupe(cands: list[Candidate], *, iou_threshold: float = 0.3,
           min_gap: float = 1.0) -> list[Candidate]:
    """
    NMS: משאיר את המועמד החזק ומסיר חופפים.
    בנוסף אוכף מרווח מינימלי בין קליפים כדי למנוע קטעים כמעט זהים.
    """
    ordered = sorted(cands, key=lambda c: c.score, reverse=True)
    keep: list[Candidate] = []
    for c in ordered:
        conflict = False
        for k in keep:
            if c.overlaps(k) > iou_threshold:
                conflict = True
                break
            gap = max(c.start, k.start) - min(c.end, k.end)
            if gap < 0 and abs(c.start - k.start) < min_gap:
                conflict = True
                break
        if not conflict:
            keep.append(c)
    keep.sort(key=lambda c: c.start)
    return keep


def enforce_total_limit(long_c: list[Candidate], short_c: list[Candidate],
                        max_total: int) -> tuple[list[Candidate], list[Candidate]]:
    """מכבד את 'מספר קליפים מרבי' תוך שמירה על יחס סביר בין הסוגים."""
    if len(long_c) + len(short_c) <= max_total:
        return long_c, short_c
    # שומרים לפחות אחד מכל סוג אם התבקש
    keep_long = min(len(long_c), max(1 if long_c else 0, math.floor(max_total * 0.4)))
    keep_short = max_total - keep_long
    if keep_short > len(short_c):
        keep_long = min(len(long_c), max_total - len(short_c))
        keep_short = len(short_c)
    long_sorted = sorted(long_c, key=lambda c: c.score, reverse=True)[:keep_long]
    short_sorted = sorted(short_c, key=lambda c: c.score, reverse=True)[:keep_short]
    return (sorted(long_sorted, key=lambda c: c.start),
            sorted(short_sorted, key=lambda c: c.start))
