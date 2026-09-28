"""עזרי טקסט: זיהוי עברית/RTL, שבירת שורות לכתוביות, פורמט זמן."""

from __future__ import annotations

import re
import unicodedata

# טווחי יוניקוד של כתבים מימין לשמאל
_RTL_RANGES = (
    (0x0590, 0x05FF),   # עברית
    (0x0600, 0x06FF),   # ערבית
    (0x0700, 0x074F),   # סורית
    (0xFB1D, 0xFB4F),   # עברית – צורות מצגת
    (0xFE70, 0xFEFF),   # ערבית – צורות מצגת
)

# תווי בקרת כיווניות
RLE = "‫"   # Right-to-Left Embedding
LRE = "‪"
PDF = "‬"   # Pop Directional Formatting
RLM = "‏"   # Right-to-Left Mark
LRM = "‎"


def is_rtl_char(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _RTL_RANGES)


def rtl_ratio(text: str) -> float:
    """שיעור התווים האלפביתיים שהם RTL. 0 = אין, 1 = הכול."""
    letters = [c for c in text if unicodedata.category(c).startswith("L")]
    if not letters:
        return 0.0
    return sum(1 for c in letters if is_rtl_char(c)) / len(letters)


def is_rtl_text(text: str, threshold: float = 0.25) -> bool:
    return rtl_ratio(text) >= threshold


def detect_language_hint(text: str) -> str:
    """ניחוש גס של שפה מהתווים בלבד: he / en / other."""
    if is_rtl_text(text):
        return "he"
    if re.search(r"[A-Za-z]", text):
        return "en"
    return "other"


def wrap_subtitle(text: str, max_chars: int = 34, max_lines: int = 2) -> list[str]:
    """
    שבירה לשורות כתובית לפי מילים.
    עובד נכון גם בעברית: השבירה היא ברמת מילים לוגיות,
    וה-bidi עצמו מטופל בזמן הרינדור על-ידי libass/fribidi.
    """
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return []
    words = text.split(" ")
    lines: list[str] = []
    current = ""
    for w in words:
        cand = f"{current} {w}".strip()
        if len(cand) <= max_chars or not current:
            current = cand
        else:
            lines.append(current)
            current = w
            if len(lines) >= max_lines:
                break
    if current and len(lines) < max_lines:
        lines.append(current)
    # אם נשארו מילים – מצרפים לשורה האחרונה כדי לא לאבד טקסט
    used = sum(len(l.split(" ")) for l in lines)
    if used < len(words) and lines:
        lines[-1] = " ".join([lines[-1]] + words[used:])
    return lines


def bidi_wrap(text: str) -> str:
    """
    עוטף טקסט RTL בסימון כיווניות כדי שמספרים/לטינית בתוכו
    יסודרו נכון ברינדור. libass+fribidi מבצעים את הסידור עצמו.
    """
    if not text:
        return text
    if is_rtl_text(text):
        return f"{RLE}{text}{PDF}"
    return text


def format_timestamp(seconds: float, *, style: str = "srt") -> str:
    """HH:MM:SS,mmm ל-SRT או H:MM:SS.cc ל-ASS."""
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if style == "ass":
        cs = int(round((seconds - int(seconds)) * 100))
        if cs >= 100:
            cs, s = 0, s + 1
        return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"
    ms = int(round((seconds - int(seconds)) * 1000))
    if ms >= 1000:
        ms, s = 0, s + 1
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def format_duration_he(seconds: float) -> str:
    """משך קריא בעברית: '3:42' או '1:05:20'."""
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def hex_to_ass_color(hex_color: str, alpha: int = 0) -> str:
    """
    ממיר #RRGGBB לפורמט צבע של ASS: &HAABBGGRR (אלפא הפוך: 0=אטום).
    """
    h = (hex_color or "#FFFFFF").strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6 or not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        h = "FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    a = max(0, min(255, int(alpha)))
    return f"&H{a:02X}{b}{g}{r}".upper()


def ass_escape(text: str) -> str:
    """בריחה של תווים מיוחדים בשורת דיאלוג של ASS."""
    return (
        (text or "")
        .replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\r\n", "\\N")
        .replace("\n", "\\N")
        .replace("\r", "\\N")
    )


_SENTENCE_END = re.compile(r"[.!?…]|[׃׀]|\.\.\.")


def looks_like_sentence_end(text: str) -> bool:
    t = (text or "").rstrip()
    return bool(t) and bool(_SENTENCE_END.search(t[-2:]))


def truncate(text: str, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
