"""
בדיקה ותיקון של זמני המילים בכתוביות, לפי האודיו.

המזהה (Whisper) נותן לכל מילה זמן התחלה וסוף, אבל הזמנים האלה לא תמיד
נכונים: מילה "נמתחת" לתוך שקט שלפניה, שתי מילים חופפות, מילה מקבלת משך
אפס, או שמשפט שלם "הוזה" בקטע שקט. כתובית שמופיעה לפני שהדובר פתח את
הפה, או נשארת אחרי שסיים, נראית לא מקצועית.

המודול עובד רק לפי אנרגיית האודיו (בלי מודל נוסף) ולעולם לא משנה טקסט:
  * סדר ורציפות – מילה לא מתחילה לפני שהקודמת הסתיימה;
  * התחלה בשקט – מוזזת לתחילת הדיבור הקרובה (snap);
  * סוף בשקט – מקוצר לסוף הדיבור;
  * מילה ארוכה מ-1.5 שניות – נחתכת לדיבור שבתוכה; בלי אודיו – לאורך סביר;
  * משך אפס או כמעט אפס – מורחבת לתוך הרווח שאחריה או לפניה;
  * מילה שכולה בשקט – מוזזת לדיבור סמוך, או מסומנת לבדיקה;
  * משפט "מוזה" – נמחק מהכתוביות רק כשהאודיו שקט *וגם* יש סימן טקסטואלי
    (ביטוי מוכר כמו „תודה שצפיתם", לולאת חזרות, או הסתברות "אין דיבור"
    גבוהה מהמזהה). אחרת רק מסומן לבדיקה.

התיקונים נשמרים בקובץ נפרד (transcript.timing.json); התמלול המקורי לא
משתנה, ומשפטים שכבר נבדקו לא נבדקים שוב.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

import numpy as np

from .transcribe import Segment, TranscriptResult, Word

log = logging.getLogger("polixor.subtitle_align")

ALIGN_VERSION = 1
FRAME = 0.02            # רזולוציית האנרגיה (שניות)
CONTEXT = 4.0           # אודיו סביב המשפט לחישוב רצפת הרעש
MIN_WORD = 0.08         # משך מילה מינימלי
MAX_WORD = 1.5          # מילה אחת ארוכה מזה – חשודה
SNAP = 0.3              # מרחק מקסימלי להזזת התחלה/סוף אל הדיבור
MIN_SHIFT = 0.04        # הזזה קטנה מזה לא נרשמת
MIN_RANGE_DB = 8.0      # פחות טווח דינמי מזה – האנרגיה לא אמינה, לא נוגעים
SILENT_FRACTION = 0.08  # משפט שפחות מזה ממנו דיבור – "בשקט"
NO_SPEECH_P = 0.6
NO_SPEECH_LOGPROB = -1.0

_WORD_RE = re.compile(r"[\w֐-׿']+", re.UNICODE)


# --------------------------------------------------------------------------
# אנרגיה
# --------------------------------------------------------------------------
@dataclass
class Energy:
    """עוצמת האודיו בחלון, ומה נחשב דיבור בו."""
    start: float
    hop: float
    db: np.ndarray
    threshold: float
    valid: bool = True

    def _i(self, t: float) -> int:
        return int(np.clip(np.floor((t - self.start) / self.hop), 0, max(0, self.db.size - 1)))

    def _span(self, a: float, b: float) -> tuple[int, int]:
        i0 = self._i(a)
        i1 = max(i0 + 1, int(np.clip(np.ceil((b - self.start) / self.hop), 0, self.db.size)))
        return i0, i1

    def t(self, i: int) -> float:
        return self.start + i * self.hop

    @property
    def speech(self) -> np.ndarray:
        return self.db >= self.threshold

    def speech_fraction(self, a: float, b: float) -> float:
        if not self.valid or self.db.size == 0 or b <= a:
            return 1.0
        i0, i1 = self._span(a, b)
        sp = self.speech[i0:i1]
        return float(sp.mean()) if sp.size else 1.0

    def is_speech(self, t: float) -> bool:
        return (not self.valid) or bool(self.speech[self._i(t)])

    def first_speech(self, a: float, b: float) -> Optional[float]:
        """תחילת הפריים הראשון עם דיבור ב-[a, b]."""
        i0, i1 = self._span(a, b)
        hits = np.flatnonzero(self.speech[i0:i1])
        return self.t(i0 + int(hits[0])) if hits.size else None

    def last_speech(self, a: float, b: float) -> Optional[float]:
        """סוף הפריים האחרון עם דיבור ב-[a, b]."""
        i0, i1 = self._span(a, b)
        hits = np.flatnonzero(self.speech[i0:i1])
        return self.t(i0 + int(hits[-1]) + 1) if hits.size else None

    def onset_before(self, t: float, lo: float) -> Optional[float]:
        """
        כשהמילה מתחילה *בתוך* דיבור: התחלת רצף הדיבור הזה, אם היא אחרי `lo`
        (כלומר – לפניה היה שקט, והמזהה איחר את תחילת המילה).
        """
        i = self._i(t)
        if not self.speech[i]:
            return None
        j = i
        lo_i = self._i(lo)
        while j > lo_i and self.speech[j - 1]:
            j -= 1
        if j == lo_i and self.speech[j]:
            return None                     # הדיבור ממשיך מהמילה הקודמת
        return self.t(j)


def energy_from_samples(samples: np.ndarray, start: float, *, rate: int = 16000,
                        hop: float = FRAME) -> Energy:
    per = max(1, int(round(hop * rate)))
    k = samples.size // per
    if k < 3:
        return Energy(start=start, hop=hop, db=np.zeros(0, np.float32), threshold=0.0, valid=False)
    x = samples[: k * per].astype(np.float32).reshape(k, per)
    rms = np.sqrt((x ** 2).mean(axis=1))
    db = (20.0 * np.log10(np.maximum(rms, 1e-6))).astype(np.float32)
    floor = float(np.percentile(db, 10))
    peak = float(np.percentile(db, 95))
    valid = (peak - floor) >= MIN_RANGE_DB
    # סף דיבור: מעל רצפת הרעש, ולא רחוק מדי מתחת לשיא
    threshold = max(floor + 0.35 * (peak - floor), peak - 30.0)
    return Energy(start=start, hop=hop, db=db, threshold=threshold, valid=valid)


def energy_for(audio_path: Path, a: float, b: float) -> Optional[Energy]:
    from ..util.wav import read_wav_float32

    a = max(0.0, a)
    x = read_wav_float32(audio_path, start=a, duration=max(0.0, b - a))
    if x is None or x.size == 0:
        return None
    return energy_from_samples(x, a)


# --------------------------------------------------------------------------
# תיקון זמני המילים במשפט
# --------------------------------------------------------------------------
def _expected(text: str) -> float:
    """משך סביר למילה לפי אורכה (כשאין אודיו להסתמך עליו)."""
    n = len(re.sub(r"\W", "", text)) or 1
    return float(min(1.2, max(0.25, 0.07 * n + 0.12)))


def repair_words(words: Sequence[Word], energy: Optional[Energy], *,
                 floor: float = 0.0, ceiling: Optional[float] = None
                 ) -> tuple[list[tuple[float, float]], list[dict[str, Any]]]:
    """
    זמנים מתוקנים לכל מילה (באותו סדר ובאותו מספר – הטקסט לא משתנה),
    ורשימת הבעיות שנמצאו. `floor` – סוף המילה הקודמת לפני המשפט;
    `ceiling` – תחילת המילה הבאה אחרי המשפט.
    """
    e_ok = energy is not None and energy.valid
    times = [(float(w.start), float(w.end)) for w in words]
    issues: list[dict[str, Any]] = []

    def note(i: int, kind: str, before: tuple[float, float], after: tuple[float, float]) -> None:
        shift = max(abs(after[0] - before[0]), abs(after[1] - before[1]))
        if kind in ("no_speech", "long_speech") or shift >= MIN_SHIFT:
            issues.append({"i": i, "kind": kind, "text": words[i].text,
                           "before": [round(before[0], 3), round(before[1], 3)],
                           "after": [round(after[0], 3), round(after[1], 3)]})

    prev_end = floor
    for i, w in enumerate(words):
        s, e = times[i]
        orig = (s, e)
        nxt = times[i + 1][0] if i + 1 < len(times) else ceiling
        # 1. סדר: לא מתחילים לפני שהמילה הקודמת הסתיימה
        if s < prev_end - 1e-3:
            s = prev_end
            e = max(e, s)
            note(i, "overlap", orig, (s, e))
        if e < s:
            e = s

        if e_ok:
            assert energy is not None
            cur = (s, e)
            # 2. מילה שכולה בשקט – מחפשים דיבור סמוך
            if e - s >= MIN_WORD and energy.speech_fraction(s, e) == 0.0:
                lo = max(prev_end, s - SNAP)
                hi = min(nxt, e + SNAP) if nxt is not None else e + SNAP
                on = energy.first_speech(lo, hi)
                if on is not None:
                    off = energy.last_speech(on, hi) or (on + (e - s))
                    s, e = on, min(off, on + max(e - s, MIN_WORD))
                    note(i, "in_silence", cur, (s, e))
                else:
                    note(i, "no_speech", cur, cur)
            else:
                # 3. התחלה בשקט – קדימה אל תחילת הדיבור
                if not energy.is_speech(s):
                    on = energy.first_speech(s, min(e, s + max(SNAP, e - s)))
                    if on is not None and on < e:
                        s = on
                        note(i, "early_start", cur, (s, e))
                # 4. התחלה באמצע דיבור שהתחיל אחרי שקט – אחורה אל תחילתו
                else:
                    on = energy.onset_before(s, max(prev_end, s - SNAP))
                    if on is not None and s - on >= MIN_SHIFT:
                        s = on
                        note(i, "late_start", cur, (s, e))
                cur2 = (s, e)
                # 5. סוף בשקט – אחורה אל סוף הדיבור
                if not energy.is_speech(max(s, e - 1e-3)):
                    off = energy.last_speech(s, e)
                    if off is not None and off > s:
                        e = max(off, s + MIN_WORD)
                        note(i, "late_end", cur2, (s, e))
            # 6. מילה ארוכה מאוד שכולה דיבור – כנראה מילים שהמזהה פספס
            if e - s > MAX_WORD and energy.speech_fraction(s, e) > 0.6:
                note(i, "long_speech", (s, e), (s, e))
        # 7. מילה ארוכה מדי בלי תמיכה באודיו – לאורך סביר
        if e - s > MAX_WORD and not (e_ok and energy is not None
                                     and energy.speech_fraction(s, e) > 0.6):
            cur = (s, e)
            e = s + _expected(w.text)
            note(i, "too_long", cur, (s, e))
        # 8. משך אפס – מרחיבים לתוך הרווח
        if e - s < MIN_WORD:
            cur = (s, e)
            room_after = (nxt - e) if nxt is not None else MIN_WORD
            e = e + max(0.0, min(MIN_WORD - (e - s), room_after))
            if e - s < MIN_WORD:
                s = max(prev_end, e - MIN_WORD)
            note(i, "too_short", cur, (s, e))
        times[i] = (round(s, 3), round(e, 3))
        prev_end = e
    return times, issues


# --------------------------------------------------------------------------
# הזיות
# --------------------------------------------------------------------------
def _tokens(text: str) -> list[str]:
    from .transcript_correct import norm_text

    return _WORD_RE.findall(norm_text(text))


def repetition_loop(text: str, *, min_repeats: int = 3) -> bool:
    """אותו רצף של 1-4 מילים שחוזר ברצף לפחות `min_repeats` פעמים (ולפחות 6 מילים)."""
    toks = _tokens(text)
    for n in range(1, 5):
        need = max(min_repeats, -(-6 // n))
        for i in range(0, len(toks) - n * need + 1):
            gram = toks[i:i + n]
            if all(toks[i + k * n: i + (k + 1) * n] == gram for k in range(need)):
                return True
    return False


def hallucination_signals(seg: Segment, energy: Optional[Energy]) -> dict[str, bool]:
    from .transcript_correct import _is_hallucination

    return {
        "known_phrase": _is_hallucination(seg.text),
        "repetition_loop": repetition_loop(seg.text),
        "no_speech": seg.no_speech_prob >= NO_SPEECH_P and seg.avg_logprob <= NO_SPEECH_LOGPROB,
        "silent_audio": bool(energy is not None and energy.valid
                             and energy.speech_fraction(seg.start, seg.end) < SILENT_FRACTION),
    }


def hallucination_action(signals: dict[str, bool]) -> Optional[str]:
    """
    "drop" – רק כשהאודיו שקט וגם יש סימן טקסטואלי; "flag" – סימן אחד בלבד.
    ביטוי מוכר עם דיבור אמיתי (מישהו באמת אמר „תודה שצפיתם") נשאר כמו שהוא.
    """
    textual = signals["known_phrase"] or signals["repetition_loop"] or signals["no_speech"]
    if signals["silent_audio"] and textual:
        return "drop"
    if signals["silent_audio"] or signals["repetition_loop"] or signals["no_speech"]:
        return "flag"
    return None


# --------------------------------------------------------------------------
# תמלול שלם
# --------------------------------------------------------------------------
EnergyFn = Callable[[float, float], Optional[Energy]]


def _forced(seg: Segment, aligner: Any) -> tuple[list[Word], list[dict[str, Any]]]:
    """זמנים מיישור כפוי (אם יש), כנקודת פתיחה לבדיקת האנרגיה."""
    if aligner is None or not seg.words:
        return list(seg.words), []
    got = aligner(seg.start, seg.end, [w.text for w in seg.words])
    if not got or len(got) != len(seg.words):
        return list(seg.words), []
    out, issues = [], []
    for i, (w, t) in enumerate(zip(seg.words, got)):
        if t is None:
            out.append(w)
            continue
        nw = Word(start=float(t[0]), end=float(t[1]), text=w.text,
                  probability=w.probability, flag=w.flag, asr=w.asr)
        if max(abs(nw.start - w.start), abs(nw.end - w.end)) >= MIN_SHIFT:
            issues.append({"i": i, "kind": "forced", "text": w.text,
                           "before": [round(w.start, 3), round(w.end, 3)],
                           "after": [round(nw.start, 3), round(nw.end, 3)]})
        out.append(nw)
    return out, issues


def _text_key(text: str) -> str:
    import hashlib

    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:12]


def align_transcript(transcript: TranscriptResult, *, energy_fn: Optional[EnergyFn],
                     spans: Optional[Sequence[tuple[float, float]]] = None,
                     previous: Optional[dict[str, Any]] = None,
                     aligner: Any = None) -> dict[str, Any]:
    """
    בודק את המשפטים (בתוך `spans` אם ניתנו) ומחזיר את קובץ תיקוני הזמנים.
    משפטים שכבר נבדקו (`previous`, אותו טקסט) לא נבדקים שוב. `aligner`
    (אופציונלי, services/forced_align) נותן זמנים מדויקים יותר לפני
    בדיקת האנרגיה.
    """
    segs = transcript.segments
    done: dict[int, dict[str, Any]] = {}
    # משפט שנבדק נזכר לפי המיקום *והטקסט* – הגהה חדשה ששינתה אותו תגרום לבדיקה חוזרת
    checked: dict[int, str] = {}
    if previous and previous.get("version") == ALIGN_VERSION:
        for r in previous.get("segments") or []:
            i = int(r.get("index", -1))
            if 0 <= i < len(segs) and segs[i].text == r.get("text"):
                done[i] = r
        for k, h in (previous.get("checked") or {}).items():
            i = int(k)
            if 0 <= i < len(segs) and _text_key(segs[i].text) == h:
                checked[i] = h
        checked.update({i: _text_key(segs[i].text) for i in done})

    def in_spans(s: Segment) -> bool:
        return spans is None or any(s.end > a and s.start < b for a, b in spans)

    for i, seg in enumerate(segs):
        if i in checked or not in_spans(seg) or not seg.text.strip():
            continue
        checked[i] = _text_key(seg.text)
        energy = energy_fn(seg.start - CONTEXT, seg.end + CONTEXT) if energy_fn else None
        floor = segs[i - 1].words[-1].end if i > 0 and segs[i - 1].words else 0.0
        ceiling = segs[i + 1].words[0].start if i + 1 < len(segs) and segs[i + 1].words else None
        start_words, forced = _forced(seg, aligner)
        times, issues = repair_words(start_words, energy, floor=floor, ceiling=ceiling)
        issues = forced + issues
        signals = hallucination_signals(seg, energy)
        action = hallucination_action(signals)
        if not issues and action is None:
            continue
        done[i] = {"index": i, "text": seg.text, "words": [list(t) for t in times],
                   "issues": issues, "action": action,
                   "signals": [k for k, v in signals.items() if v]}

    items = [done[i] for i in sorted(done)]
    kinds: dict[str, int] = {}
    for r in items:
        for x in r["issues"]:
            kinds[x["kind"]] = kinds.get(x["kind"], 0) + 1
    return {
        "version": ALIGN_VERSION,
        "checked": {str(i): checked[i] for i in sorted(checked)},
        "stats": {"segments_checked": len(checked),
                  "words_retimed": len({(r["index"], x["i"]) for r in items for x in r["issues"]
                                        if x["kind"] not in ("no_speech", "long_speech")}),
                  "forced_alignment": aligner is not None,
                  "dropped": sum(1 for r in items if r["action"] == "drop"),
                  "flagged": sum(1 for r in items if r["action"] == "flag"),
                  "issues": kinds},
        "segments": items,
    }


def apply(transcript: TranscriptResult, data: Optional[dict[str, Any]]) -> TranscriptResult:
    """
    התמלול עם הזמנים המתוקנים. משפט שהטקסט שלו השתנה מאז (הגהה חדשה)
    נשאר כמו שהוא. משפט "מוזה" לא נכנס לכתוביות; משפט חשוד מסומן לבדיקה.
    """
    if not data or data.get("version") != ALIGN_VERSION:
        return transcript
    by_index = {int(r["index"]): r for r in data.get("segments") or []}
    out: list[Segment] = []
    for i, s in enumerate(transcript.segments):
        r = by_index.get(i)
        if r is None or r.get("text") != s.text or len(r.get("words") or []) != len(s.words):
            out.append(s)
            continue
        if r.get("action") == "drop":
            continue
        silent = {int(x["i"]) for x in r.get("issues") or [] if x["kind"] == "no_speech"}
        flag_all = r.get("action") == "flag"
        words = [Word(start=float(a), end=float(b), text=w.text, probability=w.probability,
                      flag=w.flag or ("low" if (flag_all or k in silent) else ""), asr=w.asr)
                 for k, (w, (a, b)) in enumerate(zip(s.words, r["words"]))]
        start = min([s.start] + [w.start for w in words[:1]])
        end = max([s.end] + [w.end for w in words[-1:]])
        out.append(Segment(start=start, end=end, text=s.text, words=words,
                           language=s.language, avg_logprob=s.avg_logprob,
                           no_speech_prob=s.no_speech_prob))
    return TranscriptResult(segments=out, language=transcript.language,
                            duration=transcript.duration, provider=transcript.provider,
                            model=transcript.model, note=transcript.note,
                            meta={**transcript.meta, "timing": data.get("stats", {})})


def save(path: Path, data: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return path


def load(path: Optional[Path]) -> Optional[dict[str, Any]]:
    if not path or not Path(path).exists():
        return None
    try:
        data = json.loads(Path(path).read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


# --------------------------------------------------------------------------
# מדידה (להשוואה בין שיטות – scripts/alignment_spike.py)
# --------------------------------------------------------------------------
def measure(transcript: TranscriptResult, energy_fn: Optional[EnergyFn],
            spans: Optional[Sequence[tuple[float, float]]] = None) -> dict[str, Any]:
    """
    כמה מילים מתחילות או נגמרות בשקט, חופפות, קצרות או ארוכות מדי.
    בלי אמת-מידה ידנית זה המדד הכי קרוב ל"הכתובית מופיעה כשמדברים".
    """
    counts = {"words": 0, "start_in_silence": 0, "end_in_silence": 0, "overlap": 0,
              "too_short": 0, "too_long": 0}
    bad = 0
    prev_end = None
    for seg in transcript.segments:
        if spans is not None and not any(seg.end > a and seg.start < b for a, b in spans):
            continue
        energy = energy_fn(seg.start - CONTEXT, seg.end + CONTEXT) if energy_fn else None
        e_ok = energy is not None and energy.valid
        for w in seg.words:
            counts["words"] += 1
            hit = {
                "overlap": prev_end is not None and w.start < prev_end - 1e-3,
                "too_short": w.end - w.start < MIN_WORD,
                "too_long": w.end - w.start > MAX_WORD,
                # „בשקט" = אין דיבור ב-60ms הראשונות/האחרונות של המילה
                "start_in_silence": bool(e_ok and energy is not None and energy.speech_fraction(
                    w.start, min(w.end, w.start + 0.06)) == 0.0),
                "end_in_silence": bool(e_ok and energy is not None and energy.speech_fraction(
                    max(w.start, w.end - 0.06), w.end) == 0.0),
            }
            for k, v in hit.items():
                counts[k] += int(v)
            bad += int(any(hit.values()))
            prev_end = w.end
    # שיעור המילים שיש בהן בעיה אחת לפחות
    counts["problem_rate"] = round(bad / max(1, counts["words"]), 4)
    return counts
