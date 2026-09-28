"""
Caption Engine 2.0 — קיבוץ כתוביות מודע לפיסוק, שבירת שורות מאוזנת,
אזורים בטוחים, פריסטים, ומילות מפתח מודגשות במידה.

מה זה מוסיף מעל `subtitles.build_cues`:

  פיסוק      כתובית נשברת בסוף משפט, לא באמצע מחשבה. פסיק ונקודה
             אינם שווים: נקודה שוברת תמיד, פסיק רק כשהכתובית כבר
             מספיק מלאה.
  שורות      שתי שורות מאוזנות באורכן במקום שורה מלאה ושורה עם
             מילה אחת. השבירה מעדיפה גבול תחבירי ולא נשארת אחרי
             מילית קישור.
  אזור בטוח  בפיד אנכי הממשק של הפלטפורמה מכסה את תחתית הפריים.
             השוליים מחושבים מגובה הפריים, כדי שהכתובית לא תיפול
             מתחת לשכבת הכפתורים.
  פריסטים    חמישה. כל פריסט משנה פרמטרים של אותו מנוע — אין מנוע
             נפרד לכל מראה.
  דוברים     כשמסופקות תוויות דובר, מעבר דובר שובר כתובית והצבע
             משתנה. **זיהוי דוברים אוטומטי (diarization) אינו ממומש
             בפרויקט** — המנוע מכבד תוויות שמגיעות מבחוץ בלבד.
  הדגשות     מילות מפתח נצבעות, עם תקרה לדקה ומרווח מינימלי. הדגשה
             על כל מילה שנייה שווה לאפס הדגשות.

כל הטקסט מגיע מהתמלול. המנוע לא ממציא מילים ולא משנה ניסוח.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Optional

from .subtitles import Cue, SubtitleStyle, _fix_overlaps, _max_chars_for
from .transcribe import TranscriptResult, Word

log = logging.getLogger("polixor.captions")

# --------------------------------------------------------------------------
# פיסוק ומילות קישור
# --------------------------------------------------------------------------
STRONG_PUNCT = ".!?׃…"          # סוף מחשבה — שובר תמיד
WEAK_PUNCT = ",;:—–"            # גבול משני — שובר רק כשהכתובית מלאה

# מילים שלא ראוי שיסיימו שורה או כתובית: הן נשענות על המילה הבאה,
# והצופה נשאר תלוי באוויר עד הכתובית הבאה.
HANGING_WORDS = frozenset({
    # מיליות יחס וקישור
    "של", "את", "עם", "על", "אל", "אצל", "לפי", "בין", "מול", "כמו",
    "כי", "אם", "או", "גם", "רק", "כל", "לא", "יש", "אין", "זה", "מה",
    "אבל", "כדי", "עד", "מאז", "בגלל", "לכן", "אז",
    # כינויי גוף: נושא שנשאר בלי הפועל שלו משאיר את הצופה תלוי
    "אני", "אתה", "אתם", "אתן", "הוא", "היא", "הם", "הן", "אנחנו",
    "the", "a", "an", "of", "to", "in", "on", "at", "for", "with",
    "and", "or", "but", "is", "are", "was", "were", "that", "this",
    "my", "your", "his", "her", "its", "our", "their",
    "i", "we", "he", "she", "they", "you", "it",
})

_PUNCT_STRIP = re.compile(r"[\"'“”„«»\(\)\[\]\.,!?;:—–…׃]+")


def _bare(token: str) -> str:
    return _PUNCT_STRIP.sub("", (token or "").strip()).lower()


def _ends_strong(token: str) -> bool:
    t = (token or "").rstrip("\"'”»)]")
    return bool(t) and t[-1] in STRONG_PUNCT


def _ends_weak(token: str) -> bool:
    t = (token or "").rstrip("\"'”»)]")
    return bool(t) and t[-1] in WEAK_PUNCT


def _is_hanging(token: str) -> bool:
    """
    האם המילה נשענת על הבאה אחריה.

    בעברית תחיליות נכתבות מחוברות, ולכן „ואני" ו-„שהוא" הן אותה
    מילה תלויה כמו „אני" ו-„הוא". בודקים גם את הצורה המקוצרת,
    אבל רק כשמה שנשאר הוא מילה ממשית ולא אות בודדת.
    """
    bare = _bare(token)
    if bare in HANGING_WORDS:
        return True
    for prefix in ("וש", "ש", "ו", "כש"):
        if bare.startswith(prefix):
            rest = bare[len(prefix):]
            if len(rest) >= 2 and rest in HANGING_WORDS:
                return True
    return False


# --------------------------------------------------------------------------
# אזור בטוח
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class SafeZone:
    """
    חלקי הפריים שהכתובית לא נכנסת אליהם, כשבר מהגובה/הרוחב.

    בפיד אנכי, תחתית הפריים מוסתרת ברוב האפליקציות על-ידי שם
    המשתמש, תיאור הסרטון ושורת הכפתורים; העליון מוסתר על-ידי
    שורת הכותרת. הערכים כאן הם ברירת מחדל שמרנית שמכסה את
    הפריסות הנפוצות ולא מדידה של אפליקציה מסוימת — הם פרמטר
    שניתן לכוונן, לא קביעה על פלטפורמה ספציפית.
    """

    top: float = 0.10
    bottom: float = 0.16
    side: float = 0.06

    @classmethod
    def for_frame(cls, *, vertical: bool) -> "SafeZone":
        if vertical:
            return cls(top=0.10, bottom=0.16, side=0.06)
        return cls(top=0.06, bottom=0.10, side=0.05)


def safe_margins(frame_w: int, frame_h: int, zone: SafeZone,
                 position: str) -> tuple[int, int]:
    """מחזיר (margin_v, margin_h) בפיקסלים לפי האזור הבטוח."""
    w = max(1, int(frame_w))
    h = max(1, int(frame_h))
    margin_h = int(round(w * max(0.0, min(0.3, zone.side))))
    if position == "top":
        margin_v = int(round(h * max(0.0, min(0.4, zone.top))))
    elif position == "middle":
        margin_v = 0
    else:
        margin_v = int(round(h * max(0.0, min(0.4, zone.bottom))))
    return margin_v, margin_h


# --------------------------------------------------------------------------
# פריסטים
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class CaptionPreset:
    name: str
    label: str
    description: str
    # קיבוץ
    max_chars: int              # תקרת תווים לשורה (בנוסף לחישוב לפי הפריים)
    max_lines: int
    max_words: int              # תקרת מילים לכתובית; 0 = ללא תקרה
    max_cue_seconds: float
    min_cue_seconds: float
    pause_break: float          # שתיקה שמעליה מתחילה כתובית חדשה
    # מראה
    word_level: bool
    animation: str              # none | pop | punch
    position: str               # top | middle | bottom
    size_scale: float
    outline_scale: float
    uppercase_latin: bool
    # הדגשות
    emphasis_per_minute: float
    emphasis_min_gap: float
    emphasis_color: str
    # דוברים
    speaker_colors: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "label": self.label,
            "description": self.description,
            "max_chars": self.max_chars, "max_lines": self.max_lines,
            "max_words": self.max_words,
            "max_cue_seconds": self.max_cue_seconds,
            "word_level": self.word_level, "animation": self.animation,
            "position": self.position, "size_scale": self.size_scale,
            "emphasis_per_minute": self.emphasis_per_minute,
            "speaker_colors": self.speaker_colors,
        }


PRESETS: dict[str, CaptionPreset] = {
    "clean": CaptionPreset(
        name="clean", label="נקי",
        description="שתי שורות שקטות בתחתית. הכתובית משרתת את הדיבור "
                    "ולא מושכת אליה תשומת לב.",
        max_chars=38, max_lines=2, max_words=0,
        max_cue_seconds=3.8, min_cue_seconds=0.7, pause_break=0.70,
        word_level=False, animation="none", position="bottom",
        size_scale=1.0, outline_scale=1.0, uppercase_latin=False,
        emphasis_per_minute=2.0, emphasis_min_gap=6.0,
        emphasis_color="#FFD400", speaker_colors=False),

    "viral": CaptionPreset(
        name="viral", label="ויראלי",
        description="שורה אחת, עד שלוש מילים, הדגשה רצה על המילה "
                    "הנאמרת. מיועד לפיד אנכי מהיר.",
        max_chars=22, max_lines=1, max_words=3,
        max_cue_seconds=1.7, min_cue_seconds=0.35, pause_break=0.45,
        word_level=True, animation="punch", position="bottom",
        size_scale=1.26, outline_scale=1.25, uppercase_latin=True,
        emphasis_per_minute=6.0, emphasis_min_gap=2.0,
        emphasis_color="#3BE8B0", speaker_colors=False),

    "cinematic": CaptionPreset(
        name="cinematic", label="קולנועי",
        description="כתובית קטנה ומאופקת, בלי אנימציה. התמונה היא "
                    "העיקר והכתובית רק מלווה.",
        max_chars=42, max_lines=2, max_words=0,
        max_cue_seconds=4.6, min_cue_seconds=0.9, pause_break=0.85,
        word_level=False, animation="none", position="bottom",
        size_scale=0.84, outline_scale=0.7, uppercase_latin=False,
        emphasis_per_minute=1.0, emphasis_min_gap=12.0,
        emphasis_color="#E8E8E8", speaker_colors=False),

    "podcast": CaptionPreset(
        name="podcast", label="פודקאסט",
        description="שיחה ארוכה: כתוביות בינוניות, שבירה בכל מעבר "
                    "דובר, וצבע לכל דובר כשיש תוויות.",
        max_chars=36, max_lines=2, max_words=0,
        max_cue_seconds=3.2, min_cue_seconds=0.6, pause_break=0.55,
        word_level=True, animation="pop", position="bottom",
        size_scale=0.96, outline_scale=1.0, uppercase_latin=False,
        emphasis_per_minute=3.0, emphasis_min_gap=4.0,
        emphasis_color="#FFB35C", speaker_colors=True),

    "story": CaptionPreset(
        name="story", label="סיפור",
        description="כתובית במרכז הפריים, קצרה, עם קצב נשימה של "
                    "סיפור בגוף ראשון.",
        max_chars=28, max_lines=2, max_words=0,
        max_cue_seconds=2.8, min_cue_seconds=0.6, pause_break=0.60,
        word_level=True, animation="pop", position="middle",
        size_scale=1.06, outline_scale=1.1, uppercase_latin=False,
        emphasis_per_minute=3.0, emphasis_min_gap=4.0,
        emphasis_color="#FF6B8A", speaker_colors=False),
}

DEFAULT_PRESET = "clean"

# שמות ישנים שהמערכת עשויה עדיין להחזיק
LEGACY_PRESET_MAP = {
    "none": "clean", "simple": "clean", "default": "clean",
    "hype": "viral", "punch": "viral", "pop": "story",
    "film": "cinematic", "movie": "cinematic",
    "interview": "podcast", "talk": "podcast",
}


def get_preset(name: Optional[str]) -> CaptionPreset:
    key = (name or "").strip().lower()
    key = LEGACY_PRESET_MAP.get(key, key)
    return PRESETS.get(key, PRESETS[DEFAULT_PRESET])


def preset_catalog() -> list[dict[str, Any]]:
    return [p.to_dict() for p in PRESETS.values()]


# גובה הפריים שאליו מכוילים גדלי הגופן בהגדרות
REFERENCE_HEIGHT = {True: 1920, False: 1080}


def frame_scale(frame_h: int, *, vertical: bool) -> float:
    """
    יחס בין הפריים בפועל לפריים הייחוס שאליו מכוילות ההגדרות.

    גודל גופן ב-ASS נמדד ביחידות `PlayResY`, כלומר בפיקסלים של
    הפלט. גודל שמתאים לפריים 1920 מכסה רבע מהמסך בפריים 480.
    בלי הקנה מידה הזה, כל ייצוא ברזולוציה נמוכה מקבל כתוביות
    ענקיות שמסתירות את הווידאו.
    """
    ref = REFERENCE_HEIGHT[bool(vertical)]
    return float(max(0.2, min(2.0, (frame_h or ref) / ref)))


def apply_preset(style: SubtitleStyle, preset: CaptionPreset, *,
                 frame_w: int, frame_h: int, vertical: bool,
                 zone: Optional[SafeZone] = None) -> SubtitleStyle:
    """
    מחיל פריסט על סגנון קיים, מקנה מידה לפריים ומחשב שוליים
    לפי האזור הבטוח.

    גודל הגופן נגזר מגודל הבסיס של המשתמש ולא מוחלף בו — כך
    שהפריסט משנה את האופי, והמשתמש עדיין שולט בגודל.
    """
    zone = zone or SafeZone.for_frame(vertical=vertical)
    margin_v, margin_h = safe_margins(frame_w, frame_h, zone, preset.position)
    base_outline = style.outline if style.outline > 0 else 3.0
    scale = frame_scale(frame_h, vertical=vertical)
    return replace(
        style,
        size=max(10, int(round(style.size * preset.size_scale * scale))),
        position=preset.position,
        word_level=preset.word_level,
        animation=preset.animation,
        # גם המתאר נמדד ביחידות הפלט ולכן מוקטן איתו
        outline=round(max(0.6, base_outline * preset.outline_scale * scale), 2),
        margin_v=margin_v,
        margin_h=margin_h,
        emphasis_color=preset.emphasis_color,
    )


# --------------------------------------------------------------------------
# שבירת שורות מאוזנת
# --------------------------------------------------------------------------
def best_break_index(tokens: list[str], max_chars: int) -> int:
    """
    אינדקס המילה שממנה מתחילה השורה השנייה, או 0 אם שורה אחת מספיקה.

    בוחרים את השבירה שמאזנת את אורכי השורות, עם העדפה לגבול
    תחבירי (אחרי פיסוק) וקנס על שבירה אחרי מילית קישור. שורה
    מלאה עם מילה בודדת מתחתיה נקראת כתקלה, גם כשהיא „חוקית".
    """
    if len(tokens) < 2:
        return 0
    total = len(" ".join(tokens))
    if total <= max_chars:
        return 0

    best_i, best_score = 0, float("inf")
    for i in range(1, len(tokens)):
        first = " ".join(tokens[:i])
        second = " ".join(tokens[i:])
        if len(first) > max_chars or len(second) > max_chars:
            continue
        score = abs(len(first) - len(second))
        if _ends_strong(tokens[i - 1]) or _ends_weak(tokens[i - 1]):
            score -= 6.0          # גבול תחבירי — שבירה טבעית
        if _is_hanging(tokens[i - 1]):
            score += 12.0         # „...של" בסוף שורה
        if i == len(tokens) - 1 or i == 1:
            score += 4.0          # מילה בודדת בשורה
        if score < best_score:
            best_score, best_i = score, i

    if best_i:
        return best_i

    # אין שבירה שבה שתי השורות נכנסות — מחזירים את החלוקה החמדנית
    acc = 0
    for i, tok in enumerate(tokens):
        acc += len(tok) + 1
        if acc > max_chars:
            return max(1, i)
    return 0


def wrap_balanced(text: str, max_chars: int = 34,
                  max_lines: int = 2) -> list[str]:
    """שבירה לשורות מאוזנות. שומרת על סדר לוגי — ה-bidi נעשה ברינדור."""
    clean = re.sub(r"\s+", " ", (text or "").strip())
    if not clean:
        return []
    tokens = clean.split(" ")
    if max_lines <= 1 or len(clean) <= max_chars:
        return [clean]

    if max_lines == 2:
        idx = best_break_index(tokens, max_chars)
        if not idx:
            return [clean]
        return [" ".join(tokens[:idx]), " ".join(tokens[idx:])]

    # שלוש שורות ומעלה: חלוקה חמדנית, ואיזון בין שתי האחרונות
    lines: list[str] = []
    cur: list[str] = []
    for tok in tokens:
        cand = " ".join(cur + [tok])
        if cur and len(cand) > max_chars and len(lines) < max_lines - 1:
            lines.append(" ".join(cur))
            cur = [tok]
        else:
            cur.append(tok)
    if cur:
        lines.append(" ".join(cur))
    if len(lines) >= 2:
        tail = (lines[-2] + " " + lines[-1]).split(" ")
        idx = best_break_index(tail, max_chars)
        if idx:
            lines[-2:] = [" ".join(tail[:idx]), " ".join(tail[idx:])]
    return lines[:max_lines]


# --------------------------------------------------------------------------
# בניית כתוביות
# --------------------------------------------------------------------------
def build_captions(
    transcript: Optional[TranscriptResult],
    *,
    clip_start: float,
    clip_end: float,
    preset: CaptionPreset | str = DEFAULT_PRESET,
    frame_chars: Optional[int] = None,
    time_offset: float = 0.0,
    speakers: Optional[list[tuple[float, float, str]]] = None,
) -> list[Cue]:
    """
    בונה כתוביות לקטע, בזמנים יחסיים לתחילת הקליפ.

    `speakers` הוא רצף (start, end, label) בזמני מקור. הוא אופציונלי,
    ומגיע ממקור חיצוני בלבד — **אין בפרויקט זיהוי דוברים אוטומטי.**
    """
    p = preset if isinstance(preset, CaptionPreset) else get_preset(preset)
    if transcript is None or not transcript.segments:
        return []

    budget = (frame_chars or p.max_chars) * p.max_lines
    words = transcript.words_between(clip_start, clip_end)
    if not words:
        return _cues_from_segments(transcript, clip_start, clip_end, p,
                                   budget, time_offset)

    lang = transcript.language or ""
    cues: list[Cue] = []
    bucket: list[Word] = []
    bucket_speaker = ""

    def flush(carry: Optional[Word] = None) -> None:
        nonlocal bucket, bucket_speaker
        if not bucket:
            return
        items = list(bucket)
        bucket = []
        # מילית קישור בסוף כתובית עוברת לכתובית הבאה
        if carry is None and len(items) > 1 and _is_hanging(items[-1].text):
            carry = items.pop()
        text = " ".join(w.text for w in items if w.text).strip()
        if text:
            s = max(clip_start, items[0].start)
            e = min(clip_end, max(items[-1].end, s + p.min_cue_seconds))
            cues.append(Cue(
                start=round(s - clip_start + time_offset, 3),
                end=round(e - clip_start + time_offset, 3),
                text=text,
                words=[{"start": round(w.start - clip_start + time_offset, 3),
                        "end": round(w.end - clip_start + time_offset, 3),
                        "text": w.text} for w in items if w.text],
                language=lang,
                speaker=bucket_speaker,
            ))
        bucket_speaker = ""
        if carry is not None:
            bucket = [carry]
            bucket_speaker = _speaker_at(speakers, carry.start)

    for w in words:
        if not w.text:
            continue
        spk = _speaker_at(speakers, w.start)
        if bucket:
            chars = sum(len(x.text) + 1 for x in bucket) + len(w.text)
            span = w.end - bucket[0].start
            gap = w.start - bucket[-1].end
            over_words = p.max_words and len(bucket) >= p.max_words
            if (spk != bucket_speaker or over_words or chars > budget
                    or span > p.max_cue_seconds or gap > p.pause_break):
                flush()
        if not bucket:
            bucket_speaker = spk
        bucket.append(w)

        filled = sum(len(x.text) + 1 for x in bucket)
        if _ends_strong(w.text):
            flush()
        elif _ends_weak(w.text) and filled > budget * 0.6:
            flush()

    flush()
    return _finalise(cues, p)


def _speaker_at(spans: Optional[list[tuple[float, float, str]]],
                t: float) -> str:
    if not spans:
        return ""
    for s, e, label in spans:
        if s <= t < e:
            return label or ""
    return ""


def _cues_from_segments(transcript: TranscriptResult, clip_start: float,
                        clip_end: float, p: CaptionPreset, budget: int,
                        offset: float) -> list[Cue]:
    """נפילה לרמת מקטע כשאין תזמון ברמת מילה. הפיסוק עדיין נשמר."""
    cues: list[Cue] = []
    for seg in transcript.segments:
        if seg.end <= clip_start or seg.start >= clip_end:
            continue
        text = (seg.text or "").strip()
        if not text:
            continue
        s = max(seg.start, clip_start)
        e = min(seg.end, clip_end)
        if e - s < 0.15:
            continue
        chunks = _split_on_punctuation(text, budget)
        # חלוקת זמן לפי אורך הטקסט ולא שווה בשווה — משפט ארוך לוקח יותר
        lengths = [max(1, len(c)) for c in chunks]
        total_len = sum(lengths)
        cursor = s
        for chunk, ln in zip(chunks, lengths):
            span = (e - s) * (ln / total_len)
            ce = min(e, cursor + min(span, p.max_cue_seconds))
            cues.append(Cue(start=round(cursor - clip_start + offset, 3),
                            end=round(ce - clip_start + offset, 3),
                            text=chunk,
                            language=seg.language or transcript.language))
            cursor += span
    return _finalise(cues, p)


def _split_on_punctuation(text: str, budget: int) -> list[str]:
    """מחלק טקסט לחלקים בגבולות פיסוק, ורק אחר כך לפי מכסת תווים."""
    tokens = text.split()
    out: list[str] = []
    cur: list[str] = []
    for tok in tokens:
        cur.append(tok)
        joined = " ".join(cur)
        if _ends_strong(tok) or len(joined) >= budget:
            out.append(joined)
            cur = []
        elif _ends_weak(tok) and len(joined) > budget * 0.6:
            out.append(joined)
            cur = []
    if cur:
        out.append(" ".join(cur))
    return out or [text]


def _finalise(cues: list[Cue], p: CaptionPreset) -> list[Cue]:
    """מיזוג כתובית-זנב של מילה אחת, אכיפת משך מינימלי, מניעת חפיפה."""
    cues = [c for c in cues if c.text.strip()]
    merged: list[Cue] = []
    for c in cues:
        if (merged and len(c.text.split()) == 1
                and not _ends_strong(merged[-1].text)
                and c.start - merged[-1].end < 0.35
                and c.end - merged[-1].start <= p.max_cue_seconds * 1.25
                and merged[-1].speaker == c.speaker):
            prev = merged[-1]
            prev.text = f"{prev.text} {c.text}".strip()
            prev.words = list(prev.words) + list(c.words)
            prev.end = c.end
            continue
        merged.append(c)

    for c in merged:
        if c.end - c.start < p.min_cue_seconds:
            c.end = c.start + p.min_cue_seconds
    return _fix_overlaps(merged)


# --------------------------------------------------------------------------
# הדגשת מילות מפתח
# --------------------------------------------------------------------------
@dataclass
class EmphasisSpan:
    """מילה להדגשה, בזמני הכתובית (אחרי מיפוי מהמקור)."""

    start: float
    end: float
    word: str = ""
    reason: str = ""


@dataclass
class EmphasisReport:
    applied: int = 0
    requested: int = 0
    skipped_unmatched: int = 0
    skipped_budget: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"applied": self.applied, "requested": self.requested,
                "skipped_unmatched": self.skipped_unmatched,
                "skipped_budget": self.skipped_budget, "notes": self.notes}


def spans_from_decisions(decisions: Iterable[Any], *, clip_start: float,
                         mapper: Optional[Any] = None) -> list[EmphasisSpan]:
    """
    ממיר החלטות EMPHASIZE_WORD של הבמאי למרווחים בזמני הכתובית.

    `mapper` הוא אובייקט עם `map_span(start, end)` (תכנית העריכה),
    כדי שההדגשה תזוז יחד עם החיתוכים. בלעדיו מניחים שאין חיתוכים.
    """
    out: list[EmphasisSpan] = []
    for d in decisions or []:
        if getattr(d, "action", "") != "emphasize_word":
            continue
        if not getattr(d, "enabled", True):
            continue
        s = float(getattr(d, "start", 0.0)) - clip_start
        e = float(getattr(d, "end", 0.0)) - clip_start
        if mapper is not None:
            span = mapper.map_span(s, e)
            if span is None:
                continue        # המילה נחתכה מהעריכה
            s, e = span
        params = getattr(d, "params", {}) or {}
        out.append(EmphasisSpan(start=s, end=e,
                                word=str(params.get("word", "")),
                                reason=getattr(d, "reason", "")))
    return out


def apply_emphasis(cues: list[Cue], spans: list[EmphasisSpan],
                   preset: CaptionPreset | str = DEFAULT_PRESET,
                   *, total_duration: float = 0.0) -> EmphasisReport:
    """
    מסמן מילים להדגשה בתוך הכתוביות, בתוך מכסה.

    שתי הגנות שנשמרות גם אם המתכנן שגה:
      1. מספר ההדגשות מוגבל לפי אורך הקליפ ולפי הפריסט.
      2. הדגשה מוחלת רק כשהמילה בכתובית **תואמת** למילה שבהחלטה.
         אחרת עדיף לוותר על ההדגשה מאשר להדגיש את המילה הלא נכונה.
    """
    p = preset if isinstance(preset, CaptionPreset) else get_preset(preset)
    report = EmphasisReport(requested=len(spans or []))
    for c in cues:
        c.emphasis = []
    if not cues or not spans:
        return report

    span_total = total_duration or max((c.end for c in cues), default=0.0)
    minutes = max(0.2, span_total / 60.0)
    budget = max(0, int(p.emphasis_per_minute * minutes))
    if budget <= 0:
        report.skipped_budget = len(spans)
        report.notes.append("הפריסט הנוכחי אינו מדגיש מילים.")
        return report

    placed: list[float] = []
    for sp in sorted(spans, key=lambda x: x.start):
        hit = _locate_word(cues, sp)
        if hit is None:
            report.skipped_unmatched += 1
            continue
        if len(placed) >= budget:
            report.skipped_budget += 1
            continue
        if any(abs(sp.start - t) < p.emphasis_min_gap for t in placed):
            report.skipped_budget += 1
            continue
        ci, wi = hit
        if wi not in cues[ci].emphasis:
            cues[ci].emphasis.append(wi)
        placed.append(sp.start)
        report.applied += 1

    if report.skipped_budget:
        report.notes.append(
            f"הודגשו {report.applied} מילים מתוך {report.requested} — "
            f"מעל {p.emphasis_per_minute:g} לדקה ההדגשה מאבדת את כוחה.")
    if report.skipped_unmatched:
        report.notes.append(
            f"{report.skipped_unmatched} הדגשות לא הוחלו: המילה בכתובית "
            "לא תאמה למילה שבהחלטה.")
    return report


def _locate_word(cues: list[Cue], sp: EmphasisSpan
                 ) -> Optional[tuple[int, int]]:
    """מוצא (אינדקס כתובית, אינדקס מילה) לפי חפיפת זמן והתאמת טקסט."""
    mid = (sp.start + sp.end) / 2.0
    want = _bare(sp.word)
    best: Optional[tuple[float, int, int]] = None

    for ci, c in enumerate(cues):
        if c.end < sp.start - 0.25 or c.start > sp.end + 0.25:
            continue
        tokens = c.words or [{"start": c.start, "end": c.end, "text": t}
                             for t in c.text.split()]
        for wi, w in enumerate(tokens):
            ws = float(w.get("start", c.start))
            we = float(w.get("end", c.end))
            overlap = min(we, sp.end) - max(ws, sp.start)
            if overlap <= 0 and not (ws <= mid <= we):
                continue
            if want and _bare(str(w.get("text", ""))) != want:
                continue
            score = -overlap
            if best is None or score < best[0]:
                best = (score, ci, wi)
    if best is None:
        return None
    return best[1], best[2]


# --------------------------------------------------------------------------
# צבע לפי דובר
# --------------------------------------------------------------------------
SPEAKER_PALETTE = ["#FFFFFF", "#8FD3FF", "#FFD48F", "#B9FF8F", "#FF9FB0"]


def speaker_color_map(cues: list[Cue]) -> dict[str, str]:
    """
    צבע יציב לכל תווית דובר, לפי סדר ההופעה.

    מוחזר גם כשהפריסט אינו משתמש בו — הממשק יכול להציג אותו
    בעורך. אין כאן זיהוי דוברים: התוויות מגיעות מבחוץ.
    """
    order: list[str] = []
    for c in cues:
        if c.speaker and c.speaker not in order:
            order.append(c.speaker)
    return {name: SPEAKER_PALETTE[i % len(SPEAKER_PALETTE)]
            for i, name in enumerate(order)}


def max_chars_for_frame(style: SubtitleStyle, preset: CaptionPreset,
                        play_width: int) -> int:
    """תקרת התווים בפועל: המחמיר מבין הפריסט לבין רוחב הפריים."""
    return max(8, min(preset.max_chars, _max_chars_for(style, play_width)))
