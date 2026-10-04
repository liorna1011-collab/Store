"""
הגדרות פרויקט: ברירות מחדל, אימות, מיזוג, ומיפוי להגדרות הפייפליין.

`ProjectConfig` הוא מה שהמשתמש בוחר במסכי Mode ו-Settings. הפייפליין
עצמו עובד עם `AppSettings`, ולכן `settings_for_project` ממפה בין השניים.
כל ערך שמגיע מהלקוח עובר `clamp_config` – הלקוח לעולם אינו מקור אמת.
"""

from __future__ import annotations

import copy
from typing import Any, Optional

from .config import AppSettings
from .services import subtitle_style

# package: the content package – Shorts and long-form topic videos from one analysis
MODES = ("short", "longform", "package")
ASPECT_RATIOS = ("9:16", "1:1", "4:5", "16:9")
LAYOUTS = ("auto", "reaction", "face", "center", "blur")
CONTENT_LANGUAGES = ("auto", "he", "en")
UI_LANGUAGES = ("he", "en")

CLIP_LENGTHS: list[dict[str, int]] = [
    {"min": 15, "max": 30}, {"min": 30, "max": 60}, {"min": 60, "max": 90}]
CLIP_COUNTS = [3, 5, 8, 10, 15]
LONGFORM_TARGETS = [600, 900, 1200, 1800]

# יחס מסך ← רזולוציית פלט לשורטים
ASPECT_RESOLUTION = {
    "9:16": "1080x1920",
    "1:1": "1080x1080",
    "4:5": "1080x1350",
    "16:9": "1920x1080",
}

# פריסה בממשק ← פריסה בפייפליין
LAYOUT_TO_PIPELINE = {
    "auto": "auto",
    "reaction": "reaction",
    "face": "auto_face",
    "center": "center",
    "blur": "blur_pad",
}

# גבולות אורך הקליפ
CLIP_SECONDS_RANGE = (5, 180)
CLIP_COUNT_RANGE = (1, 20)
LONGFORM_RANGE = (120, 3600)


def default_config(ui_language: str = "he",
                   content_language: str = "auto") -> dict[str, Any]:
    """ברירת המחדל לפרויקט חדש."""
    sub_lang = content_language if content_language in ("he", "en") else ui_language
    return {
        "mode": "short",
        "aspect_ratio": "9:16",
        "clip_min_seconds": 20,
        "clip_max_seconds": 60,
        "clip_count": 5,
        "longform_target_seconds": 900,
        "layout": "auto",
        "subtitles": {
            "enabled": True,
            "style": subtitle_style.default_style(sub_lang or "he"),
        },
        "content_language": content_language if content_language in CONTENT_LANGUAGES
        else "auto",
        # שמות, כינויים ומונחים של הפרויקט – להטיית התמלול בלבד
        "vocabulary": [],
    }


def options() -> dict[str, Any]:
    return {
        "modes": list(MODES),
        "aspect_ratios": list(ASPECT_RATIOS),
        "layouts": list(LAYOUTS),
        "clip_lengths": copy.deepcopy(CLIP_LENGTHS),
        "clip_counts": list(CLIP_COUNTS),
        "longform_targets": list(LONGFORM_TARGETS),
        "content_languages": list(CONTENT_LANGUAGES),
        "subtitle": {
            "backgrounds": list(subtitle_style.BACKGROUNDS),
            "positions": list(subtitle_style.POSITIONS),
            "animations": list(subtitle_style.ANIMATIONS),
            "weights": list(subtitle_style.WEIGHTS),
            "size_range": list(subtitle_style.SIZE_RANGE),
            "outline_range": list(subtitle_style.OUTLINE_RANGE),
            "shadow_range": list(subtitle_style.SHADOW_RANGE),
            "offset_range": list(subtitle_style.OFFSET_RANGE),
            "words_per_line_range": list(subtitle_style.WORDS_PER_LINE_RANGE),
            "max_lines_range": list(subtitle_style.MAX_LINES_RANGE),
        },
    }


def deep_merge(base: dict[str, Any], patch: Optional[dict[str, Any]]) -> dict[str, Any]:
    """מיזוג עמוק: מילונים ממוזגים, כל ערך אחר מוחלף."""
    out = copy.deepcopy(base or {})
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def clamp_config(cfg: Optional[dict[str, Any]], *, ui_language: str = "he"
                 ) -> dict[str, Any]:
    """מחזיר הגדרות פרויקט תקינות, מתוקנות לטווחים המותרים."""
    cfg = dict(cfg or {})
    content_lang = cfg.get("content_language")
    content_lang = content_lang if content_lang in CONTENT_LANGUAGES else "auto"
    base = default_config(ui_language, content_lang)
    out: dict[str, Any] = {}

    mode = cfg.get("mode", base["mode"])
    out["mode"] = mode if mode in MODES else (None if mode is None else base["mode"])
    aspect = cfg.get("aspect_ratio", base["aspect_ratio"])
    out["aspect_ratio"] = aspect if aspect in ASPECT_RATIOS else base["aspect_ratio"]

    lo = _int(cfg.get("clip_min_seconds", base["clip_min_seconds"]),
              base["clip_min_seconds"], *CLIP_SECONDS_RANGE)
    hi = _int(cfg.get("clip_max_seconds", base["clip_max_seconds"]),
              base["clip_max_seconds"], *CLIP_SECONDS_RANGE)
    if hi < lo + 5:
        hi = min(CLIP_SECONDS_RANGE[1], lo + 5)
        lo = min(lo, hi - 5)
    out["clip_min_seconds"] = lo
    out["clip_max_seconds"] = hi
    out["clip_count"] = _int(cfg.get("clip_count", base["clip_count"]),
                             base["clip_count"], *CLIP_COUNT_RANGE)
    out["longform_target_seconds"] = _int(
        cfg.get("longform_target_seconds", base["longform_target_seconds"]),
        base["longform_target_seconds"], *LONGFORM_RANGE)
    layout = cfg.get("layout", base["layout"])
    out["layout"] = layout if layout in LAYOUTS else base["layout"]

    subs = cfg.get("subtitles") if isinstance(cfg.get("subtitles"), dict) else {}
    style_lang = content_lang if content_lang in ("he", "en") else ui_language
    style_in = subs.get("style") if isinstance(subs.get("style"), dict) else None
    out["subtitles"] = {
        "enabled": bool(subs.get("enabled", base["subtitles"]["enabled"])),
        "style": subtitle_style.clamp_style(
            style_in if style_in is not None else base["subtitles"]["style"],
            language=style_lang),
    }
    out["content_language"] = content_lang
    from .services.vocabulary import normalize_terms

    out["vocabulary"] = normalize_terms(cfg.get("vocabulary", base["vocabulary"]))
    out["studio"] = clamp_studio(cfg.get("studio"))
    return out


PROFILES = ("auto", "livestream", "podcast", "news", "solo", "general")
QUALITY_MODES = ("premium", "fast")


def clamp_studio(st: Any) -> dict[str, Any]:
    """
    Choices made in Polixor Studio when the project was created:
      auto_generate     the goal (short | package | longform) – generation starts by itself
                        when the analysis is done (None: the user configures it first)
      content_profile   auto | livestream | podcast | news | solo | general
      quality           premium (reference quality: strong full-source ASR + final ensemble)
                        | fast (lighter discovery ASR; the editor gate and QA still run)
      editorial_overlay the on-screen hook text (off by default)
    """
    st = st if isinstance(st, dict) else {}
    goal = st.get("auto_generate")
    prof = st.get("content_profile", "auto")
    q = st.get("quality", "premium")
    return {"auto_generate": goal if goal in MODES else None,
            "content_profile": prof if prof in PROFILES else "auto",
            "quality": q if q in QUALITY_MODES else "premium",
            "editorial_overlay": bool(st.get("editorial_overlay", False))}


def settings_for_project(base: AppSettings, config: dict[str, Any], *,
                         content_language: Optional[str] = None) -> AppSettings:
    """
    ממפה הגדרות פרויקט להגדרות הפייפליין.

    ההגדרות הגלובליות (סגנון עריכה, מאסטרינג, ספק תמלול וכו') נשארות
    כמו שהן; הפרויקט קובע רק את מה שהמשתמש בחר בו.
    """
    cfg = clamp_config(config)
    data = base.to_dict()
    mode = cfg.get("mode") or "short"
    if mode in ("short", "package"):
        # package: the Shorts below, plus one long-form video per topic (rendered after them)
        data.update({
            "short_enabled": True,
            "long_enabled": False,
            "short_count": cfg["clip_count"],
            "short_min_seconds": cfg["clip_min_seconds"],
            "short_max_seconds": cfg["clip_max_seconds"],
            "short_resolution": ASPECT_RESOLUTION[cfg["aspect_ratio"]],
            "short_layout": LAYOUT_TO_PIPELINE[cfg["layout"]],
            # כמות הקליפים שהמשתמש ביקש היא התקרה
            "max_clips_total": max(int(data.get("max_clips_total") or 1),
                                   cfg["clip_count"]),
            "longform_target_seconds": cfg["longform_target_seconds"],
        })
    else:
        data.update({
            "short_enabled": False,
            "long_enabled": True,
            "long_count": 1,
            "longform_target_seconds": cfg["longform_target_seconds"],
            "short_layout": LAYOUT_TO_PIPELINE[cfg["layout"]],
        })
    data["subtitles_enabled"] = bool(cfg["subtitles"]["enabled"])
    st = cfg["studio"]
    data["content_profile"] = st["content_profile"]
    data["editorial_hook_enabled"] = st["editorial_overlay"]
    if st["quality"] == "fast":
        data["discovery_asr"] = "fast"
    else:
        data["discovery_asr"], data["final_asr_ensemble"] = "strong", True
    from .services.vocabulary import merge_terms

    data["asr_vocabulary"] = merge_terms(cfg.get("vocabulary"), data.get("asr_vocabulary"))
    lang = content_language or cfg.get("content_language") or "auto"
    data["transcribe_language"] = lang if lang in CONTENT_LANGUAGES else "auto"
    return AppSettings.from_dict(data)


def aspect_is_vertical(aspect: str) -> bool:
    """„אנכי" במובן הרחב: כל יחס שאינו 16:9 נחתך מהמקור הרוחבי."""
    return aspect != "16:9"
