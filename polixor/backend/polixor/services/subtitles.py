"""
כתוביות: בניית קיוז מהתמלול, ייצוא ASS (לצריבה) ו-SRT (להורדה).

עברית מימין לשמאל:
  libass עם fribidi+harfbuzz מבצע את סידור ה-bidi ואת עיצוב האותיות
  בזמן הרינדור, ולכן הטקסט נשמר בסדר לוגי בקובץ. אנחנו אחראים על
  בחירת גופן שתומך בעברית, על שבירת שורות נכונה, ועל כך שתגיות
  העיצוב לא ישברו את סדר הכיווניות.

תזמון ברמת מילה מגיע מ-faster-whisper כשהוא זמין; אחרת הכתובית
מוצגת ברמת המשפט. אין המצאת טקסט – כל כתובית מקורה בתמלול,
והמשתמש יכול לתקן אותה לפני הייצוא.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..config import AppSettings
from .transcribe import TranscriptResult, Word
from ..util.text import (
    ass_escape,
    format_timestamp,
    hex_to_ass_color,
    is_rtl_text,
    looks_like_sentence_end,
    wrap_subtitle,
)

log = logging.getLogger("polixor.subtitles")

# גופנים מועדפים לעברית, לפי מערכת הפעלה. הראשון שקיים – נבחר.
HEBREW_FONT_CANDIDATES = [
    "Noto Sans Hebrew", "Arial", "Segoe UI", "Alef", "Rubik",
    "DejaVu Sans", "FreeSans", "Liberation Sans",
]
LATIN_FONT_CANDIDATES = [
    "Inter", "Segoe UI", "Arial", "Roboto", "DejaVu Sans", "Liberation Sans",
]


@dataclass
class Cue:
    start: float            # שניות יחסית לתחילת הקליפ
    end: float
    text: str
    words: list[dict[str, Any]] = field(default_factory=list)
    language: str = ""
    speaker: str = ""       # תווית דובר, אם סופקה. ראו caption_engine.
    emphasis: list[int] = field(default_factory=list)   # אינדקסי מילים מודגשות

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict[str, Any]:
        row = {"start": round(self.start, 3), "end": round(self.end, 3),
               "text": self.text, "words": self.words, "language": self.language}
        if self.speaker:
            row["speaker"] = self.speaker
        if self.emphasis:
            row["emphasis"] = list(self.emphasis)
        return row


@dataclass
class SubtitleStyle:
    font: str = "DejaVu Sans"
    size: int = 54
    primary_color: str = "#FFFFFF"
    outline_color: str = "#000000"
    highlight_color: str = "#FFD400"
    emphasis_color: str = "#3BE8B0"   # מילת מפתח — נבדל מצבע ההדגשה הרץ
    position: str = "bottom"        # top | middle | bottom
    bold: bool = True
    outline: float = 3.0
    shadow: float = 1.0
    margin_v: int = 110
    margin_h: int = 60
    word_level: bool = True
    animation: str = "pop"          # none | pop | punch

    @classmethod
    def from_settings(cls, s: AppSettings, *, vertical: bool) -> "SubtitleStyle":
        return cls(
            font=s.subtitle_font or "DejaVu Sans",
            size=int(s.subtitle_size if vertical else max(24, int(s.subtitle_size * 0.62))),
            primary_color=s.subtitle_color,
            outline_color=s.subtitle_outline_color,
            position=s.subtitle_position,
            word_level=bool(s.subtitle_word_level),
            animation=s.subtitle_animation,
            margin_v=150 if vertical else 70,
            margin_h=70 if vertical else 90,
        )

    def merge(self, patch: Optional[dict[str, Any]]) -> "SubtitleStyle":
        if not patch:
            return self
        data = {**self.__dict__, **{k: v for k, v in patch.items()
                                    if k in self.__dict__ and v is not None}}
        return SubtitleStyle(**data)


# --------------------------------------------------------------------------
# בניית קיוז מהתמלול
# --------------------------------------------------------------------------
def build_cues(
    transcript: Optional[TranscriptResult],
    *,
    clip_start: float,
    clip_end: float,
    max_chars: int = 34,
    max_lines: int = 2,
    max_cue_seconds: float = 3.6,
    min_cue_seconds: float = 0.55,
    time_offset: float = 0.0,
) -> list[Cue]:
    """
    בונה כתוביות לקטע. הזמנים מוחזרים יחסית לתחילת הקליפ
    (בתוספת `time_offset` – שימושי בקליפ Highlights מורכב ממקטעים).
    """
    if transcript is None or not transcript.segments:
        return []

    words = transcript.words_between(clip_start, clip_end)
    if words:
        return _cues_from_words(words, clip_start, clip_end, max_chars, max_lines,
                                max_cue_seconds, min_cue_seconds, time_offset,
                                transcript.language)
    return _cues_from_segments(transcript, clip_start, clip_end, max_chars,
                               max_lines, max_cue_seconds, time_offset)


def _cues_from_words(words: list[Word], clip_start: float, clip_end: float,
                     max_chars: int, max_lines: int, max_cue: float,
                     min_cue: float, offset: float, language: str) -> list[Cue]:
    budget = max_chars * max_lines
    cues: list[Cue] = []
    bucket: list[Word] = []

    def flush() -> None:
        if not bucket:
            return
        text = " ".join(w.text for w in bucket if w.text).strip()
        if not text:
            bucket.clear()
            return
        s = max(clip_start, bucket[0].start)
        e = min(clip_end, max(bucket[-1].end, s + min_cue))
        cues.append(Cue(
            start=round(s - clip_start + offset, 3),
            end=round(e - clip_start + offset, 3),
            text=text,
            words=[{"start": round(w.start - clip_start + offset, 3),
                    "end": round(w.end - clip_start + offset, 3),
                    "text": w.text} for w in bucket if w.text],
            language=language,
        ))
        bucket.clear()

    for w in words:
        if not w.text:
            continue
        projected = sum(len(x.text) + 1 for x in bucket) + len(w.text)
        span = (w.end - bucket[0].start) if bucket else 0.0
        gap = (w.start - bucket[-1].end) if bucket else 0.0

        if bucket and (projected > budget or span > max_cue or gap > 0.75):
            flush()
        bucket.append(w)
        if looks_like_sentence_end(w.text) and \
                sum(len(x.text) + 1 for x in bucket) > budget * 0.45:
            flush()
    flush()
    return _fix_overlaps(cues)


def _cues_from_segments(transcript: TranscriptResult, clip_start: float,
                        clip_end: float, max_chars: int, max_lines: int,
                        max_cue: float, offset: float) -> list[Cue]:
    cues: list[Cue] = []
    budget = max_chars * max_lines
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

        # פיצול מקטע ארוך לחלקים לפי מכסת תווים, בחלוקת זמן פרופורציונלית
        chunks = _split_text(text, budget)
        span = (e - s) / max(1, len(chunks))
        for k, chunk in enumerate(chunks):
            cs = s + k * span
            ce = min(e, cs + min(span, max_cue))
            cues.append(Cue(start=round(cs - clip_start + offset, 3),
                            end=round(ce - clip_start + offset, 3),
                            text=chunk, language=seg.language or transcript.language))
    return _fix_overlaps(cues)


def _split_text(text: str, budget: int) -> list[str]:
    words = text.split()
    out: list[str] = []
    cur = ""
    for w in words:
        cand = f"{cur} {w}".strip()
        if len(cand) > budget and cur:
            out.append(cur)
            cur = w
        else:
            cur = cand
    if cur:
        out.append(cur)
    return out or [text]


def _fix_overlaps(cues: list[Cue]) -> list[Cue]:
    """מונע חפיפה בין כתוביות עוקבות ומבטיח משך מינימלי."""
    cues = [c for c in cues if c.text.strip()]
    cues.sort(key=lambda c: c.start)
    for i, c in enumerate(cues):
        c.start = max(0.0, c.start)
        if c.end <= c.start:
            c.end = c.start + 0.6
        if i + 1 < len(cues) and c.end > cues[i + 1].start:
            c.end = max(c.start + 0.25, cues[i + 1].start - 0.02)
    return cues


# --------------------------------------------------------------------------
# ייצוא ASS
# --------------------------------------------------------------------------
def _alignment(position: str) -> int:
    return {"top": 8, "middle": 5, "bottom": 2}.get(position, 2)


def write_ass(
    cues: list[Cue],
    dst: str | Path,
    *,
    width: int,
    height: int,
    style: SubtitleStyle,
    title_text: str = "",
    title_seconds: float = 2.6,
) -> Path:
    """
    כותב קובץ ASS לצריבה ב-FFmpeg.
    ב-word_level כל מילה מודגשת בתורה (כמו בשורטים מודרניים).
    `title_text` מוסיף כרטיס כותרת בתחילת הסרטון – מרונדר גם הוא
    דרך libass, כדי שעברית תוצג נכון מימין לשמאל.
    """
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)

    primary = hex_to_ass_color(style.primary_color)
    outline_c = hex_to_ass_color(style.outline_color)
    highlight = hex_to_ass_color(style.highlight_color)
    emphasis = hex_to_ass_color(style.emphasis_color)
    align = _alignment(style.position)
    bold = -1 if style.bold else 0
    title_size = int(style.size * 1.22)

    header = f"""[Script Info]
; נוצר על-ידי Polixor
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: {width}
PlayResY: {height}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Polixor,{style.font},{style.size},{primary},{highlight},{outline_c},&H80000000,{bold},0,0,0,100,100,0,0,1,{style.outline},{style.shadow},{align},{style.margin_h},{style.margin_h},{style.margin_v},1
Style: PolixorTitle,{style.font},{title_size},{primary},{primary},{outline_c},&H80000000,-1,0,0,0,100,100,0,0,1,{style.outline + 1.5},{style.shadow + 1},5,{style.margin_h},{style.margin_h},0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    lines: list[str] = []
    if title_text.strip():
        lines.append(_title_event(title_text.strip(), title_seconds, style))
    for cue in cues:
        if not cue.text.strip():
            continue
        if style.word_level and cue.words and len(cue.words) > 1:
            lines.extend(_word_level_events(cue, style, highlight, primary,
                                            width, emphasis))
        else:
            body = _render_plain_cue(cue, style, width, emphasis, primary)
            lines.append(_dialogue(cue.start, cue.end, body))

    dst.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    return dst


def _dialogue(start: float, end: float, text: str, style_name: str = "Polixor") -> str:
    return (f"Dialogue: 0,{format_timestamp(start, style='ass')},"
            f"{format_timestamp(end, style='ass')},{style_name},,0,0,0,,{text}")


def _title_event(text: str, seconds: float, style: SubtitleStyle) -> str:
    """כרטיס כותרת בתחילת הסרטון, עם הופעה והיעלמות רכות."""
    lines = wrap_subtitle(text, max_chars=max(12, _max_chars_for(style) - 5),
                          max_lines=3)
    body = ass_escape("\n".join(lines))
    fade_ms = 350
    return _dialogue(0.0, max(1.0, seconds),
                     f"{{\\fad({fade_ms},{fade_ms})}}{body}", "PolixorTitle")


def _render_text(text: str, style: SubtitleStyle, play_width: int = 1080) -> str:
    """שובר שורות ומחזיר טקסט ASS. סדר התווים נשאר לוגי (bidi ברינדור)."""
    from .caption_engine import wrap_balanced

    lines = wrap_balanced(text, max_chars=_max_chars_for(style, play_width),
                          max_lines=2)
    return ass_escape("\n".join(lines))


def _emphasis_tags(style: SubtitleStyle, emphasis: str,
                   primary: str) -> tuple[str, str]:
    """
    תגיות למילת מפתח מודגשת.

    ההדגשה היא צבע + מתאר עבה יותר, בלי אנימציה: המילה צריכה לבלוט
    בקריאה, לא לקפוץ. באנימציה היא הייתה מתחרה בהדגשה הרצה של
    ה-word-level ושתיהן היו מאבדות משמעות.
    """
    return (f"{{\\c{emphasis}\\bord{style.outline + 1.0:.1f}}}",
            f"{{\\c{primary}\\bord{style.outline:.1f}}}")


def _render_plain_cue(cue: Cue, style: SubtitleStyle, play_width: int,
                      emphasis: str, primary: str) -> str:
    """
    כתובית ברמת משפט. מילים שסומנו להדגשה נצבעות בצבע ההדגשה.

    אינדקסי `cue.emphasis` מצביעים על רצף המילים של הטקסט — אותו
    רצף שמופיע ב-`cue.words` כשיש תזמון ברמת מילה.
    """
    from .caption_engine import wrap_balanced

    max_chars = _max_chars_for(style, play_width)
    tokens = (cue.text or "").split()
    if not tokens:
        return ""
    marks = {i for i in (cue.emphasis or []) if 0 <= i < len(tokens)}
    if not marks:
        lines = wrap_balanced(cue.text, max_chars=max_chars, max_lines=2)
        return ass_escape("\n".join(lines))

    # שוברים על הטקסט הנקי ואז מרכיבים מחדש עם התגיות באותם גבולות
    lines = wrap_balanced(cue.text, max_chars=max_chars, max_lines=2)
    open_tag, close_tag = _emphasis_tags(style, emphasis, primary)
    out_lines: list[str] = []
    idx = 0
    for line in lines:
        parts: list[str] = []
        for tok in line.split():
            body = ass_escape(tok)
            parts.append(f"{open_tag}{body}{close_tag}" if idx in marks else body)
            idx += 1
        out_lines.append(" ".join(parts))
    return "\\N".join(out_lines)


def _max_chars_for(style: SubtitleStyle, play_width: int = 1080) -> int:
    """
    כמה תווים נכנסים בשורה, לפי רוחב הפריים, השוליים וגודל הגופן.
    0.52 הוא רוחב תו ממוצע יחסית לגובה הגופן, מדוד על גופני sans
    בעברית ובאנגלית. מוגבל בטווח שפוי כדי לא לקבל שורה ארוכה מדי.
    """
    usable = max(80, play_width - 2 * style.margin_h)
    approx = usable / max(1.0, style.size * 0.52)
    return int(max(14, min(46, approx)))


def _word_level_events(cue: Cue, style: SubtitleStyle, highlight: str,
                       primary: str, play_width: int = 1080,
                       emphasis: str = "") -> list[str]:
    """
    יוצר אירוע נפרד לכל מילה, כשהמילה הפעילה צבועה בצבע ההדגשה.
    תגיות ASS אינן משתתפות בסידור ה-bidi, ולכן זה בטוח גם בעברית.

    מילת מפתח (`cue.emphasis`) נשארת בצבע ההדגשה שלה גם כשהיא אינה
    המילה הפעילה, כדי שהצופה יראה אותה לאורך כל הכתובית.
    """
    words = [w for w in cue.words if str(w.get("text", "")).strip()]
    if not words:
        return [_dialogue(cue.start, cue.end,
                          _render_plain_cue(cue, style, play_width,
                                            emphasis or primary, primary))]
    marks = {i for i in (cue.emphasis or []) if 0 <= i < len(words)}
    em_open, em_close = _emphasis_tags(style, emphasis or highlight, primary)

    max_chars = _max_chars_for(style, play_width)
    events: list[str] = []
    for i, w in enumerate(words):
        start = float(w.get("start", cue.start))
        end = float(w.get("end", start + 0.3))
        if i + 1 < len(words):
            end = min(end, float(words[i + 1].get("start", end)))
        end = max(start + 0.08, min(end, cue.end))
        if start >= cue.end:
            break

        active_open, active_close = _active_word_tags(style, highlight, primary)
        parts: list[str] = []
        for j, ww in enumerate(words):
            token = ass_escape(str(ww.get("text", "")).strip())
            if not token:
                continue
            if j == i:
                parts.append(f"{active_open}{token}{active_close}")
            elif j in marks:
                parts.append(f"{em_open}{token}{em_close}")
            else:
                parts.append(token)
        body = " ".join(parts)

        # שבירת שורה: מחשבים על הטקסט הנקי ואז מיישמים על אותו אינדקס מילה
        plain_words = [str(x.get("text", "")).strip() for x in words]
        break_at = _line_break_index(plain_words, max_chars)
        if break_at and 0 < break_at < len(parts):
            body = " ".join(parts[:break_at]) + "\\N" + " ".join(parts[break_at:])

        events.append(_dialogue(start, end, body))
    return events


def _active_word_tags(style: SubtitleStyle, highlight: str,
                      primary: str) -> tuple[str, str]:
    """
    תגיות ASS למילה הפעילה.

    האנימציה היא קפיצה קצרה שחוזרת לגודל המקורי. היא נשארת מתונה
    בכוונה: שינוי גודל באמצע שורה מזיז את שאר המילים, ובאחוזים
    גבוהים מדי השורה מרצדת. 108%–118% למשך 140–170 מילישניות
    נקראים כאנרגיה ולא כתקלה.

    תגיות override אינן משתתפות בסידור ה-bidi של libass, ולכן
    האנימציה בטוחה גם בעברית.
    """
    anim = (style.animation or "none").lower()

    if anim == "punch":
        open_tag = (f"{{\\c{highlight}\\fscx118\\fscy118\\bord{style.outline + 1.2:.1f}"
                    f"\\t(0,90,\\fscx104\\fscy104)"
                    f"\\t(90,190,\\fscx100\\fscy100\\bord{style.outline:.1f})}}")
    elif anim == "pop":
        open_tag = (f"{{\\c{highlight}\\fscx108\\fscy108"
                    f"\\t(0,140,\\fscx100\\fscy100)}}")
    else:
        open_tag = f"{{\\c{highlight}}}"

    close_tag = f"{{\\c{primary}\\fscx100\\fscy100\\bord{style.outline:.1f}}}"
    return open_tag, close_tag


def _line_break_index(words: list[str], max_chars: int) -> int:
    """אינדקס המילה שממנה מתחילה השורה השנייה, או 0 אם שורה אחת מספיקה."""
    from .caption_engine import best_break_index

    return best_break_index(words, max_chars)


# --------------------------------------------------------------------------
# מיפוי דרך תכנית העריכה
# --------------------------------------------------------------------------
def remap_cues(cues: list["Cue"], plan) -> list["Cue"]:
    """
    ממפה כתוביות מזמני חלון המקור לזמני התוצר הערוך.

    כשהעורך מסיר אוויר מת, כל כתובית שאחרי החיתוך זזה אחורה.
    בלי המיפוי הזה הכתוביות היו מתנתקות מהדיבור.
    כתובית שנחתכה כולה נעלמת, וכתובית שנחתכה חלקית מתכווצת.
    """
    if plan is None:
        return cues
    out: list["Cue"] = []
    for cue in cues:
        span = plan.map_span(cue.start, cue.end)
        if span is None:
            continue
        new_start, new_end = span
        words: list[dict[str, Any]] = []
        kept_index: dict[int, int] = {}     # אינדקס מקורי → אינדקס חדש
        for i, w in enumerate(cue.words or []):
            try:
                ws = float(w.get("start", cue.start))
                we = float(w.get("end", cue.end))
            except (TypeError, ValueError):
                continue
            wspan = plan.map_span(ws, we)
            if wspan is None:
                continue
            kept_index[i] = len(words)
            words.append({**w, "start": wspan[0], "end": wspan[1]})

        text = cue.text
        if words and len(words) < len(cue.words or []):
            # חלק מהמילים נחתכו – הטקסט מתעדכן כדי שלא יוצג מה שלא נשמע
            kept = " ".join(str(w.get("text", "")).strip() for w in words).strip()
            if kept:
                text = kept
        # הדגשות נודדות עם המילים; הדגשה על מילה שנחתכה נעלמת איתה
        emphasis = [kept_index[i] for i in (cue.emphasis or [])
                    if i in kept_index] if cue.words else list(cue.emphasis or [])
        out.append(Cue(start=new_start, end=new_end, text=text,
                       words=words, language=cue.language,
                       speaker=cue.speaker, emphasis=emphasis))
    return _fix_overlaps(out)


# --------------------------------------------------------------------------
# ייצוא SRT
# --------------------------------------------------------------------------
def write_srt(cues: list[Cue], dst: str | Path) -> Path:
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    blocks: list[str] = []
    for i, cue in enumerate(cues, start=1):
        if not cue.text.strip():
            continue
        lines = wrap_subtitle(cue.text, max_chars=42, max_lines=2)
        blocks.append(
            f"{i}\n{format_timestamp(cue.start)} --> {format_timestamp(cue.end)}\n"
            + "\n".join(lines)
        )
    dst.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    return dst


# --------------------------------------------------------------------------
# בחירת גופן
# --------------------------------------------------------------------------
def resolve_font(requested: str, *, hebrew: bool) -> str:
    """
    מוודא שהגופן המבוקש קיים במערכת; אחרת בוחר חלופה שתומכת בעברית.
    מונע מצב שבו כתוביות בעברית מוצגות כריבועים.
    """
    available = _installed_fonts()
    if requested and (not available or requested in available):
        if not hebrew or not available:
            return requested
        if requested in available and _font_has_hebrew(requested):
            return requested

    candidates = HEBREW_FONT_CANDIDATES if hebrew else LATIN_FONT_CANDIDATES
    for cand in candidates:
        if not available or cand in available:
            if not hebrew or not available or _font_has_hebrew(cand):
                return cand
    return requested or "DejaVu Sans"


_FONT_CACHE: dict[str, Any] = {}


def _installed_fonts() -> set[str]:
    if "list" in _FONT_CACHE:
        return _FONT_CACHE["list"]
    fonts: set[str] = set()
    try:
        import subprocess

        res = subprocess.run(["fc-list", "--format", "%{family}\n"],
                             capture_output=True, text=True, timeout=20)
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                for fam in line.split(","):
                    if fam.strip():
                        fonts.add(fam.strip())
    except Exception:
        pass
    if not fonts:
        # Windows: קוראים מרישום הגופנים
        try:
            import winreg  # type: ignore

            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts")
            for i in range(winreg.QueryInfoKey(key)[1]):
                name, _, _ = winreg.EnumValue(key, i)
                fonts.add(name.split(" (")[0].strip())
        except Exception:
            pass
    _FONT_CACHE["list"] = fonts
    return fonts


def _font_has_hebrew(family: str) -> bool:
    cache = _FONT_CACHE.setdefault("hebrew", {})
    if family in cache:
        return cache[family]
    result = True
    try:
        import subprocess

        res = subprocess.run(["fc-list", ":lang=he", "--format", "%{family}\n"],
                             capture_output=True, text=True, timeout=20)
        if res.returncode == 0 and res.stdout.strip():
            he_fonts = {f.strip() for line in res.stdout.splitlines()
                        for f in line.split(",") if f.strip()}
            result = family in he_fonts
    except Exception:
        result = True   # אין fc-list (Windows) – מניחים שכן
    cache[family] = result
    return result


def style_for_clip(settings: AppSettings, *, vertical: bool,
                   language: str, override: Optional[dict[str, Any]] = None,
                   frame_height: int = 0) -> SubtitleStyle:
    """
    סגנון כתוביות לקליפ.

    `frame_height` הוא גובה הפריים בפועל. גודל גופן ב-ASS נמדד
    ביחידות הפלט, ולכן גודל שכוון לפריים 1920 מכסה רבע מהמסך
    בפריים 480. בלי ההתאמה הזו ייצוא ברזולוציה נמוכה מקבל
    כתוביות שמסתירות את הווידאו.
    """
    style = SubtitleStyle.from_settings(settings, vertical=vertical).merge(override)
    hebrew = (language or "").lower().startswith("he")
    style.font = resolve_font(style.font, hebrew=hebrew)
    if frame_height:
        from .caption_engine import frame_scale

        scale = frame_scale(frame_height, vertical=vertical)
        style.size = max(10, int(round(style.size * scale)))
        style.outline = round(max(0.6, style.outline * scale), 2)
        style.margin_v = int(round(style.margin_v * scale))
        style.margin_h = int(round(style.margin_h * scale))
    return style


def detect_cue_language(cues: list[Cue]) -> str:
    sample = " ".join(c.text for c in cues[:20])
    if not sample.strip():
        return ""
    return "he" if is_rtl_text(sample) else "en"
