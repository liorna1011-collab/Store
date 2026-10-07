"""
Editorial hooks and titles, written from the FINAL transcript of the clip.

The content hook is the clip's first spoken line; the editorial hook is the
3–9 words drawn over the first seconds. The model writes five editorial hook
candidates (each with the exact quotes that support it) and title options;
the code then rejects every candidate that

  * is too short or too long for the overlay,
  * is generic clickbait (language packs) not said in the clip,
  * is not grounded in the clip's words (services/clip_intel/editorial.faithfulness),
  * cites support that is not in the clip,
  * uses a number, or a word that the final transcript marks unresolved,
    or a name/number not in the clip's verified words.

The survivors are ranked by the model's self-scores (truthfulness first) with
deterministic penalties for vague or pronoun-led hooks. Without a model there
is no editorial hook (the overlay is skipped) – a weak or untrue hook is worse
than none. The existing overlay renderer draws the chosen text.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional, Sequence

from ..clip_intel import editorial
from . import prompts
from .provider import SemanticError, SemanticProvider
from .validate import contains, tokens

log = logging.getLogger("polixor.semantic.hooks")

WEIGHTS = {"truthfulness": 0.30, "specificity": 0.18, "curiosity": 0.18, "clarity": 0.14, "natural": 0.12,
           "relevance": 0.08}
MIN_SCORE = 0.62           # on the 0–1 scale of the weighted self-scores (5 = 1.0)
_PRONOUN_START = set(editorial._PRONOUN_START)


def verified_terms(words: Sequence[dict[str, Any]]) -> list[str]:
    """Names and numbers of the final transcript that were not left unresolved."""
    out = []
    for w in words:
        if w.get("status") == "unresolved":
            continue
        t = str(w.get("text") or "").strip(" ,.?!:;\"'")
        if not t:
            continue
        if w.get("critical") in ("name", "number") or re.search(r"\d", t) or re.search(r"[A-Za-z]", t):
            out.append(t)
    return list(dict.fromkeys(out))[:40]


def check(text: str, support: Sequence[str], clip_text: str, words: Sequence[dict[str, Any]],
          language: Optional[str]) -> str:
    """'' when the hook may be used, else the reason it may not."""
    n = len(text.split())
    if n < editorial.MIN_WORDS:
        return "too_short"
    if n > editorial.MAX_LLM_WORDS or len(text) > editorial.MAX_CHARS + 8:
        return "too_long"
    if editorial.clickbait(text, clip_text, language):
        return "generic_clickbait"
    ctoks = tokens(clip_text)
    if not support or not any(len(tokens(s)) >= 2 and contains(ctoks, tokens(s)) for s in support):
        return "unsupported"
    ok, why = editorial.faithfulness(text, clip_text, language)
    if not ok:
        return why
    unresolved = {editorial._norm(str(w.get("text") or "")) for w in words if w.get("status") == "unresolved"}
    verified = {editorial._norm(t) for t in verified_terms(words)}
    for t in text.split():
        nt = editorial._norm(t)
        if not nt:
            continue
        if nt in unresolved and nt not in verified:
            return "uses_unverified_word"
        if re.search(r"\d", nt) and nt not in verified:
            return "unverified_number"
    return ""


def score(scores: dict[str, Any], text: str) -> float:
    v = sum(WEIGHTS[k] * (float(scores.get(k, 1)) - 1.0) / 4.0 for k in WEIGHTS)
    first = text.split()[0].strip(",.?!:").lower() if text.split() else ""
    if first in _PRONOUN_START:
        v -= 0.12                      # "he said…" – who? the overlay comes before the context
    return round(v, 3)


def from_packaging(packaging: dict[str, Any], clip_text: str, words: Sequence[dict[str, Any]], *,
                   language: Optional[str], fallback_title: str = "") -> dict[str, Any]:
    """
    Titles and caption from the final editor's shipping answer – no model call. The same checks as
    build(): no clickbait, a number only when it was verified in the clip's words. No on-screen
    hook text (the default output is the video + subtitles).
    """
    out: dict[str, Any] = {"hook": "", "hook_source": "", "title": "", "candidates": [], "rejected": [],
                           "content_hook": clip_text.split("\n")[0][:200] if clip_text else "",
                           "caption": str((packaging or {}).get("caption") or "").strip()[:300],
                           "source": "editor"}
    terms = {editorial._norm(v) for v in verified_terms(words)}
    for t in list((packaging or {}).get("titles") or []) + ([fallback_title] if fallback_title else []):
        t = re.sub(r"\s+", " ", str(t or "")).strip().strip('"“”')
        if not t or editorial.clickbait(t, clip_text, language):
            continue
        if any(re.search(r"\d", x) and editorial._norm(x) not in terms for x in t.split()):
            continue
        out["title"] = t[:90]
        break
    return out


def build(provider: Optional[SemanticProvider], clip_text: str, words: Sequence[dict[str, Any]], *,
          kind: str, language: Optional[str]) -> dict[str, Any]:
    out: dict[str, Any] = {"hook": "", "hook_source": "", "title": "", "candidates": [], "rejected": [],
                           "content_hook": clip_text.split("\n")[0][:200] if clip_text else ""}
    if provider is None:
        out["reason"] = "no_semantic_model"
        return out
    terms = verified_terms(words)
    system, user = prompts.hooks_prompt(clip_text, kind, ", ".join(terms), language or "")
    try:
        data = provider.complete_json("hooks", system, user, prompts.HOOKS_SCHEMA, max_tokens=8000)
    except SemanticError as exc:
        out["reason"] = f"model_error: {exc}"
        return out
    ok: list[dict[str, Any]] = []
    for h in data.get("hooks") or []:
        text = re.sub(r"\s+", " ", str(h.get("text") or "")).strip().strip('"“”')
        why = check(text, h.get("support") or [], clip_text, words, language) if text else "empty"
        rec = {"text": text, "support": h.get("support") or [], "scores": h.get("scores") or {}}
        if why:
            out["rejected"].append({**rec, "reason": why})
            continue
        rec["score"] = score(rec["scores"], text)
        ok.append(rec)
    ok.sort(key=lambda r: -r["score"])
    seen, uniq = set(), []
    for r in ok:
        k = editorial._norm(r["text"])
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    out["candidates"] = uniq
    if uniq and uniq[0]["score"] >= MIN_SCORE:
        out["hook"], out["hook_source"] = uniq[0]["text"], "semantic"
    elif uniq:
        out["reason"] = "weak_hooks"
    for t in data.get("titles") or []:
        t = re.sub(r"\s+", " ", str(t or "")).strip().strip('"“”')
        if not t or editorial.clickbait(t, clip_text, language):
            continue
        if any(re.search(r"\d", x) and editorial._norm(x) not in {editorial._norm(v) for v in terms}
               for x in t.split()):
            continue
        out["title"] = t[:90]
        break
    return out
