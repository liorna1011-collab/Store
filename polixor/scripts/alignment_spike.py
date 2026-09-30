"""
Polixor – compare subtitle word-timing methods on YOUR real video.

Runs three versions of the word timings on the same transcript and the same
audio, and reports how often a subtitle word starts or ends in silence,
overlaps the previous word, or is too short / too long:

  1. raw        – the speech recogniser's own word times
  2. energy     – after the built-in repair (always on)
  3. forced     – forced alignment + repair (only if the optional
                  components are installed: backend/requirements-alignment.txt)

Optionally pass subtitles you corrected by hand (SRT exported from the
editor, then fixed in any subtitle tool) with --truth: the report then also
shows the average start/end error, in milliseconds, against your version.

    # a video you already processed in Polixor
    .venv/bin/python scripts/alignment_spike.py --project <project id>
    # or a 16 kHz mono WAV + a Polixor transcript JSON
    .venv/bin/python scripts/alignment_spike.py --audio audio.wav --transcript transcript.json
    # only the clips that were selected (faster), and compare to your SRT
    .venv/bin/python scripts/alignment_spike.py --project <id> --clips-only --truth fixed.srt

Nothing is changed in the project. Output: alignment-spike-<time>.json.
Messages are in English on purpose: the Windows console shows Hebrew reversed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

_TS = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")


def parse_srt(text: str) -> list[tuple[float, float, str]]:
    out = []
    for block in re.split(r"\n\s*\n", text.replace("\r", "")):
        lines = [ln for ln in block.strip().split("\n") if ln.strip()]
        for k, ln in enumerate(lines):
            m = _TS.search(ln)
            if m:
                g = [int(x) for x in m.groups()]
                a = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000.0
                b = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000.0
                out.append((a, b, " ".join(lines[k + 1:])))
                break
    return out


def compare_to_truth(transcript: Any, truth: list[tuple[float, float, str]]) -> dict[str, Any]:
    """
    For every hand-fixed subtitle: the first and last word of the transcript
    inside it (by text), and the start/end error of those words.
    """
    from polixor.services.transcript_correct import norm_text

    words = [w for s in transcript.segments for w in s.words]
    starts, ends = [], []
    for a, b, text in truth:
        toks = norm_text(text).split()
        if not toks:
            continue
        near = [w for w in words if w.end > a - 1.5 and w.start < b + 1.5]
        first = next((w for w in near if norm_text(w.text) == toks[0]), None)
        last = next((w for w in reversed(near) if norm_text(w.text) == toks[-1]), None)
        if first is not None:
            starts.append(abs(first.start - a))
        if last is not None:
            ends.append(abs(last.end - b))

    def ms(xs: list[float]) -> Optional[float]:
        return round(1000.0 * sum(xs) / len(xs), 1) if xs else None
    return {"matched_starts": len(starts), "matched_ends": len(ends),
            "mean_start_error_ms": ms(starts), "mean_end_error_ms": ms(ends)}


def _from_project(job_id: str, clips_only: bool):
    from polixor.config import PATHS
    from polixor.db import init_db, session_scope
    from polixor.models import Clip, Job
    from polixor.pipeline import _load_transcript
    from polixor.services import transcript_correct as tc

    PATHS.ensure()
    init_db()
    with session_scope() as s:
        job = s.get(Job, job_id)
        if job is None:
            raise SystemExit(f"Project {job_id} not found in this Polixor data folder.")
        arts = dict(job.artifacts or {})
        spans = [(c.source_start, c.source_end)
                 for c in s.query(Clip).filter(Clip.job_id == job_id)] if clips_only else None
    if not arts.get("audio_path") or not arts.get("transcript_path"):
        raise SystemExit("This project has no stored audio/transcript (was it analysed?).")
    tr = _load_transcript(Path(arts["transcript_path"]))
    if tr is not None and arts.get("corrections_path"):
        tr = tc.apply(tr, tc.load(Path(arts["corrections_path"])))
    return Path(arts["audio_path"]), tr, spans


def _from_files(audio: Path, transcript: Path):
    from polixor.pipeline import _load_transcript

    return audio, _load_transcript(transcript), None


def run(audio: Path, tr: Any, spans: Optional[list[tuple[float, float]]],
        truth: Optional[list[tuple[float, float, str]]]) -> dict[str, Any]:
    from polixor.services import forced_align
    from polixor.services import subtitle_align as sa

    def energy(a: float, b: float):
        return sa.energy_for(audio, a, b)

    media = sum(s.end - s.start for s in tr.segments
                if spans is None or any(s.end > a and s.start < b for a, b in spans))
    out: dict[str, Any] = {"audio": str(audio), "speech_seconds": round(media, 1),
                           "clips_only": spans is not None, "methods": {}}
    variants: list[tuple[str, Any]] = [("raw", None), ("energy", False)]
    st = forced_align.status()
    out["forced_alignment"] = st
    if st["available"]:
        variants.append(("forced", True))
    for name, use_fa in variants:
        t0 = time.time()
        if use_fa is None:
            result = tr
        else:
            aligner = forced_align.make_aligner(audio) if use_fa else None
            data = sa.align_transcript(tr, energy_fn=energy, spans=spans, aligner=aligner)
            result = sa.apply(tr, data)
        took = time.time() - t0
        m = sa.measure(result, energy, spans)
        m["sentences_removed_as_hallucination"] = \
            0 if use_fa is None else int(data["stats"].get("dropped", 0))
        m["seconds"] = round(took, 2)
        m["rtf"] = round(took / max(1e-6, media), 4)
        if truth:
            m["truth"] = compare_to_truth(result, truth)
        out["methods"][name] = m
    return out


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project")
    ap.add_argument("--audio")
    ap.add_argument("--transcript")
    ap.add_argument("--clips-only", action="store_true")
    ap.add_argument("--truth", help="hand-corrected SRT (times relative to the source video)")
    ap.add_argument("--out", default=".")
    a = ap.parse_args(argv)
    if a.project:
        audio, tr, spans = _from_project(a.project, a.clips_only)
    elif a.audio and a.transcript:
        audio, tr, spans = _from_files(Path(a.audio), Path(a.transcript))
    else:
        ap.print_help()
        return 2
    if tr is None or not tr.segments:
        print("No transcript with speech.")
        return 1
    truth = parse_srt(Path(a.truth).read_text("utf-8-sig")) if a.truth else None
    rep = run(audio, tr, spans, truth)
    print(f"\nSpeech analysed: {rep['speech_seconds']:.0f}s"
          + ("  (selected clips only)" if rep["clips_only"] else ""))
    if not rep["forced_alignment"]["available"]:
        print("Forced alignment: not installed (missing: "
              + ", ".join(rep["forced_alignment"]["missing"]) + ")")
    print(f"\n{'method':8s} {'words':>6s} {'start@silence':>14s} {'end@silence':>12s} "
          f"{'overlap':>8s} {'short':>6s} {'long':>5s} {'problems':>9s} {'time':>7s}")
    for name, m in rep["methods"].items():
        print(f"{name:8s} {m['words']:6d} {m['start_in_silence']:14d} {m['end_in_silence']:12d} "
              f"{m['overlap']:8d} {m['too_short']:6d} {m['too_long']:5d} "
              f"{m['problem_rate'] * 100:8.1f}% {m['seconds']:6.1f}s")
        if "truth" in m:
            t = m["truth"]
            print(f"         vs your SRT: start error {t['mean_start_error_ms']} ms, "
                  f"end error {t['mean_end_error_ms']} ms "
                  f"({t['matched_starts']}/{t['matched_ends']} subtitles matched)")
    dst = Path(a.out) / f"alignment-spike-{datetime.now():%Y%m%d-%H%M%S}.json"
    dst.write_text(json.dumps(rep, ensure_ascii=False, indent=2), "utf-8")
    print(f"\nSaved: {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
