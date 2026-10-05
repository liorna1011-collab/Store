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


def project(job: Job, minutes_charged_ms: int, plan: dict[str, Any]) -> dict[str, Any]:
    p = prices()
    intel = read_json((job.artifacts or {}).get("intel_report_path")) or {}
    usage = intel.get("usage") or {}
    tin, tout = int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
    ai = tin / 1e6 * p["input_per_m"] + tout / 1e6 * p["output_per_m"]
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
    }
