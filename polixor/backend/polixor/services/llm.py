"""
שכבת מודל שפה – אופציונלית, לשיפור הכותרות, התיאורים והבנת ההקשר.

שלושה מצבים:
  heuristic – ללא מודל שפה כלל. מנוע הלקסיקון והאותות בלבד.
              עובד תמיד, ללא רשת וללא עלות. איכות הכותרות נמוכה יותר.
  ollama    – מודל שפה מקומי דרך שרת Ollama (http://localhost:11434).
              ללא עלות וללא שליחת מידע החוצה, אך דורש חומרה מתאימה
              (מומלץ 8GB RAM ומעלה; GPU משפר מאוד את המהירות).
  cloud     – Anthropic או OpenAI. איכות הגבוהה ביותר, דורש מפתח API,
              ושולח קטעי תמלול לשירות חיצוני.

המודל מתבקש במפורש להסתמך רק על מה שנאמר בפועל ולא להמציא כותרות.
כישלון בשכבה הזו לעולם אינו מפיל את המשימה – חוזרים להיוריסטיקה.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from typing import Any, Optional

from .. import i18n
from ..config import SECRETS, AppSettings
from ..errors import AiProviderError, JobCancelledError
from .selection import Candidate
from .transcribe import TranscriptResult
from ..util.text import truncate

log = logging.getLogger("polixor.llm")

OLLAMA_URL = "http://localhost:11434"
_TIMEOUT = 120.0


@dataclass
class LlmOutcome:
    used: bool = False
    mode: str = "heuristic"
    model: str = ""
    refined: int = 0
    discovered: int = 0
    note: str = ""


SYSTEM_PROMPT = """אתה עוזר לעורך וידאו לבחור רגעים מעניינים משידור של סטרימר.

חוקים מחייבים:
1. הסתמך אך ורק על התמלול שקיבלת. אל תמציא תוכן, ציטוטים או אירועים.
2. כותרת חייבת לשקף את מה שנאמר בפועל בקטע. אסור לכתוב כותרת מפתה
   שאינה תואמת לתוכן (clickbait מטעה).
3. אל תבטיח צפיות, viral, CTR או ביצועים כלשהם.
4. אם קטע אינו מעניין – אמור זאת בכנות והורד את הציון.
5. ציון העניין הוא הערכה פנימית בלבד בסולם 0-100.
6. ענה בעברית, אלא אם התמלול כולו באנגלית.
7. החזר JSON תקין בלבד, ללא טקסט נוסף לפניו או אחריו."""

REFINE_INSTRUCTIONS = """לכל קטע ברשימה, החזר אובייקט עם השדות:
  "id": המזהה שקיבלת
  "title": כותרת קצרה (עד 70 תווים) המבוססת על מה שנאמר
  "description": תיאור עובדתי של מה קורה בקטע (עד 200 תווים)
  "reason": למה הקטע נבחר (עד 150 תווים)
  "category": אחד מ: funny, win, fail, surprise, story, argument, highlight, moment
  "interest": מספר שלם 0-100

פורמט התשובה:
{"clips": [ {...}, {...} ]}"""

DISCOVER_INSTRUCTIONS = """מצא בתמלול עד %d רגעים מעניינים שראוי לחתוך מהם קליפ.
חשוב במיוחד: כלול גם רגעים שנאמרים בשקט – סיפור אישי, גילוי, הסבר
מעניין, נקודה חדה בוויכוח – ולא רק צעקות והתרגשות.

לכל רגע החזר:
  "start": שנייה שבה הרגע מתחיל (מספר, מתוך חותמות הזמן שקיבלת)
  "end": שנייה שבה הרגע נגמר
  "title": כותרת המבוססת על מה שנאמר
  "description": מה קורה שם
  "reason": למה זה מעניין
  "category": funny/win/fail/surprise/story/argument/highlight/moment
  "interest": 0-100

פורמט התשובה:
{"moments": [ {...} ]}"""


# --------------------------------------------------------------------------
# לקוחות
# --------------------------------------------------------------------------
def _http():
    import httpx

    return httpx.Client(timeout=_TIMEOUT, trust_env=True)


def _call_anthropic(system: str, user: str, model: str) -> str:
    from .paid_guard import check

    check("anthropic:llm")
    key = SECRETS.get("anthropic_api_key")
    if not key:
        raise AiProviderError(message_key="processing.llm.no_anthropic_key",
                              hint_key="processing.llm.no_key_hint")
    with _http() as client:
        r = client.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": model, "max_tokens": 4096, "system": system,
                  "messages": [{"role": "user", "content": user}]},
        )
    if r.status_code >= 400:
        raise AiProviderError(message_key="processing.llm.provider_error",
                              params={"provider": "Anthropic", "code": r.status_code},
                              detail=r.text[:600])
    data = r.json()
    return "".join(b.get("text", "") for b in data.get("content", []))


def _call_openai(system: str, user: str, model: str) -> str:
    from .paid_guard import check

    check("openai:llm")
    key = SECRETS.get("openai_api_key")
    if not key:
        raise AiProviderError(message_key="processing.llm.no_openai_key",
                              hint_key="processing.llm.no_key_hint")
    with _http() as client:
        r = client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}",
                     "content-type": "application/json"},
            json={"model": model, "temperature": 0.2,
                  "response_format": {"type": "json_object"},
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": user}]},
        )
    if r.status_code >= 400:
        raise AiProviderError(message_key="processing.llm.provider_error",
                              params={"provider": "OpenAI", "code": r.status_code},
                              detail=r.text[:600])
    data = r.json()
    return data["choices"][0]["message"]["content"]


def _call_ollama(system: str, user: str, model: str) -> str:
    with _http() as client:
        try:
            r = client.post(
                f"{OLLAMA_URL}/api/chat",
                json={"model": model, "stream": False, "format": "json",
                      "options": {"temperature": 0.2},
                      "messages": [{"role": "system", "content": system},
                                   {"role": "user", "content": user}]},
            )
        except Exception as exc:
            raise AiProviderError(
                message_key="processing.llm.ollama_unreachable",
                hint_key="processing.llm.ollama_unreachable_hint",
                params={"url": OLLAMA_URL, "model": model},
                detail=str(exc),
            ) from exc
    if r.status_code >= 400:
        raise AiProviderError(message_key="processing.llm.provider_error",
                              params={"provider": "Ollama", "code": r.status_code},
                              detail=r.text[:600])
    return r.json().get("message", {}).get("content", "")


def call_model(system: str, user: str, settings: AppSettings) -> str:
    mode = _mode(settings)
    if mode == "cloud":
        if settings.ai_provider == "openai":
            return _call_openai(system, user, settings.ai_model)
        return _call_anthropic(system, user, settings.ai_model)
    if mode == "ollama":
        return _call_ollama(system, user, settings.ai_model or "llama3.1")
    raise AiProviderError(message_key="processing.llm.no_model_mode")


def _mode(settings: AppSettings) -> str:
    if settings.ai_mode == "cloud":
        return "cloud"
    if settings.ai_mode == "ollama" or settings.ai_provider == "ollama":
        return "ollama"
    if settings.ai_mode == "auto":
        key = "openai_api_key" if settings.ai_provider == "openai" else "anthropic_api_key"
        return "cloud" if SECRETS.has(key) else "heuristic"
    return "heuristic"


def is_llm_enabled(settings: AppSettings) -> bool:
    return _mode(settings) in ("cloud", "ollama")


def check_availability(settings: AppSettings) -> dict[str, Any]:
    """בדיקת זמינות למסך ההגדרות – בלי לבזבז טוקנים על בקשה אמיתית."""
    mode = _mode(settings)
    if mode == "heuristic":
        return {"mode": mode, "available": True,
                "note": i18n.tr("processing.llm.heuristic_note")}
    if mode == "ollama":
        try:
            with _http() as c:
                r = c.get(f"{OLLAMA_URL}/api/tags", timeout=6.0)
            models = [m.get("name", "") for m in (r.json().get("models") or [])]
            return {"mode": mode, "available": r.status_code < 400,
                    "models": models,
                    "note": i18n.tr("processing.llm.ollama_running" if r.status_code < 400
                                    else "processing.llm.ollama_not_responding")}
        except Exception as exc:
            return {"mode": mode, "available": False, "models": [],
                    "note": i18n.tr("processing.llm.ollama_connect_failed", error=exc)}
    provider = settings.ai_provider
    key_name = "openai_api_key" if provider == "openai" else "anthropic_api_key"
    has_key = SECRETS.has(key_name)
    return {"mode": mode, "provider": provider, "available": has_key,
            "note": i18n.tr("processing.llm.key_set" if has_key
                            else "processing.llm.key_missing")}


# --------------------------------------------------------------------------
# פענוח JSON עמיד
# --------------------------------------------------------------------------
def _parse_json(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        raise AiProviderError(message_key="processing.llm.empty_answer")
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError as exc:
            raise AiProviderError(message_key="processing.llm.bad_json",
                                  detail=truncate(text, 400)) from exc
    raise AiProviderError(message_key="processing.llm.bad_json", detail=truncate(text, 400))


# --------------------------------------------------------------------------
# שיפור מועמדים
# --------------------------------------------------------------------------
def refine_candidates(
    candidates: list[Candidate],
    transcript: Optional[TranscriptResult],
    settings: AppSettings,
    *,
    cancel_event: Optional[threading.Event] = None,
    batch_size: int = 8,
) -> LlmOutcome:
    """
    משפר כותרות/תיאורים/קטגוריות עבור המועמדים שנבחרו.
    שינוי במקום (in-place). כישלון => מחזירים את ההיוריסטיקה כמות שהיא.
    """
    outcome = LlmOutcome(mode=_mode(settings), model=settings.ai_model)
    if not candidates or not is_llm_enabled(settings):
        outcome.note = i18n.tr("processing.llm.refine_skipped_heuristic")
        return outcome
    if transcript is None or not transcript.has_speech:
        outcome.note = i18n.tr("processing.llm.refine_skipped_no_transcript")
        return outcome

    by_id = {f"c{i}": c for i, c in enumerate(candidates)}
    items = list(by_id.items())

    for i in range(0, len(items), batch_size):
        if cancel_event is not None and cancel_event.is_set():
            raise JobCancelledError()
        batch = items[i:i + batch_size]
        payload = []
        for cid, c in batch:
            text = transcript.text_between(c.start, c.end)
            payload.append({
                "id": cid,
                "start_seconds": round(c.start, 1),
                "end_seconds": round(c.end, 1),
                "duration_seconds": round(c.duration, 1),
                "transcript": truncate(text, 2400) or "(אין דיבור בקטע)",
                "audio_visual_signals": c.signals,
            })

        user = (f"{REFINE_INSTRUCTIONS}\n\nהקטעים:\n"
                f"{json.dumps(payload, ensure_ascii=False, indent=1)}")
        try:
            data = _parse_json(call_model(SYSTEM_PROMPT, user, settings))
        except AiProviderError as exc:
            log.warning("LLM refine failed: %s", exc.message)
            outcome.note = i18n.tr("processing.llm.refine_failed_detail", error=exc.message)
            return outcome
        except Exception as exc:  # noqa: BLE001
            log.warning("LLM refine crashed: %s", exc)
            outcome.note = i18n.tr("processing.llm.refine_failed")
            return outcome

        for item in (data.get("clips") or []):
            cand = by_id.get(str(item.get("id", "")))
            if cand is None:
                continue
            title = truncate(str(item.get("title") or "").strip(), 70)
            if title:
                cand.title = title
                cand.title_source = "llm"
            desc = truncate(str(item.get("description") or "").strip(), 300)
            if desc:
                cand.description = desc
            reason = truncate(str(item.get("reason") or "").strip(), 220)
            if reason:
                cand.reason = reason
            cat = str(item.get("category") or "").strip().lower()
            if cat in ("funny", "win", "fail", "surprise", "story",
                       "argument", "highlight", "moment", "visual"):
                cand.category = cat
            try:
                interest = float(item.get("interest"))
                # ממזגים עם ציון האותות במקום להחליף אותו
                cand.score = round(float(min(1.0, max(0.0,
                    0.55 * cand.score + 0.45 * (interest / 100.0)))), 4)
            except (TypeError, ValueError):
                pass
            outcome.refined += 1

    outcome.used = outcome.refined > 0
    if outcome.used:
        outcome.note = i18n.tr("processing.llm.refined", count=outcome.refined)
    return outcome


# --------------------------------------------------------------------------
# גילוי רגעים שקטים שהאותות פספסו
# --------------------------------------------------------------------------
def discover_moments(
    transcript: Optional[TranscriptResult],
    settings: AppSettings,
    *,
    max_moments: int = 8,
    chunk_seconds: float = 480.0,
    cancel_event: Optional[threading.Event] = None,
) -> tuple[list[dict[str, Any]], LlmOutcome]:
    """
    קורא את התמלול בחלונות ומבקש מהמודל רגעים מעניינים –
    במיוחד כאלה שאינם קולניים ולכן אינם בולטים באותות האודיו.
    """
    outcome = LlmOutcome(mode=_mode(settings), model=settings.ai_model)
    if not is_llm_enabled(settings) or transcript is None or not transcript.has_speech:
        return [], outcome

    chunks = _chunk_transcript(transcript, chunk_seconds)
    found: list[dict[str, Any]] = []
    per_chunk = max(2, math_ceil(max_moments / max(1, len(chunks))))

    for chunk in chunks:
        if cancel_event is not None and cancel_event.is_set():
            raise JobCancelledError()
        if len(found) >= max_moments * 2:
            break
        lines = "\n".join(
            f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}" for s in chunk
        )
        user = (DISCOVER_INSTRUCTIONS % per_chunk) + \
            f"\n\nתמלול (חותמות זמן בשניות מתחילת השידור):\n{truncate(lines, 12000)}"
        try:
            data = _parse_json(call_model(SYSTEM_PROMPT, user, settings))
        except Exception as exc:  # noqa: BLE001
            log.warning("LLM discover failed: %s", exc)
            outcome.note = i18n.tr("processing.llm.discover_failed")
            break

        for m in (data.get("moments") or []):
            try:
                start = float(m["start"])
                end = float(m["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if end <= start:
                continue
            found.append({
                "start": start, "end": end,
                "title": truncate(str(m.get("title") or ""), 70),
                "description": truncate(str(m.get("description") or ""), 300),
                "reason": truncate(str(m.get("reason") or ""), 220),
                "category": str(m.get("category") or "moment").lower(),
                "interest": float(m.get("interest") or 50) / 100.0,
            })

    found.sort(key=lambda x: x["interest"], reverse=True)
    found = found[:max_moments]
    outcome.discovered = len(found)
    outcome.used = bool(found)
    if found:
        outcome.note = i18n.tr("processing.llm.discovered", count=len(found))
    return found, outcome


def math_ceil(x: float) -> int:
    import math

    return int(math.ceil(x))


def _chunk_transcript(transcript: TranscriptResult,
                      chunk_seconds: float) -> list[list[dict[str, Any]]]:
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    anchor = None
    for seg in transcript.segments:
        if not (seg.text or "").strip():
            continue
        if anchor is None:
            anchor = seg.start
        if seg.start - anchor > chunk_seconds and current:
            chunks.append(current)
            current = []
            anchor = seg.start
        current.append({"start": seg.start, "end": seg.end, "text": seg.text.strip()})
    if current:
        chunks.append(current)
    return chunks
