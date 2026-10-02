"""
תמלול חזק של חלונות המועמדים – לפני הבחירה הסופית.

המעבר המהיר על כל השידור (מודל קטן) טוב מספיק כדי למצוא *איפה* אולי יש
רגע, אבל בעברית הטקסט שלו משובש מכדי לשפוט וו, עמדה או הכרעה. לכן:

  1. הבחירה רצה על התמלול המהיר ומדרגת סיפורים (גם כאלה שנפסלו).
  2. החלונות של המועמדים המבטיחים ביותר (עד תקציב שניות) מתומללים מחדש
     במודל החזק (בעברית: ivrit.ai), עם אוצר המילים של הפרויקט.
  3. בתוך כל חלון, משפטי התמלול המהיר מוחלפים במשפטי המודל החזק – רק
     כשהתוצאה תקינה (לא הזיה, אורך סביר, ביטחון סביר). אחרת המקור נשאר.
  4. הבחירה רצה שוב על התמלול המשולב. אותו טקסט משמש אחר כך לכתוביות.

קובץ התמלול המקורי לא משתנה: החלונות נשמרים בקובץ נפרד ומוחלים בטעינה
(לפני תיקוני ההגהה וזמני המילים), כך שיצירה מחדש וייצוא מחדש משתמשים
באותו טקסט בלי לתמלל שוב.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from .transcribe import Segment, TranscriptResult, Word

log = logging.getLogger("polixor.strong_windows")

VERSION = 1
# כמה מועמדים לכל היותר, ותקציב שניות האודיו לתמלול החזק
MAX_WINDOWS = 10
BUDGET_SECONDS = 300.0
# ריפוד: הקשר לפני/אחרי (גבולות יכולים לזוז), ושוליים לאודיו שנשלח
PAD_BEFORE = 8.0
PAD_AFTER = 20.0
# חלק המילים הלא בטוחות שמעליו טקסט התמלול המהיר לא אמין לשיפוט תוכן
UNRELIABLE_TEXT = 0.3
AUDIO_PAD = 0.6
# סף תקינות לחלופה
MIN_RATIO, MAX_RATIO = 0.4, 2.6
MIN_MEAN_P = 0.35

Retranscriber = Callable[[float, float], Optional[list[Segment]]]


# --------------------------------------------------------------------------
# תכנון: אילו חלונות
# --------------------------------------------------------------------------
# דחייה שתמלול נקי עשוי להפוך (הטקסט היה משובש) מקבלת עדיפות
_TEXT_SENSITIVE = {"weak_hook", "below_quality_bar", "unclear_opening", "no_payoff",
                   "ends_before_answer", "too_short", "ordinary_conversation", "slow_start", ""}


def plan_windows(stories: Sequence[Any], *, duration: float, threshold: float,
                 max_windows: int = MAX_WINDOWS, budget: float = BUDGET_SECONDS,
                 extra: Sequence[tuple[float, float]] = ()) -> list[tuple[float, float]]:
    """
    החלונות למעבר החזק: הסיפורים הכי מבטיחים (עברו, או נפסלו מסיבה שטקסט
    נקי עשוי לשנות), עם ריפוד, ממוזגים, עד התקציב.
    """
    pool = []
    for s in stories:
        rej = getattr(s, "rejection", "") or ""
        prio = float(getattr(s, "final", 0.0))
        low = float(getattr(s, "low_confidence", 0.0))
        if low >= UNRELIABLE_TEXT:
            # הטקסט המהיר לא אמין – אי אפשר לשפוט ממנו תוכן. מדרגים לפי
            # אותות שלא תלויים בטקסט, והמודל החזק יכריע אם יש שם משהו.
            comp = getattr(s, "components", {}) or {}
            prio = max(prio, threshold - 0.25 + 0.2 * float(comp.get("signal", 0.0))
                       + 0.1 * float(comp.get("density", 0.0)) + 0.05 * low)
        elif rej and rej not in _TEXT_SENSITIVE:
            prio -= 0.12
        prio += 0.1 * low
        if getattr(s, "passed", False):
            prio += 0.05
        if prio < threshold - 0.3:
            continue
        pool.append((prio, float(s.start), float(s.end)))
    pool.sort(reverse=True)
    chosen: list[tuple[float, float]] = []
    used = 0.0
    for a, b in list(extra) + [(a, b) for _, a, b in pool]:
        w = (max(0.0, a - PAD_BEFORE), min(duration or b + PAD_AFTER, b + PAD_AFTER))
        merged = _merge(chosen + [w])
        cost = sum(y - x for x, y in merged)
        if cost > budget:
            continue
        chosen, used = merged, cost
        if len(chosen) >= max_windows:
            break
    return chosen


def _merge(wins: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for a, b in sorted(wins):
        if out and a <= out[-1][1] + 1.0:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def seg_to_dict(s: Segment) -> dict[str, Any]:
    return {"start": round(s.start, 3), "end": round(s.end, 3), "text": s.text,
            "language": s.language, "words": [w.to_dict() for w in s.words]}


def seg_from_dict(d: dict[str, Any]) -> Segment:
    return Segment(start=float(d["start"]), end=float(d["end"]), text=str(d.get("text", "")),
                   language=str(d.get("language", "")),
                   words=[Word(start=float(w["start"]), end=float(w["end"]), text=str(w.get("text", "")),
                               probability=float(w.get("p", 1.0)), flag=str(w.get("flag", "")),
                               asr=str(w.get("asr", "")) or "strong")
                          for w in d.get("words") or []])


# --------------------------------------------------------------------------
# תמלול
# --------------------------------------------------------------------------
_HALLU = ("תודה שצפיתם", "תודה רבה שצפיתם", "כתוביות", "הירשמו לערוץ", "thanks for watching",
          "subtitles by", "amara.org")


def _mean_p(words: Sequence[Word]) -> float:
    return sum(float(w.probability) for w in words) / len(words) if words else 0.0


def _looping(words: Sequence[Word]) -> bool:
    """„קבודלת לי, קבודלת לי, …" – לולאת הזיה של Whisper."""
    toks = [re.sub(r"\W+", "", w.text.lower()) for w in words]
    toks = [t for t in toks if t]
    if len(toks) < 8:
        return False
    for n in (1, 2, 3):
        grams = [tuple(toks[i:i + n]) for i in range(0, len(toks) - n + 1)]
        if grams:
            top = max(grams.count(g) for g in set(grams))
            if top * n >= 0.5 * len(toks) and top >= 4:
                return True
    return False


def _segments_in(segs: Sequence[Segment], a: float, b: float) -> list[int]:
    return [i for i, s in enumerate(segs) if a <= (s.start + s.end) / 2.0 <= b]


def transcribe_windows(base: TranscriptResult, windows: Sequence[tuple[float, float]],
                       retranscribe: Retranscriber, *, model: str = "",
                       previous: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """מתמלל כל חלון שעוד לא תומלל (לפי `previous`) ומחזיר את קובץ החלונות."""
    done = {(round(w["start"], 2), round(w["end"], 2)): w
            for w in (previous or {}).get("windows") or []
            if (previous or {}).get("version") == VERSION}
    out: list[dict[str, Any]] = list(done.values())
    wall = float((previous or {}).get("wall_seconds", 0.0))
    audio = float((previous or {}).get("audio_seconds", 0.0))
    for a, b in windows:
        idx = _segments_in(base.segments, a, b)
        if not idx:
            continue
        lo = base.segments[idx[0]].start
        hi = base.segments[idx[-1]].end
        if any(w["start"] <= lo + 0.05 and w["end"] >= hi - 0.05 for w in out):
            continue
        t0 = time.monotonic()
        try:
            alt = retranscribe(max(0.0, lo - AUDIO_PAD), hi + AUDIO_PAD) or []
        except Exception as exc:                       # noqa: BLE001
            log.warning("strong window %.1f-%.1f failed: %s", lo, hi, exc)
            alt = []
        wall += time.monotonic() - t0
        audio += hi - lo + 2 * AUDIO_PAD
        rec = {"start": round(lo, 3), "end": round(hi, 3), "model": model,
               "segments": [seg_to_dict(s) for s in alt], "accepted": False, "reason": ""}
        rec["accepted"], rec["reason"] = _acceptable(base, idx, alt, lo, hi)
        out.append(rec)
    out.sort(key=lambda w: w["start"])
    return {"version": VERSION, "model": model, "wall_seconds": round(wall, 2),
            "audio_seconds": round(audio, 2), "windows": out}


def _clip_words(alt: Sequence[Segment], lo: float, hi: float) -> list[Segment]:
    """רק המילים שבתוך גבולות המשפטים שמוחלפים (בלי חפיפה עם השכנים)."""
    out = []
    for s in alt:
        words = [w for w in s.words if lo - 0.25 <= (w.start + w.end) / 2.0 <= hi + 0.25]
        if not words:
            continue
        text = " ".join(w.text.strip() for w in words if w.text.strip())
        if not text:
            continue
        out.append(replace(s, start=words[0].start, end=words[-1].end, text=text, words=words))
    return out


def _acceptable(base: TranscriptResult, idx: Sequence[int], alt: Sequence[Segment],
                lo: float, hi: float) -> tuple[bool, str]:
    alt = _clip_words(alt, lo, hi)
    words = [w for s in alt for w in s.words]
    if not words:
        return False, "empty"
    text = " ".join(w.text for w in words).lower()
    if any(h in text for h in _HALLU):
        return False, "hallucination"
    if _looping(words):
        return False, "loop"
    n_base = sum(len(base.segments[i].words) or len(base.segments[i].text.split()) for i in idx)
    ratio = len(words) / max(1, n_base)
    if not MIN_RATIO <= ratio <= MAX_RATIO:
        return False, "length"
    if _mean_p(words) < MIN_MEAN_P:
        return False, "low_confidence"
    return True, "ok"


# --------------------------------------------------------------------------
# החלה / שמירה
# --------------------------------------------------------------------------
def apply(base: Optional[TranscriptResult], data: Optional[dict[str, Any]]) -> Optional[TranscriptResult]:
    """התמלול המשולב: בכל חלון שהתקבל – משפטי המודל החזק במקום המהיר."""
    if base is None or not data or data.get("version") != VERSION:
        return base
    segs = list(base.segments)
    changed = False
    for w in sorted(data.get("windows") or [], key=lambda x: -float(x["start"])):
        if not w.get("accepted"):
            continue
        lo, hi = float(w["start"]), float(w["end"])
        idx = _segments_in(segs, lo - 0.05, hi + 0.05)
        if not idx:
            continue
        alt = _clip_words([seg_from_dict(s) for s in w.get("segments") or []], lo, hi)
        if not alt:
            continue
        for s in alt:
            s.language = s.language or base.language
        segs[idx[0]:idx[-1] + 1] = alt
        changed = True
    if not changed:
        return base
    return replace(base, segments=segs)


def covered(data: Optional[dict[str, Any]]) -> list[tuple[float, float]]:
    return [(float(w["start"]), float(w["end"])) for w in (data or {}).get("windows") or []
            if w.get("accepted")]


def learned_vocabulary(data: Optional[dict[str, Any]], *, stop: frozenset[str] = frozenset(),
                       limit: int = 40) -> list[str]:
    """
    שמות ומונחים שהמודל החזק שמע בביטחון גבוה יותר מפעם אחת (למשל שמות
    שחקנים, מותגים, מילים באנגלית) – רמזים לתמלולים הבאים באותו פרויקט.
    """
    counts: dict[str, int] = {}
    for w in (data or {}).get("windows") or []:
        if not w.get("accepted"):
            continue
        for s in w.get("segments") or []:
            for word in s.get("words") or []:
                t = re.sub(r"[^\w'֐-׿]+", "", str(word.get("text", ""))).strip("'")
                if len(t) < 3 or t.lower() in stop or float(word.get("p", word.get("probability", 0))) < 0.85:
                    continue
                latin = bool(re.search(r"[A-Za-z]", t))
                if latin or len(t) >= 5:
                    counts[t] = counts.get(t, 0) + 1
    ranked = sorted((c, t) for t, c in counts.items() if c >= 2)
    return [t for _, t in reversed(ranked)][:limit]


def save(path: Path, data: dict[str, Any]) -> Path:
    path.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    return path


def load(path: Optional[Path]) -> Optional[dict[str, Any]]:
    try:
        return json.loads(Path(path).read_text("utf-8")) if path else None
    except (OSError, ValueError):
        return None
