"""
מטא-דאטה לרשתות: כותרת, כיתוב/תיאור והאשטגים לכל פלטפורמה – מתוך התמלול
האמיתי של הקליפ שנבחר (ומהסיבה שבגללה נבחר: הוו והפאנץ').

  * עם מודל שפה (Anthropic / OpenAI / Ollama, לפי ההגדרות): גרסה נפרדת לכל
    פלטפורמה, בשפת התוכן של הסרטון. ההנחיה אוסרת להמציא עובדות, שמות או
    ציטוטים שלא מופיעים בתמלול; התשובה נבדקת, מקוצרת למגבלות הפלטפורמה,
    וההאשטגים מנורמלים.
  * בלי מודל שפה: הצעה לפי כללים – כותרת הקליפ / משפט הוו, תיאור ממשפטי
    התמלול, והאשטגים ממילות המפתח. תמיד מסומן כך (source="rules").
  * שום דבר לא נשלח לבד: ההצעה ממלאת את חלון הפרסום ואפשר לערוך הכול.
  * נשמר בקליפ (render_params.social_metadata) לפי טביעה של התמלול, כך
    שפתיחה חוזרת לא עולה שוב קריאה למודל.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Optional, Sequence

from .. import i18n
from ..config import AppSettings
from ..db import session_scope
from ..models import Clip, Job

log = logging.getLogger("polixor.social_metadata")

VERSION = 1
PLATFORMS = ("youtube", "instagram", "facebook", "tiktok")

# מגבלות ושיטות עבודה לכל פלטפורמה (תואם לבדיקות שבספקים)
RULES: dict[str, dict[str, Any]] = {
    "youtube": {"title_max": 100, "text_max": 5000, "tags": (3, 8), "style": "title + description"},
    "instagram": {"title_max": 0, "text_max": 2200, "tags": (3, 8), "style": "caption"},
    "facebook": {"title_max": 255, "text_max": 2200, "tags": (1, 4), "style": "caption"},
    "tiktok": {"title_max": 0, "text_max": 2200, "tags": (3, 6), "style": "caption"},
}

SYSTEM = (
    "You write social media metadata for a short video clip cut from a livestream. "
    "Use ONLY what is said in the transcript and the facts given. Never invent names, "
    "numbers, events, quotes or claims that are not in the transcript. Write in the "
    "language code given in 'language' (Hebrew → natural Israeli Hebrew, not a translation). "
    "No clickbait lies; a hook-style title is fine if it is true to the clip. "
    "Answer ONLY with JSON: {\"platforms\": {\"<platform>\": {\"title\": str, \"text\": str, "
    "\"hashtags\": [str]}}} for exactly the platforms requested. Respect each platform's "
    "limits and style given in 'rules'. Hashtags without spaces, relevant to the clip."
)

_STOP = {
    "he": set("""של את על עם זה זו זאת אני אתה את הוא היא אנחנו אתם הם הן לא כן גם רק כל מה מי
                 איך למה כי אם או אבל אז יש אין היה היתה היו יהיה עוד כמו כבד פה שם הנה ממש
                 כזה כזאת אחד אחת שלי שלך שלו שלה שלנו לי לך לו לה לנו להם בו בה אותו אותה
                 טוב בסדר יאללה סתם מאוד הרבה קצת עכשיו אחרי לפני בגלל כדי היום אתמול מחר
                 לכם אתכם שלכם אליכם אותם אותן להן שלהם הזה הזאת האלה אלה איזה כאן שוב
                 בואו תראו תגידו אומר אומרת אמרתי רוצה צריך יכול אפשר דבר דברים משהו""".split()),
    "en": set("""the a an and or but if then so to of in on at for with from by is are was were be
                 been it this that these those i you he she we they me my your his her our their
                 not no yes just like really very what who how why when there here have has had
                 do does did will would can could gonna okay ok yeah every some also about into
                 out up get got going know think right well want""".split()),
}
_WORD = re.compile(r"[\w֐-׿']+", re.UNICODE)


# --------------------------------------------------------------------------
def clip_context(clip_id: str) -> Optional[dict[str, Any]]:
    """מה שהמודל רואה: תמלול הקליפ, הכותרת, הסיבה, הוו והפאנץ' ושפת התוכן."""
    from ..pipeline import load_transcript_for_job
    from . import analysis_store

    with session_scope() as s:
        clip = s.get(Clip, clip_id)
        if clip is None:
            return None
        job = s.get(Job, clip.job_id)
        tr = load_transcript_for_job(job) if job is not None else None
        spans = [(float(a), float(b)) for a, b in (clip.segments_json or [])] or \
            [(float(clip.source_start), float(clip.source_end))]
        text = " ".join(tr.text_between(a, b) for a, b in spans).strip() if tr else ""
        lang = ""
        if job is not None:
            lang = (job.content_language or "").strip()
            if lang in ("", "auto"):
                lang = (tr.language if tr else "") or (job.artifacts or {}).get("content_language", "")
        hook = payoff = ""
        rp = (job.artifacts or {}).get("clip_review_path") if job is not None else None
        review = analysis_store.load_clip_review(__import__("pathlib").Path(rp)) if rp else None
        for r in (review or {}).get("selected") or []:
            if abs(float(r.get("start", -1)) - float(clip.source_start)) < 0.5:
                hook = (r.get("hook") or {}).get("text", "")
                payoff = (r.get("payoff") or {}).get("text", "")
                break
        return {"clip_id": clip.id, "title": clip.title or "", "reason": clip.reason or "",
                "duration": round(float(clip.duration or 0.0), 1),
                "format": "short" if (clip.height or 0) > (clip.width or 0) else "long",
                "language": lang or _guess_lang(text), "transcript": text[:6000],
                "hook": hook, "payoff": payoff, "project": job.title if job else ""}


def _guess_lang(text: str) -> str:
    heb = len(re.findall(r"[א-ת]", text))
    lat = len(re.findall(r"[A-Za-z]", text))
    return "he" if heb >= lat else "en"


def _fingerprint(ctx: dict[str, Any]) -> str:
    raw = json.dumps({k: ctx[k] for k in ("transcript", "title", "hook", "payoff", "language")},
                     ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------
def normalize_hashtag(tag: str) -> str:
    t = re.sub(r"[\s\-]+", "", str(tag or "")).lstrip("#")
    t = re.sub(r"[^\w֐-׿]", "", t, flags=re.UNICODE)
    return t[:50]


def _clean(platform: str, item: dict[str, Any]) -> dict[str, Any]:
    r = RULES[platform]
    title = re.sub(r"\s+", " ", str(item.get("title") or "")).strip()
    text = str(item.get("text") or item.get("caption") or item.get("description") or "").strip()
    tags: list[str] = []
    for t in item.get("hashtags") or []:
        n = normalize_hashtag(t)
        if n and n.lower() not in {x.lower() for x in tags}:
            tags.append(n)
    tags = tags[: r["tags"][1]]
    # ההאשטגים יוצאים בנפרד (הספק מוסיף אותם לכיתוב) – מסירים כפילות מהטקסט
    text = re.sub(r"(?:\s*#[\w֐-׿]+)+\s*$", "", text).strip()
    if r["title_max"]:
        title = title[: r["title_max"]].rstrip()
    else:
        title = ""                                      # Instagram / TikTok: כיתוב בלבד
    room = r["text_max"] - (len(title) + 2 if title else 0) - sum(len(t) + 2 for t in tags) - 2
    if len(text) > room:
        text = text[: max(0, room - 1)].rstrip() + "…"
    return {"title": title, "text": text, "hashtags": tags}


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?…])\s+|\n+", text)
    return [p.strip() for p in parts if len(p.strip()) > 3]


_HE_PREFIX = "הובלמשכ"


def keywords(text: str, language: str, limit: int = 6) -> list[str]:
    """מילות מפתח לפי שכיחות; בעברית מאחדים צורות עם אותיות שימוש (השקשוקה → שקשוקה)."""
    stop = _STOP.get(language, set()) | _STOP["he"] | _STOP["en"]
    words = [w for w in _WORD.findall(text) if len(w) >= 3 and not w.isdigit()]
    seen = {w.lower() for w in words}

    def base(w: str) -> str:
        lw = w.lower()
        # מסירים עד שתי אותיות שימוש, רק כשהצורה הקצרה מופיעה גם היא בטקסט
        for _ in range(2):
            if len(lw) >= 5 and lw[0] in _HE_PREFIX and lw[1:] in seen:
                lw = lw[1:]
        return lw

    counts: dict[str, int] = {}
    shown: dict[str, str] = {}
    order: list[str] = []
    for w in words:
        b = base(w)
        if b in stop or w.lower() in stop or len(b) < 3:
            continue
        if b not in counts:
            order.append(b)
            shown[b] = w if w.lower() == b else b
        elif w.lower() == b:
            shown[b] = w
        counts[b] = counts.get(b, 0) + 1
    first = {b: i for i, b in enumerate(order)}
    ranked = sorted(order, key=lambda b: (-counts[b], -min(len(b), 8), first[b]))
    return [shown[b] for b in ranked[:limit]]


def rules_based(ctx: dict[str, Any], platforms: Sequence[str]) -> dict[str, dict[str, Any]]:
    """בלי מודל שפה: רק מה שיש בקליפ – בלי להמציא."""
    sents = _sentences(ctx["transcript"])
    hook = ctx.get("hook") or (sents[0] if sents else "")
    title = ctx.get("title") or hook
    body = " ".join(sents[:3]) if sents else ""
    tags = keywords(ctx["transcript"] or title, ctx["language"])
    out = {}
    for p in platforms:
        lo, hi = RULES[p]["tags"]
        text = body if RULES[p]["title_max"] else (hook + ("\n\n" + body if body and body != hook else ""))
        out[p] = _clean(p, {"title": title, "text": text, "hashtags": tags[:hi]})
    return out


def ai_based(ctx: dict[str, Any], platforms: Sequence[str], settings: AppSettings) -> dict[str, dict[str, Any]]:
    from . import llm

    user = json.dumps({
        "language": ctx["language"], "platforms": list(platforms),
        "rules": {p: {"title_max": RULES[p]["title_max"], "text_max": RULES[p]["text_max"],
                      "hashtags_min_max": list(RULES[p]["tags"]), "style": RULES[p]["style"]}
                  for p in platforms},
        "clip": {"format": ctx["format"], "duration_seconds": ctx["duration"],
                 "working_title": ctx["title"], "why_selected": ctx["reason"],
                 "hook": ctx["hook"], "payoff": ctx["payoff"]},
        "transcript": ctx["transcript"],
    }, ensure_ascii=False)
    data = llm._parse_json(llm.call_model(SYSTEM, user, settings))
    got = data.get("platforms") or {}
    out = {}
    for p in platforms:
        item = got.get(p) if isinstance(got, dict) else None
        if not isinstance(item, dict):
            raise ValueError(f"missing platform {p}")
        out[p] = _clean(p, item)
    return out


def generate(clip_id: str, platforms: Sequence[str], settings: AppSettings, *,
             regenerate: bool = False) -> dict[str, Any]:
    """הצעה לכל פלטפורמה. {platforms: {...}, source: ai|rules, language, note?}."""
    platforms = [p for p in platforms if p in RULES] or list(PLATFORMS)
    ctx = clip_context(clip_id)
    if ctx is None:
        raise LookupError(clip_id)
    fp = _fingerprint(ctx)
    with session_scope() as s:
        clip = s.get(Clip, clip_id)
        cache = dict((clip.render_params or {}).get("social_metadata") or {})
    if not regenerate and cache.get("version") == VERSION and cache.get("fingerprint") == fp:
        have = cache.get("platforms") or {}
        if all(p in have for p in platforms):
            return {"platforms": {p: have[p] for p in platforms}, "source": cache.get("source", "rules"),
                    "language": ctx["language"], "cached": True}
    from . import llm

    source, note = "rules", ""
    result: dict[str, dict[str, Any]] = {}
    if llm.is_llm_enabled(settings) and ctx["transcript"]:
        try:
            result = ai_based(ctx, platforms, settings)
            source = "ai"
        except Exception as exc:                        # noqa: BLE001
            log.info("social metadata via AI failed: %s", type(exc).__name__)
            note = i18n.tr("social_metadata.ai_failed")
    if not result:
        result = rules_based(ctx, platforms)
        if not note and not llm.is_llm_enabled(settings):
            note = i18n.tr("social_metadata.rules_note")
    if not ctx["transcript"]:
        note = i18n.tr("social_metadata.no_transcript")
    with session_scope() as s:
        clip = s.get(Clip, clip_id)
        params = dict(clip.render_params or {})
        prev = dict(params.get("social_metadata") or {})
        merged = dict(prev.get("platforms") or {}) if prev.get("fingerprint") == fp else {}
        merged.update(result)
        params["social_metadata"] = {"version": VERSION, "fingerprint": fp, "source": source,
                                     "platforms": merged}
        clip.render_params = params
    return {"platforms": result, "source": source, "language": ctx["language"],
            "cached": False, **({"note": note} if note else {})}
