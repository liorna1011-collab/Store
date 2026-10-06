"""
יצירת תמונות – שכבת ספקים מבודדת.

עיקרון מרכזי: **הפיצ'ר הזה לא יכול להפיל את עורך הווידאו.** כל קריאה
לספק עטופה, כל מצב כישלון ממופה לשגיאה מובנית עם הודעה בעברית,
ואם הספק לא זמין המערכת ממשיכה לעבוד בלעדיו.

מפתח ה-API לעולם אינו מגיע לדפדפן: הוא נשמר מוצפן בדיסק דרך
`SecretStore`, נקרא רק בצד השרת, וה-API מחזיר עליו מסכה בלבד.

ספקים:
  openai       – OpenAI Images API. יצירה אמיתית של תמונות.
  placeholder  – **לא AI.** מייצר כרטיס גרדיאנט מקומי עם טקסט הפרומפט.
                 קיים כדי שאפשר יהיה לבדוק את כל שרשרת ההוספה לווידאו
                 בלי מפתח, וכדי שיהיה מה להכניס כאינטרו גם בלי חיבור.
                 מסומן ככזה בכל מקום בממשק.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import math
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .. import i18n
from ..config import PATHS, SECRETS, AppSettings
from ..errors import PolixorError

log = logging.getLogger("polixor.images")

# יחס → (רוחב, גובה) עבור כל דגם. ה-API מקבל רק מידות מסוימות.
ASPECTS = ("1:1", "16:9", "9:16")

OPENAI_SIZES = {
    "gpt-image-1": {"1:1": "1024x1024", "16:9": "1536x1024", "9:16": "1024x1536"},
    "dall-e-3":    {"1:1": "1024x1024", "16:9": "1792x1024", "9:16": "1024x1792"},
    "dall-e-2":    {"1:1": "1024x1024", "16:9": "1024x1024", "9:16": "1024x1024"},
}

PLACEHOLDER_SIZES = {"1:1": (1024, 1024), "16:9": (1536, 864), "9:16": (864, 1536)}

# מכסות הגנה – פרומפט ארוך מדי נדחה לפני שיוצא מהמחשב
MAX_PROMPT = 1800
MIN_PROMPT = 3
# פרומפט מורכב של הסטודיו (הוראה + הקשר השיחה) – עדיין קצר בהרבה מהמגבלה של המודלים
MAX_COMPOSED_PROMPT = 4000


# --------------------------------------------------------------------------
# שגיאות
# --------------------------------------------------------------------------
class ImageError(PolixorError):
    code = "image_failed"
    message = "יצירת התמונה נכשלה."


class ImageKeyMissingError(ImageError):
    code = "image_key_missing"
    message = "לא הוגדר מפתח API ליצירת תמונות."
    hint = ("הוסף מפתח OpenAI במסך ההגדרות → AI Images. המפתח נשמר מוצפן "
            "במחשב שלך ואינו נחשף בדפדפן.")


class ImagePromptError(ImageError):
    code = "image_bad_prompt"
    message = "הפרומפט אינו תקין."


class ImageRateLimitError(ImageError):
    code = "image_rate_limit"
    message = "חרגת ממכסת הבקשות של ספק התמונות."
    hint = "המתן דקה ונסה שוב, או בדוק את מצב החשבון שלך אצל הספק."


class ImageTimeoutError(ImageError):
    code = "image_timeout"
    message = "יצירת התמונה לקחה יותר מדי זמן."
    hint = "נסה שוב, או קצר את הפרומפט."


class ImageRejectedError(ImageError):
    code = "image_rejected"
    message = "ספק התמונות דחה את הבקשה."
    hint = "ייתכן שהפרומפט חורג ממדיניות התוכן של הספק. נסח אותו אחרת."


class ImageInvalidResponseError(ImageError):
    code = "image_bad_response"
    message = "התקבלה תשובה לא צפויה מספק התמונות."


class ImageCancelledError(ImageError):
    code = "image_cancelled"
    message = "יצירת התמונה בוטלה."


# --------------------------------------------------------------------------
# תוצאה
# --------------------------------------------------------------------------
@dataclass
class GeneratedImageData:
    data: bytes
    width: int
    height: int
    provider: str
    model: str
    revised_prompt: str = ""
    is_ai: bool = True
    note: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


ProgressFn = Optional[Callable[[str], None]]


# --------------------------------------------------------------------------
# ממשק הספק
# --------------------------------------------------------------------------
class ImageProvider:
    name = "base"
    is_ai = True

    @property
    def label(self) -> str:
        """שם הספק בשפה הפעילה (images.provider.<name>)."""
        return i18n.tr(f"images.provider.{self.name}", default=self.name)

    def available(self) -> tuple[bool, str]:
        """(זמין, סיבה אם לא). נבדק לפני כל בקשה וגם במסך ההגדרות."""
        return False, i18n.tr("images.unavailable.not_implemented")

    def generate(self, prompt: str, *, aspect: str, settings: AppSettings,
                 on_progress: ProgressFn = None,
                 cancel_event: Optional[threading.Event] = None
                 ) -> GeneratedImageData:
        raise NotImplementedError

    def vary(self, prompt: str, source: Path, *, aspect: str,
             settings: AppSettings, on_progress: ProgressFn = None,
             cancel_event: Optional[threading.Event] = None
             ) -> GeneratedImageData:
        """וריאציה על תמונה קיימת. ברירת מחדל: יצירה מחדש מאותו פרומפט."""
        return self.generate(prompt, aspect=aspect, settings=settings,
                             on_progress=on_progress, cancel_event=cancel_event)

    def edit(self, prompt: str, sources: list[Path], *, aspect: str,
             settings: AppSettings, background: str = "auto",
             on_progress: ProgressFn = None,
             cancel_event: Optional[threading.Event] = None) -> GeneratedImageData:
        """עריכה / יצירה מתמונות קלט (התמונה הנוכחית + ייחוסים)."""
        return self.vary(prompt, sources[0], aspect=aspect, settings=settings,
                         on_progress=on_progress, cancel_event=cancel_event)


# --------------------------------------------------------------------------
# OpenAI
# --------------------------------------------------------------------------
class OpenAIImageProvider(ImageProvider):
    name = "openai"
    is_ai = True

    BASE = "https://api.openai.com/v1"
    transport = None              # לבדיקות: httpx.MockTransport (אף בקשה לא יוצאת)

    def available(self) -> tuple[bool, str]:
        if not SECRETS.has("openai_api_key"):
            return False, i18n.tr("images.unavailable.no_key")
        try:
            import httpx  # noqa: F401
        except ImportError:
            return False, i18n.tr("images.unavailable.no_httpx")
        return True, ""

    # ---- עזרים ----
    def _key(self) -> str:
        key = SECRETS.get("openai_api_key")
        if not key:
            raise ImageKeyMissingError()
        return key

    def _size(self, model: str, aspect: str) -> str:
        from . import image_models

        return image_models.size_for(image_models.get(model), aspect)

    def _params(self, model_id: str, aspect: str, settings: AppSettings,
                background: str = "auto") -> dict[str, Any]:
        """פרמטרים לפי שכבת היכולות – רק מה שהמודל מכיר."""
        from . import image_models

        m = image_models.get(model_id)
        out: dict[str, Any] = {"model": m.id, "n": 1, "size": image_models.size_for(m, aspect)}
        if "quality" in m.params:
            out["quality"] = image_models.quality_for(m, settings.image_quality or "high")
        if "output_format" in m.params:
            out["output_format"] = "png"
        if "response_format" in m.params:
            out["response_format"] = "b64_json"
        if background and background != "auto":
            if background == "transparent" and not m.transparent:
                raise ImagePromptError(message_key="image_studio.error.no_transparent")
            if "background" in m.params:
                out["background"] = background
        return out

    def _post(self, path: str, *, json_body: Optional[dict] = None,
              files: Any = None, data: Optional[dict] = None,
              timeout: float, cancel_event: Optional[threading.Event]):
        from .paid_guard import check

        check("openai:images")
        import httpx

        if cancel_event is not None and cancel_event.is_set():
            raise ImageCancelledError()

        headers = {"Authorization": f"Bearer {self._key()}"}
        try:
            with httpx.Client(timeout=timeout, trust_env=True, transport=self.transport) as client:
                if files is not None:
                    return client.post(self.BASE + path, headers=headers,
                                       files=files, data=data or {})
                headers["Content-Type"] = "application/json"
                return client.post(self.BASE + path, headers=headers,
                                   json=json_body or {})
        except httpx.TimeoutException as exc:
            raise ImageTimeoutError(detail=str(exc)) from exc
        except httpx.HTTPError as exc:
            raise ImageError(message_key="images.error.unreachable",
                             hint_key="images.error.unreachable_hint",
                             detail=str(exc)) from exc

    def _raise_for_status(self, response) -> None:
        if response.status_code < 400:
            return
        body = ""
        try:
            body = json.dumps(response.json(), ensure_ascii=False)[:600]
        except Exception:
            body = (response.text or "")[:600]

        code = response.status_code
        if code == 401:
            raise ImageKeyMissingError(
                message_key="images.error.key_rejected",
                hint_key="images.error.key_rejected_hint", detail=body)
        if code == 429:
            raise ImageRateLimitError(detail=body)
        if code in (400, 422):
            low = body.lower()
            if "content_policy" in low or "safety" in low or "rejected" in low:
                raise ImageRejectedError(detail=body)
            raise ImagePromptError(message_key="images.error.bad_params", detail=body)
        if code in (500, 502, 503, 504):
            raise ImageError(message_key="images.error.provider_down",
                             hint_key="images.error.provider_down_hint", detail=body)
        raise ImageError(message_key="images.error.http", params={"code": code},
                         detail=body)

    def _extract(self, response, *, provider_model: str) -> GeneratedImageData:
        try:
            payload = response.json()
        except Exception as exc:
            raise ImageInvalidResponseError(detail=(response.text or "")[:400]) from exc

        items = payload.get("data") or []
        if not items:
            raise ImageInvalidResponseError(
                message_key="images.error.no_image",
                detail=json.dumps(payload, ensure_ascii=False)[:400])
        item = items[0]

        raw: Optional[bytes] = None
        if item.get("b64_json"):
            try:
                raw = base64.b64decode(item["b64_json"])
            except Exception as exc:
                raise ImageInvalidResponseError(
                    message_key="images.error.corrupt", detail=str(exc)) from exc
        elif item.get("url"):
            import httpx

            try:
                with httpx.Client(timeout=90.0, trust_env=True) as c:
                    r = c.get(item["url"])
                r.raise_for_status()
                raw = r.content
            except Exception as exc:
                raise ImageInvalidResponseError(
                    message_key="images.error.download_failed", detail=str(exc)) from exc

        if not raw or len(raw) < 512:
            raise ImageInvalidResponseError(message_key="images.error.empty")

        w, h = _probe_image_size(raw)
        return GeneratedImageData(
            data=raw, width=w, height=h, provider=self.name,
            model=provider_model,
            revised_prompt=str(item.get("revised_prompt") or ""),
            is_ai=True,
            meta={"usage": payload.get("usage")},
        )

    # ---- API ציבורי ----
    def generate(self, prompt: str, *, aspect: str, settings: AppSettings,
                 on_progress: ProgressFn = None,
                 cancel_event: Optional[threading.Event] = None,
                 background: str = "auto") -> GeneratedImageData:
        ok, why = self.available()
        if not ok:
            # ההבחנה לפי מצב המפתח עצמו, לא לפי נוסח ההודעה (שמתורגם)
            if not SECRETS.has("openai_api_key"):
                raise ImageKeyMissingError()
            raise ImageError(why)

        from . import image_models

        model = image_models.get(settings.image_model).id
        body: dict[str, Any] = {"prompt": prompt,
                                **self._params(model, aspect, settings, background)}

        if on_progress:
            on_progress(i18n.tr("images.progress.sending"))
        response = self._post("/images/generations", json_body=body,
                              timeout=float(settings.image_timeout_seconds),
                              cancel_event=cancel_event)
        self._raise_for_status(response)
        if cancel_event is not None and cancel_event.is_set():
            raise ImageCancelledError()
        if on_progress:
            on_progress(i18n.tr("images.progress.downloading"))
        return self._extract(response, provider_model=model)

    def vary(self, prompt: str, source: Path, *, aspect: str,
             settings: AppSettings, on_progress: ProgressFn = None,
             cancel_event: Optional[threading.Event] = None
             ) -> GeneratedImageData:
        """
        וריאציה דרך /images/edits עם התמונה המקורית כקלט.
        אם הדגם או הנתיב לא תומכים – נופלים ליצירה מחדש, ומדווחים.
        """
        from . import image_models

        model = image_models.get(settings.image_model)
        if not source.exists() or not model.edit:
            out = self.generate(prompt, aspect=aspect, settings=settings,
                                on_progress=on_progress, cancel_event=cancel_event)
            out.note = i18n.tr("images.note.variation_regenerated")
            return out
        try:
            return self.edit(prompt, [source], aspect=aspect, settings=settings,
                             on_progress=on_progress, cancel_event=cancel_event)
        except (ImagePromptError, ImageInvalidResponseError) as exc:
            log.warning("image edit failed, regenerating: %s", exc.message)
            out = self.generate(prompt, aspect=aspect, settings=settings,
                                on_progress=on_progress, cancel_event=cancel_event)
            out.note = i18n.tr("images.note.edit_failed")
            return out

    def edit(self, prompt: str, sources: list[Path], *, aspect: str,
             settings: AppSettings, background: str = "auto",
             on_progress: ProgressFn = None,
             cancel_event: Optional[threading.Event] = None) -> GeneratedImageData:
        """
        /images/edits עם כמה תמונות קלט (image[]): התמונה שעורכים ראשונה,
        ואחריה תמונות הייחוס – עד המגבלה של המודל.
        """
        from . import image_models

        ok, why = self.available()
        if not ok:
            if not SECRETS.has("openai_api_key"):
                raise ImageKeyMissingError()
            raise ImageError(why)
        model = image_models.get(settings.image_model)
        if not model.edit:
            raise ImagePromptError(message_key="image_studio.error.no_edit", params={"model": model.id})
        if len(sources) > model.max_refs:
            raise ImagePromptError(message_key="image_studio.error.too_many_refs",
                                   params={"max": model.max_refs})
        if on_progress:
            on_progress(i18n.tr("images.progress.variation"))
        field_name = "image" if model.max_refs == 1 else "image[]"
        files = [(field_name, (p.name, p.read_bytes(), "image/png")) for p in sources]
        data = {k: str(v) for k, v in self._params(model.id, aspect, settings, background).items()
                if k != "response_format" or model.id == "dall-e-2"}
        data["prompt"] = prompt
        response = self._post("/images/edits", files=files, data=data,
                              timeout=float(settings.image_timeout_seconds),
                              cancel_event=cancel_event)
        self._raise_for_status(response)
        if cancel_event is not None and cancel_event.is_set():
            raise ImageCancelledError()
        if on_progress:
            on_progress(i18n.tr("images.progress.downloading"))
        return self._extract(response, provider_model=model.id)


# --------------------------------------------------------------------------
# כרטיס מקומי – לא AI
# --------------------------------------------------------------------------
class PlaceholderImageProvider(ImageProvider):
    """
    מייצר כרטיס גרדיאנט עם טקסט הפרומפט. **זו אינה יצירת תמונה בבינה
    מלאכותית** – זה כרטיס עיצובי מקומי.

    קיים לשתי מטרות אמיתיות:
      1. לבדוק את כל שרשרת ההוספה לווידאו בלי מפתח API.
      2. לתת כרטיס אינטרו/אאוטרו נקי גם למי שלא רוצה לשלם על תמונות.
    """

    name = "placeholder"
    is_ai = False

    def available(self) -> tuple[bool, str]:
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            return False, i18n.tr("images.unavailable.no_pillow")
        return True, ""

    def edit(self, prompt: str, sources: list[Path], *, aspect: str,
             settings: AppSettings, background: str = "auto",
             on_progress: ProgressFn = None,
             cancel_event: Optional[threading.Event] = None) -> GeneratedImageData:
        out = self.generate(prompt, aspect=aspect, settings=settings,
                            on_progress=on_progress, cancel_event=cancel_event)
        out.note = i18n.tr("images.note.local_card_refs", count=len(sources))
        return out

    def generate(self, prompt: str, *, aspect: str, settings: AppSettings,
                 on_progress: ProgressFn = None,
                 cancel_event: Optional[threading.Event] = None,
                 background: str = "auto") -> GeneratedImageData:
        from PIL import Image, ImageDraw, ImageFilter

        if on_progress:
            on_progress(i18n.tr("images.progress.local_card"))
        w, h = PLACEHOLDER_SIZES.get(aspect, PLACEHOLDER_SIZES["1:1"])

        # גוון נגזר מהפרומפט, כדי ששני פרומפטים שונים ייראו שונה
        seed = sum(ord(c) * (i + 7) for i, c in enumerate(prompt[:64])) or 1
        hue = (seed % 360) / 360.0
        top = _hsv_to_rgb(hue, 0.55, 0.30)
        bottom = _hsv_to_rgb((hue + 0.11) % 1.0, 0.70, 0.10)

        img = Image.new("RGB", (w, h), top)
        draw = ImageDraw.Draw(img)
        for y in range(h):
            f = y / max(1, h - 1)
            draw.line([(0, y), (w, y)], fill=(
                int(top[0] * (1 - f) + bottom[0] * f),
                int(top[1] * (1 - f) + bottom[1] * f),
                int(top[2] * (1 - f) + bottom[2] * f)))

        # צורות רכות, כדי שלא ייראה כמו שגיאה
        rng = seed
        for k in range(7):
            rng = (rng * 1103515245 + 12345) & 0x7FFFFFFF
            cx = rng % w
            rng = (rng * 1103515245 + 12345) & 0x7FFFFFFF
            cy = rng % h
            r = int(min(w, h) * (0.06 + 0.10 * ((k * 37 + seed) % 10) / 10))
            accent = _hsv_to_rgb((hue + 0.05 * k) % 1.0, 0.45, 0.55)
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=accent)
        img = img.filter(ImageFilter.GaussianBlur(radius=max(8, min(w, h) // 26)))

        _draw_wrapped_text(img, prompt, w, h)

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        if cancel_event is not None and cancel_event.is_set():
            raise ImageCancelledError()
        return GeneratedImageData(
            data=buf.getvalue(), width=w, height=h, provider=self.name,
            model="placeholder-card", is_ai=False,
            note=i18n.tr("images.note.local_card"),
        )


def _hsv_to_rgb(h: float, s: float, v: float) -> tuple[int, int, int]:
    i = int(h * 6) % 6
    f = h * 6 - int(h * 6)
    p, q, t = v * (1 - s), v * (1 - f * s), v * (1 - (1 - f) * s)
    r, g, b = [(v, t, p), (q, v, p), (p, v, t),
               (p, q, v), (t, p, v), (v, p, q)][i]
    return int(r * 255), int(g * 255), int(b * 255)


def _draw_wrapped_text(img, text: str, w: int, h: int) -> None:
    """כותב את הפרומפט על הכרטיס, עם התאמה לרוחב."""
    from PIL import ImageDraw, ImageFont

    draw = ImageDraw.Draw(img)
    size = max(18, int(min(w, h) * 0.045))
    font = None
    for candidate in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                      "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
                      "C:/Windows/Fonts/arial.ttf"):
        try:
            font = ImageFont.truetype(candidate, size)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()

    max_chars = max(12, int(w / (size * 0.55)))
    words = re.sub(r"\s+", " ", text.strip()).split(" ")
    lines: list[str] = []
    cur = ""
    for word in words:
        cand = f"{cur} {word}".strip()
        if len(cand) > max_chars and cur:
            lines.append(cur)
            cur = word
        else:
            cur = cand
        if len(lines) >= 5:
            break
    if cur and len(lines) < 5:
        lines.append(cur)
    if not lines:
        return

    line_h = int(size * 1.35)
    block_h = line_h * len(lines)
    y = (h - block_h) // 2
    for line in lines:
        try:
            bbox = draw.textbbox((0, 0), line, font=font)
            tw = bbox[2] - bbox[0]
        except Exception:
            tw = len(line) * size // 2
        x = (w - tw) // 2
        draw.text((x + 2, y + 2), line, font=font, fill=(0, 0, 0))
        draw.text((x, y), line, font=font, fill=(245, 245, 250))
        y += line_h


# --------------------------------------------------------------------------
# מפעל ובדיקת זמינות
# --------------------------------------------------------------------------
PROVIDERS: dict[str, type[ImageProvider]] = {
    "openai": OpenAIImageProvider,
    "placeholder": PlaceholderImageProvider,
}


def get_provider(name: str) -> ImageProvider:
    cls = PROVIDERS.get(name or "openai") or OpenAIImageProvider
    return cls()


def provider_is_ai(name: str) -> bool:
    """האם הספק מייצר תמונה במודל אמיתי. placeholder מחזיר False."""
    cls = PROVIDERS.get(name or "openai") or OpenAIImageProvider
    return bool(getattr(cls, "is_ai", True))


def provider_status(settings: AppSettings) -> list[dict[str, Any]]:
    """מצב כל הספקים – למסך ההגדרות ולאזור AI Images."""
    out: list[dict[str, Any]] = []
    for key, cls in PROVIDERS.items():
        p = cls()
        ok, why = p.available()
        out.append({
            "name": p.name, "label": p.label, "is_ai": p.is_ai,
            "available": ok, "reason": why,
            "selected": key == settings.image_provider,
            "key_configured": SECRETS.has("openai_api_key") if key == "openai" else True,
            "key_masked": SECRETS.mask("openai_api_key") or "" if key == "openai" else "",
        })
    return out


def validate_prompt(prompt: str, max_len: int = MAX_PROMPT) -> str:
    """בודק ומנקה פרומפט לפני שהוא יוצא מהמחשב."""
    clean = (prompt or "").strip()
    clean = re.sub(r"[ \t]+", " ", clean) if max_len > MAX_PROMPT else re.sub(r"\s+", " ", clean)
    if len(clean) < MIN_PROMPT:
        raise ImagePromptError(message_key="images.error.prompt_short",
                               hint_key="images.error.prompt_short_hint")
    if len(clean) > max_len:
        raise ImagePromptError(message_key="images.error.prompt_long",
                               hint_key="images.error.prompt_long_hint",
                               params={"length": len(clean), "max": max_len})
    return clean


def validate_aspect(aspect: str) -> str:
    if aspect not in ASPECTS:
        raise ImagePromptError(message_key="images.error.bad_aspect",
                               hint_key="images.error.bad_aspect_hint",
                               params={"aspect": aspect, "allowed": ", ".join(ASPECTS)})
    return aspect


# --------------------------------------------------------------------------
# יצירה עם ניסיונות חוזרים
# --------------------------------------------------------------------------
def generate_image(
    prompt: str,
    *,
    aspect: str,
    settings: AppSettings,
    provider_name: Optional[str] = None,
    source: Optional[Path] = None,
    on_progress: ProgressFn = None,
    cancel_event: Optional[threading.Event] = None,
    sources: Optional[list[Path]] = None,
    background: str = "auto",
    max_prompt: int = MAX_PROMPT,
) -> GeneratedImageData:
    """
    יצירת תמונה עם ניסיונות חוזרים על כשלים זמניים בלבד.

    לא חוזרים על: מפתח חסר, פרומפט פסול, דחיית מדיניות, ביטול.
    כן חוזרים על: חריגת מכסה, timeout, ספק לא זמין.
    """
    prompt = validate_prompt(prompt, max_prompt)
    aspect = validate_aspect(aspect)
    provider = get_provider(provider_name or settings.image_provider)

    attempts = max(1, int(settings.image_retries) + 1)
    last: Optional[ImageError] = None

    for attempt in range(attempts):
        if cancel_event is not None and cancel_event.is_set():
            raise ImageCancelledError()
        try:
            if sources:
                return provider.edit(prompt, list(sources), aspect=aspect, settings=settings,
                                     background=background, on_progress=on_progress,
                                     cancel_event=cancel_event)
            if background != "auto":
                return provider.generate(prompt, aspect=aspect, settings=settings,
                                         on_progress=on_progress, cancel_event=cancel_event,
                                         background=background)
            if source is not None:
                return provider.vary(prompt, source, aspect=aspect,
                                     settings=settings, on_progress=on_progress,
                                     cancel_event=cancel_event)
            return provider.generate(prompt, aspect=aspect, settings=settings,
                                     on_progress=on_progress,
                                     cancel_event=cancel_event)
        except (ImageKeyMissingError, ImagePromptError, ImageRejectedError,
                ImageCancelledError):
            raise
        except ImageError as exc:
            last = exc
            if attempt >= attempts - 1:
                break
            wait = min(20.0, 2.0 * (2 ** attempt))
            if on_progress:
                on_progress(i18n.tr("images.progress.retry", attempt=attempt + 2,
                                    attempts=attempts, seconds=int(wait)))
            waited = 0.0
            while waited < wait:
                if cancel_event is not None and cancel_event.is_set():
                    raise ImageCancelledError()
                time.sleep(0.25)
                waited += 0.25

    raise last or ImageError()


# --------------------------------------------------------------------------
# שמירה לדיסק
# --------------------------------------------------------------------------
def save_image(data: GeneratedImageData, image_id: str) -> dict[str, Any]:
    """שומר את התמונה ותמונה ממוזערת, ומחזיר את הנתיבים והמידות."""
    root = PATHS.images
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{image_id}.png"
    path.write_bytes(data.data)

    thumb = root / f"{image_id}_thumb.jpg"
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data.data)) as im:
            im = im.convert("RGB")
            im.thumbnail((512, 512), Image.LANCZOS)
            im.save(thumb, "JPEG", quality=84, optimize=True)
    except Exception as exc:
        log.warning("thumbnail failed for %s: %s", image_id, exc)
        thumb = None

    return {
        "file_path": str(path),
        "thumb_path": str(thumb) if thumb else "",
        "width": data.width, "height": data.height,
        "size_bytes": path.stat().st_size,
    }


def _probe_image_size(raw: bytes) -> tuple[int, int]:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(raw)) as im:
            return im.size
    except Exception:
        # PNG: המידות נמצאות ב-IHDR, בבתים 16..24
        if raw[:8] == b"\x89PNG\r\n\x1a\n" and len(raw) > 24:
            w = int.from_bytes(raw[16:20], "big")
            h = int.from_bytes(raw[20:24], "big")
            if w and h:
                return w, h
        return 0, 0


def aspect_of(width: int, height: int) -> str:
    """מחזיר את היחס הקרוב ביותר מבין הנתמכים."""
    if not width or not height:
        return "1:1"
    ratio = width / height
    best, best_d = "1:1", math.inf
    for a in ASPECTS:
        w, h = (int(x) for x in a.split(":"))
        d = abs(ratio - w / h)
        if d < best_d:
            best, best_d = a, d
    return best
