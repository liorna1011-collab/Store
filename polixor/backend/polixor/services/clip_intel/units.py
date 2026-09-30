"""
יחידות שיח: המשפטים של התמלול, עם האותות שהמנוע צריך.

כל האותות מנורמלים **מקומית** (מול הסביבה של כשתי דקות סביב המשפט) ולא
מול השידור כולו. בשידור של שעות, שעה רועשת אחת לא אמורה להעלים רגעים
טובים בשעה שקטה יותר – תגובה נמדדת ביחס למה שהיה רגיל *באותו רגע*.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import numpy as np

from .. import lang as _lang
from ..hook_engine import score_sentence
from ..scoring import Timeline
from ..semantics import Sentence, annotate, split_sentences
from ..transcribe import TranscriptResult, Word

# מילה שהסתברות הזיהוי שלה נמוכה מזה נחשבת "לא בטוחה"
LOW_CONFIDENCE_P = 0.45
# כמה שניות אחרי משפט מחפשים תגובה (צחוק, צעקה)
REACTION_WINDOW = 2.5
# רוחב הסביבה לנרמול מקומי (שניות, לכל צד)
LOCAL_RADIUS = 60.0
# כמה הערוץ (סקאלה 0..1) צריך לעלות מעל החציון המקומי כדי להיחשב שיא מלא
MIN_SPAN = 0.25

_SENT_END = re.compile(r"[.!?…]+[\"'”’)]*$")
_EXCLAIM = re.compile(r"!")


@dataclass
class Unit:
    index: int
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)

    energy: float = 0.0          # עוצמה מקומית-יחסית 0..1
    peak: float = 0.0            # שיא מקומי-יחסי 0..1
    interest: float = 0.0        # הציון המשולב הקיים (כולל חזותי/צ'אט)
    lexical: float = 0.0
    filler_ratio: float = 0.0
    is_question: bool = False
    exclaim: bool = False
    hook: float = 0.0            # hook_engine.score_sentence
    hook_detail: dict[str, float] = field(default_factory=dict)
    pause_before: float = 0.0
    pause_after: float = 0.0
    low_conf: float = 0.0        # חלק המילים עם הסתברות זיהוי נמוכה
    reaction_after: float = 0.0  # תגובה קולית מיד אחרי המשפט 0..1
    ends_sentence: bool = True   # נגמר בסוף משפט (פיסוק/הפסקה), לא באמצע
    flags: dict[str, int] = field(default_factory=dict)
    tokens: list[str] = field(default_factory=list)   # מילות תוכן להשוואת כפילויות

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def has(self, flag: str) -> bool:
        return self.flags.get(flag, 0) > 0


# --------------------------------------------------------------------------
class LocalNorm:
    """נרמול מקומי של ערוץ בציר הזמן: (ערך − חציון מקומי) / (p95 − חציון)."""

    def __init__(self, arr: np.ndarray, hop: float, radius: float = LOCAL_RADIUS) -> None:
        self.hop = hop
        self.arr = np.asarray(arr, dtype=np.float32)
        # מצמצמים לשנייה (מקסימום בכל שנייה) – זול גם לשידור של 8 שעות
        per = max(1, int(round(1.0 / max(1e-6, hop))))
        n_sec = max(1, int(np.ceil(self.arr.size / per)))
        padded = np.zeros(n_sec * per, dtype=np.float32)
        padded[: self.arr.size] = self.arr
        sec = padded.reshape(n_sec, per).max(axis=1)
        r = max(1, int(round(radius)))
        ext = np.pad(sec, (r, r), mode="edge")
        win = np.lib.stride_tricks.sliding_window_view(ext, 2 * r + 1)
        self.med = np.median(win, axis=1).astype(np.float32)
        self.hi = np.percentile(win, 95, axis=1).astype(np.float32)
        self.sec = sec

    def rel(self, value: float, t: float) -> float:
        i = int(min(self.med.size - 1, max(0, int(t))))
        # רוחב מינימלי מוחלט: בערוץ שטוח (רעש בלבד) p95 קרוב לחציון, ובלי
        # הרצפה הזו רעש של ±2% היה נקרא כ"תגובה מלאה"
        span = max(MIN_SPAN, float(self.hi[i] - self.med[i]))
        return float(np.clip((value - float(self.med[i])) / span, 0.0, 1.0))

    def window(self, start: float, end: float) -> np.ndarray:
        i0 = int(max(0, np.floor(start / self.hop)))
        i1 = int(min(self.arr.size, max(i0 + 1, np.ceil(end / self.hop))))
        return self.arr[i0:i1]


# --------------------------------------------------------------------------
def build_units(transcript: Optional[TranscriptResult], tl: Timeline,
                language: Optional[str]) -> list[Unit]:
    """מפרק את התמלול ליחידות ומחשב לכל אחת את האותות."""
    if transcript is None or not transcript.has_speech:
        return []
    sentences: list[Sentence] = split_sentences(transcript)
    if not sentences:
        return []
    annotate(sentences, audio=None, language=language)

    packs = _lang.packs_for(language)
    vocal = LocalNorm(tl.vocal, tl.hop) if tl.n else None

    units: list[Unit] = []
    for s in sentences:
        u = Unit(index=len(units), start=float(s.start), end=float(s.end),
                 text=s.text.strip(), words=list(s.words))
        u.lexical, u.filler_ratio = float(s.lexical), float(s.filler_ratio)
        u.is_question = bool(s.is_question)
        u.exclaim = bool(_EXCLAIM.search(u.text))
        u.pause_before, u.pause_after = float(s.pause_before), float(s.pause_after)

        if vocal is not None:
            seg = vocal.window(u.start, u.end)
            if seg.size:
                u.energy = vocal.rel(float(seg.mean()), (u.start + u.end) / 2)
                u.peak = vocal.rel(float(seg.max()), (u.start + u.end) / 2)
            after = vocal.window(u.end, u.end + REACTION_WINDOW)
            if after.size:
                u.reaction_after = vocal.rel(float(after.max()), u.end)
        if tl.n:
            u.interest = float(tl.window_mean(tl.score, u.start, u.end))

        s.energy = u.energy          # hook_engine קורא את אותם אותות
        hs = score_sentence(s, language=language)
        u.hook, u.hook_detail = float(hs.total), hs.to_dict()

        if u.words:
            u.low_conf = sum(1 for w in u.words if float(w.probability) < LOW_CONFIDENCE_P) \
                / len(u.words)
        u.flags = _flags(u.text, packs)
        u.tokens = _content_tokens(u.text, packs)
        punctuated = bool(_SENT_END.search(u.text))
        u.ends_sentence = punctuated or u.pause_after >= 0.45 or u.index == len(sentences) - 1
        last = u.text.split()[-1] if u.text.split() else ""
        # פיסוק סוגר גובר: „עשיתי את זה!" נגמר, גם ש„זה" לבדו נשען קדימה
        if not punctuated and last and _hanging(last, packs):
            u.ends_sentence = False
        units.append(u)
    return units


def _hanging(token: str, packs: Sequence[Any]) -> bool:
    """
    מילה שמשפט לא נגמר בה („של", „את", „ו…"). מחמיר יותר מ-is_hanging של
    הכתוביות: רק התחילית ו' מוסרת, כדי ש„כזה" לא ייקרא כ-כ+„זה".
    """
    bare = re.sub(r"[^\w֐-׿']", "", (token or "").lower())
    if not bare:
        return False
    for p in packs:
        words = p.hanging_words
        if bare in words or (bare.startswith("ו") and bare[1:] in words and len(bare) > 2):
            return True
    return False


def _flags(text: str, packs: Sequence[Any]) -> dict[str, int]:
    fields = ("backrefs", "payoff_markers", "reaction_tokens", "closure_markers",
              "story_openers", "emotion", "offtopic", "afk", "cta", "topic_shift",
              "curiosity")
    out: dict[str, int] = {}
    for f in fields:
        out[f] = sum(p.count(text, getattr(p, f, ()) or ()) for p in packs)
    out["continuation"] = int(any(_starts_with(text, getattr(p, "continuation_starters", ()), p)
                                  for p in packs))
    return out


def _starts_with(text: str, phrases: Sequence[str], pack: Any) -> bool:
    """האם הטקסט *נפתח* באחד הביטויים (לא סתם מכיל אותו)."""
    low = pack.normalizer(text)
    low = re.sub(r"^[\s\"'“”‘’(\[\-–—.,…]+", "", low)
    for ph in phrases:
        ph_low = pack.normalizer(ph)
        if low == ph_low or low.startswith(ph_low + " ") or low.startswith(ph_low + ","):
            return True
    return False


_TOKEN = re.compile(r"[\w֐-׿']+", re.UNICODE)


def _content_tokens(text: str, packs: Sequence[Any]) -> list[str]:
    stop: set[str] = set()
    fillers: set[str] = set()
    for p in packs:
        stop |= set(p.stop_words)
        fillers |= set(p.filler_tokens)
    out = []
    for tok in _TOKEN.findall((text or "").lower()):
        if len(tok) < 2 or tok in stop or tok in fillers or tok.isdigit():
            continue
        # עברית: מסירים תחילית דבוקה כדי ש„והמשחק" ו„המשחק" ייחשבו אותה מילה
        for p in packs:
            base = p.strip_prefix(tok) if p.prefixes else None
            if base:
                tok = base
                break
        out.append(tok)
    return out
