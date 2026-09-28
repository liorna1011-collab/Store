"""
הבנה סמנטית של הסרטון: משפטים, מילות מילוי, ותפקיד נרטיבי.

המודול הזה לא עורך כלום. הוא רק **קורא** את התמלול ואת אותות האודיו
ומחזיר תיאור מובנה של מה קורה בסרטון — אילו משפטים נאמרו, מה תפקיד כל
אחד בסיפור, ואיפה יש מילות מילוי או התחלות כושלות.

מי שמחליט מה לעשות עם המידע הזה הוא ה-Director. ההפרדה הזו מכוונת:
כך אפשר לבדוק את ההבנה בנפרד מההחלטות, וההחלטות ניתנות לשינוי בלי
לגעת בניתוח.

שני מנועים
----------
* **היוריסטי** — זמין תמיד, ללא מודל שפה ובלי עלות. לקסיקון עברית
  ואנגלית, מיקום במבנה, אנרגיית אודיו, קצב דיבור וסימני פיסוק.
* **מודל שפה** — כשמוגדר, לסיווג מדויק יותר. נופל חזרה להיוריסטי אם
  אינו זמין או החזיר תשובה פסולה. **לעולם אינו רץ בלי היוריסטי לפניו**,
  כדי שתמיד תהיה תוצאה.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Sequence

import numpy as np

from .. import i18n
from ..config import AppSettings
from . import lang as _lang
from .audio import AudioFeatures
from .transcribe import TranscriptResult, Word

log = logging.getLogger("polixor.semantics")


# --------------------------------------------------------------------------
# תפקידים נרטיביים
# --------------------------------------------------------------------------
class BeatRole(str, Enum):
    HOOK = "hook"                    # שלוש השניות שמחזיקות את הצופה
    SETUP = "setup"                  # הקשר, רקע
    MAIN_IDEA = "main_idea"          # הטענה המרכזית
    TENSION = "tension"              # בעיה, שאלה, החרפה
    EMOTIONAL_PEAK = "emotional_peak"  # השיא הרגשי
    PAYOFF = "payoff"                # הפתרון, הפאנץ'
    KEY_CLAIM = "key_claim"          # משפט שכדאי להדגיש
    CTA = "cta"                      # קריאה לפעולה
    TOPIC_CHANGE = "topic_change"    # מעבר לנושא חדש
    FILLER = "filler"                # לא מוסיף כלום


ROLE_LABELS_HE: dict[str, str] = {
    "hook": "וו פתיחה",
    "setup": "הקשר",
    "main_idea": "הרעיון המרכזי",
    "tension": "מתח",
    "emotional_peak": "שיא רגשי",
    "payoff": "פאנץ׳",
    "key_claim": "טענה מרכזית",
    "cta": "קריאה לפעולה",
    "topic_change": "מעבר נושא",
    "filler": "מילוי",
}

# תפקידים שראוי להדגיש ויזואלית (זום פנימה, כתובית מודגשת)
EMPHASIS_ROLES = frozenset({"hook", "emotional_peak", "key_claim", "payoff"})
# תפקידים שמותר לחתוך בלב שלם כשצריך לקצר
DROPPABLE_ROLES = frozenset({"filler", "setup"})


# --------------------------------------------------------------------------
# לקסיקונים
# --------------------------------------------------------------------------
# הלקסיקונים עצמם נמצאים בחבילות השפה (services/lang). השמות הישנים
# נשארים כאן לתאימות לאחור עם קוד ובדיקות שמייבאים אותם.
_HE, _EN = _lang.HEBREW, _lang.ENGLISH
FILLER_TOKENS_HE = set(_HE.filler_tokens)
FILLER_TOKENS_EN = set(_EN.filler_tokens)
FILLER_PHRASES_HE = list(_HE.filler_phrases)
FILLER_PHRASES_EN = list(_EN.filler_phrases)
CTA_HE, CTA_EN = list(_HE.cta), list(_EN.cta)
HOOK_HE, HOOK_EN = list(_HE.hook), list(_EN.hook)
TOPIC_SHIFT_HE, TOPIC_SHIFT_EN = list(_HE.topic_shift), list(_EN.topic_shift)
EMOTION_HE, EMOTION_EN = list(_HE.emotion), list(_EN.emotion)
CLAIM_HE, CLAIM_EN = list(_HE.claim), list(_EN.claim)


def role_label(role: str, lang: Optional[str] = None) -> str:
    """שם התפקיד בשפת הממשק."""
    return i18n.tr(f"analysis.role.{role}", lang, default=role)


def _pack_hits(text: str, field: str, packs: Sequence[Any], *,
               skip_negated: bool = False) -> int:
    """כמה ביטויים מהרשימה `field` של כל חבילה מופיעים בטקסט."""
    return sum(p.count(text, getattr(p, field), skip_negated=skip_negated)
               for p in packs)


def _is_filler_token(tok: str, packs: Sequence[Any]) -> bool:
    return any(tok in p.filler_tokens for p in packs)


_HEBREW_RE = re.compile(r"[֐-׿]")
_SENT_END_RE = re.compile(r"[.!?…]+[\"'”״]?\s*$")
_WORD_RE = re.compile(r"[\w֐-׿']+", re.UNICODE)

# פער שקט שמסיים משפט גם בלי פיסוק
SENTENCE_GAP = 0.62
# משפט ארוך מזה נשבר גם באמצע. שישה שניות הן כבר פסקה, לא משפט,
# ומעליהן אי אפשר להחליט עליו שום דבר מועיל.
MAX_SENTENCE_SECONDS = 6.0


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def _tokens(text: str) -> list[str]:
    return _WORD_RE.findall(text or "")


# אותיות שנחשבות חלק ממילה. משמש לגבולות מילה בעברית ובאנגלית.
_WORDCHAR = r"\w֐-׿"
# תחיליות עברית שנדבקות למילה: ו, ה, ב, ל, כ, מ, ש, כש
_PREFIX = r"(?:ו?[הבלכמש]|כש|ו)?"

_phrase_cache: dict[str, re.Pattern[str]] = {}


def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    """
    בונה ביטוי רגולרי לביטוי מהלקסיקון, עם גבולות מילה.

    התאמת תת-מחרוזת פשוטה שוברת את הלקסיקון בעברית: הביטוי „כי "
    מותאם בתוך „ה<b>כי </b>נמוך", ו„לבד" בתוך „לבדוק". לכן כל ביטוי
    נבדק בגבולות מילה, עם אפשרות לתחילית עברית דבוקה (ו/ה/ב/ל/כ/מ/ש)
    לפני המילה הראשונה.
    """
    pat = _phrase_cache.get(phrase)
    if pat is None:
        clean = phrase.strip()
        body = re.escape(clean)
        # רווח בביטוי = רווח אחד או יותר
        body = re.sub(r"(\\?\s)+", r"\\s+", body)
        # מילה קצרה לא מקבלת הרחבת תחילית: „הכי" (=הכי) היה נקרא
        # כ-ה+„כי", ו„ולא" כ-ו+„לא". מעל שתי אותיות הסיכון זניח.
        first_word = clean.split()[0] if clean.split() else clean
        prefix = _PREFIX if len(first_word) > 2 else ""
        pat = re.compile(
            rf"(?<![{_WORDCHAR}]){prefix}{body}(?![{_WORDCHAR}])",
            re.IGNORECASE)
        _phrase_cache[phrase] = pat
    return pat


def _hits(text_low: str, phrases: Sequence[str]) -> int:
    """כמה ביטויים מהרשימה מופיעים בטקסט, בגבולות מילה."""
    return sum(1 for p in phrases if _phrase_pattern(p).search(text_low))


def is_hebrew(text: str) -> bool:
    letters = re.findall(r"[^\W\d_]", text or "", re.UNICODE)
    if not letters:
        return False
    return sum(1 for c in letters if _HEBREW_RE.match(c)) > len(letters) * 0.3


# --------------------------------------------------------------------------
# משפטים
# --------------------------------------------------------------------------
@dataclass
class Sentence:
    """משפט אחד עם התזמון שלו ועם האותות שחושבו עליו."""

    index: int
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)

    # אותות
    energy: float = 0.0            # 0..1 – עוצמת האודיו הממוצעת
    peak_energy: float = 0.0
    speech_rate: float = 0.0       # מילים לשנייה
    lexical: float = 0.0           # ציון עניין לקסיקלי
    filler_ratio: float = 0.0      # חלק המילים שהן מילוי
    is_question: bool = False
    pause_before: float = 0.0
    pause_after: float = 0.0
    # המיקום בתוך חלון הקליפ. `start` הוא זמן בשידור המלא, ולכן
    # כל שיפוט „מוקדם/מאוחר" חייב להתבסס על השדה הזה.
    rel_start: float = 0.0

    # סיווג
    role: str = "setup"
    confidence: float = 0.0
    reason: str = ""
    role_source: str = "heuristic"

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def word_count(self) -> int:
        return len(self.words) or len(_tokens(self.text))

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "text": self.text,
            "role": self.role,
            "role_label": ROLE_LABELS_HE.get(self.role, self.role),
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
            "source": self.role_source,
            "energy": round(self.energy, 3),
            "lexical": round(self.lexical, 3),
            "filler_ratio": round(self.filler_ratio, 3),
            "words": self.word_count,
        }


def split_sentences(transcript: Optional[TranscriptResult], *,
                    start: float = 0.0,
                    end: Optional[float] = None) -> list[Sentence]:
    """
    מפרק את התמלול למשפטים.

    שוברים על פיסוק, על פער שקט משמעותי, ועל אורך מקסימלי — כדי שלא
    ייווצר "משפט" של 20 שניות שאי אפשר להחליט עליו כלום.
    """
    if transcript is None:
        return []
    end = float("inf") if end is None else end

    words: list[Word] = []
    # גבולות המקטעים של המתמלל נושאים מידע: הוא כבר החליט איפה יחידה
    # נגמרת. שוברים גם עליהם, ולא רק על פיסוק ופערים.
    seg_ends: set[int] = set()
    for seg in transcript.segments:
        if seg.words:
            words.extend(seg.words)
        elif seg.text.strip():
            # אין תזמון ברמת מילה: המקטע כולו הוא משפט אחד
            words.append(Word(start=seg.start, end=seg.end,
                              text=seg.text.strip(), probability=1.0))
        if words:
            seg_ends.add(len(words) - 1)

    keep = [i for i, w in enumerate(words) if w.end > start and w.start < end]
    if not keep:
        return _sentences_from_segments(transcript, start, end)
    # שומרים את סימוני הגבולות אחרי הסינון
    remap = {old: new for new, old in enumerate(keep)}
    seg_ends = {remap[i] for i in seg_ends if i in remap}
    words = [words[i] for i in keep]

    sentences: list[Sentence] = []
    bucket: list[Word] = []
    idx = 0

    def flush() -> None:
        nonlocal bucket, idx
        if not bucket:
            return
        text = " ".join(w.text for w in bucket).strip()
        if text:
            sentences.append(Sentence(
                index=idx, start=bucket[0].start, end=bucket[-1].end,
                text=text, words=list(bucket)))
            idx += 1
        bucket = []

    for i, w in enumerate(words):
        bucket.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        gap = (nxt.start - w.end) if nxt else 0.0
        span = w.end - bucket[0].start
        if (_SENT_END_RE.search(w.text) or gap >= SENTENCE_GAP
                or span >= MAX_SENTENCE_SECONDS or i in seg_ends
                or nxt is None):
            flush()
    flush()

    for i, s in enumerate(sentences):
        s.pause_before = (s.start - sentences[i - 1].end) if i else 0.0
        s.pause_after = (sentences[i + 1].start - s.end) if i + 1 < len(sentences) else 0.0
    return sentences


def _sentences_from_segments(transcript: TranscriptResult, start: float,
                             end: float) -> list[Sentence]:
    """נפילה לאחור כשאין תזמון ברמת מילה."""
    out: list[Sentence] = []
    for seg in transcript.segments:
        if seg.end <= start or seg.start >= end or not seg.text.strip():
            continue
        out.append(Sentence(index=len(out), start=seg.start, end=seg.end,
                            text=seg.text.strip()))
    for i, s in enumerate(out):
        s.pause_before = (s.start - out[i - 1].end) if i else 0.0
        s.pause_after = (out[i + 1].start - s.end) if i + 1 < len(out) else 0.0
    return out


# --------------------------------------------------------------------------
# מילות מילוי, היסוסים והתחלות כושלות
# --------------------------------------------------------------------------
@dataclass
class Disfluency:
    """קטע שראוי לשקול להסיר. הזמנים מוחלטים בקובץ המקור."""

    start: float
    end: float
    kind: str          # filler_word | filler_phrase | false_start | repeat
    text: str
    confidence: float
    sentence_index: int = -1

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict[str, Any]:
        return {"start": round(self.start, 3), "end": round(self.end, 3),
                "kind": self.kind, "text": self.text,
                "confidence": round(self.confidence, 3),
                "duration": round(self.duration, 3)}


def _clean_token(text: str) -> str:
    return re.sub(r"[^\w֐-׿']", "", (text or "").lower())


def find_disfluencies(sentences: Sequence[Sentence],
                      language: Optional[str] = None) -> list[Disfluency]:
    """
    מאתר מילות מילוי, היסוסים, התחלות כושלות וחזרות.

    דורש תזמון ברמת מילה: בלעדיו אי אפשר לחתוך מילה בודדת בלי לפגוע
    במשפט, ולכן מוחזרת רשימה ריקה במקום ניחוש.
    """
    packs = _lang.packs_for(language)
    out: list[Disfluency] = []
    for s in sentences:
        if not s.words:
            continue
        out.extend(_filler_words(s, packs))
        out.extend(_false_starts(s))
    out.extend(_cross_boundary_false_starts(sentences))
    out.extend(_repeated_phrases(sentences))
    out.sort(key=lambda d: d.start)
    return _merge_adjacent(out)


def _cross_boundary_false_starts(sentences: Sequence[Sentence]
                                 ) -> list[Disfluency]:
    """
    היסוס שנפל בדיוק על תפר בין משפטים.

    „ואז… ואז הגעתי" עלול להישבר לשני משפטים, ואז אף אחד מהם לבדו
    אינו נראה כמו היסוס. בלי הבדיקה הזו ההיסוסים הכי בולטים דווקא
    נעלמים, כי הם אלה שגורמים לשבירה מלכתחילה.
    """
    found: list[Disfluency] = []
    for a, b in zip(sentences, sentences[1:]):
        if not a.words or not b.words:
            continue
        last, first = a.words[-1], b.words[0]
        ta, tb = _clean_token(last.text), _clean_token(first.text)
        if not ta or ta != tb or len(ta) < 2:
            continue
        if first.start - last.end > 1.2:
            continue
        found.append(Disfluency(
            start=last.start, end=first.start, kind="false_start",
            text=last.text, confidence=0.72, sentence_index=a.index))
    return found


def _filler_words(s: Sentence, packs: Optional[Sequence[Any]] = None) -> list[Disfluency]:
    packs = packs or _lang.packs_for(None)
    found: list[Disfluency] = []
    n = len(s.words)
    for i, w in enumerate(s.words):
        tok = _clean_token(w.text)
        if not tok:
            continue
        if _is_filler_token(tok, packs):
            # מילת מילוי בתחילת משפט פחות מפריעה מאשר באמצעו
            conf = 0.92 if 0 < i < n - 1 else 0.8
            found.append(Disfluency(
                start=w.start, end=w.end, kind="filler_word",
                text=w.text, confidence=conf, sentence_index=s.index))
    return found


def _false_starts(s: Sentence) -> list[Disfluency]:
    """
    מילה שחוזרת על עצמה מיד — „אני... אני רוצה" — היא היסוס.
    מסירים את המופע הראשון ומשאירים את השני.
    """
    found: list[Disfluency] = []
    for i in range(len(s.words) - 1):
        a, b = s.words[i], s.words[i + 1]
        ta, tb = _clean_token(a.text), _clean_token(b.text)
        if not ta or ta != tb or len(ta) < 2:
            continue
        gap = b.start - a.end
        if gap > 1.2:                      # רחוק מדי – כנראה הדגשה מכוונת
            continue
        found.append(Disfluency(
            start=a.start, end=b.start, kind="false_start",
            text=a.text, confidence=0.78, sentence_index=s.index))
    return found


def _repeated_phrases(sentences: Sequence[Sentence], *, n: int = 4,
                      window: float = 25.0) -> list[Disfluency]:
    """
    ביטוי בן ארבע מילים שחוזר בתוך חלון זמן קצר — סימן שהדובר חזר
    על עצמו. מסמנים את המופע **השני**, כדי לשמור על הראשון.
    """
    seen: dict[str, tuple[float, int]] = {}
    found: list[Disfluency] = []
    for s in sentences:
        if len(s.words) < n:
            continue
        toks = [_clean_token(w.text) for w in s.words]
        for i in range(len(toks) - n + 1):
            key = " ".join(toks[i:i + n])
            if len(key) < 12:
                continue
            prev = seen.get(key)
            start_t = s.words[i].start
            if prev and start_t - prev[0] <= window:
                found.append(Disfluency(
                    start=start_t, end=s.words[i + n - 1].end,
                    kind="repeat", text=key, confidence=0.6,
                    sentence_index=s.index))
            seen[key] = (start_t, s.index)
    return found


def _merge_adjacent(items: list[Disfluency], gap: float = 0.12
                    ) -> list[Disfluency]:
    """מאחד קטעים צמודים כדי לא לייצר עשרה חיתוכים זעירים ברצף."""
    if not items:
        return []
    merged = [items[0]]
    for d in items[1:]:
        last = merged[-1]
        if d.start - last.end <= gap and d.kind == last.kind:
            merged[-1] = Disfluency(
                start=last.start, end=max(last.end, d.end), kind=last.kind,
                text=f"{last.text} {d.text}".strip(),
                confidence=max(last.confidence, d.confidence),
                sentence_index=last.sentence_index)
        else:
            merged.append(d)
    return merged


# --------------------------------------------------------------------------
# אותות לכל משפט
# --------------------------------------------------------------------------
def _energy_at(audio: Optional[AudioFeatures], start: float,
               end: float) -> tuple[float, float]:
    """
    (עוצמה ממוצעת, שיא) בטווח, מנורמל 0..1.

    הנרמול הוא מול האחוזון ה-95 של השידור כולו ולא מול המקסימום, כדי
    שפיצוץ בודד לא ידחוס את כל השאר לאפס.
    """
    if audio is None or audio.n == 0:
        return 0.0, 0.0
    e = np.asarray(audio.energy, dtype=np.float32)
    if e.size == 0:
        return 0.0, 0.0
    i0 = max(0, audio.index_at(start))
    i1 = min(e.size, max(i0 + 1, audio.index_at(end) + 1))
    seg = e[i0:i1]
    if seg.size == 0:
        return 0.0, 0.0
    ref = float(np.percentile(e, 95)) or float(e.max()) or 1.0
    return (float(np.clip(seg.mean() / ref, 0.0, 1.0)),
            float(np.clip(seg.max() / ref, 0.0, 1.0)))


def annotate(sentences: list[Sentence],
             audio: Optional[AudioFeatures] = None,
             language: Optional[str] = None) -> list[Sentence]:
    """ממלא את שדות האותות של כל משפט. משנה במקום ומחזיר את הרשימה."""
    from .scoring import lexical_score

    packs = _lang.packs_for(language)
    for s in sentences:
        low = _norm(s.text)
        s.energy, s.peak_energy = _energy_at(audio, s.start, s.end)
        s.speech_rate = s.word_count / max(0.3, s.duration)
        s.lexical = lexical_score(s.text, language)
        s.is_question = "?" in s.text

        toks = [_clean_token(w.text) for w in s.words] or _tokens(low)
        fillers = sum(1 for t in toks if _is_filler_token(t, packs))
        phrase_hits = _pack_hits(s.text, "filler_phrases", packs)
        s.filler_ratio = min(1.0, (fillers + phrase_hits) / max(1, len(toks)))
    return sentences


# --------------------------------------------------------------------------
# סיווג תפקידים — היוריסטי
# --------------------------------------------------------------------------
def classify_roles(sentences: list[Sentence], *,
                   total_duration: float,
                   start_at: float = 0.0,
                   has_audio: bool = True,
                   language: Optional[str] = None) -> list[Sentence]:
    """
    מסווג כל משפט לתפקיד נרטיבי.

    השיטה: לכל תפקיד מחשבים ציון מאותות המשפט ומהמיקום שלו במבנה,
    והתפקיד בעל הציון הגבוה ביותר מנצח. הביטחון הוא המרחק בין המקום
    הראשון לשני — כך שמשפט מובהק מקבל ביטחון גבוה, ומשפט גבולי נמוך.

    כשאין אודיו, ערוץ העוצמה שווה אפס לכולם. במקרה הזה מגבירים את
    המשקל הלקסיקלי במקום להשאיר תפקידים שתלויים בעוצמה בנחיתות
    שרירותית — אחרת „הגעתי למקום הכי נמוך בחיים שלי" לא היה מזוהה
    כשיא רגשי רק משום שלא היה פס קול לנתח.
    """
    if not sentences:
        return sentences
    packs = _lang.packs_for(language)
    total = max(0.5, total_duration or sentences[-1].end)
    # כל האותות המיקומיים נמדדים **יחסית לתחילת החלון**. זמני
    # המשפטים הם זמני השידור המלא, ולכן קליפ שמתחיל בדקה 2 היה
    # מקבל „מיקום" של 5.0 במקום 0 — וכל הסיווג היה נהרס.
    origin = start_at if start_at else sentences[0].start
    energies = [s.energy for s in sentences] or [0.0]
    e_hi = max(energies) or 1.0
    # כמה לסמוך על עוצמה מול טקסט
    w_energy = 1.0 if has_audio and e_hi > 1e-3 else 0.0
    w_lex = 1.0 if w_energy else 1.7

    for i, s in enumerate(sentences):
        rel = max(0.0, s.start - origin)
        s.rel_start = rel
        pos = min(1.0, rel / total)                # 0 בתחילה, 1 בסוף
        low = _norm(s.text)
        rel_energy = s.energy / e_hi
        scores: dict[str, float] = {}

        # --- מילוי ---
        scores["filler"] = (
            2.2 * s.filler_ratio
            + (0.5 if s.word_count <= 2 and s.lexical < 0.2 else 0.0)
            + (0.35 if s.duration < 0.8 else 0.0)
        )

        # --- וו פתיחה ---
        hook_lex = _pack_hits(s.text, "hook", packs)
        scores["hook"] = (
            (1.15 if rel <= 3.5 else 0.0)
            + (0.55 if rel <= 8.0 else 0.0)
            + 0.75 * w_lex * hook_lex
            + (0.35 if s.is_question and pos < 0.25 else 0.0)
            + 0.4 * w_energy * rel_energy * (1.0 if pos < 0.2 else 0.0)
        )

        # --- קריאה לפעולה ---
        cta_lex = _pack_hits(s.text, "cta", packs)
        scores["cta"] = (1.35 * w_lex * cta_lex
                         + (0.7 if pos > 0.72 and cta_lex else 0.0))

        # --- מעבר נושא ---
        shift_lex = _pack_hits(s.text, "topic_shift", packs)
        scores["topic_change"] = (
            1.1 * w_lex * shift_lex + (0.35 if s.pause_before > 0.7 else 0.0))

        # --- שיא רגשי ---
        emo_lex = _pack_hits(s.text, "emotion", packs, skip_negated=True)
        scores["emotional_peak"] = (
            0.85 * w_lex * emo_lex
            + 1.0 * w_energy * rel_energy
            + (0.4 * w_lex if s.lexical > 0.6 else 0.0)
            + (0.3 if 0.35 < pos < 0.9 else 0.0)
        )

        # --- טענה מרכזית ---
        claim_lex = _pack_hits(s.text, "claim", packs)
        scores["key_claim"] = (
            0.8 * w_lex * claim_lex + 0.6 * s.lexical
            + (0.3 if s.word_count >= 7 else 0.0)
        )

        # --- מתח ---
        scores["tension"] = (
            (0.6 if s.is_question else 0.0)
            + (0.45 if s.pause_after > 0.8 else 0.0)
            + (0.4 if 0.2 < pos < 0.7 else 0.0)
            + 0.3 * w_energy * rel_energy
        )

        # --- פאנץ' ---
        scores["payoff"] = (
            (0.75 if pos > 0.6 else 0.0)
            + 0.55 * w_energy * rel_energy
            + 0.45 * s.lexical
            + (0.3 if s.pause_before > 0.5 else 0.0)
        )

        # --- רקע ורעיון מרכזי: ברירות מחדל לפי מיקום ---
        scores["setup"] = (0.95 if pos < 0.3 else 0.25) + (
            0.3 * w_energy if s.energy < 0.4 * e_hi else 0.0)
        scores["main_idea"] = (0.9 if 0.25 <= pos <= 0.65 else 0.3) + 0.4 * s.lexical

        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        best, best_score = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else 0.0
        s.role = best
        s.confidence = float(np.clip((best_score - second) / 1.2, 0.05, 0.98))
        s.reason = _role_reason(best, s)
        s.role_source = "heuristic"

    _enforce_structure(sentences, total, origin)
    return sentences


def _role_reason(role: str, s: Sentence) -> str:
    """משפט אחד שמסביר למה הוחלט ככה, בשפת הממשק. מוצג למשתמש."""
    if role == "hook":
        return i18n.tr("analysis.why.hook_open" if s.rel_start <= 3.5
                       else "analysis.why.hook_phrase")
    if role == "cta":
        return i18n.tr("analysis.why.cta")
    if role == "topic_change":
        return i18n.tr("analysis.why.topic_change")
    if role == "emotional_peak":
        if s.energy > 0.01:
            return i18n.tr("analysis.why.emotion_energy", energy=f"{s.energy:.0%}")
        return i18n.tr("analysis.why.emotion_text")
    if role == "key_claim":
        return i18n.tr("analysis.why.key_claim")
    if role == "tension":
        return i18n.tr("analysis.why.tension")
    if role == "payoff":
        return i18n.tr("analysis.why.payoff")
    if role == "filler":
        return i18n.tr("analysis.why.filler", ratio=f"{s.filler_ratio:.0%}")
    if role == "setup":
        return i18n.tr("analysis.why.setup")
    return i18n.tr("analysis.why.body")


def _enforce_structure(sentences: list[Sentence], total: float,
                       origin: float = 0.0) -> None:
    """
    מתקן סיווגים שלא מסתדרים עם מבנה של סרטון.

    בלי זה אפשר לקבל חמישה „שיאים רגשיים" או hook באמצע — שני דברים
    שלא קיימים בסרטון אמיתי.
    """
    # 1. hook רק בהתחלה
    for s in sentences:
        if s.role == "hook" and (s.start - origin) > 8.0:
            s.role, s.confidence = "key_claim", s.confidence * 0.6
            s.reason = i18n.tr("analysis.why.late_hook")

    # 2. וו פתיחה אחד בלבד. סרטון לא נפתח פעמיים, והשני הוא כמעט תמיד
    #    משפט שקיבל בונוס רק על כך שהוא מוקדם.
    hooks = [s for s in sentences if s.role == "hook"]
    if len(hooks) > 1:
        hooks.sort(key=lambda s: (-s.confidence, s.start))
        for s in hooks[1:]:
            s.role = "filler" if s.filler_ratio > 0.34 else "setup"
            s.confidence = max(0.15, s.confidence * 0.6)
            s.reason = i18n.tr("analysis.why.second_hook_filler" if s.role == "filler"
                               else "analysis.why.second_hook")

    # 3. אם אין hook כלל — המשפט הראשון בעל התוכן מקבל את התפקיד
    if not any(s.role == "hook" for s in sentences):
        for s in sentences:
            if s.role != "filler" and s.word_count >= 3:
                s.role = "hook"
                s.confidence = max(0.25, s.confidence * 0.7)
                s.reason = i18n.tr("analysis.why.first_content")
                break

    # 4. שיא רגשי אחד בלבד: החזק ביותר שורד
    peaks = [s for s in sentences if s.role == "emotional_peak"]
    if len(peaks) > 1:
        peaks.sort(key=lambda s: -(s.energy + s.lexical))
        for s in peaks[1:]:
            s.role = "main_idea"
            s.confidence *= 0.7
            s.reason = i18n.tr("analysis.why.not_peak")

    # 5. CTA רק ברבע האחרון
    for s in sentences:
        if s.role == "cta" and (s.start - origin) < total * 0.6:
            s.role = "main_idea"
            s.reason = i18n.tr("analysis.why.early_cta")


# --------------------------------------------------------------------------
# סיווג בעזרת מודל שפה
# --------------------------------------------------------------------------
LLM_SYSTEM = """אתה עורך וידאו שמנתח מבנה של סרטון מדבר.
אתה מקבל משפטים ממוספרים עם זמנים, ומחזיר לכל אחד תפקיד נרטיבי.
אתה לא מוסיף, לא משנה ולא ממציא טקסט."""

LLM_INSTRUCTIONS = """התפקידים האפשריים:
hook, setup, main_idea, tension, emotional_peak, payoff, key_claim, cta,
topic_change, filler

החזר JSON יחיד בלבד:
{"roles": [{"index": <מספר המשפט>, "role": "<תפקיד>", "confidence": <0-1>, "reason": "<למה, בעברית, משפט אחד>"}]}

כללים:
- hook רק בתחילת הסרטון.
- שיא רגשי אחד לכל היותר.
- cta רק בסוף.
- משפט בלי תוכן = filler.
- החזר שורה לכל משפט שקיבלת."""


def classify_with_llm(sentences: list[Sentence], settings: AppSettings,
                      ) -> tuple[bool, str]:
    """
    משפר את הסיווג ההיוריסטי בעזרת מודל שפה.

    מחזיר (האם הוחל, הערה). המשפטים מעודכנים במקום. כישלון אינו נזרק:
    הסיווג ההיוריסטי כבר קיים ונשאר תקף.
    """
    from . import llm as llm_svc

    if not llm_svc.is_llm_enabled(settings):
        return False, ""
    usable = [s for s in sentences if s.text.strip()][:150]
    if not usable:
        return False, ""

    lines = [f"[{s.index}] {s.start:.1f}-{s.end:.1f}: {s.text}" for s in usable]
    user = ("משפטי הסרטון:\n" + "\n".join(lines) + "\n\n" + LLM_INSTRUCTIONS)

    try:
        raw = llm_svc.call_model(LLM_SYSTEM, user, settings)
        data = llm_svc._parse_json(raw)
    except Exception as exc:
        log.warning("LLM role classification failed: %s", exc)
        return False, "מודל השפה לא היה זמין; הסיווג נעשה במנוע המקומי."

    rows = data.get("roles")
    if not isinstance(rows, list) or not rows:
        return False, "מודל השפה החזיר תשובה פסולה; נעשה שימוש במנוע המקומי."

    by_index = {s.index: s for s in sentences}
    valid = {r.value for r in BeatRole}
    applied = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            idx = int(row.get("index", -1))
        except (TypeError, ValueError):
            continue
        role = str(row.get("role") or "").strip().lower()
        s = by_index.get(idx)
        if s is None or role not in valid:
            continue
        try:
            conf = float(row.get("confidence", 0.6))
        except (TypeError, ValueError):
            conf = 0.6
        s.role = role
        s.confidence = float(np.clip(conf, 0.05, 0.99))
        s.reason = str(row.get("reason") or "").strip() or _role_reason(role, s)
        s.role_source = "llm"
        applied += 1

    if not applied:
        return False, "מודל השפה לא החזיר סיווג שמיש; נעשה שימוש במנוע המקומי."

    _enforce_structure(sentences, sentences[-1].end if sentences else 0.0,
                       sentences[0].start if sentences else 0.0)
    return True, ""


# --------------------------------------------------------------------------
# קיבוץ ל-Beats
# --------------------------------------------------------------------------
@dataclass
class NarrativeBeat:
    """רצף משפטים בעלי אותו תפקיד — יחידת העריכה של ה-Director."""

    start: float
    end: float
    role: str
    confidence: float
    sentences: list[Sentence] = field(default_factory=list)
    reason: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.sentences).strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "duration": round(self.duration, 3),
            "role": self.role,
            "role_label": ROLE_LABELS_HE.get(self.role, self.role),
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
            "text": self.text[:300],
            "sentences": [s.index for s in self.sentences],
        }


def group_beats(sentences: Sequence[Sentence]) -> list[NarrativeBeat]:
    """מאחד משפטים סמוכים בעלי אותו תפקיד ל-Beat אחד."""
    beats: list[NarrativeBeat] = []
    for s in sentences:
        if beats and beats[-1].role == s.role and s.start - beats[-1].end < 2.5:
            b = beats[-1]
            b.end = s.end
            b.sentences.append(s)
            b.confidence = max(b.confidence, s.confidence)
        else:
            beats.append(NarrativeBeat(
                start=s.start, end=s.end, role=s.role,
                confidence=s.confidence, sentences=[s], reason=s.reason))
    return beats


# --------------------------------------------------------------------------
# נקודת כניסה
# --------------------------------------------------------------------------
@dataclass
class SemanticAnalysis:
    """התוצר המלא של ההבנה הסמנטית."""

    sentences: list[Sentence] = field(default_factory=list)
    beats: list[NarrativeBeat] = field(default_factory=list)
    disfluencies: list[Disfluency] = field(default_factory=list)
    language: str = ""
    source: str = "heuristic"
    note: str = ""

    @property
    def has_transcript(self) -> bool:
        return bool(self.sentences)

    def beats_by_role(self, role: str) -> list[NarrativeBeat]:
        return [b for b in self.beats if b.role == role]

    def role_at(self, t: float) -> Optional[NarrativeBeat]:
        for b in self.beats:
            if b.start <= t <= b.end:
                return b
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "source": self.source,
            "note": self.note,
            "sentences": [s.to_dict() for s in self.sentences],
            "beats": [b.to_dict() for b in self.beats],
            "disfluencies": [d.to_dict() for d in self.disfluencies],
            "filler_seconds": round(
                sum(d.duration for d in self.disfluencies), 2),
        }


def analyze(transcript: Optional[TranscriptResult],
            audio: Optional[AudioFeatures] = None, *,
            settings: Optional[AppSettings] = None,
            start: float = 0.0,
            end: Optional[float] = None,
            use_llm: bool = True,
            language: Optional[str] = None) -> SemanticAnalysis:
    """
    מנתח את הסרטון ומחזיר תיאור סמנטי מלא.

    בלי תמלול מוחזר ניתוח ריק עם הערה מפורשת — ולא ניחוש. כל מה
    שמבוסס על טקסט פשוט לא זמין אז, וה-Director יודע לעבוד גם ככה.
    """
    sentences = split_sentences(transcript, start=start, end=end)
    if not sentences:
        return SemanticAnalysis(note=i18n.tr("analysis.semantics.no_transcript"))

    if language is None and transcript is not None:
        language = _lang.resolve_language(transcript)
    annotate(sentences, audio, language=language)
    # ראשית החלון: מה שנמסר, ואם לא נמסר — תחילת התמלול עצמו.
    # בלי זה, ניתוח של קליפ שמתחיל בדקה 2 מקבל „אורך" של שתי דקות
    # ועשרים שניות, וכל שיפוט מיקום בתוכו יוצא מעוות.
    origin = start if start else sentences[0].start
    total = (end if end is not None else sentences[-1].end) - origin
    has_audio = audio is not None and audio.n > 0
    classify_roles(sentences, total_duration=total, start_at=origin,
                   has_audio=has_audio, language=language)

    source, note = "heuristic", ""
    if use_llm and settings is not None:
        ok, msg = classify_with_llm(sentences, settings)
        if ok:
            source = "llm"
        note = msg

    return SemanticAnalysis(
        sentences=sentences,
        beats=group_beats(sentences),
        disfluencies=find_disfluencies(sentences, language),
        language=(language or (transcript.language if transcript else "")),
        source=source,
        note=note,
    )
