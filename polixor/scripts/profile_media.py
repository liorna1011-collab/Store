"""
Polixor – performance profile on YOUR real media.

Runs the real pipeline (real transcription, analysis and rendering – no
fixtures) on one video and reports, per stage, the wall time and the
real-time factor (RTF = processing seconds / media seconds; 0.25 means four
times faster than real time). The report also lists the sub-stages that the
pipeline records (for example analyze → visual.faces) and the environment
(CPU, GPU, model, package versions), so numbers from different machines can
be compared.

    # macOS / Linux / Codespaces
    .venv/bin/python scripts/profile_media.py /path/to/video.mp4 --language he
    # Windows
    .venv\\Scripts\\python.exe scripts\\profile_media.py C:\\path\\video.mp4 --language he

Options:
    --language auto|he|en      transcription language (default: your setting)
    --model small|medium|...   Whisper model (default: your setting)
    --device auto|cpu|cuda     (default: your setting)
    --analyze-only             stop after analysis (no rendering)
    --out DIR                  where to write profile.json / profile.md
                               (default: ./polixor-profile-<time>)

The run uses your normal Polixor data folder (settings, downloaded models) and
appears in the job list as "Profile: <file name>". Messages are in English on
purpose: the Windows console shows Hebrew reversed.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

ANALYSIS_STAGES = ("download", "capture", "probe", "audio", "transcribe", "analyze", "select")
RENDER_STAGES = ("render_short", "render_long")


def _versions() -> dict[str, str]:
    from importlib import metadata

    out = {}
    for name in ("faster-whisper", "av", "ctranslate2", "numpy", "opencv-python-headless"):
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            out[name] = ""
    return out


def _peak_rss_mb() -> float | None:
    try:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(peak / (1024 * 1024 if sys.platform == "darwin" else 1024), 1)
    except Exception:
        return None


def _fmt_rtf(v: float | None) -> str:
    return "-" if v is None else f"{v:.3f}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("media", type=Path)
    ap.add_argument("--language", choices=("auto", "he", "en"))
    ap.add_argument("--model")
    ap.add_argument("--device", choices=("auto", "cpu", "cuda"))
    ap.add_argument("--analyze-only", action="store_true")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    media = args.media.expanduser().resolve()
    if not media.exists():
        print(f"File not found: {media}")
        return 2

    from polixor.config import PATHS, SETTINGS, AppSettings
    from polixor.db import init_db, session_scope
    from polixor.models import Clip, Job, JobStatus, RunScope, StageTiming, new_id
    from polixor.pipeline import run_job
    from polixor.services.transcribe import _cuda_available, pyav_status
    from polixor.util.ffmpeg import probe
    from polixor.util.timing import rtf

    PATHS.ensure()
    init_db()

    base = SETTINGS.get().to_dict()
    base["transcript_provider"] = "faster-whisper"      # real ASR, never the fixture
    if args.language:
        base["transcribe_language"] = args.language
    if args.model:
        base["whisper_model"] = args.model
    if args.device:
        base["whisper_device"] = args.device
    settings = AppSettings.from_dict(base)

    duration = float(probe(media).duration or 0.0)
    job_id = new_id()
    with session_scope() as s:
        s.add(Job(id=job_id, title=f"Profile: {media.name}", input_url="",
                  status=JobStatus.QUEUED, settings_snapshot=settings.to_dict(),
                  artifacts={"source_path": str(media)}, completed_stages=[],
                  run_scope=(RunScope.ANALYZE.value if args.analyze_only else RunScope.ALL.value)))

    print(f"Media:    {media}  ({duration / 60:.1f} min)")
    print(f"Whisper:  model={settings.whisper_model} device={settings.whisper_device} "
          f"compute={settings.whisper_compute_type} language={settings.transcribe_language}")
    print("Running the real pipeline - this can take a long time for long videos...\n")

    t0 = time.time()
    error = ""
    try:
        run_job(job_id, threading.Event())
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
        print(f"The pipeline failed: {error}")
    wall = time.time() - t0

    with session_scope() as s:
        job = s.get(Job, job_id)
        timings = [(t.stage, float(t.seconds), float(t.media_seconds))
                   for t in s.query(StageTiming).filter(StageTiming.job_id == job_id)
                   .order_by(StageTiming.id)]
        subs = [i for run in ((job.artifacts or {}).get("substage_timings") or [])
                for i in run.get("items", [])]
        notes = list((job.artifacts or {}).get("notes") or [])
        asr = {}
        tpath = (job.artifacts or {}).get("transcript_path")
        if tpath and Path(tpath).exists():
            data = json.loads(Path(tpath).read_text("utf-8"))
            segs = data.get("segments") or []
            asr = {"provider": data.get("provider", ""), "model": data.get("model", ""),
                   "language": data.get("language", ""), "segments": len(segs),
                   "words": sum(len(s.get("words") or []) for s in segs)}
        clips = s.query(Clip).filter(Clip.job_id == job_id).all()
        clip_info = [{"kind": c.kind.value, "title": c.title, "start": c.source_start,
                      "end": c.source_end, "score": c.score, "status": c.status.value}
                     for c in clips]

    stages = [{"stage": st, "seconds": round(sec, 2), "media_seconds": round(ms, 2),
               "rtf": rtf(sec, ms)} for st, sec, ms in timings]
    analysis_s = sum(sec for st, sec, _ in timings if st in ANALYSIS_STAGES)
    render_s = sum(sec for st, sec, _ in timings if st in RENDER_STAGES)
    render_media = sum(ms for st, _, ms in timings if st in RENDER_STAGES)
    # Without a real transcript (model not downloadable, no speech) the
    # "transcription" stage did no recognition, so it gets no RTF.
    asr_ran = asr.get("provider") == "faster-whisper" and asr.get("segments", 0) > 0
    trans = [(sec, ms) for st, sec, ms in timings if st == "transcribe"] if asr_ran else []
    summary = {
        "source_seconds": round(duration, 2),
        "wall_seconds": round(wall, 2),
        "transcription_rtf": rtf(*trans[0]) if trans else None,
        "analysis_rtf": rtf(analysis_s, duration),
        "render_rtf": rtf(render_s, render_media),
        "total_rtf": rtf(wall, duration),
        "peak_memory_mb": _peak_rss_mb(),
    }
    env = {
        "platform": platform.platform(), "python": platform.python_version(),
        "cpu_count": os.cpu_count(), "cuda": _cuda_available(),
        "whisper_model": settings.whisper_model, "whisper_device": settings.whisper_device,
        "whisper_compute_type": settings.whisper_compute_type,
        "transcribe_language": settings.transcribe_language,
        "versions": _versions(), "pyav_compatible": pyav_status()["compatible"],
    }
    report = {"media": str(media), "job_id": job_id, "error": error,
              "created": datetime.now().isoformat(timespec="seconds"),
              "summary": summary, "stages": stages, "substages": subs,
              "environment": env, "asr": asr, "asr_ran": asr_ran, "notes": notes, "clips": clip_info}

    out = args.out or Path.cwd() / f"polixor-profile-{datetime.now():%Y%m%d-%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "profile.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), "utf-8")

    lines = [f"# Polixor performance profile – {media.name}", "",
             f"- Source length: {duration / 60:.1f} min · wall time {wall / 60:.1f} min",
             f"- Transcription RTF: {_fmt_rtf(summary['transcription_rtf'])} · "
             f"analysis RTF: {_fmt_rtf(summary['analysis_rtf'])} · "
             f"render RTF: {_fmt_rtf(summary['render_rtf'])} · "
             f"**total RTF: {_fmt_rtf(summary['total_rtf'])}**",
             f"- Peak memory: {summary['peak_memory_mb']} MB",
             f"- Machine: {env['platform']}, {env['cpu_count']} CPUs, CUDA={env['cuda']}",
             f"- Whisper: {env['whisper_model']} / {env['whisper_device']} / "
             f"{env['whisper_compute_type']} / language={env['transcribe_language']}",
             f"- Versions: " + ", ".join(f"{k} {v}" for k, v in env["versions"].items() if v),
             ""]
    if not asr_ran:
        lines += ["**No speech recognition ran in this profile** (see the notes below), "
                  "so there is no transcription RTF and the analysis numbers do not "
                  "include transcript-based analysis.", ""]
    else:
        lines += [f"- Transcript: {asr['segments']} segments, {asr['words']} words, "
                  f"language={asr['language']}", ""]
    if error:
        lines += [f"**The pipeline failed:** {error}", ""]
    for n in notes:
        lines.append(f"> {n}")
    if notes:
        lines.append("")
    lines += ["| Stage | Seconds | Share | Media seconds | RTF |", "|---|---:|---:|---:|---:|"]
    for st in stages:
        share = st["seconds"] / wall * 100 if wall else 0
        lines.append(f"| {st['stage']} | {st['seconds']:.1f} | {share:.0f}% | "
                     f"{st['media_seconds']:.0f} | {_fmt_rtf(st['rtf'])} |")
    if subs:
        lines += ["", "## Sub-stages", "", "| Sub-stage | Seconds | Media seconds | RTF |",
                  "|---|---:|---:|---:|"]
        for i in subs:
            lines.append(f"| {i['name']} | {i['seconds']:.1f} | {i['media_seconds']:.0f} | "
                         f"{_fmt_rtf(rtf(i['seconds'], i['media_seconds']))} |")
    if clip_info:
        lines += ["", f"## Clips ({len(clip_info)})", ""]
        for c in clip_info:
            lines.append(f"- [{c['kind']}] {c['start']:.1f}–{c['end']:.1f}s · score "
                         f"{c['score']:.2f} · {c['status']} · {c['title']}")
    (out / "profile.md").write_text("\n".join(lines) + "\n", "utf-8")

    print("\n".join(lines))
    print(f"\nReport written to: {out}")
    return 1 if error else 0


if __name__ == "__main__":
    sys.exit(main())
