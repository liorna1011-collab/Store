"""
Topic maps: per chunk (with overlap), then one global map.

The source is read in chunks of ~CHUNK_SECONDS of sentences. Each chunk is
mapped with the tail of the previous chunk as read-only context, so a topic
that crosses a chunk boundary is recognised as one. Chunks are mapped in
parallel and each result is checkpointed separately – a crash after three
hours resumes at the first unmapped chunk.

The global merge first joins pieces the chunk maps marked as continuing,
then asks the model to merge pieces of the same subject (including a subject
that comes back later). Every answer is validated; an invalid merge falls
back to the deterministic one.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from ...util import profiler
from dataclasses import asdict, dataclass, field
from typing import Any, Optional, Sequence

from . import prompts
from .checkpoint import StageStore, key_of
from .provider import SemanticError, SemanticProvider
from .sentences import Sentence, render
from .validate import Validator

log = logging.getLogger("polixor.semantic.topics")

CHUNK_SECONDS = 600.0
CONTEXT_SECONDS = 90.0
PARALLEL = 4


@dataclass
class Topic:
    id: str
    title: str
    summary: str
    spans: list[tuple[int, int]]                 # sentence index ranges (inclusive), in time order
    central: str = ""
    short_potential: str = "medium"
    long_form_value: str = "medium"
    pieces: list[str] = field(default_factory=list)

    @property
    def first(self) -> int:
        return self.spans[0][0]

    @property
    def last(self) -> int:
        return self.spans[-1][1]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["spans"] = [list(x) for x in self.spans]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Topic":
        return cls(**{**d, "spans": [tuple(x) for x in d["spans"]]})


@dataclass
class TopicMap:
    topics: list[Topic]
    junk: list[dict[str, Any]]                   # {start, end (indices), kind}
    chunks: int = 0
    rejected: list[dict[str, Any]] = field(default_factory=list)
    merge: str = "model"                         # model | deterministic
    names: list[str] = field(default_factory=list)   # proper names, as hints for the ASR (never as text)

    def topic_of(self, idx: int) -> Optional[Topic]:
        for t in self.topics:
            if any(a <= idx <= b for a, b in t.spans):
                return t
        return None

    def to_dict(self) -> dict[str, Any]:
        return {"topics": [t.to_dict() for t in self.topics], "junk": self.junk, "chunks": self.chunks,
                "rejected": self.rejected, "merge": self.merge, "names": self.names}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TopicMap":
        return cls(topics=[Topic.from_dict(t) for t in d["topics"]], junk=list(d.get("junk") or []),
                   chunks=int(d.get("chunks", 0)), rejected=list(d.get("rejected") or []),
                   merge=str(d.get("merge", "model")), names=list(d.get("names") or []))


def plan_chunks(sentences: Sequence[Sentence], seconds: float = CHUNK_SECONDS) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    i = 0
    n = len(sentences)
    while i < n:
        j = i
        while j + 1 < n and sentences[j + 1].end - sentences[i].start <= seconds:
            j += 1
        # don't leave a tiny last chunk
        if n - 1 - j > 0 and sentences[-1].end - sentences[j + 1].start < seconds * 0.25:
            j = n - 1
        out.append((i, j))
        i = j + 1
    return out


def _context(sentences: Sequence[Sentence], i: int) -> list[Sentence]:
    out = []
    k = i - 1
    while k >= 0 and sentences[i].start - sentences[k].start <= CONTEXT_SECONDS:
        out.insert(0, sentences[k])
        k -= 1
    return out


def map_chunk(provider: SemanticProvider, sentences: Sequence[Sentence], lo: int, hi: int,
              language: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """(pieces, junk, rejected) of one chunk, validated against its own sentences."""
    chunk = sentences[lo:hi + 1]
    system, user = prompts.topic_map_prompt(render(chunk), render(_context(sentences, lo)), language)
    data = provider.complete_json("topic_map", system, user, prompts.TOPIC_MAP_SCHEMA)
    v = Validator(sentences)
    pieces, junk, rejected = [], [], []
    for t in data.get("topics") or []:
        c = v.topic(t)
        if not c.ok or c.start_idx < lo or c.end_idx > hi:
            rejected.append({"stage": "topic_map", "item": t, "reasons": c.reasons or ["outside_chunk"]})
            continue
        pieces.append({"start": c.start_idx, "end": c.end_idx, "title": str(t["title"]).strip(),
                       "summary": str(t.get("summary") or ""), "central": str(t.get("central") or ""),
                       "continues": bool(t.get("continues_previous")),
                       "short_potential": t.get("short_potential", "medium"),
                       "long_form_value": t.get("long_form_value", "medium")})
    for j in data.get("junk") or []:
        a, b, why = v.span(j.get("start_id"), j.get("end_id"))
        if why or a is None or b is None or a < lo or b > hi:
            rejected.append({"stage": "junk", "item": j, "reasons": why or ["outside_chunk"]})
            continue
        junk.append({"start": a, "end": b, "kind": j.get("kind", "tangent")})
    # proper names: only when the transcript really wrote something close to them (a hint, not text)
    from .validate import contains, tokens

    chunk_toks = tokens(" ".join(s.text for s in chunk))
    for nm in data.get("names") or []:
        name, heard = str(nm.get("name") or "").strip(), str(nm.get("heard_as") or "").strip()
        if not name or len(name) > 40 or not heard or not contains(chunk_toks, tokens(heard)):
            rejected.append({"stage": "names", "item": nm, "reasons": ["not_in_transcript"]})
            continue
        if _close(tokens(name), tokens(heard)):
            junk.append({"name": name})                 # carried to the global map below
    # no overlaps: a later piece starts after the previous one ends
    pieces.sort(key=lambda p: p["start"])
    clean: list[dict[str, Any]] = []
    for p in pieces:
        if clean and p["start"] <= clean[-1]["end"]:
            p["start"] = clean[-1]["end"] + 1
        if p["start"] <= p["end"]:
            clean.append(p)
    return clean, junk, rejected


def _edits(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, y in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y))
        prev = cur
    return prev[-1]


def _close(name: list[str], heard: list[str]) -> bool:
    """A correct spelling may differ from what was heard by a few letters, not be another word."""
    a, b = " ".join(name), " ".join(heard)
    return bool(a) and _edits(a, b) <= max(2, len(a) // 4)


def build_topic_map(provider: SemanticProvider, sentences: Sequence[Sentence], *, language: str,
                    store: StageStore, fingerprint: str, cancel=None) -> TopicMap:
    chunks = plan_chunks(sentences)
    results: dict[int, tuple[list, list, list]] = {}

    def one(k: int) -> None:
        lo, hi = chunks[k]
        key = key_of("topic_chunk", fingerprint, lo, hi, provider.name, provider.model)
        hit = store.get(f"topic_chunk_{k:03d}", key)
        if hit is not None:
            results[k] = (hit["pieces"], hit["junk"], hit["rejected"])
            return
        if cancel is not None and cancel.is_set():
            return
        res = map_chunk(provider, sentences, lo, hi, language)
        store.put(f"topic_chunk_{k:03d}", key, {"pieces": res[0], "junk": res[1], "rejected": res[2]})
        results[k] = res

    with ThreadPoolExecutor(max_workers=PARALLEL) as ex:
        profiler.pmap(ex, one, range(len(chunks)))
    if cancel is not None and cancel.is_set():
        from ...errors import JobCancelledError

        raise JobCancelledError()

    pieces, junk, rejected, names = [], [], [], []
    for k in range(len(chunks)):
        p, j, r = results.get(k, ([], [], []))
        for x in p:
            x["chunk"] = k
        pieces += p
        names += [x["name"] for x in j if "name" in x]
        junk += [x for x in j if "name" not in x]
        rejected += r
    pieces = _fill_gaps(pieces, sentences, junk)
    # deterministic merge: a piece that continues the last piece of the previous chunk
    merged: list[dict[str, Any]] = []
    for p in pieces:
        if merged and p.get("continues") and p.get("chunk") != merged[-1].get("chunk") \
                and p["start"] <= merged[-1]["end"] + 3:
            m = merged[-1]
            m["end"] = max(m["end"], p["end"])
            m["summary"] = (m["summary"] + " " + p["summary"]).strip()
            m["chunk"] = p.get("chunk")
            continue
        merged.append(dict(p))
    for i, p in enumerate(merged):
        p["id"] = f"P{i + 1}"

    key = key_of("topic_merge", fingerprint, [(p["start"], p["end"], p["title"]) for p in merged],
                 provider.name, provider.model)
    hit = store.get("topic_merge", key)
    if hit is not None:
        return TopicMap.from_dict(hit)
    tm = _global_merge(provider, merged, sentences, junk, rejected, len(chunks))
    tm.names = list(dict.fromkeys(names))[:60]
    store.put("topic_merge", key, tm.to_dict())
    return tm


def _fill_gaps(pieces: list[dict[str, Any]], sentences: Sequence[Sentence],
               junk: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sentences no topic and no junk range covers join the topic before them (nothing is lost)."""
    covered = [False] * len(sentences)
    for p in pieces + junk:
        for i in range(p["start"], p["end"] + 1):
            covered[i] = True
    pieces = sorted(pieces, key=lambda p: p["start"])
    for i, c in enumerate(covered):
        if c or not pieces:
            continue
        before = [p for p in pieces if p["end"] < i]
        target = before[-1] if before else pieces[0]
        if target["end"] == i - 1:
            target["end"] = i
        elif not before and target["start"] == i + 1:
            target["start"] = i
    return pieces


def _global_merge(provider: SemanticProvider, pieces: list[dict[str, Any]], sentences: Sequence[Sentence],
                  junk: list[dict[str, Any]], rejected: list[dict[str, Any]], n_chunks: int) -> TopicMap:
    def as_topic(i: int, ps: list[dict[str, Any]], title: str, summary: str, lfv: str) -> Topic:
        spans = sorted((p["start"], p["end"]) for p in ps)
        return Topic(id=f"T{i + 1}", title=title, summary=summary, spans=spans,
                     central=" / ".join(p.get("central", "") for p in ps if p.get("central"))[:400],
                     short_potential=max((p.get("short_potential", "low") for p in ps),
                                         key=lambda x: ["none", "low", "medium", "high"].index(x)),
                     long_form_value=lfv, pieces=[p["id"] for p in ps])

    deterministic = TopicMap([as_topic(i, [p], p["title"], p["summary"], p.get("long_form_value", "medium"))
                              for i, p in enumerate(pieces)], junk, n_chunks, rejected, "deterministic")
    if len(pieces) <= 1:
        return deterministic
    listing = "\n".join(f"{p['id']} [{sentences[p['start']].start:.0f}-{sentences[p['end']].end:.0f}s] "
                        f"{p['title']} – {p['summary']}" for p in pieces)
    system, user = prompts.merge_topics_prompt(listing)
    try:
        data = provider.complete_json("topic_merge", system, user, prompts.MERGE_SCHEMA)
    except SemanticError as exc:
        log.warning("topic merge failed, keeping chunk topics: %s", exc)
        return deterministic
    by_id = {p["id"]: p for p in pieces}
    used: list[str] = []
    topics: list[Topic] = []
    for t in data.get("topics") or []:
        ids = [str(x) for x in t.get("pieces") or []]
        if not ids or any(x not in by_id for x in ids) or any(x in used for x in ids):
            rejected.append({"stage": "topic_merge", "item": t, "reasons": ["bad_piece_ids"]})
            return deterministic
        used += ids
        topics.append(as_topic(len(topics), [by_id[x] for x in ids], str(t.get("title") or by_id[ids[0]]["title"]),
                               str(t.get("summary") or ""), str(t.get("long_form_value") or "medium")))
    if sorted(used) != sorted(by_id):
        rejected.append({"stage": "topic_merge", "item": None, "reasons": ["pieces_missing"]})
        return deterministic
    topics.sort(key=lambda t: t.first)
    for i, t in enumerate(topics):
        t.id = f"T{i + 1}"
    return TopicMap(topics, junk, n_chunks, rejected, "model")
