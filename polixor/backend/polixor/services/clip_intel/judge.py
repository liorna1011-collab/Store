"""
עורך AI אופציונלי: שכבת איכות *משנית* מעל הכללים הדטרמיניסטיים.

מודל שפה קורא את התמלול הממוספר של המועמדים המובילים (עם כמה משפטים לפני
ואחרי) ונותן ציון לפי אמת המידה של עורך אנושי: וו, בהירות בלי הקשר חיצוני,
עניין (דעה/ויכוח/סקרנות), פאנץ', שלמות, והאם שווה להעלות. הוא יכול גם להציע
גבולות טובים יותר – רק כמספרי משפטים.

התמלול והווידאו הם המקור המחייב:
  * כל שיפוט תוכן חייב ציטוט *מדויק* מהתמלול (משפט הוו ומשפט הפאנץ'). ציטוט
    שלא מופיע בקליפ (המצאה, פרפרזה, תרגום) – כל הפסיקה נזרקת ואין לה השפעה;
  * המודל לא כותב טקסט לקליפ, לא ממציא דיאלוג או הקשר ולא מזיז מילים –
    הצעת גבולות היא מספרי משפטים בסדר כרונולוגי, והמנוע הדטרמיניסטי מחשב
    מחדש את הציון לגבולות המוצעים לפני שהם מאומצים;
  * בלי מודל שפה מוגדר (או כשהוא לא זמין) – לא רץ, והבחירה הדטרמיניסטית
    נשארת כמו שהיא.

דוגמאות מדירוגים אמיתיים (אופציונלי, מקומי בלבד): הקובץ
<data>/clip_judge_examples.json – רשימה של {"text", "label", "why"}.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import unicodedata
from typing import Any, Optional, Sequence

from ...config import AppSettings
from .score import Scored
from .units import Unit

log = logging.getLogger("polixor.clip_intel.judge")

# כמה מהמועמדים המובילים נשלחים לשיפוט (עלות/זמן)
MAX_JUDGED = 12
# השפעה מרבית על הציון (±)
MAX_ADJUST = 0.06
# משפטים לפני ואחרי הקליפ שמוצגים למודל (לצורך הצעת גבולות)
AROUND = 3
MAX_EXAMPLES = 4
RUBRIC = ("hook", "clarity", "interest", "payoff", "complete")

_CACHE: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()

SYSTEM = (
    "You are a senior short-form video editor reviewing a candidate clip from a livestream. "
    "The transcript is the source of truth. You get numbered sentences; the ones marked "
    "in_clip form the candidate. Score 0-10: hook (do the first seconds give a real reason to "
    "keep watching - an opinion, disagreement, meaningful question, surprising claim, tension, "
    "emotion or a promise of payoff; a routine question like 'what day is it?' or a shout is "
    "NOT a hook), clarity (understandable without anything said before), interest (conflict, "
    "opinion, curiosity), payoff (a real answer, verdict, twist or punchline - not just noise), "
    "complete (does not stop before the answer or jump to another topic). Set upload_worthy "
    "false for clips that are technically fine but not worth posting. "
    "EVIDENCE IS REQUIRED: hook_quote and payoff_quote must be EXACT words copied from the "
    "in_clip sentences (no paraphrase, no translation, no invented words). Never rewrite or "
    "invent dialogue or context. You may suggest better boundaries ONLY as sentence numbers "
    "(start <= end, chronological), or null to keep them. "
    "Answer ONLY with JSON: {\"hook\": 0-10, \"clarity\": 0-10, \"interest\": 0-10, "
    "\"payoff\": 0-10, \"complete\": 0-10, \"upload_worthy\": true|false, "
    "\"standalone\": true|false, \"ordinary\": true|false, \"hook_quote\": \"...\", "
    "\"payoff_quote\": \"...\", \"start\": n|null, \"end\": n|null, "
    "\"reason\": \"<one short sentence>\"}."
)


def enabled(settings: AppSettings) -> bool:
    from .. import llm

    return bool(getattr(settings, "clip_llm_judge", True)) and llm.is_llm_enabled(settings)


def window(s: Scored, units: Sequence[Unit]) -> tuple[int, int]:
    p = s.proposal
    return max(0, p.hook_idx - AROUND), min(len(units) - 1, p.end_idx + AROUND)


def excerpt(s: Scored, units: Sequence[Unit]) -> dict[str, Any]:
    p = s.proposal
    a, b = window(s, units) if units else (0, -1)
    return {
        "hook": units[p.hook_idx].text if units else "",
        "context": " ".join(u.text for u in units[p.hook_idx + 1: p.payoff_idx]),
        "payoff": " ".join(u.text for u in units[p.payoff_idx: p.end_idx + 1]),
        "seconds": round(p.duration, 1),
        "sentences": [{"n": k, "text": units[k].text, "in_clip": p.hook_idx <= k <= p.end_idx}
                      for k in range(a, b + 1)],
    }


def _num(data: dict[str, Any], key: str) -> Optional[float]:
    if key not in data or data[key] is None:
        return None
    return max(0.0, min(10.0, float(data[key])))


def _idx(v: Any) -> Optional[int]:
    if v is None or isinstance(v, bool):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def parse_verdict(raw: str) -> Optional[dict[str, Any]]:
    from .. import llm

    try:
        data = llm._parse_json(raw)
    except Exception:                                      # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    try:
        out: dict[str, Any] = {
            "hook": _num(data, "hook") or 0.0,
            "payoff": _num(data, "payoff") or 0.0,
            "standalone": bool(data.get("standalone", False)),
            "ordinary": bool(data.get("ordinary", False)),
            "upload_worthy": bool(data.get("upload_worthy", True)),
            "reason": str(data.get("reason", ""))[:240],
            "hook_quote": str(data.get("hook_quote") or "")[:300],
            "payoff_quote": str(data.get("payoff_quote") or "")[:300],
            "start": _idx(data.get("start")),
            "end": _idx(data.get("end")),
        }
        for k in ("clarity", "interest", "complete"):
            out[k] = _num(data, k)
    except (TypeError, ValueError):
        return None
    return out


# --------------------------------------------------------------------------
# עיגון בתמלול
# --------------------------------------------------------------------------
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def _norm(text: str) -> str:
    # בלי ניקוד, פיסוק וגרשיים; רווחים מאוחדים
    t = "".join(ch for ch in unicodedata.normalize("NFKD", text or "")
                if not unicodedata.combining(ch))
    t = _PUNCT.sub(" ", t.lower())
    return " ".join(t.split())


def _quoted(quote: str, texts: Sequence[str]) -> bool:
    q = _norm(quote)
    if len(q.split()) < 2 and len(q) < 6:
        return False
    return q in _norm(" ".join(texts))


def ground(verdict: dict[str, Any], s: Scored, units: Sequence[Unit]) -> Optional[str]:
    """
    בודק שהפסיקה נשענת על התמלול. מחזיר None כשהיא מעוגנת, אחרת את הסיבה
    לזריקתה. הצעת גבולות לא תקינה לא פוסלת את הפסיקה – רק נמחקת.
    """
    p = s.proposal
    clip = [u.text for u in units[p.hook_idx: p.end_idx + 1]]
    opening = [u.text for u in units[p.hook_idx: min(p.end_idx, p.hook_idx + 2) + 1]]
    if not verdict.get("hook_quote") or not _quoted(verdict["hook_quote"], opening):
        return "unsupported_hook_quote"
    if not verdict.get("payoff_quote") or not _quoted(verdict["payoff_quote"], clip):
        return "unsupported_payoff_quote"
    a, b = window(s, units)
    st, en = verdict.get("start"), verdict.get("end")
    if st is None and en is None:
        return None
    st = p.hook_idx if st is None else st
    en = p.end_idx if en is None else en
    if not (a <= st <= en <= b) or (st, en) == (p.hook_idx, p.end_idx):
        verdict["start"] = verdict["end"] = None
    else:
        verdict["start"], verdict["end"] = st, en
    return None


def apply_verdict(s: Scored, verdict: dict[str, Any]) -> None:
    """פסילה (לא עומד בפני עצמו / שגרתי / אין פאנץ' / לא שווה העלאה) או תיקון קטן לציון."""
    s.judge = dict(verdict)
    if (not verdict["standalone"] or verdict["ordinary"] or verdict["payoff"] < 4
            or not verdict.get("upload_worthy", True)):
        s.passed = False
        s.rejection = "judge"
        return
    vals = [verdict[k] for k in RUBRIC if verdict.get(k) is not None]
    q = sum(vals) / (10.0 * len(vals)) if vals else 0.5
    adj = MAX_ADJUST * (q - 0.5) * 2
    s.final = round(max(0.0, min(1.0, s.final + adj)), 4)
    s.components["judge"] = round(q, 3)
    if s.final < s.threshold:
        s.passed = False
        s.rejection = "below_quality_bar"


def _examples() -> list[dict[str, str]]:
    from ...config import PATHS

    path = PATHS.data / "clip_judge_examples.json"
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for e in data if isinstance(data, list) else []:
        if isinstance(e, dict) and e.get("text") and e.get("label"):
            out.append({"text": str(e["text"])[:600], "label": str(e["label"])[:20],
                        "why": str(e.get("why", ""))[:200]})
    return out[:MAX_EXAMPLES]


def _system() -> str:
    ex = _examples()
    if not ex:
        return SYSTEM
    return SYSTEM + " Examples rated by this creator (label = would they post it): " + \
        json.dumps(ex, ensure_ascii=False)


def judge(candidates: Sequence[Scored], units: Sequence[Unit], settings: AppSettings,
          language: Optional[str]) -> int:
    """שופט את המועמדים המובילים. מחזיר כמה נשפטו בפועל (פסיקה מעוגנת שהוחלה)."""
    from .. import llm

    if not enabled(settings) or not units:
        return 0
    system = _system()
    done = 0
    for s in list(candidates)[:MAX_JUDGED]:
        ex = excerpt(s, units)
        key = hashlib.sha256(json.dumps({**ex, "lang": language, "sys": system}, ensure_ascii=False)
                             .encode("utf-8")).hexdigest()
        with _LOCK:
            verdict = _CACHE.get(key)
        if verdict is None:
            try:
                raw = llm.call_model(system, json.dumps({"language": language, **ex},
                                                        ensure_ascii=False), settings)
            except Exception as exc:                       # noqa: BLE001
                log.info("clip judge unavailable: %s", exc)
                return done
            verdict = parse_verdict(raw)
            if verdict is None:
                continue
            with _LOCK:
                _CACHE[key] = verdict
        verdict = dict(verdict)
        problem = ground(verdict, s, units)
        if problem:
            # פסיקה בלי ראיה מהתמלול – נרשמת לשקיפות, בלי שום השפעה
            s.judge = {"discarded": problem, "reason": verdict.get("reason", "")}
            continue
        apply_verdict(s, verdict)
        done += 1
    return done
