"""
שופט אופציונלי: מודל שפה שקורא את התמלול של המועמדים המובילים ונותן ציון
לפי אותה אמת מידה של עורך אנושי – וו, פאנץ', האם הקליפ עומד בפני עצמו,
והאם זו שיחה שגרתית.

הוא לא כותב שום טקסט לקליפ ולא מזיז גבולות: הוא רק יכול לפסול מועמד או
להזיז מעט את הציון שלו. בלי מודל שפה מוגדר – לא רץ בכלל, והבחירה
ההיוריסטית נשארת כמו שהיא. התוצאה נשמרת ברשומת ההסבר.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from typing import Any, Optional, Sequence

from ...config import AppSettings
from .score import Scored
from .units import Unit

log = logging.getLogger("polixor.clip_intel.judge")

# כמה מהמועמדים המובילים נשלחים לשיפוט (עלות/זמן)
MAX_JUDGED = 12
# השפעה מרבית על הציון (±)
MAX_ADJUST = 0.06

_CACHE: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()

SYSTEM = (
    "You are a senior short-form video editor. You judge whether a transcript excerpt "
    "from a livestream works as a standalone short clip. A good clip has a HOOK (the first "
    "seconds make you want to keep watching), the CONTEXT it needs, and a PAYOFF (a punchline, "
    "twist, reaction or insight) - and it does not need anything said before it. Reject "
    "ordinary chit-chat, filler and clips that stop before the interesting moment. "
    "Answer ONLY with JSON: {\"hook\": 0-10, \"payoff\": 0-10, \"standalone\": true|false, "
    "\"ordinary\": true|false, \"reason\": \"<one short sentence>\"}. Never rewrite the text."
)


def enabled(settings: AppSettings) -> bool:
    from .. import llm

    return bool(getattr(settings, "clip_llm_judge", True)) and llm.is_llm_enabled(settings)


def excerpt(s: Scored, units: Sequence[Unit]) -> dict[str, Any]:
    p = s.proposal
    return {
        "hook": units[p.hook_idx].text,
        "context": " ".join(u.text for u in units[p.hook_idx + 1: p.payoff_idx]),
        "payoff": " ".join(u.text for u in units[p.payoff_idx: p.end_idx + 1]),
        "seconds": round(p.duration, 1),
    }


def parse_verdict(raw: str) -> Optional[dict[str, Any]]:
    from .. import llm

    try:
        data = llm._parse_json(raw)
    except Exception:                                      # noqa: BLE001
        return None
    try:
        return {"hook": max(0.0, min(10.0, float(data.get("hook", 0)))),
                "payoff": max(0.0, min(10.0, float(data.get("payoff", 0)))),
                "standalone": bool(data.get("standalone", False)),
                "ordinary": bool(data.get("ordinary", False)),
                "reason": str(data.get("reason", ""))[:240]}
    except (TypeError, ValueError):
        return None


def apply_verdict(s: Scored, verdict: dict[str, Any]) -> None:
    """פסילה (לא עומד בפני עצמו / שיחה שגרתית / אין פאנץ') או תיקון קטן לציון."""
    s.judge = dict(verdict)
    if not verdict["standalone"] or verdict["ordinary"] or verdict["payoff"] < 4:
        s.passed = False
        s.rejection = "judge"
        return
    adj = MAX_ADJUST * ((verdict["hook"] + verdict["payoff"]) / 20.0 - 0.5) * 2
    s.final = round(max(0.0, min(1.0, s.final + adj)), 4)
    s.components["judge"] = round((verdict["hook"] + verdict["payoff"]) / 20.0, 3)
    if s.final < s.threshold:
        s.passed = False
        s.rejection = "below_quality_bar"


def judge(candidates: Sequence[Scored], units: Sequence[Unit], settings: AppSettings,
          language: Optional[str]) -> int:
    """שופט את המועמדים המובילים. מחזיר כמה נשפטו בפועל."""
    from .. import llm

    if not enabled(settings):
        return 0
    done = 0
    for s in list(candidates)[:MAX_JUDGED]:
        ex = excerpt(s, units)
        key = hashlib.sha256(json.dumps({**ex, "lang": language}, ensure_ascii=False)
                             .encode("utf-8")).hexdigest()
        with _LOCK:
            verdict = _CACHE.get(key)
        if verdict is None:
            try:
                raw = llm.call_model(SYSTEM, json.dumps({"language": language, **ex},
                                                        ensure_ascii=False), settings)
            except Exception as exc:                       # noqa: BLE001
                log.info("clip judge unavailable: %s", exc)
                return done
            verdict = parse_verdict(raw)
            if verdict is None:
                continue
            with _LOCK:
                _CACHE[key] = verdict
        apply_verdict(s, verdict)
        done += 1
    return done
