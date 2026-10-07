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
    """
    strict=True (POLIXOR_SCRIPTED_STRICT=1) imitates a strict editor on a source with good moments
    and rough first cuts – the RC1 zero-output pattern: the ranking judges say "maybe" to most raw
    cuts, the editor rejects a first cut for stopping before its payoff (no fix inside the narrow
    range), and only a reconstructed cut can ship; weak moments stay rejected for their content.
    """

    def __init__(self, *, latency: float | None = None, moment_seconds: float = 30.0,
                 strict: bool | None = None) -> None:
        self.latency = float(os.environ.get("POLIXOR_SCRIPTED_LATENCY", "0") or 0) if latency is None else latency
        self.moment_seconds = moment_seconds
        self.repaired: set[str] = set()
        self.strict = os.environ.get("POLIXOR_SCRIPTED_STRICT") == "1" if strict is None else strict
        self.calls: dict[str, int] = {}
        self.first_end: dict[str, int] = {}

    def _kind(self, sid: str) -> str:
        h = zlib.crc32(sid.encode()) % 15
        return "weak" if h % 3 == 2 else ("repair" if h % 5 == 4 else "ship")

    def __call__(self, task: str, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        if self.latency:
            time.sleep(self.latency)
        self.calls[task] = self.calls.get(task, 0) + 1
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
            if self.strict:
                return {"ranking": keys, "verdicts": [
                    {"key": k, "verdict": "no" if zlib.crc32(k.encode()) % 4 == 0 else "maybe",
                     "reason": "rough cut" } for k in keys]}
            return {"ranking": keys, "verdicts": [{"key": k, "verdict": "ship", "reason": ""} for k in keys]}
        if task == "reconstruct" and os.environ.get("POLIXOR_SCRIPTED_UNFIXABLE") == "1":
            # the worst case: the senior editor cannot rebuild the cut either
            return {"fixable": False, "start_id": "", "end_id": "", "cut_ids": [], "start_reason": "",
                    "end_reason": "", "cut_reason": "no cut in range reaches the payoff"}
        if task == "reconstruct":
            st = re.findall(r"^(s\d{4}) \(", user.split("ALLOWED STARTS:")[1].split("ALLOWED ENDS:")[0], re.M)
            en = re.findall(r"^(s\d{4}) \(", user.split("ALLOWED ENDS:")[1], re.M)
            cur = re.search(r"CURRENT CUT: (s\d{4}) → (s\d{4})", user)
            start = cur.group(1) if cur and cur.group(1) in st else st[-1]
            later = [e for e in en if cur and e > cur.group(2)]
            return {"fixable": True, "start_id": start, "end_id": (later[:2] or en)[-1], "cut_ids": [],
                    "start_reason": "", "end_reason": "extended to the payoff", "cut_reason": ""}
        if task == "boundaries":
            st = re.findall(r"^(s\d{4}) \(", user.split("ALLOWED STARTS:")[1].split("ALLOWED ENDS:")[0], re.M)
            en = re.findall(r"^(s\d{4}) \(", user.split("ALLOWED ENDS:")[1], re.M)
            prop = re.search(r"PROPOSED CUT \(rough, from discovery\): (s\d{4}) → (s\d{4})", user)
            a0 = prop.group(1) if prop and prop.group(1) in st else st[-1]
            b0 = prop.group(2) if prop and prop.group(2) in en else en[0]
            return {"start_id": a0, "end_id": b0, "cut_ids": [], "start_reason": "", "end_reason": "",
                    "cut_reason": ""}
        if task == "editor":
            m = re.search(r"CUT: (s\d{4}) → (s\d{4})", user)
            start = m.group(1) if m else "s0000"
            kind = self._kind(start)
            ok = {k: True for k in CHECKS}
            if self.strict and kind != "weak":
                end = int(m.group(2)[1:]) if m else 0
                first = self.first_end.setdefault(start, end)
                if end < first + 2:
                    # the payoff lies two sentences past the first cut: one more sentence is not enough
                    return {"verdict": "reject", "checks": ok | {"payoff": False}, "fixes": [],
                            "reason": "strong moment, but the clip ends before the answer", "scores": _rubric(2)}
                return {"verdict": "ship", "checks": ok, "fixes": [], "reason": "scripted", "scores": _rubric(2),
                        "packaging": {"titles": ["כותרת עורך"], "caption": "תיאור קצר"}}
            if kind == "weak":
                return {"verdict": "reject", "checks": ok, "fixes": [], "reason": "scripted: nothing happens",
                        "scores": {**_rubric(1), "interest": {"score": 0, "reason": "dull"}}}
            if kind == "repair" and start not in self.repaired:
                self.repaired.add(start)
                return {"verdict": "repair", "checks": ok | {"payoff": False}, "fixes": [],
                        "reason": "scripted: stops before the point", "scores": _rubric(2)}
            return {"verdict": "ship", "checks": ok, "fixes": [], "reason": "scripted", "scores": _rubric(2),
                        "packaging": {"titles": ["כותרת עורך"], "caption": "תיאור קצר"}}
        if task == "hooks":
            body = user.split("CLIP TRANSCRIPT:\n")[1] if "CLIP TRANSCRIPT:\n" in user else user
            words = body.split()[:4]
            return {"hooks": [{"text": " ".join(words), "support": [" ".join(words)],
                               "scores": {k: 4 for k in ("truthfulness", "specificity", "curiosity", "clarity",
                                                         "natural", "relevance")}}], "titles": ["כותרת"]}
        if task == "longform_review":
            return {"verdict": "ship", "checks": {k: True for k in ("opening", "context", "development", "payoff",
                                                                     "coherent")}, "reason": "scripted"}
        if task == "adjudicate":
            return {"decisions": []}
        if task == "longform":
            if not lines:
                return {"keep": [], "title": "", "description": ""}
            return {"keep": [{"start_id": lines[0][0], "end_id": lines[-1][0], "purpose": "all"}],
                    "title": "השידור", "description": ""}
        return {}
