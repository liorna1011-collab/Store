"""
סגנון כתוביות v2 – מודל הנתונים שהממשק, הפרויקט והרינדור חולקים.

עקרונות:
  * כל הגדלים **יחסיים לפריים**: `size` באחוזים מגובה הפריים, `offset`
    באחוזים מגובה הפריים מהקצה, `outline`/`shadow` בפיקסלים ביחס לפריים
    בגובה 1080. ההמרה לפיקסלים נעשית פעם אחת, רק בכתיבת קובץ ה-ASS.
    כך אין „כיווץ כפול" ואין שינוי גודל בכל ייצוא חוזר.
  * Presets הם נתונים: מזהה, תווית בכל שפה וסגנון מלא. הוספת preset
    היא הוספת רשומה אחת ל-`PRESETS`.
  * כל ערך שמגיע מבחוץ עובר `clamp_style` בצד השרת.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, fields
from typing import Any, Optional

# --------------------------------------------------------------------------
# ערכים חוקיים
# --------------------------------------------------------------------------
WEIGHTS = (400, 500, 600, 700, 800, 900)
BACKGROUNDS = ("none", "box", "bar")
POSITIONS = ("top", "middle", "bottom")
ANIMATIONS = ("none", "fade", "pop", "karaoke", "word", "bounce")

# טווחים (מינימום, מקסימום)
SIZE_RANGE = (2.0, 12.0)          # % מגובה הפריים
OUTLINE_RANGE = (0.0, 12.0)       # px בפריים 1080
SHADOW_RANGE = (0.0, 8.0)
OFFSET_RANGE = (0.0, 40.0)        # % מגובה הפריים
WORDS_PER_LINE_RANGE = (1, 12)
MAX_LINES_RANGE = (1, 3)

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")

# גופנים מועדפים לפי שפה. הראשון שמותקן במחשב נבחר כברירת מחדל.
FONT_CANDIDATES: dict[str, tuple[str, ...]] = {
    "he": ("Noto Sans Hebrew", "Heebo", "Rubik", "Assistant", "Arial",
           "Segoe UI", "DejaVu Sans", "FreeSans"),
    "en": ("Inter", "Noto Sans", "Roboto", "Arial", "Segoe UI",
           "DejaVu Sans", "Liberation Sans", "FreeSans"),
}


@dataclass
class SubtitleStyleV2:
    preset: Optional[str] = "clean"
    font: str = ""
    size: float = 4.5
    weight: int = 700
    color: str = "#FFFFFF"
    highlight_color: str = "#FFD400"
    background: str = "none"
    background_color: str = "#000000"
    background_opacity: float = 0.6
    outline: float = 3.0
    outline_color: str = "#000000"
    shadow: float = 1.0
    shadow_color: str = "#000000"
    shadow_opacity: float = 0.6
    position: str = "bottom"
    offset: float = 12.0
    words_per_line: int = 4
    max_lines: int = 2
    animation: str = "karaoke"
    uppercase: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]],
                  *, language: str = "") -> "SubtitleStyleV2":
        return cls(**clamp_style(data or {}, language=language))


FIELD_NAMES = tuple(f.name for f in fields(SubtitleStyleV2))


# --------------------------------------------------------------------------
# Presets
# --------------------------------------------------------------------------
PRESETS: dict[str, dict[str, Any]] = {
    "clean": {
        "label": {"he": "נקי", "en": "Clean"},
        "description": {
            "he": "שתי שורות קריאות בתחתית, עם הדגשה רצה על המילה הנאמרת.",
            "en": "Two readable lines at the bottom, with the spoken word highlighted."},
        "style": {
            "size": 4.5, "weight": 700, "color": "#FFFFFF",
            "highlight_color": "#FFD400", "background": "none",
            "outline": 3.0, "outline_color": "#000000", "shadow": 1.0,
            "position": "bottom", "offset": 12.0, "words_per_line": 4,
            "max_lines": 2, "animation": "karaoke", "uppercase": False},
    },
    "viral": {
        "label": {"he": "ויראלי", "en": "Viral"},
        "description": {
            "he": "שורה אחת, מעט מילים, טקסט גדול ומילה פעילה שקופצת.",
            "en": "One short line, big bold text and a popping active word."},
        "style": {
            "size": 6.0, "weight": 900, "color": "#FFFFFF",
            "highlight_color": "#FFE600", "background": "none",
            "outline": 5.0, "outline_color": "#000000", "shadow": 2.0,
            "position": "bottom", "offset": 18.0, "words_per_line": 3,
            "max_lines": 1, "animation": "pop", "uppercase": True},
    },
    "cinematic": {
        "label": {"he": "קולנועי", "en": "Cinematic"},
        "description": {
            "he": "כתובית קטנה ומאופקת שנכנסת ויוצאת בעדינות.",
            "en": "Small, restrained captions that fade in and out."},
        "style": {
            "size": 3.6, "weight": 500, "color": "#FFFFFF",
            "highlight_color": "#E8E8E8", "background": "none",
            "outline": 1.5, "outline_color": "#000000", "shadow": 0.8,
            "position": "bottom", "offset": 8.0, "words_per_line": 7,
            "max_lines": 2, "animation": "fade", "uppercase": False},
    },
    "podcast": {
        "label": {"he": "פודקאסט", "en": "Podcast"},
        "description": {
            "he": "טקסט על רקע כהה חצי-שקוף, נוח לשיחות ארוכות.",
            "en": "Text on a semi-transparent dark box, easy on long talks."},
        "style": {
            "size": 4.2, "weight": 700, "color": "#FFFFFF",
            "highlight_color": "#FFB35C", "background": "box",
            "background_color": "#000000", "background_opacity": 0.55,
            "outline": 0.0, "shadow": 0.0,
            "position": "bottom", "offset": 10.0, "words_per_line": 6,
            "max_lines": 2, "animation": "karaoke", "uppercase": False},
    },
    "story": {
        "label": {"he": "סיפור", "en": "Story"},
        "description": {
            "he": "מילים שמופיעות אחת-אחת במרכז הפריים, בקצב של סיפור.",
            "en": "Words appear one by one in the middle of the frame."},
        "style": {
            "size": 5.0, "weight": 800, "color": "#FFFFFF",
            "highlight_color": "#FF6B8A", "background": "none",
            "outline": 3.5, "outline_color": "#000000", "shadow": 1.5,
            "position": "middle", "offset": 0.0, "words_per_line": 3,
            "max_lines": 2, "animation": "word", "uppercase": False},
    },
}

DEFAULT_PRESET = "clean"


def preset_catalog(lang: Optional[str] = None) -> list[dict[str, Any]]:
    """רשימת ה-presets לממשק, עם סגנון מלא ותוויות בכל השפות."""
    out = []
    for pid, p in PRESETS.items():
        style = default_style(lang or "he", preset=pid)
        out.append({"id": pid, "label": dict(p["label"]),
                    "description": dict(p.get("description") or {}),
                    "style": style})
    return out


# --------------------------------------------------------------------------
# ברירות מחדל ואימות
# --------------------------------------------------------------------------
def default_font(language: str) -> str:
    """הגופן הראשון מהרשימה שמותקן במחשב (או הראשון ברשימה כשאין מידע)."""
    code = "he" if (language or "").lower().startswith(("he", "iw")) else "en"
    candidates = FONT_CANDIDATES[code]
    try:
        from .fonts import installed_families

        installed = installed_families()
    except Exception:                                 # noqa: BLE001
        installed = set()
    if installed:
        for fam in candidates:
            if fam in installed:
                return fam
    return candidates[0] if not installed else "DejaVu Sans"


def default_style(language: str = "he", *,
                  preset: str = DEFAULT_PRESET) -> dict[str, Any]:
    base = SubtitleStyleV2().to_dict()
    p = PRESETS.get(preset) or PRESETS[DEFAULT_PRESET]
    base.update(p["style"])
    base["preset"] = preset if preset in PRESETS else DEFAULT_PRESET
    base["font"] = default_font(language)
    return base


def _num(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v != v:                       # NaN
        return default
    return round(min(hi, max(lo, v)), 3)


def _int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        return default
    return min(hi, max(lo, v))


def _color(value: Any, default: str) -> str:
    text = str(value or "").strip()
    if len(text) == 4 and text.startswith("#"):
        text = "#" + "".join(c * 2 for c in text[1:])
    return text.upper() if _HEX.match(text) else default


def _font(value: Any, default: str) -> str:
    text = str(value or "").strip()
    # שם גופן: אותיות, ספרות, רווח וסימנים בסיסיים בלבד (נכנס לקובץ ASS)
    text = re.sub(r"[^\w \-\.]", "", text, flags=re.UNICODE)[:64].strip()
    return text or default


def clamp_style(data: dict[str, Any], *, language: str = "") -> dict[str, Any]:
    """
    מאמת ומתקן סגנון שמגיע מבחוץ. ערך לא חוקי מוחלף בברירת המחדל של
    ה-preset (או של clean), כך שהתוצאה תמיד ניתנת לרינדור.
    """
    data = dict(data or {})
    preset = data.get("preset")
    preset = preset if isinstance(preset, str) and preset in PRESETS else None
    base = default_style(language or "he", preset=preset or DEFAULT_PRESET)
    base["preset"] = preset

    out: dict[str, Any] = {"preset": preset}
    out["font"] = _font(data.get("font"), base["font"])
    out["size"] = _num(data.get("size", base["size"]), base["size"], *SIZE_RANGE)
    weight = _int(data.get("weight", base["weight"]), base["weight"], 400, 900)
    out["weight"] = min(WEIGHTS, key=lambda w: abs(w - weight))
    out["color"] = _color(data.get("color"), base["color"])
    out["highlight_color"] = _color(data.get("highlight_color"), base["highlight_color"])
    bg = data.get("background", base["background"])
    out["background"] = bg if bg in BACKGROUNDS else base["background"]
    out["background_color"] = _color(data.get("background_color"), base["background_color"])
    out["background_opacity"] = _num(data.get("background_opacity",
                                              base["background_opacity"]),
                                     base["background_opacity"], 0.0, 1.0)
    out["outline"] = _num(data.get("outline", base["outline"]), base["outline"],
                          *OUTLINE_RANGE)
    out["outline_color"] = _color(data.get("outline_color"), base["outline_color"])
    out["shadow"] = _num(data.get("shadow", base["shadow"]), base["shadow"], *SHADOW_RANGE)
    out["shadow_color"] = _color(data.get("shadow_color"), base["shadow_color"])
    out["shadow_opacity"] = _num(data.get("shadow_opacity", base["shadow_opacity"]),
                                 base["shadow_opacity"], 0.0, 1.0)
    pos = data.get("position", base["position"])
    out["position"] = pos if pos in POSITIONS else base["position"]
    out["offset"] = _num(data.get("offset", base["offset"]), base["offset"], *OFFSET_RANGE)
    out["words_per_line"] = _int(data.get("words_per_line", base["words_per_line"]),
                                 base["words_per_line"], *WORDS_PER_LINE_RANGE)
    out["max_lines"] = _int(data.get("max_lines", base["max_lines"]),
                            base["max_lines"], *MAX_LINES_RANGE)
    anim = data.get("animation", base["animation"])
    out["animation"] = anim if anim in ANIMATIONS else base["animation"]
    out["uppercase"] = bool(data.get("uppercase", base["uppercase"]))
    return out


def apply_preset(style: dict[str, Any], preset: str,
                 *, language: str = "he") -> dict[str, Any]:
    """מחיל preset על סגנון קיים ושומר את הגופן שהמשתמש בחר."""
    fresh = default_style(language, preset=preset)
    if style.get("font"):
        fresh["font"] = style["font"]
    return clamp_style(fresh, language=language)


# --------------------------------------------------------------------------
# המרה מסגנונות ישנים
# --------------------------------------------------------------------------
_LEGACY_ANIMATION = {"none": "none", "pop": "pop", "punch": "bounce"}


def from_legacy(old: Optional[dict[str, Any]], *, vertical: bool,
                language: str = "") -> dict[str, Any]:
    """
    ממיר סגנון ישן (SubtitleStyle בפיקסלים, או הגדרות subtitle_*) לסגנון v2.

    הסגנון הישן נמדד בפיקסלים של פריים ייחוס (1920 באנכי, 1080 באופקי),
    ולכן ההמרה מחלקת בגובה הייחוס.
    """
    old = dict(old or {})
    if "size" in old and isinstance(old.get("size"), (int, float)) \
            and "primary_color" not in old and "weight" in old:
        # כבר v2
        return clamp_style(old, language=language)

    ref_h = 1920.0 if vertical else 1080.0
    size_px = old.get("size", old.get("subtitle_size"))
    out: dict[str, Any] = {"preset": None}
    if isinstance(size_px, (int, float)) and size_px > 0:
        out["size"] = round(float(size_px) / ref_h * 100.0, 2)
    if old.get("font") or old.get("subtitle_font"):
        out["font"] = old.get("font") or old.get("subtitle_font")
    out["color"] = old.get("primary_color") or old.get("subtitle_color") or "#FFFFFF"
    out["outline_color"] = old.get("outline_color") or old.get("subtitle_outline_color") \
        or "#000000"
    if old.get("highlight_color"):
        out["highlight_color"] = old["highlight_color"]
    if isinstance(old.get("outline"), (int, float)):
        out["outline"] = float(old["outline"]) * (1080.0 / ref_h)
    if isinstance(old.get("shadow"), (int, float)):
        out["shadow"] = float(old["shadow"])
    out["position"] = old.get("position") or old.get("subtitle_position") or "bottom"
    anim = old.get("animation") or old.get("subtitle_animation") or "none"
    word_level = old.get("word_level", old.get("subtitle_word_level", True))
    out["animation"] = _LEGACY_ANIMATION.get(anim, "none")
    if word_level and out["animation"] == "none":
        out["animation"] = "karaoke"
    out["weight"] = 700 if old.get("bold", True) else 400
    return clamp_style(out, language=language)


def is_v2(style: Optional[dict[str, Any]]) -> bool:
    return bool(style) and "words_per_line" in style and "max_lines" in style \
        and "weight" in style
