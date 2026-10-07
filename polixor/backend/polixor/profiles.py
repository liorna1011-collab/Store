"""
פרופילי ביצועים ותצורת התמלול.

  fast     מעבד בלבד. בשידור ארוך – ניתוח חזותי רק בחלונות המועמדים.
           תמלול הגילוי המלא הוא המודל החזק גם כאן (discovery_asr="strong");
           המודל הקל משמש רק לתצוגה מקדימה מהירה (discovery_asr="fast").
  quality  כרטיס מסך (CUDA). המודל החזק לכל התמלול, ניתוח חזותי מלא.
  auto     quality כשיש CUDA, אחרת fast.

המודל שהמשתמש בחר במפורש תמיד גובר על ברירת המחדל של הפרופיל.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional

from .config import AppSettings

# מודל עברי מכוונן (ivrit.ai) בפורמט CTranslate2 – נטען דרך faster-whisper
# כמזהה מאגר של Hugging Face ומורד פעם אחת כמו כל מודל אחר
HEBREW_MODEL = "ivrit-ai/whisper-large-v3-turbo-ct2"
GENERAL_STRONG_MODEL = "large-v3-turbo"
FAST_MODEL = "small"

# קיצורים שמוצגים בממשק
MODEL_ALIASES = {"hebrew": HEBREW_MODEL, "ivrit-turbo": HEBREW_MODEL}


def cuda_available() -> bool:
    try:
        import ctranslate2

        return int(ctranslate2.get_cuda_device_count()) > 0
    except Exception:
        return False


def resolve_profile(s: AppSettings) -> str:
    """fast | quality."""
    if s.performance_profile in ("fast", "quality"):
        return s.performance_profile
    return "quality" if cuda_available() else "fast"


def _alias(model: str) -> str:
    return MODEL_ALIASES.get(model, model)


def strong_model_for(language: Optional[str], s: AppSettings) -> str:
    """המודל החזק: לתמלול המלא ב-quality, ולתמלול החוזר הממוקד ב-fast."""
    chosen = getattr(s, "asr_strong_model", "auto") or "auto"
    if chosen != "auto":
        return _alias(chosen)
    return HEBREW_MODEL if (language or "").startswith("he") else GENERAL_STRONG_MODEL


@dataclass
class AsrPlan:
    profile: str
    model: str
    device: str
    compute_type: str
    beam_size: int
    batched: bool
    batch_size: int
    language: Optional[str]
    hotwords: Optional[str]
    strong_model: str
    # discovery (finding the moments) decodes greedily with a short fallback ladder; the subtitles of
    # what ships are re-heard by the finalist ensemble at beam_size (asr_ensemble), never from this
    discovery_beam: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def asr_plan(s: AppSettings, *, language: Optional[str] = None) -> AsrPlan:
    from .services.vocabulary import hotwords

    profile = resolve_profile(s)
    lang = None if s.transcribe_language == "auto" else s.transcribe_language
    lang = lang or language
    device = s.whisper_device
    if device == "auto":
        device = "cuda" if cuda_available() else "cpu"
    compute = s.whisper_compute_type
    if compute == "auto":
        compute = "float16" if device == "cuda" else "int8"
    model = s.whisper_model or "auto"
    if model == "auto":
        # the production discovery transcript is the strong model on every profile;
        # the fast model is only the quick preview (discovery_asr = "fast")
        strong = profile == "quality" or getattr(s, "discovery_asr", "strong") != "fast"
        model = strong_model_for(lang, s) if strong else FAST_MODEL
    model = _alias(model)
    beam = int(getattr(s, "whisper_beam_size", 5) or 5)
    batched = bool(getattr(s, "asr_batched", True))
    return AsrPlan(profile=profile, model=model, device=device, compute_type=compute,
                   beam_size=beam, batched=batched,
                   batch_size=16 if device == "cuda" else 8,
                   language=lang, hotwords=hotwords(getattr(s, "asr_vocabulary", [])),
                   strong_model=strong_model_for(lang, s),
                   discovery_beam=int(getattr(s, "discovery_beam_size", 1) or 1))
