"""
Internal unit economics per project – ADMIN ONLY. Never sent on a customer route.

  ai_cost_usd        language-model tokens of the run (intel_report.json → usage) at the model's
                     list price; cached answers cost nothing (they made no call)
  infra_usd          wall time of the run's stages × the machine's hourly price (an estimate)
  revenue_usd        the minutes charged × the plan's price per included minute
  gross_margin_usd   revenue − AI − infrastructure

Prices are configuration, not truth: POLIXOR_PRICE_INPUT_PER_M / POLIXOR_PRICE_OUTPUT_PER_M (USD per
million tokens), POLIXOR_INFRA_USD_PER_HOUR, POLIXOR_USD_PER_ILS.
"""

from __future__ import annotations

import os
from typing import Any

from ..db import session_scope
from ..models import Job, StageTiming
from ..util.jsoncache import read_json


def _env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def prices() -> dict[str, float]:
    return {"input_per_m": _env("POLIXOR_PRICE_INPUT_PER_M", 4.0),
            "output_per_m": _env("POLIXOR_PRICE_OUTPUT_PER_M", 20.0),
            "infra_per_hour": _env("POLIXOR_INFRA_USD_PER_HOUR", 0.36),
            "usd_per_ils": _env("POLIXOR_USD_PER_ILS", 0.27)}


def usd(usage: dict[str, Any]) -> float:
    p = prices()
    return int(usage.get("input_tokens") or 0) / 1e6 * p["input_per_m"] + \
        int(usage.get("output_tokens") or 0) / 1e6 * p["output_per_m"]


def run_record(usage: dict[str, Any], *, kind: str) -> dict[str, Any]:
    """One model run of a project (a generation, a re-edit) for the cost ledger in its artifacts."""
    import time

    return {"at": round(time.time(), 1), "kind": kind, "calls": int(usage.get("calls") or 0),
            "cached": int(usage.get("cached") or 0), "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0), "usd": round(usd(usage), 4)}


# what a re-edit of an existing analysis pays for: the steps whose prompts or logic changed run
# again; the topic map, profile, candidates and judges are answered from the project's cache
_REPLAY_PER_CANDIDATE = {"boundaries": 1.0, "editor": 1.6, "reconstruct": 0.6}
_REPLAY_PER_SHORT = {"hooks": 1.0, "adjudicate": 1.0}


def replay_estimate(job: Job) -> dict[str, Any]:
    """
    Expected model cost of "Re-edit with the improved editor" for this project, BEFORE it runs:
    candidates the editor will judge × calls per candidate × the project's own tokens per call
    (from its last run; the editor's figure for tasks it never ran). A range, not a promise.
    """
    from ..pipeline import settings_for_job
    from .semantic import ranking

    arts = job.artifacts or {}
    intel = read_json(arts.get("intel_report_path")) or {}
    by_task = (intel.get("usage") or {}).get("by_task") or {}
    pool = len(intel.get("pool") or [])
    duration = float((arts.get("source_info") or {}).get("duration") or 0.0)
    limit = int(settings_for_job(job).short_count or 0)
    if not pool or not limit:
        return {"available": False, "reason": "no earlier analysis with candidates"}
    budget = ranking.edit_budget(limit, duration, pool)

    def per_call(task: str) -> tuple[float, float]:
        t = by_task.get(task) or by_task.get("editor") or {}
        n = max(1.0, float(t.get("calls") or 0))
        if not t.get("calls"):
            return 6000.0, 1500.0                     # no history at all: a typical editor call
        return float(t.get("input_tokens") or 0) / n, float(t.get("output_tokens") or 0) / n
    p = prices()
    calls, tin, tout = 0.0, 0.0, 0.0
    for task, k in _REPLAY_PER_CANDIDATE.items():
        i, o = per_call(task)
        calls += budget * k; tin += budget * k * i; tout += budget * k * o
    for task, k in _REPLAY_PER_SHORT.items():
        i, o = per_call(task)
        calls += limit * k; tin += limit * k * i; tout += limit * k * o
    mid = tin / 1e6 * p["input_per_m"] + tout / 1e6 * p["output_per_m"]
    return {"available": True, "candidates_judged": budget, "shorts_wanted": limit, "model_calls": round(calls),
            "input_tokens": round(tin), "output_tokens": round(tout), "usd": round(mid, 2),
            "usd_range": [round(mid * 0.6, 2), round(mid * 1.5, 2)],
            "cached_free": [k for k in ("topic_map", "topic_merge", "profile", "candidates", "rank") if k in by_task]}


def project(job: Job, minutes_charged_ms: int, plan: dict[str, Any]) -> dict[str, Any]:
    p = prices()
    intel = read_json((job.artifacts or {}).get("intel_report_path")) or {}
    usage = intel.get("usage") or {}
    tin, tout = int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
    runs = list((job.artifacts or {}).get("ai_runs") or [])
    # every run of the project counts (a re-edit does not erase what the first run cost)
    ai = sum(float(r.get("usd") or 0) for r in runs) if runs else usd(usage)
    with session_scope() as s:
        wall = sum(float(r.seconds or 0) for r in s.query(StageTiming).filter(StageTiming.job_id == job.id).all())
    infra = wall / 3600 * p["infra_per_hour"]
    per_min_usd = (plan["price"] * (p["usd_per_ils"] if plan.get("currency") == "ILS" else 1.0)) \
        / max(1, plan["minutes"])
    revenue = minutes_charged_ms / 60000 * per_min_usd
    calls, cached = int(usage.get("calls") or 0), int(usage.get("cached") or 0)
    return {
        "source_seconds": float(((job.artifacts or {}).get("source_info") or {}).get("duration") or 0.0),
        "minutes_charged_exact": round(minutes_charged_ms / 60000, 3),
        "ai_cost_usd": round(ai, 4), "infra_usd": round(infra, 4), "revenue_usd": round(revenue, 4),
        "gross_margin_usd": round(revenue - ai - infra, 4),
        "gross_margin_pct": round((revenue - ai - infra) / revenue * 100, 1) if revenue else None,
        "model": {"calls": calls, "cached": cached, "failures": int(usage.get("failures") or 0),
                  "input_tokens": tin, "output_tokens": tout,
                  "cache_hit_rate": round(cached / (calls + cached), 3) if calls + cached else None,
                  "by_task": usage.get("by_task") or {}},
        "processing_seconds": round(wall, 1),
        "ai_runs": runs,
    }
