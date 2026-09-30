"""
שכבת יכולות לספקי ולמודלי התמונות: מה כל מודל יודע לעשות, כדי שהממשק
יציג רק אפשרויות נתמכות, והשרת יבנה בקשה חוקית בלי לנחש.

נבדק מול התיעוד הרשמי של OpenAI ב-30.9.2026 (ראו docs/providers/openai-images.md):

  * gpt-image-2.5-sunburst / gpt-image-2.5-flare (8.9.2026) – יצירה ועריכה
    ב-Images API (/v1/images/generations, /v1/images/edits). איכות
    auto/low/medium/high/xhigh/max; מידות חופשיות (צלע כפולה של 16, צלע
    ארוכה עד 3840, יחס בין 1:3 ל-3:1); עד 16 תמונות ייחוס; מסכה; רקע שקוף
    (png/webp). **לא** זמינים בכלי image_generation של Responses API.
  * gpt-image-2 – איכות low/medium/high; מידות חופשיות (כפולה של 16, עד 3840);
    רקע שקוף בתצוגה מקדימה; input_fidelity לא משפיע.
  * gpt-image-1 – מידות קבועות (1024x1024, 1536x1024, 1024x1536); איכות
    low/medium/high; עד 16 תמונות ייחוס; רקע שקוף.
  * dall-e-3 / dall-e-2 – ישנים. נשארים רק לתאימות להגדרות קיימות; לא
    מוצגים לבחירה, ו-dall-e-3 לא תומך בעריכה.

ברירת המחדל: gpt-image-2.5-sunburst – המודל החזק ביותר שנתמך כעת.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

DEFAULT_MODEL = "gpt-image-2.5-sunburst"
QUALITY_ORDER = ("low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True)
class ImageModel:
    id: str
    provider: str = "openai"
    selectable: bool = True           # מוצג לבחירה בהגדרות ובסטודיו
    generate: bool = True
    edit: bool = True                 # /images/edits עם תמונות קלט
    max_refs: int = 16                # כמה תמונות קלט בבקשת עריכה אחת
    mask: bool = True
    transparent: bool = True
    transparent_preview: bool = False
    qualities: tuple[str, ...] = ("low", "medium", "high")
    # יחס → מידות שנשלחות. במודלים עם מידות חופשיות: באיכות וידאו (≥1080p)
    sizes: dict[str, str] = field(default_factory=dict)
    formats: tuple[str, ...] = ("png", "jpeg", "webp")
    params: tuple[str, ...] = ("quality", "size", "background", "output_format")
    speed: str = "balanced"           # fast | balanced | detailed – לתיאור בממשק

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["qualities"] = list(self.qualities)
        d["formats"] = list(self.formats)
        d["params"] = list(self.params)
        return d


_VIDEO_SIZES = {"1:1": "1536x1536", "16:9": "2048x1152", "9:16": "1152x2048"}
_FIXED_SIZES = {"1:1": "1024x1024", "16:9": "1536x1024", "9:16": "1024x1536"}

MODELS: dict[str, ImageModel] = {m.id: m for m in (
    ImageModel("gpt-image-2.5-sunburst", qualities=("auto",) + QUALITY_ORDER,
               sizes=_VIDEO_SIZES, speed="detailed"),
    ImageModel("gpt-image-2.5-flare", qualities=("auto",) + QUALITY_ORDER,
               sizes=_VIDEO_SIZES, speed="fast"),
    ImageModel("gpt-image-2", qualities=("auto", "low", "medium", "high"),
               transparent_preview=True, sizes=_VIDEO_SIZES),
    ImageModel("gpt-image-1", qualities=("auto", "low", "medium", "high"), sizes=_FIXED_SIZES),
    ImageModel("dall-e-3", selectable=False, edit=False, max_refs=0, mask=False,
               transparent=False, qualities=("standard", "hd"), formats=("png",),
               sizes={"1:1": "1024x1024", "16:9": "1792x1024", "9:16": "1024x1792"},
               params=("quality", "size", "response_format")),
    ImageModel("dall-e-2", selectable=False, max_refs=1, transparent=False,
               qualities=("standard",), formats=("png",),
               sizes={"1:1": "1024x1024", "16:9": "1024x1024", "9:16": "1024x1024"},
               params=("size", "response_format")),
    # כרטיס מקומי – לא AI. "עריכה" = כרטיס חדש שמציין את הייחוסים.
    ImageModel("placeholder", provider="placeholder", max_refs=4, mask=False,
               transparent=False, qualities=("medium",), formats=("png",),
               sizes={"1:1": "1024x1024", "16:9": "1536x864", "9:16": "864x1536"}, params=()),
)}

ALL_QUALITIES = ("auto",) + QUALITY_ORDER


def get(model_id: str) -> ImageModel:
    return MODELS.get(model_id or "") or MODELS[DEFAULT_MODEL]


def for_provider(provider: str, model_id: str) -> ImageModel:
    """המודל שבפועל ישמש: הספק המקומי תמיד 'placeholder'."""
    if provider == "placeholder":
        return MODELS["placeholder"]
    return get(model_id)


def selectable(provider: str = "openai") -> list[ImageModel]:
    return [m for m in MODELS.values() if m.provider == provider and m.selectable]


def size_for(model: ImageModel, aspect: str) -> str:
    return model.sizes.get(aspect) or model.sizes.get("1:1") or "1024x1024"


def quality_for(model: ImageModel, wanted: str) -> str:
    """האיכות המבוקשת אם נתמכת; אחרת הקרובה ביותר שמתחתיה (בלי לחרוג בעלות)."""
    if wanted in model.qualities:
        return wanted
    if model.id == "dall-e-3":
        return "hd" if wanted in ("high", "xhigh", "max") else "standard"
    if wanted in QUALITY_ORDER:
        for q in reversed(QUALITY_ORDER[: QUALITY_ORDER.index(wanted) + 1]):
            if q in model.qualities:
                return q
    return "auto" if "auto" in model.qualities else model.qualities[0]


def capabilities(provider: str, model_id: str) -> dict[str, Any]:
    """מה שהממשק צריך: מה נתמך במודל הפעיל, ואילו מודלים אפשר לבחור."""
    m = for_provider(provider, model_id)
    return {"provider": provider, "model": m.to_dict(),
            "models": [x.to_dict() for x in selectable("openai")],
            "default_model": DEFAULT_MODEL}
