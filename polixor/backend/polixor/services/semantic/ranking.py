"""
Global ranking of the candidate pool.

1. Absolute rubric: the proposer's 0–2 scores on hook, clarity, payoff,
   interest and feasibility (a weak prior, never the decision alone).
2. Listwise tournament: the pool is shuffled and split into groups; the model
   ranks each group and gives each candidate a verdict (ship / maybe / no).
   This repeats ROUNDS times with different shuffles, so every candidate is
   compared with different rivals from anywhere in the source and position
   effects average out.
3. Final round: the leaders are ranked again against each other.
4. Score = rubric + tournament position + ship votes; then the global order
   is cut with duplicate removal and topic diversity.

Audio peaks and phrase lists play no part here. A moment's place in the
source does not matter.
"""

from __future__ import annotations

import logging
import random
from concurrent.futures import ThreadPoolExecutor

from ...util import profiler
from typing import Any, Optional, Sequence

from . import prompts
from .candidates import CRITERIA, Cand, clip_text
from .checkpoint import StageStore, key_of
from .provider import SemanticError, SemanticProvider
from .sentences import Sentence

log = logging.getLogger("polixor.semantic.ranking")

GROUP = 6
ROUNDS = 3
# the tournament judges the strongest TOURNAMENT_MAX candidates by their rubric (from anywhere in
# the source); the rest keep their rubric score only (cost stays bounded on a 4-hour source)
TOURNAMENT_MAX = 150
FINAL_TOP = 18
FINAL_ROUNDS = 2
PARALLEL = 4
# weights of the final score
W_RUBRIC, W_RANK, W_SHIP = 0.35, 0.40, 0.25
# a candidate the judges call "no" in most rounds is not shipped, whatever its score
MAX_NO_SHARE = 0.5
SHIP_THRESHOLD = 0.55
DUP_IOU = 0.3
TOPIC_PENALTY = 0.08          # per already-selected Short from the same topic
# "maybe" is not "publish": most of the judges' verdicts must be "ship"
MIN_SHIP_SHARE = 0.5
# spread over the whole source: a Short within REGION_SECONDS of an already-selected one pays
# REGION_PENALTY each (long sources only – five clips from one 12-minute stretch feel uncurated)
REGION_SECONDS = 600.0
REGION_PENALTY = 0.05


def _groups(keys: list[str], seed: int, size: int = GROUP) -> list[list[str]]:
    rnd = random.Random(seed)
    k = list(keys)
    rnd.shuffle(k)
    n = max(1, round(len(k) / size))
    return [k[i::n] for i in range(n)] if len(k) > size else [k]


def _judge(provider: SemanticProvider, group: list[str], by_key: dict[str, Cand],
           sentences: Sequence[Sentence]) -> Optional[dict[str, Any]]:
    items = []
    for key in group:
        c = by_key[key]
        dur = sum(sentences[i].duration for i in range(c.start_idx, c.end_idx + 1) if i not in set(c.cut_idx))
        items.append(f"=== {key} ({dur:.0f} s) ===\n{clip_text(c, sentences)}")
    system, user = prompts.rank_prompt("\n\n".join(items))
    try:
        data = provider.complete_json("rank", system, user, prompts.RANK_SCHEMA)
    except SemanticError as exc:
        log.warning("ranking group failed: %s", exc)
        return None
    ranking = [str(k) for k in data.get("ranking") or [] if str(k) in group]
    ranking = list(dict.fromkeys(ranking))
    if sorted(ranking) != sorted(group):
        # a ranking that drops or invents keys is not used
        return None
    verdicts = {str(v.get("key")): v for v in data.get("verdicts") or [] if str(v.get("key")) in group}
    return {"ranking": ranking, "verdicts": verdicts}


def rank_pool(provider: SemanticProvider, pool: list[Cand], sentences: Sequence[Sentence], *,
              store: StageStore, fingerprint: str) -> list[Cand]:
    if not pool:
        return []
    by_key = {c.key: c for c in pool}
    key = key_of("rank", fingerprint, [(c.key, c.start_idx, c.end_idx, c.cut_idx) for c in pool],
                 provider.name, provider.model, ROUNDS, GROUP, FINAL_TOP)
    hit = store.get("ranking", key)
    if hit is not None:
        for c in pool:
            c.scores, c.verdicts = hit[c.key]["scores"], hit[c.key]["verdicts"]
        return sorted(pool, key=lambda c: -c.scores.get("final", 0.0))

    places: dict[str, list[float]] = {c.key: [] for c in pool}
    votes: dict[str, list[dict[str, str]]] = {c.key: [] for c in pool}

    def run_round(groups: list[list[str]], tag: str) -> None:
        def one(g: list[str]) -> Optional[dict[str, Any]]:
            return _judge(provider, g, by_key, sentences) if len(g) > 1 else None

        with ThreadPoolExecutor(max_workers=PARALLEL) as ex:
            outs = profiler.pmap(ex, one, groups)
        for g, out in zip(groups, outs):
            if out is None:
                continue
            n = len(out["ranking"])
            for pos, k in enumerate(out["ranking"]):
                places[k].append(1.0 - pos / max(1, n - 1))
                v = out["verdicts"].get(k) or {}
                votes[k].append({"round": tag, "verdict": str(v.get("verdict") or "maybe"),
                                 "reason": str(v.get("reason") or "")[:300]})

    rnd = random.Random(7)
    order = sorted(pool, key=lambda c: (-c.rubric_total, rnd.random()))
    keys = [c.key for c in order[:TOURNAMENT_MAX]]
    outside = {c.key for c in order[TOURNAMENT_MAX:]}
    if len(keys) > 1:
        for r in range(ROUNDS):
            run_round(_groups(keys, seed=1000 + r), f"r{r + 1}")
    _score(pool, places, votes, outside)
    leaders = [c.key for c in sorted(pool, key=lambda c: -c.scores["final"])[:FINAL_TOP]]
    if len(leaders) > GROUP:
        for r in range(FINAL_ROUNDS):
            run_round(_groups(leaders, seed=2000 + r, size=GROUP + 1), f"final{r + 1}")
        _score(pool, places, votes, outside)
    store.put("ranking", key, {c.key: {"scores": c.scores, "verdicts": c.verdicts} for c in pool})
    return sorted(pool, key=lambda c: -c.scores["final"])


def _score(pool: list[Cand], places: dict[str, list[float]], votes: dict[str, list[dict[str, str]]],
           outside: set[str] = frozenset()) -> None:
    for c in pool:
        rub = c.rubric_total / (2.0 * len(CRITERIA))
        pl = places[c.key]
        vs = votes[c.key]
        neutral = 0.0 if c.key in outside else 0.5    # not judged in the tournament: rubric only
        rank = sum(pl) / len(pl) if pl else neutral
        ship = sum(1.0 if v["verdict"] == "ship" else 0.5 if v["verdict"] == "maybe" else 0.0
                   for v in vs) / len(vs) if vs else neutral
        no = sum(1 for v in vs if v["verdict"] == "no") / len(vs) if vs else 0.0
        c.scores = {"rubric": round(rub, 3), "rank": round(rank, 3), "ship_votes": round(ship, 3),
                    "no_share": round(no, 3), "rounds": len(pl),
                    "final": round(W_RUBRIC * rub + W_RANK * rank + W_SHIP * ship, 4)}
        c.verdicts = vs


def select(ranked: list[Cand], sentences: Sequence[Sentence], *, limit: int,
           threshold: float = SHIP_THRESHOLD) -> tuple[list[Cand], list[dict[str, Any]]]:
    """
    Walks the global order: a candidate ships when it clears the threshold, the
    judges did not mostly reject it, it is not a duplicate of one already
    chosen, and its topic-diversity-adjusted score still clears the bar.
    Returns (selected, decisions for the report).
    """
    chosen: list[Cand] = []
    log_: list[dict[str, Any]] = []
    per_topic: dict[str, int] = {}
    span = (sentences[-1].end - sentences[0].start) if sentences else 0.0
    for c in ranked:
        s = c.scores.get("final", 0.0)
        near = sum(1 for o in chosen if abs((o.start + o.end) - (c.start + c.end)) / 2 < REGION_SECONDS) \
            if span > 3 * REGION_SECONDS else 0
        adj = s - TOPIC_PENALTY * per_topic.get(c.topic, 0) - REGION_PENALTY * near
        vs = c.verdicts or []
        ship_share = sum(1 for v in vs if v.get("verdict") == "ship") / len(vs) if vs else 1.0
        why = ""
        if len(chosen) >= limit:
            why = "limit"
        elif c.scores.get("no_share", 0.0) > MAX_NO_SHARE or ship_share < MIN_SHIP_SHARE:
            why = "judges_rejected"
        elif adj < threshold:
            why = "below_bar" if s < threshold else "topic_diversity"
        else:
            for o in chosen:
                inter = max(0.0, min(o.end, c.end) - max(o.start, c.start))
                iou = inter / max(0.01, (o.end - o.start) + (c.end - c.start) - inter)
                if iou >= DUP_IOU or o.closing_idx == c.closing_idx:
                    why = f"duplicate_of:{o.key}"
                    break
        log_.append({"key": c.key, "start": round(c.start, 2), "end": round(c.end, 2), "type": c.type,
                     "topic": c.topic, "title": c.title, "scores": c.scores, "decision": why or "selected"})
        if not why:
            chosen.append(c)
            per_topic[c.topic] = per_topic.get(c.topic, 0) + 1
    return chosen, log_
