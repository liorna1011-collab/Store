"""
Per-project diagnostics from what a run already recorded – nothing is re-run.

  profile(job)   timing by stage and sub-stage, RTF, model calls / cache hits / time by task,
                 milestones (time to first Short, all Shorts, long-form), the top bottlenecks
  gate(job)      the final editor's outcome: shipped, repaired, rejected by category, not
                 evaluated; works for older reports too (classified from their recorded checks)
"""

from __future__ import annotations

from typing import Any, Optional

from ..db import session_scope
from ..models import Clip, ClipKind, ClipStatus, Job, StageTiming

CHECK_CATEGORY = {"opening_hooks": "weak_hook", "standalone": "missing_context", "payoff": "missing_payoff",
                  "clean_ending": "bad_ending", "pacing": "pacing"}


def _read(path: Any) -> Any:
    from ..util.jsoncache import read_json

    return read_json(path)    # cached per file version: polled pages never re-parse a big report


def _interest(scores: dict[str, Any]) -> Optional[int]:
    v = (scores or {}).get("interest")
    try:
        return int(v.get("score") if isinstance(v, dict) else v)
    except (TypeError, ValueError):
        return None


def classify_record(rec: dict[str, Any]) -> dict[str, Any]:
    """Category of one editor-rejected clip from its report record (new or old format)."""
    if rec.get("category"):
        return {"category": rec["category"], "failed_checks": rec.get("failed_checks") or [],
                "repaired": bool(rec.get("repaired")), "near_pass": bool(rec.get("near_pass"))}
    hist = rec.get("history") or []
    last = hist[-1] if hist else {}
    reason = str(rec.get("reason") or last.get("reason") or "")
    checks = last.get("checks") or {}
    failed = [k for k in CHECK_CATEGORY if checks and not checks.get(k, False)]
    problems = list(last.get("problems") or [])
    repaired = len(hist) > 1
    interest = _interest(last.get("scores") or {})
    if "editor unavailable" in reason:
        cat = "editor_unavailable"
    elif "duration_outside_platform_limits" in problems or "duration outside" in reason:
        cat = "boundary"
    elif any(str(p).startswith("critical_unresolved") or p == "loop" for p in problems):
        cat = "subtitle_uncertainty"
    elif interest == 0:
        cat = "weak_moment"
    elif failed:
        cat = CHECK_CATEGORY[failed[0]]
    elif "repair was not possible" in reason or "requested repair" in reason:
        cat = "repair_not_applicable"
    else:
        cat = "editor_rejected"
    if repaired and cat not in ("editor_unavailable", "weak_moment"):
        cat = "repair_failed:" + cat
    return {"category": cat, "failed_checks": failed, "repaired": repaired,
            "near_pass": len(failed) == 1 and (interest or 0) >= 1 and not problems}


def gate(job: Job) -> dict[str, Any]:
    intel = _read((job.artifacts or {}).get("intel_report_path")) or {}
    review = _read((job.artifacts or {}).get("clip_review_path")) or {}
    rej = intel.get("editor_rejected") or []
    rows, cats = [], {}
    for r in rej:
        c = classify_record(r)
        cats[c["category"]] = cats.get(c["category"], 0) + 1
        hist = r.get("history") or []
        last = hist[-1] if hist else {}
        rows.append({"title": r.get("title") or "", "spans": r.get("spans"), **c,
                     "reason": str(r.get("reason") or last.get("reason") or "")[:400],
                     "scores": {k: (v.get("score") if isinstance(v, dict) else v)
                                for k, v in (last.get("scores") or {}).items()}})
    decisions = intel.get("decisions") or []
    ranking_out: dict[str, int] = {}
    for d in decisions:
        why = str(d.get("decision") or "")
        if why == "selected":
            continue
        key = "duplicate" if why.startswith("duplicate_of") else why
        ranking_out[key] = ranking_out.get(key, 0) + 1
    shipped = len(intel.get("shipped") or [])
    unev = cats.get("editor_unavailable", 0) + len(intel.get("editor_unreviewed") or [])
    real = [r for r in rows if r["category"] != "editor_unavailable"]
    if shipped:
        verdict = "shipped"
    elif rows and len(real) == 0:
        verdict = "editor_unavailable"           # the editor never judged the content
    elif any(r["near_pass"] or r["category"].startswith("repair") or r["category"] in
             ("missing_payoff", "bad_ending", "missing_context", "weak_hook", "pacing", "repair_not_applicable")
             for r in real):
        verdict = "repairable_moments_rejected"  # B: real moments failed on construction
    elif real:
        verdict = "no_publishable_moments"      # A: weak moments
    else:
        verdict = "no_candidates"
    return {"mode": intel.get("mode") or review.get("mode") or "", "candidates": len(intel.get("pool") or []),
            "judged_by_editor": shipped + len(rows), "shipped": shipped, "rejected": len(rows),
            "not_evaluated": unev, "repaired": sum(1 for r in rows if r["repaired"]) + (intel.get("gate") or {}).get(
                "shipped_after_repair", 0),
            "near_pass": sum(1 for r in rows if r["near_pass"]), "rejected_by_category": cats,
            "ranking_filtered": ranking_out, "verdict": verdict, "rejected_clips": rows,
            "forensics": _forensics(intel)}


def _forensics(intel: dict[str, Any]) -> dict[str, Any]:
    """The earliest real failure of every candidate (services/semantic/forensics) – also for old runs."""
    if not intel.get("pool") and not intel.get("decisions"):
        return {}
    try:
        from .semantic import forensics

        f = intel.get("forensics") or forensics.classify(intel)
        return {**f, "rows": (f.get("rows") or [])[:80]}
    except Exception:                                   # noqa: BLE001 – diagnostics never fail a page
        return {}


def profile(job: Job, *, internal: bool = False) -> dict[str, Any]:
    """internal=False (customer): timings only – model calls, tokens and cache are admin data."""
    arts = job.artifacts or {}
    src = float((arts.get("source_info") or {}).get("duration") or 0.0)
    with session_scope() as s:
        rows = s.query(StageTiming).filter(StageTiming.job_id == job.id).order_by(StageTiming.id).all()
        resources: dict[str, dict[str, float]] = {}
        for r in rows:
            d = resources.setdefault(r.stage, {"wall": 0.0, "cpu_thread": 0.0, "cpu_children": 0.0,
                                               "io_read_mb": 0.0, "io_write_mb": 0.0, "waiting": 0.0})
            wall, cpu, ch = float(r.seconds or 0), float(r.cpu_seconds or 0), float(r.child_cpu_seconds or 0)
            for k, v in (("wall", wall), ("cpu_thread", cpu), ("cpu_children", ch),
                         ("io_read_mb", float(r.io_read_mb or 0)), ("io_write_mb", float(r.io_write_mb or 0)),
                         ("waiting", max(0.0, wall - cpu - ch))):
                d[k] = round(d[k] + v, 2)
        stages: dict[str, dict[str, float]] = {}
        for r in rows:
            st = stages.setdefault(r.stage, {"seconds": 0.0, "runs": 0})
            st["seconds"] = round(st["seconds"] + float(r.seconds or 0), 1)
            st["runs"] += 1
        clips = s.query(Clip).filter(Clip.job_id == job.id).all()
        made = {"shorts": sum(1 for c in clips if c.kind != ClipKind.LONG
                              and c.status in (ClipStatus.READY, ClipStatus.NEEDS_REVIEW)),
                "long": sum(1 for c in clips if c.kind == ClipKind.LONG
                            and c.status in (ClipStatus.READY, ClipStatus.NEEDS_REVIEW)),
                "failed": sum(1 for c in clips if c.status == ClipStatus.FAILED)}
    total = round(sum(v["seconds"] for v in stages.values()), 1)
    subs: dict[str, dict[str, float]] = {}
    for run in arts.get("substage_timings") or []:
        for it in run.get("items") or []:
            d = subs.setdefault(str(it.get("name")), {"seconds": 0.0, "runs": 0, "media_seconds": 0.0})
            d["seconds"] = round(d["seconds"] + float(it.get("seconds") or 0), 1)
            d["runs"] += 1
            d["media_seconds"] = round(d["media_seconds"] + float(it.get("media_seconds") or 0), 1)
    intel = _read(arts.get("intel_report_path")) or {}
    usage = intel.get("usage") or {}
    by_task = {}
    for task, t in (usage.get("by_task") or {}).items():
        calls, cached = int(t.get("calls", 0)), int(t.get("cached", 0))
        by_task[task] = {"calls": calls, "cached": cached, "failures": int(t.get("failures", 0)),
                         "seconds": round(float(t.get("seconds", 0)), 1),
                         "cache_hit_rate": round(cached / (calls + cached), 3) if calls + cached else None}
    calls, cached = int(usage.get("calls", 0)), int(usage.get("cached", 0))
    m = arts.get("run_metrics") or {}
    t0 = m.get("generate_started")

    def since(k: str) -> Optional[float]:
        return round(m[k] - t0, 1) if t0 and m.get(k) else None
    items = [(f"stage:{k}", v["seconds"]) for k, v in stages.items()] + \
            [(f"sub:{k}", v["seconds"]) for k, v in subs.items()] + \
            [(f"semantic:{k}", float(v)) for k, v in (intel.get("timings") or {}).items()]
    top = sorted(items, key=lambda x: -x[1])[:8]
    out = {
        "source_seconds": src, "total_seconds": total, "rtf": round(total / src, 3) if src else None,
        "stages": {k: {**v, "rtf": round(v["seconds"] / src, 3) if src else None} for k, v in stages.items()},
        "substages": subs, "semantic_timings": intel.get("timings") or {},
        "model": {"calls": calls, "cached": cached, "failures": int(usage.get("failures", 0)),
                  "seconds": usage.get("seconds"), "input_tokens": usage.get("input_tokens"),
                  "output_tokens": usage.get("output_tokens"),
                  "cache_hit_rate": round(cached / (calls + cached), 3) if calls + cached else None,
                  "by_task": by_task},
        "stage_cache": intel.get("cache") or {},
        "milestones": {"time_to_first_short": since("first_short_at"), "time_to_all_shorts": since("all_shorts_at"),
                       "time_to_longform": since("longform_at")},
        "outputs": made, "top_bottlenecks": [{"name": n, "seconds": round(sec, 1)} for n, sec in top],
    }
    if internal:
        out["resources"] = resources               # wall / CPU / children CPU / disk / waiting per stage
        out["queue_seconds"] = round(float(job.queue_seconds or 0), 2)
        # the job's measured profile (util/profiler): ranked spans, processes, full-source decodes, caches
        from ..config import PATHS
        from ..util import profiler

        prof = profiler.load(PATHS.job_work_dir(job.id)).get("total") or {}
        out["profile"] = {"bottlenecks": prof.get("bottlenecks") or [],
                          "subprocesses": prof.get("subprocesses") or {},
                          "full_source_decodes": prof.get("full_source_decodes"),
                          "cache": prof.get("cache") or {}, "model_wait": prof.get("model_wait") or {}}
    else:
        out.pop("model", None)
        out.pop("stage_cache", None)
    return out
