"""
Long-form topic videos from the same topic map the Shorts come from.

A topic with long-form value becomes one cleaned video: the model chooses
the sentence ranges to keep (every question with its answer, every argument
with its response, the conclusion) and drops greetings, logistics, tangents,
dead air and unintelligible crosstalk. The code validates every ID, keeps
ranges in order, and puts back any evidence sentence of a validated Short
candidate the edit would have split (a question kept without its answer is
repaired, not shipped). Inside kept ranges, silences are shortened from the
word times exactly like the existing long-form planner, and the result is a
services/longform.LongformPlan that the existing long-form renderer draws.

The Shorts inside each topic are the selected candidates whose span lies in
it – one topic gives one long video plus several Shorts.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

from ..longform import Chapter, LongformPlan, Section, _capped, _merge_spans, _output_time
from . import prompts
from .candidates import Cand
from .checkpoint import StageStore, key_of
from .provider import SemanticError, SemanticProvider
from .sentences import Sentence, render
from .topics import Topic, TopicMap
from .validate import Validator

log = logging.getLogger("polixor.semantic.longform")

MIN_TOPIC_SECONDS = 90.0
MIN_OUTPUT_SECONDS = 60.0
KEEP_PAUSE = 0.35
DEAD_AIR = 2.0
MERGE_GAP = 3.0
JUNK_REASON = {"greeting": "off_topic", "signoff": "off_topic", "logistics": "off_topic", "technical": "off_topic",
               "chat_reading": "off_topic", "ad": "off_topic", "tangent": "off_topic", "dead_air": "dead_air",
               "crosstalk": "filler", "unintelligible": "filler"}


def topic_seconds(t: Topic, sentences: Sequence[Sentence]) -> float:
    return sum(sentences[b].end - sentences[a].start for a, b in t.spans)


def eligible(tmap: TopicMap, sentences: Sequence[Sentence], limit: int = 8) -> list[Topic]:
    """Topics worth a long video: high value first, then the longest; at most `limit`, in time order."""
    ok = [t for t in tmap.topics if t.long_form_value in ("high", "medium")
          and topic_seconds(t, sentences) >= MIN_TOPIC_SECONDS]
    ok.sort(key=lambda t: (t.long_form_value != "high", -topic_seconds(t, sentences)))
    return sorted(ok[:max(0, limit)], key=lambda t: t.first)


def plan_topic(provider: Optional[SemanticProvider], t: Topic, sentences: Sequence[Sentence], tmap: TopicMap,
               pool: Sequence[Cand], *, target_s: float, store: StageStore, fingerprint: str
               ) -> Optional[dict[str, Any]]:
    """{topic, title, description, keep: [(i, j)], plan: LongformPlan dict, shorts: [keys]} or None."""
    key = key_of("longform", fingerprint, t.id, t.spans, target_s, provider.name if provider else "",
                 provider.model if provider else "")
    hit = store.get(f"longform_{t.id}", key)
    if hit is not None:
        return hit
    v = Validator(sentences)
    junk_idx = {i for j in tmap.junk for i in range(j["start"], j["end"] + 1)}
    keep: list[tuple[int, int]] = []
    title, desc, source = t.title, t.summary, "deterministic"
    if provider is not None:
        text = "\n".join(render(sentences[a:b + 1]) for a, b in t.spans)
        system, user = prompts.longform_prompt(text, t.title, MIN_OUTPUT_SECONDS, target_s)
        try:
            data = provider.complete_json("longform", system, user, prompts.LONGFORM_SCHEMA)
            for r in data.get("keep") or []:
                a, b, why = v.span(r.get("start_id"), r.get("end_id"))
                if why or a is None or b is None or not any(x <= a and b <= y for x, y in t.spans):
                    continue
                keep.append((a, b))
            title = str(data.get("title") or t.title)[:100]
            desc = str(data.get("description") or t.summary)[:300]
            source = "model"
        except SemanticError as exc:
            log.warning("long-form edit failed for %s: %s", t.id, exc)
    if not keep:
        # without a model answer: the topic minus its junk ranges
        for a, b in t.spans:
            run = None
            for i in range(a, b + 1):
                if i in junk_idx:
                    if run:
                        keep.append(tuple(run))
                        run = None
                    continue
                run = [run[0], i] if run else [i, i]
            if run:
                keep.append(tuple(run))
    keep = _normalise(keep)
    keep, repaired = _keep_pairs(keep, t, pool)
    plan = build_plan(keep, sentences, title, junk=[j for j in tmap.junk
                                                    if any(x <= j["start"] <= y for x, y in t.spans)],
                      topic_spans=t.spans, target_s=target_s)
    if plan.output_seconds < MIN_OUTPUT_SECONDS:
        return None
    shorts = [c.key for c in pool if c.topic == t.id]
    out = {"topic": t.id, "title": title, "description": desc, "keep": [list(x) for x in keep],
           "repaired_pairs": repaired, "source": source, "plan": plan.to_dict(), "shorts": shorts}
    store.put(f"longform_{t.id}", key, out)
    return out


def _normalise(keep: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for a, b in sorted(set(keep)):
        if out and a <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _keep_pairs(keep: list[tuple[int, int]], t: Topic, pool: Sequence[Cand]) -> tuple[list[tuple[int, int]], int]:
    """A validated moment is kept whole or not at all: missing evidence sentences are put back."""
    kept = {i for a, b in keep for i in range(a, b + 1)}
    repaired = 0
    for c in pool:
        if c.topic != t.id:
            continue
        ev = {k for e in c.evidence for k in range(e["idx"], e.get("idx_end", e["idx"]) + 1)}
        if ev & kept and not ev <= kept:
            for i in range(min(ev), max(ev) + 1):
                kept.add(i)
            repaired += 1
    if not kept:
        return keep, repaired
    idx = sorted(kept)
    out, run = [], [idx[0], idx[0]]
    for i in idx[1:]:
        if i == run[1] + 1:
            run[1] = i
        else:
            out.append(tuple(run))
            run = [i, i]
    out.append(tuple(run))
    return out, repaired


def build_plan(keep: Sequence[tuple[int, int]], sentences: Sequence[Sentence], title: str, *,
               junk: Sequence[dict[str, Any]] = (), topic_spans: Sequence[tuple[int, int]] = (),
               target_s: float = 900.0) -> LongformPlan:
    spoken: list[tuple[float, float]] = []
    sections: list[Section] = []
    for a, b in keep:
        spans = []
        for i in range(a, b + 1):
            for x, y, _t in sentences[i].words:
                spans.append((max(0.0, x - KEEP_PAUSE / 2), y + KEEP_PAUSE / 2))
        merged = _merge_spans(sorted(spans), DEAD_AIR)
        spoken += merged
        sections.append(Section(start=sentences[a].start, end=sentences[b].end, score=1.0, topic=0,
                                title=title, reasons=["semantic"]))
    spoken = [(x, y) for x, y in _merge_spans(sorted(spoken), KEEP_PAUSE) if y - x >= 0.3]
    segments = [(x, y) for x, y in _merge_spans(spoken, MERGE_GAP) if y - x >= 1.0]
    beats = [_capped([(x, y) for x, y in spoken if x >= a - 1e-6 and y <= b + 1e-6]) for a, b in segments]
    flat = [bt for seg in beats for bt in seg]
    output = sum(y - x for x, y in flat)
    chapters = [Chapter(start=0.0, title=title, source_start=segments[0][0])] if segments else []
    kept_idx = {i for a, b in keep for i in range(a, b + 1)}
    removed = []
    for a, b in topic_spans:
        for i in range(a, b + 1):
            if i in kept_idx:
                continue
            reason = next((JUNK_REASON.get(j.get("kind"), "off_topic") for j in junk
                           if j["start"] <= i <= j["end"]), "low_interest")
            removed.append({"start": sentences[i].start, "end": sentences[i].end, "reason": reason})
    src = sum(sentences[b].end - sentences[a].start for a, b in topic_spans) if topic_spans else output
    plan = LongformPlan(segments=segments, beats=beats, sections=sections, removed=removed, chapters=chapters,
                        output_seconds=round(output, 3),
                        stats={"source_seconds": round(src, 2), "speech_seconds": round(output, 2),
                               "available_seconds": round(src, 2), "target_seconds": round(target_s, 2),
                               "output_seconds": round(output, 2), "within_tolerance": output <= target_s * 1.12,
                               "short_source": False, "topics": 1, "topics_off": 0, "sections": len(sections),
                               "units": len(kept_idx), "removed_seconds": {}, "language": "",
                               "engine": "semantic"})
    plan.stats["removed_seconds"] = plan.removed_seconds()
    # the chapter times are output times
    for c in plan.chapters:
        t = _output_time(flat, c.source_start)
        c.start = 0.0 if t is None else round(t, 3)
    if plan.chapters:
        plan.chapters[0].start = 0.0
    return plan
