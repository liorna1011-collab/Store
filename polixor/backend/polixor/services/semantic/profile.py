"""
Content profiles: one pipeline, profile-specific editorial judgment.

The profile is detected once per source from a sample of the transcript and
the topic map (one small model call, cached), or set by the user. It becomes
GUIDANCE appended to the judgment prompts (candidates, ranking, boundaries,
editor, long-form, titles): what makes a moment worth publishing differs
between a livestream reaction and a political interview, the architecture
does not.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

from .checkpoint import StageStore, key_of
from .provider import SemanticError, SemanticProvider
from .sentences import Sentence

log = logging.getLogger("polixor.semantic.profile")

PROFILES = ("livestream", "podcast", "news", "solo", "general")

GUIDANCE = {
    "livestream": (
        "CONTENT PROFILE: livestream / reaction. What earns a Short here: a genuine reaction, humour, "
        "personality, an unexpected moment, a strong take on what is on screen. A reaction only works "
        "when the viewer gets what is being reacted to – keep that context (or reject the moment). "
        "Stream housekeeping (chat names, donations, technical setup, 'wait, let me…') is never content."),
    "podcast": (
        "CONTENT PROFILE: podcast / interview. What earns a Short here: a story with a turn, a real "
        "insight, an argument that lands, a personal experience, an emotional payoff. The question that "
        "set it up usually belongs in the clip; a story without its ending is not a Short."),
    "news": (
        "CONTENT PROFILE: news / political interview. What earns a Short here: a clear claim, a strong or "
        "evasive answer to a pointed question, a significant statement, a sharp exchange. Context accuracy "
        "matters: never cut a statement so it means something the speaker did not say; keep the "
        "question with its answer."),
    "solo": (
        "CONTENT PROFILE: solo talking head. What earns a Short here: one strong idea, a teaching moment, "
        "a personal story, a transformation, a clear opinion or piece of advice – stated, developed and "
        "concluded."),
    "general": (
        "CONTENT PROFILE: general. Judge every moment by whether a stranger scrolling past would stop, "
        "understand it alone, and feel it paid off."),
}

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"profile": {"type": "string", "enum": list(PROFILES)},
                   "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                   "reason": {"type": "string"}},
    "required": ["profile", "confidence", "reason"], "additionalProperties": False,
}


def guidance(profile: str) -> str:
    return GUIDANCE.get(profile, GUIDANCE["general"])


def _sample(sentences: Sequence[Sentence], n: int = 40) -> str:
    """Sentences from the beginning, the middle and the end (the format of a source shows everywhere)."""
    if len(sentences) <= 3 * n:
        pick = list(sentences)
    else:
        m = len(sentences) // 2
        pick = list(sentences[:n]) + list(sentences[m - n // 2:m + n // 2]) + list(sentences[-n:])
    return "\n".join(f"[{s.start:.0f}s t{s.turn}] {s.text}" for s in pick)


def detect(provider: Optional[SemanticProvider], sentences: Sequence[Sentence], topics: Sequence[str], *,
           store: Optional[StageStore] = None, fingerprint: str = "",
           requested: str = "auto") -> dict[str, Any]:
    """{'profile', 'source': user|model|default, 'confidence', 'reason'}"""
    if requested in PROFILES:
        return {"profile": requested, "source": "user", "confidence": "high", "reason": ""}
    if provider is None or not sentences:
        return {"profile": "general", "source": "default", "confidence": "low", "reason": "no model"}
    key = key_of("profile", fingerprint, provider.name, provider.model)
    if store is not None:
        hit = store.get("profile", key)
        if hit is not None:
            return hit
    from .prompts import COMMON

    system = COMMON + """

Task: classify the FORMAT of this video so the editors judge it with the right standard. \
livestream = a streamer live (reacting to videos/games/chat, long, informal); podcast = a \
conversation/interview between a host and guests in a studio; news = a news or political \
interview, panel or statement; solo = one person talking to camera on one subject; general = \
anything else or unclear."""
    user = ("Topics found: " + "; ".join(topics[:30]) + "\n\nTranscript sample (time, turn):\n"
            + _sample(sentences) + "\n\nReturn profile, confidence and a one-line reason.")
    try:
        data = provider.complete_json("profile", system, user, _SCHEMA, max_tokens=1000)
    except SemanticError as exc:
        log.warning("profile detection failed: %s", exc)
        return {"profile": "general", "source": "default", "confidence": "low", "reason": str(exc)[:200]}
    prof = str(data.get("profile") or "general")
    out = {"profile": prof if prof in PROFILES else "general", "source": "model",
           "confidence": str(data.get("confidence") or "low"), "reason": str(data.get("reason") or "")[:300]}
    if store is not None:
        store.put("profile", key, out)
    return out
