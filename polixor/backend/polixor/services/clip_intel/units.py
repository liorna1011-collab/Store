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
    # "" (לא שאלה) | tag („נכון?") | trivial („איזה יום היום?") | meaningful
    question_kind: str = ""
    private: bool = False        # שיחה פרטית/קריאה למישהו מחוץ למיקרופון
    garbled: bool = False        # רוב המילים בזיהוי לא בטוח – הטקסט לא אמין

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def content_count(self) -> int:
        return len(self.tokens)

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
        u.question_kind = question_kind(u.text, u.is_question, packs)
        # שאלה שגרתית/שאלת-תג אינה סקרנות: לא נותנים לה את ניקוד השאלה
        s.is_question = u.question_kind == "meaningful"
        hs = score_sentence(s, language=language)
        s.is_question = u.is_question
        u.hook, u.hook_detail = float(hs.total), hs.to_dict()

        if u.words:
            u.low_conf = sum(1 for w in u.words if float(w.probability) < LOW_CONFIDENCE_P) \
                / len(u.words)
        u.flags = _flags(u.text, packs)
        u.tokens = _content_tokens(u.text, packs)
        if u.flags.get("verdict_markers", 0) == 0 and _conditional_verdict(u.text, packs) \
                and len(u.tokens) >= 4:
            u.flags["verdict_markers"] = 1
        u.garbled = len(u.words) >= 3 and u.low_conf >= GARBLED_SHARE
        u.private = bool(u.flags.get("private_markers")) or _vocative(u.text, packs)
        punctuated = bool(_SENT_END.search(u.text))
        u.ends_sentence = punctuated or u.pause_after >= 0.45 or u.index == len(sentences) - 1
        last = u.text.split()[-1] if u.text.split() else ""
        # פיסוק סוגר גובר: „עשיתי את זה!" נגמר, גם ש„זה" לבדו נשען קדימה
        if not punctuated and last and _hanging(last, packs):
            u.ends_sentence = False
        units.append(u)
    _mark_name_calls(units, packs)
    return units


# --------------------------------------------------------------------------
# סמנטיקה: סוג שאלה, קריאה למישהו, מסקנה מותנית
# --------------------------------------------------------------------------
GARBLED_SHARE = 0.5
_WORD = re.compile(r"[\w֐-׿']+", re.UNICODE)


def _norm(text: str, pack: Any) -> str:
    return re.sub(r"\s+", " ", pack.normalizer(text or "")).strip()


def question_kind(text: str, is_question: bool, packs: Sequence[Any]) -> str:
    """
    סוג השאלה: tag („…, נכון?", „אתה מבין?"), trivial („איזה יום היום?",
    „אתה כבר בבית?") או meaningful (שאלה עם תוכן: „האם שווה לשדר?",
    „למה דווקא הוא?"). שאלה לא הופכת וו רק בגלל סימן השאלה.
    """
    if not is_question:
        return ""
    parts = [p for p in re.split(r"(?<=\?)", text or "") if "?" in p]
    clause = (parts[-1] if parts else text or "").replace("?", " ")
    # אם המשפט כולו כמה שאלות – השאלה עם הכי הרבה תוכן קובעת
    if len(parts) > 1:
        clause = max((p.replace("?", " ") for p in parts),
                     key=lambda c: len(_content_tokens(c, packs)))
    for pack in packs:
        low = _norm(clause, pack)
        for ph in pack.trivial_questions:
            if pack.pattern(ph).search(low):
                return "trivial"
        words = _WORD.findall(low)
        tags = {_norm(t, pack) for t in pack.tag_questions}
        if words and (" ".join(words) in tags or (len(words) <= 2 and words[-1] in tags)):
            return "tag"
        # „…יום ראשון, נכון?" – משפט חיווי עם תג בסופו
        tail = re.split(r"[,،]", low)[-1].strip()
        if tail and tail in tags and len(words) > len(tail.split()):
            return "tag"
    content = _content_tokens(clause, packs)
    interrog = any(p.count(clause, p.curiosity) for p in packs)
    stance = any(p.count(clause, f) for p in packs
                 for f in (p.stance_markers, p.conflict_markers, p.comparison_markers))
    if len(content) >= 2 and (interrog or stance):
        return "meaningful"
    if len(content) >= 3:
        return "meaningful"
    return "trivial"


def _vocative(text: str, packs: Sequence[Any]) -> bool:
    """„שילו! שילו!" – קריאה בשם שחוזרת, בלי תוכן: פנייה למישהו מחוץ לשידור."""
    if "!" not in (text or ""):
        return False
    for pack in packs:
        low = _norm(text, pack)
        words = [w for w in _WORD.findall(low) if w not in pack.stop_words]
        if not words or len(words) > 4:
            continue
        if pack.count(text, pack.reaction_tokens) or pack.count(text, pack.cheers):
            continue
        if len(set(words)) < len(words):
            return True
    return False


def _mark_name_calls(units: list[Unit], packs: Sequence[Any]) -> None:
    """שתי יחידות רצופות שכל אחת היא אותה מילה בודדת בקריאה („שילו!" / „שילו!")."""
    for a, b in zip(units, units[1:]):
        wa, wb = _WORD.findall(a.text.lower()), _WORD.findall(b.text.lower())
        if (len(wa) == 1 and wa == wb and "!" in a.text + b.text
                and b.start - a.end < 2.0
                and not any(p.count(a.text, p.reaction_tokens) or p.count(a.text, p.cheers)
                            for p in packs)):
            a.private = b.private = True


_CONDITIONAL = {
    "he": re.compile(r"(^|\s)(ו?אם)\s.+\s(אז|ת\w{2,})(\s|$)"),
    "en": re.compile(r"(^|\s)if\s.+\s(then|you'll|you will|you won't)(\s|$)"),
}


def _conditional_verdict(text: str, packs: Sequence[Any]) -> bool:
    """„אם אתה מספיק טוב – תצליח; אם לא, אז…" – מסקנה מותנית, הכרעה של טיעון."""
    for pack in packs:
        rx = _CONDITIONAL.get(pack.code)
        if rx and rx.search(_norm(text, pack)):
            return True
    return False


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
              "curiosity", "chitchat", "claim", "stance_markers", "conflict_markers",
              "comparison_markers", "verdict_markers", "private_markers", "explain_requests")
    out: dict[str, int] = {}
    for f in fields:
        out[f] = sum(p.count(text, getattr(p, f, ()) or ()) for p in packs)
    out["continuation"] = int(any(_starts_with(text, getattr(p, "continuation_starters", ()), p)
                                  for p in packs))
    out["pronoun_start"] = int(any(_starts_with(text, getattr(p, "dangling_pronouns", ()), p)
                                   for p in packs))
    out["trailing_tag"] = int(_only_trailing(text, packs))
    return out


def _only_trailing(text: str, packs: Sequence[Any]) -> bool:
    """המשפט כולו זנב („אתה מבין?", „כאילו בסוף תחשוב") – לא תוכן."""
    for p in packs:
        low = _norm(text, p)
        words = _WORD.findall(low)
        if not words or len(words) > 4:
            continue
        rest = low
        for t in sorted(p.trailing_tags + p.tag_questions, key=len, reverse=True):
            rest = p.pattern(t).sub(" ", rest)
        if _WORD.findall(rest) == words:
            continue                                  # אף תג לא נמצא
        left = [w for w in _WORD.findall(rest) if w not in p.stop_words and w not in p.filler_tokens]
        if len(left) <= 1:
            return True
    return False


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
