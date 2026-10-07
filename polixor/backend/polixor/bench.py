"""
Zero-paid-AI pipeline benchmark – what this machine does with a source, stage by stage.

  python -m polixor.bench --minutes 30            a synthetic long source made of the bundled test
                                                  video (or --source FILE; --fixture TRANSCRIPT.json)
  python -m polixor.bench --project <id>          the first --minutes of an uploaded project's source,
                                                  with the real local speech model (no AI calls)

The semantic editor is the deterministic stand-in (services/semantic/scripted.py,
POLIXOR_SEMANTIC_SCRIPTED=1); POLIXOR_PAID_AI=off is forced, so no paid call can leave the machine.
It runs the real pipeline (probe, audio, ASR, analysis, selection, editor gate, final transcript,
render, QC, long-form) in a separate data folder and reports:

  * wall time per stage and per sub-step, CPU, child-process CPU (FFmpeg), realtime factors
  * processes started and full-source decodes
  * cache hits / misses
  * time to first Short, to all Shorts, to long-form
  * API latency of a live server while the job runs (--server URL)

Results: <data>/bench/<stamp>.json (also shown in the admin view when run from there).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Optional

TEST_VIDEO = Path(os.environ.get("POLIXOR_TEST_VIDEO", "/home/claude/testdata/polixor_test_stream.mp4"))


def make_long_source(minutes: float, out_dir: Path, base: Path = TEST_VIDEO) -> tuple[Path, Optional[Path]]:
    """A long source made by repeating a short one (stream copy, seconds to make), plus its transcript."""
    from .util.ffmpeg import ffmpeg_bin, probe

    out_dir.mkdir(parents=True, exist_ok=True)
    dur = float(probe(base).duration or 90.0)
    loops = max(1, int(round(minutes * 60 / dur)))
    lst = out_dir / "loop.txt"
    lst.write_text("".join(f"file '{base}'\n" for _ in range(loops)), "utf-8")
    out = out_dir / f"bench_{loops}x.mp4"
    if not out.exists():
        subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-c", "copy", str(out)], check=True)
    fx = base.with_suffix(".transcript.json")
    tr = None
    if fx.exists():
        data = json.loads(fx.read_text("utf-8"))
        segs = []
        for k in range(loops):
            off = k * dur
            for s in data["segments"]:
                # a distinct word per repetition: the repeats are different moments, not duplicates
                words = [{**w, "start": w["start"] + off, "end": w["end"] + off} for w in s.get("words", [])]
                tag = f"פרק{k + 1}"
                if words:
                    words[-1] = {**words[-1], "text": words[-1]["text"] + f" {tag}"}
                segs.append({**s, "start": s["start"] + off, "end": s["end"] + off,
                             "text": f"{s['text']} {tag}", "words": words})
        tr = out_dir / f"bench_{loops}x.transcript.json"
        tr.write_text(json.dumps({"language": data.get("language", "he"), "segments": segs}, ensure_ascii=False),
                      "utf-8")
    return out, tr


def _latency_probe(url: str, stop: threading.Event, out: list[float]) -> None:
    import httpx

    with httpx.Client(timeout=10) as c:
        while not stop.is_set():
            t0 = time.time()
            try:
                c.get(url)
                out.append(time.time() - t0)
            except Exception:  # noqa: BLE001
                out.append(10.0)
            stop.wait(0.25)


def run(source: Path, *, fixture: Optional[Path] = None, shorts: int = 5, mode: str = "package",
        quality: str = "premium", server: str = "", label: str = "", routing: str = "balanced",
        hook_overlay: bool = False) -> dict[str, Any]:
    os.environ["POLIXOR_PAID_AI"] = "off"
    os.environ["POLIXOR_SEMANTIC_SCRIPTED"] = "1"
    if fixture:
        os.environ["POLIXOR_FIXTURE_TRANSCRIPT"] = str(fixture)
    from .config import PATHS, AppSettings, SETTINGS
    from .db import init_db, session_scope
    from .models import Clip, ClipKind, ClipStatus, Job, JobStatus, ProjectPhase, RunScope, StageTiming, new_id
    from .pipeline import run_job
    from .project_config import clamp_config
    from .services import hardware
    from .util import profiler
    from .util.ffmpeg import probe

    PATHS.ensure()
    init_db()
    base = SETTINGS.get().to_dict()
    base.update({"transcript_provider": "fixture" if fixture else "faster-whisper", "ai_mode": "cloud",
                 "ai_routing": routing, "editorial_hook_enabled": hook_overlay})
    cfg = clamp_config({"mode": mode, "clip_count": shorts, "studio": {"quality": quality, "auto_generate": mode,
                                                                          "editorial_overlay": hook_overlay}})
    jid = new_id()
    with session_scope() as s:
        s.add(Job(id=jid, title=label or f"bench {source.name}", input_url="", status=JobStatus.QUEUED,
                  phase=ProjectPhase.ANALYZING.value, run_scope=RunScope.ANALYZE.value,
                  settings_snapshot=AppSettings.from_dict(base).to_dict(), project_config=cfg, mode=mode,
                  artifacts={"source_path": str(source)}, completed_stages=[]))
    media = float(probe(source).duration or 0.0)
    lat: list[float] = []
    stop = threading.Event()
    th = None
    if server:
        th = threading.Thread(target=_latency_probe, args=(server.rstrip("/") + "/api/health", stop, lat), daemon=True)
        th.start()
    from .worker import MANAGER

    t0 = time.time()
    MANAGER._mark_started(jid)
    run_job(jid, threading.Event())                         # analysis
    MANAGER._finish(jid, JobStatus.COMPLETED, "")
    t_an = time.time()
    with session_scope() as s:
        j = s.get(Job, jid)
        cfg2 = dict(j.project_config or {})
        cfg2["studio"] = {**dict(cfg2.get("studio") or {}), "auto_generate": None}
        j.project_config, j.mode = cfg2, mode
        j.run_scope, j.phase, j.status = RunScope.GENERATE.value, ProjectPhase.GENERATING.value, JobStatus.QUEUED
    MANAGER._mark_started(jid)
    run_job(jid, threading.Event())                         # generation
    MANAGER._finish(jid, JobStatus.COMPLETED, "")           # a finished project, as the app leaves it
    t_end = time.time()
    stop.set()
    with session_scope() as s:
        j = s.get(Job, jid)
        m = dict((j.artifacts or {}).get("run_metrics") or {})
        clips = s.query(Clip).filter(Clip.job_id == jid).all()
        made = {"shorts": sum(1 for c in clips if c.kind != ClipKind.LONG and c.status != ClipStatus.FAILED),
                "long": sum(1 for c in clips if c.kind == ClipKind.LONG and c.status != ClipStatus.FAILED),
                "failed": sum(1 for c in clips if c.status == ClipStatus.FAILED)}
        stages = [(r.stage, r.seconds, r.media_seconds, r.cpu_seconds, r.child_cpu_seconds)
                  for r in s.query(StageTiming).filter(StageTiming.job_id == jid).order_by(StageTiming.id).all()]
    prof = profiler.load(PATHS.job_work_dir(jid)).get("total") or {}
    gs = m.get("generate_started")

    def since(k: str) -> Optional[float]:
        return round(m[k] - t0, 1) if m.get(k) else None
    lat.sort()
    out = {
        "label": label, "source": str(source), "media_seconds": round(media, 1), "machine": hardware.profile(),
        "tuning": hardware.tuning(), "asr": "fixture" if fixture else "local model", "ai": "scripted (no paid calls)",
        "wall_seconds": round(t_end - t0, 1), "analysis_seconds": round(t_an - t0, 1),
        "generation_seconds": round(t_end - t_an, 1),
        "rtf_total": round((t_end - t0) / media, 3) if media else None,
        "time_to_first_short": since("first_short_at"), "time_to_all_shorts": since("all_shorts_at"),
        "time_to_longform": since("longform_at"),
        "time_to_transcribed": since("transcribed_at"), "time_to_first_window": since("first_window_at"),
        "time_to_first_candidates": since("first_candidates_at"),
        "streaming": _streaming(jid),
        "ai": _ai(jid),
        "first_short_after_generation_start": round(m["first_short_at"] - gs, 1) if gs and m.get("first_short_at") else None,
        "outputs": made,
        "stages": [{"stage": a, "seconds": round(b, 2), "media": round(c, 1), "cpu": round(d or 0, 2),
                    "ffmpeg_cpu": round(e or 0, 2), "rtf": round(b / c, 4) if c else None} for a, b, c, d, e in stages],
        "bottlenecks": prof.get("bottlenecks", [])[:12], "subprocesses": prof.get("subprocesses"),
        "full_source_decodes": prof.get("full_source_decodes"), "cache": prof.get("cache"),
        "api_latency": ({"samples": len(lat), "p50": round(lat[len(lat) // 2], 3),
                         "p95": round(lat[int(len(lat) * 0.95) - 1], 3), "max": round(lat[-1], 3)} if lat else None),
        "project_id": jid,
    }
    d = PATHS.data / "bench"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{time.strftime('%Y%m%d-%H%M%S')}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), "utf-8")
    return out


def _ai(jid: str) -> dict[str, Any]:
    """Model calls / tokens / cost of the run, per model and per task (the stand-in's token counts)."""
    from .db import session_scope
    from .models import Job
    with session_scope() as s:
        job = s.get(Job, jid)
        runs = list((job.artifacts or {}).get("ai_runs") or [])
    tot: dict[str, Any] = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "usd": 0.0, "by_model": {},
                           "premium_calls": 0, "escalations": 0}
    for r in runs:
        for k in ("calls", "input_tokens", "output_tokens", "premium_calls", "escalations"):
            tot[k] += int(r.get(k) or 0)
        tot["usd"] = round(tot["usd"] + float(r.get("usd") or 0), 4)
        for m, u in (r.get("by_model") or {}).items():
            cur = tot["by_model"].setdefault(m, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "usd": 0.0})
            for k in ("calls", "input_tokens", "output_tokens"):
                cur[k] += int(u.get(k) or 0)
            cur["usd"] = round(cur["usd"] + float(u.get("usd") or 0), 4)
    tot["premium_share"] = round(tot["premium_calls"] / tot["calls"], 3) if tot["calls"] else 0.0
    return tot


def _streaming(jid: str) -> Optional[dict[str, Any]]:
    from .config import PATHS
    from . import streaming

    st = streaming.load_state(PATHS.job_work_dir(jid))
    if not st:
        return None
    return {"windows": st.get("windows"), "early_shorts": sum(1 for x in st.get("shorts") or [] if x.get("clip_id")),
            "model_calls": (st.get("usage") or {}).get("calls")}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--minutes", type=float, default=10)
    ap.add_argument("--source")
    ap.add_argument("--fixture")
    ap.add_argument("--shorts", type=int, default=5)
    ap.add_argument("--asr-rtf", type=float, default=0.0,
                    help="fixture ASR: seconds of work per second of media, in 5-minute chunks (0 = instant)")
    ap.add_argument("--no-stream", action="store_true", help="disable the streaming windows (before/after)")
    ap.add_argument("--routing", default="balanced", help="balanced | premium (the old call graph) | single")
    ap.add_argument("--hook-overlay", action="store_true", help="on-screen hook text on (adds the hooks call)")
    ap.add_argument("--mode", default="package")
    ap.add_argument("--server", default="")
    ap.add_argument("--label", default="")
    ap.add_argument("--data", help="data folder for the benchmark (default: a new temporary one)")
    a = ap.parse_args()
    if a.asr_rtf:
        os.environ["POLIXOR_FIXTURE_ASR_RTF"] = str(a.asr_rtf)
    if a.no_stream:
        os.environ["POLIXOR_STREAMING"] = "off"
    if not os.environ.get("POLIXOR_DATA_DIR"):
        os.environ["POLIXOR_DATA_DIR"] = a.data or tempfile.mkdtemp(prefix="polixor_bench_")
    if a.source:
        src, fx = Path(a.source), (Path(a.fixture) if a.fixture else None)
    else:
        src, fx = make_long_source(a.minutes, Path(os.environ["POLIXOR_DATA_DIR"]) / "bench_media")
    print(json.dumps(run(src, fixture=fx, shorts=a.shorts, mode=a.mode, server=a.server, label=a.label,
                         routing=a.routing, hook_overlay=a.hook_overlay), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

__all__ = ["make_long_source", "run", "shutil"]
