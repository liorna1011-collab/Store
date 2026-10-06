"""
A deterministic stand-in for the language model – benchmarks and development only.

POLIXOR_SEMANTIC_SCRIPTED=1 makes `provider.resolve()` return it instead of a paid model. It answers
every semantic task with schema-valid output derived from the transcript lines in the prompt, so the
WHOLE editorial pipeline runs (topic map, candidates, tournament, boundaries, editor gate with ship /
repair / reject, hooks, long-form plan) with zero network and zero cost. It is not an editor: its
choices are mechanical (every third moment is weak, every fifth needs a repair). Its latency per call is
POLIXOR_SCRIPTED_LATENCY seconds (default 0) to imitate model wait in pipeline-overlap measurements.

Never selectable from the interface; never used when the variable is unset.
"""

from __future__ import annotations

import os
import re
import time
import zlib
from typing import Any

LINE = re.compile(r"\[(s\d{4}) ([\d.]+)-([\d.]+) t\d+\] (.*)")
CHECKS = ("opening_hooks", "standalone", "payoff", "clean_ending", "pacing")


def _rubric(n: int) -> dict[str, Any]:
    return {k: {"score": n, "reason": "scripted"} for k in ("hook", "clarity", "payoff", "interest", "feasibility")}


def enabled() -> bool:
    return os.environ.get("POLIXOR_SEMANTIC_SCRIPTED", "") == "1"


class ScriptedEditor:
    def __init__(self, *, latency: float | None = None, moment_seconds: float = 30.0) -> None:
        self.latency = float(os.environ.get("POLIXOR_SCRIPTED_LATENCY", "0") or 0) if latency is None else latency
        self.moment_seconds = moment_seconds
        self.repaired: set[str] = set()

    def _kind(self, sid: str) -> str:
        h = zlib.crc32(sid.encode()) % 15
        return "weak" if h % 3 == 2 else ("repair" if h % 5 == 4 else "ship")

    def __call__(self, task: str, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        if self.latency:
            time.sleep(self.latency)
        lines = [(m.group(1), float(m.group(2)), float(m.group(3)), m.group(4)) for m in LINE.finditer(user)]
        if task == "topic_map":
            if not lines:
                return {"topics": [], "junk": [], "names": []}
            # one topic per ~4 minutes of lines
            topics, i = [], 0
            while i < len(lines):
                j = i
                while j + 1 < len(lines) and lines[j + 1][2] - lines[i][1] < 240:
                    j += 1
                topics.append({"start_id": lines[i][0], "end_id": lines[j][0], "title": f"נושא {len(topics) + 1}",
                               "summary": "", "central": "", "continues_previous": False,
                               "short_potential": "high", "long_form_value": "high"})
                i = j + 1
            return {"topics": topics, "junk": [], "names": []}
        if task == "topic_merge":
            pieces = re.findall(r"^(P\d+) ", user, re.M)
            return {"topics": [{"pieces": [p], "title": f"נושא {n + 1}", "summary": "", "long_form_value": "high"}
                               for n, p in enumerate(pieces)]}
        if task == "profile":
            return {"profile": "livestream", "confidence": "high", "reason": "scripted"}
        if task == "candidates":
            out, i = [], 0
            while i < len(lines) and len(out) < 6:
                j = i
                while j + 1 < len(lines) and lines[j][2] - lines[i][1] < self.moment_seconds:
                    j += 1
                if lines[j][2] - lines[i][1] >= 10:
                    out.append({"type": "story", "start_id": lines[i][0], "end_id": lines[j][0],
                                "evidence": [{"role": "story", "sentence_id": lines[i][0], "quote": lines[i][3]},
                                             {"role": "conclusion", "sentence_id": lines[j][0], "quote": lines[j][3]}],
                                "rubric": _rubric(2), "standalone": "", "cut_ids": [], "title": f"רגע {lines[i][0]}"})
                i = j + 1
            return {"moments": out}
        if task == "rank":
            keys = re.findall(r"=== (C\d+) ", user)
            return {"ranking": keys, "verdicts": [{"key": k, "verdict": "ship", "reason": ""} for k in keys]}
        if task == "boundaries":
            st = re.findall(r"^(s\d{4}) \(", user.split("ALLOWED STARTS:")[1].split("ALLOWED ENDS:")[0], re.M)
            en = re.findall(r"^(s\d{4}) \(", user.split("ALLOWED ENDS:")[1], re.M)
            return {"start_id": st[-1], "end_id": en[0], "cut_ids": [], "start_reason": "", "end_reason": "",
                    "cut_reason": ""}
        if task == "editor":
            m = re.search(r"CUT: (s\d{4})", user)
            start = m.group(1) if m else "s0000"
            kind = self._kind(start)
            ok = {k: True for k in CHECKS}
            if kind == "weak":
                return {"verdict": "reject", "checks": ok, "fixes": [], "reason": "scripted: nothing happens",
                        "scores": {**_rubric(1), "interest": {"score": 0, "reason": "dull"}}}
            if kind == "repair" and start not in self.repaired:
                self.repaired.add(start)
                return {"verdict": "repair", "checks": ok | {"payoff": False}, "fixes": [],
                        "reason": "scripted: stops before the point", "scores": _rubric(2)}
            return {"verdict": "ship", "checks": ok, "fixes": [], "reason": "scripted", "scores": _rubric(2)}
        if task == "hooks":
            body = user.split("CLIP TRANSCRIPT:\n")[1] if "CLIP TRANSCRIPT:\n" in user else user
            words = body.split()[:4]
            return {"hooks": [{"text": " ".join(words), "support": [" ".join(words)],
                               "scores": {k: 4 for k in ("truthfulness", "specificity", "curiosity", "clarity",
                                                         "natural", "relevance")}}], "titles": ["כותרת"]}
        if task == "adjudicate":
            return {"decisions": []}
        if task == "longform":
            if not lines:
                return {"keep": [], "title": "", "description": ""}
            return {"keep": [{"start_id": lines[0][0], "end_id": lines[-1][0], "purpose": "all"}],
                    "title": "השידור", "description": ""}
        return {}
