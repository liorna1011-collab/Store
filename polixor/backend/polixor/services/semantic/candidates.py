"""
Typed candidate discovery, per topic, over the whole source.

Every topic of the global map is read in full (long topics in overlapping
windows) and the model proposes every moment that could become a Short,
typed (question→answer, accusation→response, setup→payoff …) with its
evidence: sentence IDs and exact quotes in role order, a rubric and the
sentences an editor would cut. Each proposal is validated (validate.py);
rejected proposals are kept with their reasons for the run report.

The result is the global candidate POOL – the input of ranking. A moment at
3:20 and one at minute 212 are in the same pool and are judged the same way.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any, Optional, Sequence

from . import prompts
from .checkpoint import StageStore, key_of
from .provider import SemanticError, SemanticProvider
from .sentences import Sentence, render
from .topics import Topic, TopicMap
from .validate import Validator

log = logging.getLogger("polixor.semantic.candidates")

WINDOW_SECONDS = 720.0
WINDOW_OVERLAP = 120.0
CONTEXT_SENTENCES = 3
PARALLEL = 4
CRITERIA = ("hook", "clarity", "payoff", "interest", "feasibility")


@dataclass
class Cand:
    key: str
    type: str
    start_idx: int
    end_idx: int
    evidence: list[dict[str, Any]]
    rubric: dict[str, dict[str, Any]]
    title: str
    standalone: str
    cut_idx: list[int]
    topic: str
    start: float = 0.0
    end: float = 0.0
    # filled by ranking / boundaries / editor
    scores: dict[str, float] = field(default_factory=dict)
    verdicts: list[dict[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def rubric_total(self) -> int:
        return int(sum(int((self.rubric.get(k) or {}).get("score", 0)) for k in CRITERIA))

    @property
    def closing_idx(self) -> int:
        """The last sentence that holds evidence (the payoff's words end there)."""
        return max(e.get("idx_end", e["idx"]) for e in self.evidence)

    @property
    def opening_idx(self) -> int:
        return min(e["idx"] for e in self.evidence)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Cand":
        return cls(**d)


def topic_windows(topic: Topic, sentences: Sequence[Sentence]) -> list[tuple[int, int]]:
    """A topic's spans, long ones cut into overlapping windows."""
    out: list[tuple[int, int]] = []
    for a, b in topic.spans:
        i = a
        while i <= b:
            j = i
            while j + 1 <= b and sentences[j + 1].end - sentences[i].start <= WINDOW_SECONDS:
                j += 1
            out.append((i, j))
            if j >= b:
                break
            # next window starts WINDOW_OVERLAP seconds before this one ends
            k = j
            while k > i and sentences[j].end - sentences[k].start < WINDOW_OVERLAP:
                k -= 1
            i = max(i + 1, k)
    return out


def _window_text(sentences: Sequence[Sentence], lo: int, hi: int) -> tuple[str, int, int]:
    a = max(0, lo - CONTEXT_SENTENCES)
    b = min(len(sentences) - 1, hi + CONTEXT_SENTENCES)
    lines = []
    for i in range(a, b + 1):
        line = render([sentences[i]])
        lines.append(("» " + line) if (i < lo or i > hi) else line)
    return "\n".join(lines), a, b


def discover(provider: SemanticProvider, sentences: Sequence[Sentence], tmap: TopicMap, *,
             language: str, min_s: float, max_s: float, store: StageStore, fingerprint: str,
             cancel=None) -> tuple[list[Cand], list[dict[str, Any]]]:
    jobs: list[tuple[Topic, int, int]] = []
    for t in tmap.topics:
        if t.short_potential == "none" and t.long_form_value == "none":
            continue
        for lo, hi in topic_windows(t, sentences):
            jobs.append((t, lo, hi))
    v = Validator(sentences)
    results: dict[int, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = {}

    def one(k: int) -> None:
        t, lo, hi = jobs[k]
        key = key_of("cands", fingerprint, t.id, t.title, lo, hi, min_s, max_s, provider.name, provider.model)
        hit = store.get(f"cands_{k:03d}", key)
        if hit is not None:
            results[k] = (hit["ok"], hit["rejected"])
            return
        if cancel is not None and cancel.is_set():
            return
        text, ca, cb = _window_text(sentences, lo, hi)
        system, user = prompts.candidates_prompt(t.title, t.summary, text, language, min_s, max_s)
        try:
            data = provider.complete_json("candidates", system, user, prompts.CANDIDATES_SCHEMA)
        except SemanticError as exc:
            log.warning("candidate discovery failed for %s: %s", t.id, exc)
            results[k] = ([], [{"stage": "candidates", "topic": t.id, "reasons": [f"model_error: {exc}"]}])
            return
        ok, rej = [], []
        for m in data.get("moments") or []:
            c = v.candidate(m)
            # the span may use the context lines, but must touch the window itself
            if c.ok and (c.end_idx < lo or c.start_idx > hi or c.start_idx < ca or c.end_idx > cb):
                c.ok, c.reasons = False, ["outside_window"]
            if not c.ok:
                rej.append({"stage": "candidates", "topic": t.id, "item": m, "reasons": c.reasons})
                continue
            cuts = v.sentence_ids(m.get("cut_ids") or [], c.start_idx, c.end_idx) or []
            ev_idx = {k for e in c.evidence for k in range(e["idx"], e.get("idx_end", e["idx"]) + 1)}
            cuts = [i for i in cuts if i not in ev_idx and c.start_idx < i < c.end_idx]
            ok.append({"type": m["type"], "start_idx": c.start_idx, "end_idx": c.end_idx,
                       "evidence": c.evidence, "rubric": m.get("rubric") or {}, "title": str(m.get("title") or ""),
                       "standalone": str(m.get("standalone") or ""), "cut_idx": cuts, "topic": t.id})
        store.put(f"cands_{k:03d}", key, {"ok": ok, "rejected": rej})
        results[k] = (ok, rej)

    with ThreadPoolExecutor(max_workers=PARALLEL) as ex:
        list(ex.map(one, range(len(jobs))))
    if cancel is not None and cancel.is_set():
        from ...errors import JobCancelledError

        raise JobCancelledError()

    pool: list[Cand] = []
    rejected: list[dict[str, Any]] = []
    for k in range(len(jobs)):
        ok, rej = results.get(k, ([], []))
        rejected += rej
        for d in ok:
            c = Cand(key="", **d)
            c.start, c.end = sentences[c.start_idx].start, sentences[c.end_idx].end
            pool.append(c)
    pool = merge_duplicates(pool)
    pool.sort(key=lambda c: c.start)
    for i, c in enumerate(pool):
        c.key = f"C{i + 1:03d}"
    return pool, rejected


def merge_duplicates(pool: list[Cand]) -> list[Cand]:
    """The same moment proposed twice (overlapping windows): keep the better-rated proposal."""
    out: list[Cand] = []
    for c in sorted(pool, key=lambda c: -c.rubric_total):
        dup = None
        for o in out:
            inter = max(0, min(o.end_idx, c.end_idx) - max(o.start_idx, c.start_idx) + 1)
            shorter = min(o.end_idx - o.start_idx, c.end_idx - c.start_idx) + 1
            if (inter / max(1, shorter) >= 0.8) or o.closing_idx == c.closing_idx and inter > 0:
                dup = o
                break
        if dup is None:
            out.append(c)
    return out


def clip_text(c: Cand, sentences: Sequence[Sentence], start_idx: Optional[int] = None,
              end_idx: Optional[int] = None, cuts: Optional[Sequence[int]] = None) -> str:
    a = c.start_idx if start_idx is None else start_idx
    b = c.end_idx if end_idx is None else end_idx
    cut = set(c.cut_idx if cuts is None else cuts)
    return "\n".join(render([sentences[i]], with_turns=True) for i in range(a, b + 1) if i not in cut)
