"""
הגהה של התמלול – בלי להמציא דיבור.

האודיו הוא מקור האמת. מודל שפה שרואה רק טקסט שגוי לא יכול לדעת מה נאמר,
ולכן הסדר הוא:

  1. איתור    מילים/משפטים שהזיהוי בהם לא בטוח (הסתברות מילה נמוכה,
              רצף מילים לא בטוחות, avg_logprob נמוך)
  2. שמיעה חוזרת  תמלול מחדש של *החלון הקצר הזה בלבד* במודל חזק יותר
              (בעברית: ivrit.ai), עם אותה שפה ואותו אוצר מילים
  3. השוואה   החלופה מול המקור: ביטחון, אורך, דמיון, מילים מאוצר המילים
  4. החלטה    תיקון (כולל הוספה/הסרה של מילים) **רק** כשיש ראיה חזקה:
              התמלול החוזר בטוח יותר בבירור, או תואם מונח מאוצר המילים.
              אחרת – המשפט מסומן לבדיקה ידנית, והחלופה מוצעת בעורך.
  5. מודל שפה (אופציונלי) רק *בוחר* בין המקור לחלופות שהגיעו מהאודיו –
              הוא לא כותב טקסט חופשי.

נשמר לכל משפט: הטקסט המקורי, הטקסט המתוקן, המקור והסיבה, רמת הביטחון,
הזמנים, והראיות (החלופות). קובץ התמלול המקורי לעולם לא משתנה; התיקונים
נשמרים בקובץ נפרד ומוחלים בטעינה.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from .. import i18n
from ..config import AppSettings
from .transcribe import Segment, TranscriptResult, Word

log = logging.getLogger("polixor.correct")

CORRECTIONS_VERSION = 1
# מילה עם הסתברות מתחת לזה – לא בטוחה
LOW_P = 0.5
# משפט חשוד: חלק כזה של מילים לא בטוחות, או רצף של כמה
SUSPECT_FRACTION = 0.25
SUSPECT_RUN = 2
SUSPECT_LOGPROB = -1.0
# ריפוד סביב החלון שנשלח לתמלול חוזר, ואורך מרבי של חלון (חלון Whisper)
PAD = 0.6
MAX_WINDOW = 28.0
# כמה מהמקור מותר לתמלל מחדש (תקציב זמן במצב מהיר)
BUDGET_FRACTION = 0.25
# תיקון מהתמלול החוזר: שיפור ביטחון מינימלי, ביטחון מינימלי מוחלט
MIN_GAIN = 0.15
MIN_ALT_CONF = 0.6
# מונח מאוצר המילים: דמיון מינימלי (אחרי נרמול עברי) להחלפת מילה לא בטוחה
VOCAB_SIM = 0.84
# תמלול בענן: דמיון שנחשב "שמעו אותו דבר", וביטחון מינימלי לתיקון בלי חלופה מקומית
CLOUD_AGREE = 0.9
CLOUD_ALONE_CONF = 0.8
CLOUD_ALONE_SIM = 0.5
CLOUD_PAD = 0.3
# ביטויים ש-Whisper נוטה "להזות" בשקט – לעולם לא תיקון
HALLUCINATIONS = (
    "תודה שצפיתם", "תודה רבה שצפיתם", "כתוביות", "תרגום", "הירשמו לערוץ",
    "thanks for watching", "thank you for watching", "subtitles by", "subscribe",
    "amara.org",
)

_NIQQUD = re.compile(r"[֑-ׇ]")
_PUNCT = re.compile(r"[^\w\s֐-׿']", re.UNICODE)
_FINALS = str.maketrans({"ך": "כ", "ם": "מ", "ן": "נ", "ף": "פ", "ץ": "צ"})


def norm_text(text: str) -> str:
    """השוואה בלי ניקוד, פיסוק, רישיות ואותיות סופיות."""
    t = unicodedata.normalize("NFKD", text or "")
    t = _NIQQUD.sub("", t)
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn")
    t = _PUNCT.sub(" ", t).lower().translate(_FINALS)
    return re.sub(r"\s+", " ", t).strip()


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, norm_text(a), norm_text(b)).ratio()


# --------------------------------------------------------------------------
@dataclass
class SegmentReview:
    index: int
    start: float
    end: float
    original: str
    status: str = "ok"                       # ok | confirmed | corrected | flagged
    corrected: Optional[str] = None
    source: str = ""                         # retranscription | vocabulary | llm_choice
    confidence: float = 0.0
    reason: dict[str, Any] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    low_words: list[dict[str, Any]] = field(default_factory=list)
    corrected_words: Optional[list[dict[str, Any]]] = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def _t(key: str, **params: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"key": key, "text": i18n.tr(f"correct.{key}", **params)}
    if params:
        out["params"] = {k: str(v) for k, v in params.items()}
    return out


# --------------------------------------------------------------------------
# 1. איתור
# --------------------------------------------------------------------------
def suspicious(seg: Segment) -> tuple[bool, list[dict[str, Any]]]:
    """האם המשפט חשוד, ואילו מילים בו לא בטוחות."""
    low = [{"i": i, "text": w.text, "p": round(float(w.probability), 3),
            "start": round(w.start, 3), "end": round(w.end, 3)}
           for i, w in enumerate(seg.words) if float(w.probability) < LOW_P]
    if not seg.words:
        return (seg.avg_logprob < SUSPECT_LOGPROB and bool(seg.text.strip())), low
    run = best = 0
    for w in seg.words:
        run = run + 1 if float(w.probability) < LOW_P else 0
        best = max(best, run)
    frac = len(low) / len(seg.words)
    return (frac >= SUSPECT_FRACTION or best >= SUSPECT_RUN
            or seg.avg_logprob < SUSPECT_LOGPROB), low


def windows_for(segments: Sequence[Segment], idxs: Sequence[int],
                total: float) -> list[tuple[float, float, list[int]]]:
    """חלונות לתמלול חוזר: משפטים חשודים סמוכים מתאחדים, עד ~28 שניות."""
    out: list[tuple[float, float, list[int]]] = []
    for i in sorted(idxs):
        s = segments[i]
        a, b = max(0.0, s.start - PAD), min(total or s.end + PAD, s.end + PAD)
        if out and a <= out[-1][1] + 1.0 and b - out[-1][0] <= MAX_WINDOW:
            out[-1] = (out[-1][0], max(out[-1][1], b), out[-1][2] + [i])
        else:
            out.append((a, b, [i]))
    return out


# --------------------------------------------------------------------------
# 3–4. השוואה והחלטה
# --------------------------------------------------------------------------
def _mean_p(words: Sequence[Word]) -> float:
    return float(sum(float(w.probability) for w in words) / len(words)) if words else 0.0


def _alt_words_for(seg: Segment, alt: Sequence[Segment]) -> list[Word]:
    """המילים של התמלול החוזר שנופלות בתחום הזמן של המשפט המקורי."""
    out = []
    for a in alt:
        for w in a.words:
            mid = (w.start + w.end) / 2.0
            if seg.start - 0.25 <= mid <= seg.end + 0.25:
                out.append(w)
    return out


def _is_hallucination(text: str) -> bool:
    n = norm_text(text)
    return any(norm_text(h) in n for h in HALLUCINATIONS)


def _vocab_hits(text: str, vocabulary: Sequence[str]) -> list[str]:
    n = f" {norm_text(text)} "
    return [v for v in vocabulary if norm_text(v) and f" {norm_text(v)} " in n]


def decide(seg: Segment, index: int, alt: Optional[Sequence[Segment]], *,
           vocabulary: Sequence[str] = (), strong_model: str = "",
           cloud: Optional[Sequence[Segment]] = None, cloud_model: str = "") -> SegmentReview:
    """
    מחליט על משפט חשוד אחד לפי הראיות. לעולם לא ממציא טקסט.
    `cloud` (מצב איכות, אם הוגדר) – תמלול נוסף של אותו קטע בענן; ראו _with_cloud.
    """
    rv = _decide_local(seg, index, alt, vocabulary=vocabulary, strong_model=strong_model)
    if cloud is None:
        return rv
    return _with_cloud(seg, rv, alt, cloud, cloud_model=cloud_model)


def _with_cloud(seg: Segment, rv: SegmentReview, alt: Optional[Sequence[Segment]],
                cloud: Sequence[Segment], *, cloud_model: str) -> SegmentReview:
    """
    הכרעה עם ראיה שלישית (תמלול בענן), לפי הסכמה בין מקורות שמיעה:
      * הענן שומע כמו המקור ולא כמו החלופה – המקור נשאר (גם אם המודל החזק
        "תיקן");
      * הענן שומע כמו החלופה המקומית – החלופה מתקבלת גם כשלבד היא הייתה חלשה;
      * אין חלופה מקומית – הענן לבד מתקן רק כשהוא בטוח בבירור יותר מהמקור;
      * אחרת – סימון לבדיקה, והחלופה מהענן מוצגת בעורך.
    """
    words = [w for s in cloud for w in s.words]
    text = " ".join(w.text for w in words).strip()
    conf = _mean_p(words)
    rv.evidence.append({"kind": "cloud", "model": cloud_model, "text": text,
                        "confidence": round(conf, 3), "words": [w.to_dict() for w in words]})
    if not text or _is_hallucination(text):
        return rv
    alt_words = _alt_words_for(seg, alt) if alt is not None else []
    alt_text = " ".join(w.text for w in alt_words).strip()
    # "מסכים" = דומה מאוד *וגם* קרוב יותר לצד הזה מאשר לשני (הבדל של אות
    # אחת במשפט קצר – „שלום"/„שלוש" – הוא בדיוק מה שמכריעים עליו)
    sim_o = similarity(text, seg.text)
    sim_a = similarity(text, alt_text) if alt_text else 0.0
    agree_orig = sim_o >= CLOUD_AGREE and sim_o > sim_a
    agree_alt = bool(alt_text) and sim_a >= CLOUD_AGREE and sim_a > sim_o
    if agree_orig and not agree_alt and rv.source != "vocabulary":
        rv.status, rv.source, rv.corrected, rv.corrected_words = "confirmed", "cloud", None, None
        rv.confidence = round(max(_mean_p(seg.words), conf), 3)
        rv.reason = _t("reason.cloud_confirmed")
        return rv
    if rv.status == "corrected":
        if agree_alt:
            rv.source = "retranscription+cloud"
        return rv
    orig_n = len(seg.words) or len(seg.text.split())
    if agree_alt and rv.status == "flagged" and not _is_hallucination(alt_text) \
            and 0.5 <= len(alt_words) / max(1, orig_n) <= 2.0:
        rv.status, rv.source = "corrected", "retranscription+cloud"
        rv.corrected = alt_text
        rv.corrected_words = [w.to_dict() for w in alt_words]
        rv.confidence = round(max(_mean_p(alt_words), conf), 3)
        rv.reason = _t("reason.cloud_agrees")
        return rv
    gain = conf - _mean_p(seg.words)
    # לבד: רק כשהענן בטוח בבירור, והטקסט עדיין קרוב למה שנשמע במקור (טעות
    # זיהוי, לא משפט אחר לגמרי – שם כנראה החלון נפל על דיבור אחר)
    if alt is None and rv.status == "flagged" and conf >= CLOUD_ALONE_CONF \
            and gain >= MIN_GAIN and sim_o >= CLOUD_ALONE_SIM \
            and 0.5 <= len(words) / max(1, orig_n) <= 2.0:
        rv.status, rv.source = "corrected", "cloud"
        rv.corrected = text
        rv.corrected_words = [w.to_dict() for w in words]
        rv.confidence = round(conf, 3)
        rv.reason = _t("reason.cloud", confidence=f"{conf * 100:.0f}")
    return rv


def _decide_local(seg: Segment, index: int, alt: Optional[Sequence[Segment]], *,
                  vocabulary: Sequence[str] = (), strong_model: str = "") -> SegmentReview:
    _, low = suspicious(seg)
    rv = SegmentReview(index=index, start=round(seg.start, 3), end=round(seg.end, 3),
                       original=seg.text, low_words=low, confidence=round(_mean_p(seg.words), 3))

    # ---- ראיה מתמלול חוזר ----
    if alt is not None:
        alt_words = _alt_words_for(seg, alt)
        alt_text = " ".join(w.text for w in alt_words).strip()
        orig_conf, alt_conf = _mean_p(seg.words), _mean_p(alt_words)
        rv.evidence.append({"kind": "retranscription", "model": strong_model, "text": alt_text,
                            "confidence": round(alt_conf, 3),
                            "words": [w.to_dict() for w in alt_words]})
        if alt_text and norm_text(alt_text) == norm_text(seg.text):
            rv.status, rv.confidence = "confirmed", round(max(orig_conf, alt_conf), 3)
            rv.reason = _t("reason.confirmed")
            return rv
        orig_n, alt_n = len(seg.words) or len(seg.text.split()), len(alt_words)
        length_ok = alt_n > 0 and 0.5 <= alt_n / max(1, orig_n) <= 2.0
        alt_low = sum(1 for w in alt_words if float(w.probability) < LOW_P) / max(1, alt_n)
        vocab_new = [v for v in _vocab_hits(alt_text, vocabulary)
                     if v not in _vocab_hits(seg.text, vocabulary)]
        gain = alt_conf - orig_conf
        strong = (length_ok and not _is_hallucination(alt_text) and alt_low <= 0.2
                  and alt_conf >= MIN_ALT_CONF
                  and (gain >= MIN_GAIN or (vocab_new and gain >= 0.05)))
        if strong:
            rv.status, rv.source = "corrected", "retranscription"
            rv.corrected = alt_text
            rv.confidence = round(alt_conf, 3)
            rv.corrected_words = [w.to_dict() for w in alt_words]
            rv.reason = _t("reason.retranscription", gain=f"{gain * 100:+.0f}",
                           confidence=f"{alt_conf * 100:.0f}") if not vocab_new else \
                _t("reason.retranscription_vocab", terms=", ".join(vocab_new))
            return rv
        rv.status = "flagged"
        rv.reason = _t("reason.alternative_weak") if alt_text else _t("reason.no_alternative")
        _vocabulary_fix(seg, rv, vocabulary)
        return rv

    # ---- בלי תמלול חוזר: רק אוצר המילים, ורק למילים לא בטוחות ----
    rv.status = "flagged"
    rv.reason = _t("reason.low_confidence")
    _vocabulary_fix(seg, rv, vocabulary)
    return rv


def _vocabulary_fix(seg: Segment, rv: SegmentReview, vocabulary: Sequence[str]) -> None:
    """
    מילה לא בטוחה שדומה מאוד למונח מאוצר המילים (בכתיב אחר) מוחלפת בו –
    רק מילה בודדת, רק כשהיא לא בטוחה, ורק בדמיון גבוה. אחרת נשאר הסימון.
    """
    if not vocabulary or not seg.words:
        return
    single = [v for v in vocabulary if len(v.split()) == 1 and len(norm_text(v)) >= 3]
    changes = []
    words = [w.to_dict() for w in seg.words]
    for i, w in enumerate(seg.words):
        if float(w.probability) >= LOW_P:
            continue
        core = norm_text(w.text)
        if len(core) < 3:
            continue
        best, best_sim, best_ok = None, 0.0, False
        for v in single:
            nv = norm_text(v)
            sim = difflib.SequenceMatcher(None, core, nv).ratio()
            # מילה קצרה: אות אחת שונה („אוהת"/„אוהד") מורידה את הדמיון מתחת
            # לסף, ולכן גם מרחק עריכה 1 נחשב התאמה (במילה של 4+ אותיות)
            ok = sim >= VOCAB_SIM or (min(len(core), len(nv)) >= 4 and _edit1(core, nv))
            if ok and sim > best_sim:
                best, best_sim, best_ok = v, sim, ok
        if best and best_ok and norm_text(best) != core:
            lead = re.match(r"^\W*", w.text).group(0)
            tail = re.search(r"\W*$", w.text).group(0)
            words[i]["text"] = f"{lead}{best}{tail}"
            changes.append({"from": w.text, "to": best, "similarity": round(best_sim, 3)})
    if changes:
        rv.status, rv.source = "corrected", "vocabulary"
        rv.corrected = " ".join(d["text"] for d in words)
        rv.corrected_words = words
        rv.confidence = round(min(c["similarity"] for c in changes), 3)
        rv.evidence.append({"kind": "vocabulary", "changes": changes})
        rv.reason = _t("reason.vocabulary", terms=", ".join(c["to"] for c in changes))


def _edit1(a: str, b: str) -> bool:
    """מרחק עריכה בדיוק 1 (החלפה, הוספה או מחיקה של תו אחד)."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(1 for x, y in zip(a, b) if x != y) == 1
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


# --------------------------------------------------------------------------
# 2. שמיעה חוזרת + תזמור
# --------------------------------------------------------------------------
Retranscriber = Callable[[float, float], Optional[list[Segment]]]


class WhisperRetranscriber:
    """
    מתמלל חלון קצר במודל החזק. המודל נטען רק בקריאה הראשונה (אם אין שום
    משפט חשוד – הוא לא נטען ולא מורד בכלל). `status`: "" | same_model |
    unavailable.
    """

    def __init__(self, audio_path: Path, settings: AppSettings, language: Optional[str],
                 cancel_event: Optional[threading.Event] = None) -> None:
        from ..profiles import asr_plan

        self.audio_path = Path(audio_path)
        self.settings = settings
        self.language = language
        self.cancel_event = cancel_event
        base = asr_plan(settings, language=language)
        self.plan = type(base)(**{**base.to_dict(), "model": base.strong_model,
                                  "beam_size": max(5, base.beam_size)})
        self.model_name = self.plan.model
        self.status = "same_model" if base.strong_model == base.model else ""
        self._model: Any = None

    @property
    def usable(self) -> bool:
        return self.status == ""

    def _load(self) -> Any:
        if self._model is None and self.usable:
            from .transcribe import FasterWhisperProvider

            try:
                self._model = FasterWhisperProvider()._load_model(self.settings, self.plan)
            except Exception as exc:                   # noqa: BLE001
                log.warning("strong model unavailable for targeted re-transcription: %s", exc)
                self.status = "unavailable"
        return self._model

    def __call__(self, a: float, b: float) -> Optional[list[Segment]]:
        from ..util.wav import read_wav_float32

        if self.cancel_event is not None and self.cancel_event.is_set():
            return None
        model = self._load()
        if model is None:
            return None
        audio = read_wav_float32(self.audio_path, start=a, duration=b - a)
        if audio is None or audio.size == 0:
            return None
        segs, info = model.transcribe(
            audio, language=self.language or self.plan.language, task="transcribe",
            beam_size=self.plan.beam_size, vad_filter=False, word_timestamps=True,
            condition_on_previous_text=False, hotwords=self.plan.hotwords)
        out = []
        for sgm in segs:
            out.append(Segment(
                start=float(sgm.start) + a, end=float(sgm.end) + a, text=(sgm.text or "").strip(),
                words=[Word(start=float(w.start) + a, end=float(w.end) + a,
                            text=str(w.word).strip(),
                            probability=float(getattr(w, "probability", 1.0) or 1.0))
                       for w in (sgm.words or []) if w.start is not None and w.end is not None],
                language=getattr(info, "language", "") or (self.language or "")))
        return out


def review_transcript(transcript: TranscriptResult, *, spans: Optional[Sequence[tuple[float, float]]],
                      retranscribe: Optional[Retranscriber], vocabulary: Sequence[str] = (),
                      strong_model: str = "", budget_seconds: Optional[float] = None,
                      cloud: Optional[Retranscriber] = None, cloud_model: str = "",
                      previous: Optional[dict[str, Any]] = None,
                      on_progress: Optional[Callable[[float], None]] = None) -> dict[str, Any]:
    """
    בודק את המשפטים החשודים (רק בתוך `spans` אם ניתן – למשל הקליפים שנבחרו)
    ומחזיר את קובץ התיקונים. תיקונים קודמים (`previous`) נשמרים ולא נבדקים שוב.
    """
    segs = transcript.segments
    done: dict[int, dict[str, Any]] = {}
    if previous and previous.get("version") == CORRECTIONS_VERSION:
        for r in previous.get("segments") or []:
            if 0 <= int(r.get("index", -1)) < len(segs) and \
                    segs[int(r["index"])].text == r.get("original"):
                done[int(r["index"])] = r

    def in_spans(s: Segment) -> bool:
        return spans is None or any(s.end > a and s.start < b for a, b in spans)

    todo = []
    for i, s in enumerate(segs):
        if i in done or not in_spans(s) or not s.text.strip():
            continue
        sus, _ = suspicious(s)
        if sus:
            todo.append(i)

    budget = budget_seconds if budget_seconds is not None else \
        max(60.0, BUDGET_FRACTION * float(transcript.duration or 0.0))
    windows = windows_for(segs, todo, float(transcript.duration or 0.0))
    used = 0.0
    skipped = 0
    for k, (a, b, idxs) in enumerate(windows):
        alt = None
        if retranscribe is not None and used + (b - a) <= budget:
            try:
                alt = retranscribe(a, b)
                used += b - a
            except Exception as exc:                   # noqa: BLE001
                log.warning("re-transcription %.1f-%.1f failed: %s", a, b, exc)
        elif retranscribe is not None:
            skipped += 1
        for i in idxs:
            cl = None
            if cloud is not None:
                # משפט-משפט (לא כל החלון): לענן אין זמני מילים, אז החלון קצר
                # ומדויק כדי שהטקסט ייוחס למשפט הנכון
                try:
                    cl = cloud(max(0.0, segs[i].start - CLOUD_PAD), segs[i].end + CLOUD_PAD)
                except Exception as exc:               # noqa: BLE001
                    log.warning("cloud re-transcription failed: %s", type(exc).__name__)
            done[i] = decide(segs[i], i, alt, vocabulary=vocabulary, strong_model=strong_model,
                             cloud=cl, cloud_model=cloud_model).to_dict()
        if on_progress:
            on_progress((k + 1) / max(1, len(windows)))

    items = [done[i] for i in sorted(done)]
    return {
        "version": CORRECTIONS_VERSION, "strong_model": strong_model,
        "retranscribed_seconds": round(used + float((previous or {}).get("retranscribed_seconds", 0.0)), 2),
        "budget_skipped_windows": skipped,
        "stats": {s: sum(1 for r in items if r["status"] == s)
                  for s in ("confirmed", "corrected", "flagged")},
        "cloud_model": cloud_model if cloud is not None else "",
        "segments": items,
    }


# --------------------------------------------------------------------------
# 5. מודל שפה – בחירה בין חלופות שהגיעו מהאודיו בלבד
# --------------------------------------------------------------------------
def llm_choose(corrections: dict[str, Any], transcript: TranscriptResult,
               settings: AppSettings, *, vocabulary: Sequence[str] = ()) -> int:
    """
    למשפטים שסומנו ויש להם חלופה מהתמלול החוזר: מודל השפה בוחר "A" (המקור)
    או "B" (החלופה) לפי ההקשר ואוצר המילים. הוא לא יכול לכתוב טקסט משלו –
    תשובה שאינה A/B נדחית. מחזיר כמה תיקונים התקבלו.
    """
    from . import llm

    if not llm.is_llm_enabled(settings):
        return 0
    segs = transcript.segments
    changed = 0
    for r in corrections.get("segments") or []:
        if r.get("status") != "flagged":
            continue
        alt = next((e for e in r.get("evidence") or [] if e.get("kind") == "retranscription"
                    and e.get("text")), None) or \
            next((e for e in r.get("evidence") or [] if e.get("kind") == "cloud"
                  and e.get("text")), None)
        if alt is None or norm_text(alt["text"]) == norm_text(r["original"]):
            continue
        i = int(r["index"])
        before = " ".join(s.text for s in segs[max(0, i - 2): i])
        after = " ".join(s.text for s in segs[i + 1: i + 3])
        system = ("You compare two speech-recognition hypotheses of the same audio. "
                  "Answer ONLY with JSON {\"choice\": \"A\"|\"B\"|\"unsure\"}. "
                  "Never write new text.")
        user = json.dumps({"context_before": before, "context_after": after,
                           "A": r["original"], "B": alt["text"],
                           "known_terms": list(vocabulary)[:40]}, ensure_ascii=False)
        try:
            raw = llm.call_model(system, user, settings)
            choice = str(llm._parse_json(raw).get("choice", "")).strip().upper()
        except Exception as exc:                       # noqa: BLE001
            log.info("llm choice skipped: %s", exc)
            continue
        r["evidence"].append({"kind": "llm_choice", "choice": choice})
        if choice == "B":
            seg = segs[i]
            r["status"], r["source"] = "corrected", "llm_choice"
            r["corrected"] = alt["text"]
            r["confidence"] = alt.get("confidence", 0.0)
            # הזמנים: של החלופה (מהתמלול החוזר) כשיש, אחרת פריסה על משך המשפט
            toks = alt["text"].split()
            step = (seg.end - seg.start) / max(1, len(toks))
            r["corrected_words"] = alt.get("words") or [
                {"start": round(seg.start + k * step, 3), "end": round(seg.start + (k + 1) * step, 3),
                 "text": t, "p": alt.get("confidence", 0.0)} for k, t in enumerate(toks)]
            r["reason"] = _t("reason.llm_choice")
            changed += 1
    if changed:
        items = corrections.get("segments") or []
        corrections["stats"] = {s: sum(1 for x in items if x["status"] == s)
                                for s in ("confirmed", "corrected", "flagged")}
    return changed


# --------------------------------------------------------------------------
# החלה על התמלול, ועריכות משתמש
# --------------------------------------------------------------------------
def apply(transcript: TranscriptResult, corrections: Optional[dict[str, Any]]) -> TranscriptResult:
    """
    התמלול האפקטיבי: המקור, עם התיקונים (מילים וזמנים מהראיה) ועם סימון
    המילים הלא בטוחות במשפטים שסומנו לבדיקה. המקור עצמו לא משתנה.
    """
    if not corrections:
        return transcript
    by_index = {int(r["index"]): r for r in corrections.get("segments") or []}
    out: list[Segment] = []
    for i, s in enumerate(transcript.segments):
        r = by_index.get(i)
        if r is None or r.get("original") != s.text:
            out.append(s)
            continue
        if r["status"] == "corrected" and r.get("corrected_words"):
            words = [Word(start=float(w["start"]), end=float(w["end"]), text=w["text"],
                          probability=float(w.get("p", 1.0)), flag="corrected")
                     for w in r["corrected_words"]]
            # המילים המקוריות נשמרות על המילה הראשונה של התיקון, לעורך
            if words:
                words[0].asr = s.text
            out.append(Segment(start=s.start, end=s.end, text=r["corrected"] or s.text,
                               words=words, language=s.language, avg_logprob=s.avg_logprob,
                               no_speech_prob=s.no_speech_prob))
        elif r["status"] == "flagged":
            low = {int(x["i"]) for x in r.get("low_words") or []}
            words = [Word(start=w.start, end=w.end, text=w.text, probability=w.probability,
                          flag=("low" if k in low else ""))
                     for k, w in enumerate(s.words)]
            out.append(Segment(start=s.start, end=s.end, text=s.text, words=words,
                               language=s.language, avg_logprob=s.avg_logprob,
                               no_speech_prob=s.no_speech_prob))
        else:
            out.append(s)
    return TranscriptResult(segments=out, language=transcript.language,
                            duration=transcript.duration, provider=transcript.provider,
                            model=transcript.model, note=transcript.note,
                            meta={**transcript.meta, "corrections": corrections.get("stats", {})})


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


def record_user_edit(path: Path, *, clip_id: str, cue_id: int, source_start: float,
                     source_end: float, before: str, after: str, asr_text: str) -> None:
    """עריכה ידנית בעורך: נשמרת ביומן התיקונים לצד מה שהמזהה שמע."""
    from datetime import datetime, timezone

    data = load(path) or {"version": CORRECTIONS_VERSION, "segments": []}
    data.setdefault("user_edits", []).append({
        "clip_id": clip_id, "cue_id": cue_id,
        "source_start": round(source_start, 3), "source_end": round(source_end, 3),
        "asr_text": asr_text, "before": before, "after": after,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    data["user_edits"] = data["user_edits"][-2000:]
    save(path, data)
