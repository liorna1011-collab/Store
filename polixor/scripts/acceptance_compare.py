"""
Polixor – BEFORE vs AFTER acceptance comparison on YOUR real livestream.

Runs the same video through two versions of Polixor – the version you
tested before the upgrade (BEFORE, git ref 0cc671b by default) and the
current one (AFTER) – exactly like the app does (through its own API), and
compares them:

  * processing time (analysis, clip generation, per stage, real-time factor)
  * re-export speed (after editing one subtitle line)
  * clip quality, measured with ONE yardstick for both versions: every clip
    of both runs is scored by the current engine on the same transcript
    (hook, context, payoff, starts/ends mid-sentence, passes the quality bar)
  * duplicates (the same story twice) and weak/random clips
  * subtitle timing (words that light up in silence, overlaps, too short or
    too long – also what the word highlight follows)
  * subtitle text accuracy – against a reference you correct by hand
    (--reference, an .srt/.vtt of a few minutes), otherwise in a blind
    side-by-side check in the report
  * the number of genuinely usable clips: YOU decide, in a blind review page
    (clips of both versions mixed, labels hidden until you press "Reveal")

The automatic numbers are a proxy. The acceptance question is the blind
review: "Would you post this clip?".

Usage (from the Polixor folder, with the same Python you run Polixor with):

    .venv/bin/python scripts/acceptance_compare.py --media /path/to/livestream.mp4 --language he
    # options:
    #   --before-ref 0cc671b     git ref of the BEFORE version (needs a git clone)
    #   --before-src DIR         or a folder with the old Polixor (unzipped)
    #   --only after|before      run just one side (the other is read from --out)
    #   --settings-from FILE     use your own settings.json for both runs
    #   --reference FILE.srt     hand-corrected subtitles for text accuracy
    #   --out DIR                where to put everything (default: acceptance_compare_<date>)

    (Windows: .venv\\Scripts\\python.exe scripts\\acceptance_compare.py ...)

A one-hour livestream takes as long as it takes in the app – the BEFORE run
took hours for you last time; use --only after to rerun just the new
version later. Nothing is uploaded anywhere: each version gets its own data
folder inside --out, and the video is linked, not copied. Messages are in
English on purpose: the Windows console shows Hebrew reversed.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
DEFAULT_BEFORE = "0cc671b"
HEADERS = {"X-Polixor-Request": "1", "X-Polixor-Lang": "en", "Content-Type": "application/json"}


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ==========================================================================
# 1. running one version through its API
# ==========================================================================
def _req(base: str, method: str, path: str, body: Any = None, timeout: float = 60.0) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{method} {path} -> {exc.code}: {exc.read()[:400]!r}") from exc
    return json.loads(raw) if raw else None


class Server:
    """Starts one Polixor version (uvicorn) on its own data folder."""

    def __init__(self, backend_dir: Path, data_dir: Path, port: int, env_extra: dict[str, str]):
        self.backend_dir, self.data_dir, self.port = backend_dir, data_dir, port
        self.env = {**os.environ, "POLIXOR_DATA_DIR": str(data_dir), "POLIXOR_SCHEDULER": "0",
                    "PYTHONPATH": str(backend_dir), **env_extra}
        self.env.pop("POLIXOR_ACCESS_PASSWORD", None)
        self.proc: Optional[subprocess.Popen] = None
        self.base = f"http://127.0.0.1:{port}"

    def __enter__(self) -> str:
        logf = open(self.data_dir / "server.log", "ab")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "polixor.main:app", "--host", "127.0.0.1",
             "--port", str(self.port), "--log-level", "warning"],
            cwd=str(self.backend_dir), env=self.env, stdout=logf, stderr=logf)
        for _ in range(240):
            if self.proc.poll() is not None:
                raise RuntimeError(f"server exited – see {self.data_dir / 'server.log'}")
            try:
                urllib.request.urlopen(self.base + "/api/health", timeout=2)
                return self.base
            except Exception:                           # noqa: BLE001
                time.sleep(0.5)
        raise RuntimeError("server did not start")

    def __exit__(self, *exc: Any) -> None:
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def _link_media(media: Path, sources: Path) -> str:
    sources.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^\w.\-]+", "_", media.name)
    dest = sources / name
    if not dest.exists():
        try:
            dest.symlink_to(media.resolve())
        except OSError:
            try:
                os.link(media, dest)
            except OSError:
                log(f"  copying the video (links not allowed here): {media.stat().st_size / 1e9:.1f} GB")
                shutil.copy2(media, dest)
    return name


def _wait_phase(base: str, pid: str, target: str, label: str) -> dict[str, Any]:
    last = 0.0
    while True:
        p = _req(base, "GET", f"/api/projects/{pid}")
        # Status decides: right after a retry the phase still says "failed" until the
        # pipeline starts, while the status is already "queued".
        if p.get("status") in ("failed", "cancelled"):
            err = p.get("error") or {}
            raise RuntimeError(f"{label}: {err.get('code', '')} {err.get('message', '')}")
        if p.get("phase") == target and p.get("status") not in ("queued", "running"):
            return p
        if time.time() - last > 60:
            log(f"  {label}: {p.get('phase')} / {p.get('status')} "
                f"{round(100 * float(p.get('overall_progress') or 0))}%")
            last = time.time()
        time.sleep(3)


def _media_seconds(media: Path) -> float:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=nw=1:nk=1", str(media)],
                             capture_output=True, text=True, timeout=60)
        return float(out.stdout.strip())
    except Exception:                                   # noqa: BLE001
        return 0.0


def _db_rows(data_dir: Path, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    con = sqlite3.connect(str(data_dir / "polixor.db"))
    con.row_factory = sqlite3.Row
    try:
        return con.execute(sql, args).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()


# --------------------------------------------------------------------------
# Resuming an interrupted BEFORE run from the old version's own checkpoint
# --------------------------------------------------------------------------
# The old version (0cc671b) saves a checkpoint after each stage: the job's
# completed_stages plus the files in its artifacts. Its own retry endpoint
# (POST /api/jobs/<id>/retry, without from_start) re-runs only what is not
# completed: probe (seconds), audio is skipped when audio16k.wav exists, the
# saved transcript is reused, and an interrupted analysis runs again from the
# start of that stage (the old analysis saves nothing until it finishes).
# NOTE: the old POST /api/projects/<id>/analyze must NOT be used to resume – it
# deletes the transcript and resets every checkpoint.
#
# Before reusing anything, inspect_before() proves on a copy of the database
# (the originals are not opened for writing) that each reused file is complete
# and belongs to this video and this commit. The old code itself would accept
# any audio16k.wav over 1 KB, so a truncated file is refused here.

ANALYZE_SUBSTEPS = ((0.30, "audio features"), (0.35, "silences"), (0.65, "video frames (visual analysis)"),
                    (0.94, "layout / facecam detection"), (1.01, "building the timeline and saving"))
OLD_WEIGHTS = {"probe": 1.0, "audio": 4.0, "transcribe": 26.0, "analyze": 16.0}


def _sha256(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def wav_info(path: Path) -> dict[str, Any]:
    """Reads the WAV header and checks the data chunk is fully on disk."""
    import struct

    info: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        return {**info, "valid": False, "problem": "missing"}
    size = path.stat().st_size
    info["bytes"] = size
    with open(path, "rb") as f:
        head = f.read(4096)
    if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
        return {**info, "valid": False, "problem": "not a WAV file"}
    pos, fmt, data_off, data_size = 12, None, None, None
    while pos + 8 <= len(head):
        cid, clen = head[pos:pos + 4], struct.unpack("<I", head[pos + 4:pos + 8])[0]
        if cid == b"fmt ":
            fmt = struct.unpack("<HHIIHH", head[pos + 8:pos + 24])
        elif cid == b"data":
            data_off, data_size = pos + 8, clen
            break
        pos += 8 + clen + (clen & 1)
    if fmt is None or data_off is None:
        return {**info, "valid": False, "problem": "WAV header incomplete"}
    _, channels, rate, _, _, bits = fmt
    frame = channels * bits // 8 or 1
    on_disk = size - data_off
    info.update({"channels": channels, "rate": rate, "bits": bits,
                 "header_data_bytes": data_size, "data_bytes_on_disk": on_disk,
                 "seconds": round(min(data_size, on_disk) / (rate * frame), 2) if rate else 0.0})
    if data_size in (0, 0xFFFFFFFF) or data_size > on_disk:
        # ffmpeg writes the final sizes only when it finishes; a killed extraction
        # leaves a placeholder or a header larger than the data
        return {**info, "valid": False, "problem": "audio extraction did not finish (header/data mismatch)"}
    info["valid"] = True
    return info


def transcript_info(path: Path, expected_provider: str = "faster-whisper",
                    allow_fixture: bool = False) -> dict[str, Any]:
    info: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        return {**info, "valid": False, "problem": "missing"}
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        return {**info, "valid": False, "problem": f"not readable JSON ({type(exc).__name__}) – write was cut off"}
    segs = data.get("segments") or []
    words = sum(len(s.get("words") or []) for s in segs)
    info.update({"bytes": path.stat().st_size, "provider": data.get("provider"), "model": data.get("model"),
                 "language": data.get("language"), "note": data.get("note") or "",
                 "duration": float(data.get("duration") or 0.0), "segments": len(segs), "words": words,
                 "first_start": round(float(segs[0]["start"]), 2) if segs else None,
                 "last_end": round(float(segs[-1]["end"]), 2) if segs else None})
    problems = []
    if not segs:
        problems.append("no speech segments")
    if (data.get("provider") or "") != expected_provider:
        problems.append(f"made by {data.get('provider')!r}, but the job was set to use "
                        f"{expected_provider!r} (a fallback, not the configured transcription)")
    # the old version writes a note only when transcription did NOT really run
    # (disabled, model unavailable → fallback, or the offline test loader)
    if data.get("note") and not (allow_fixture and data.get("provider") == "fixture"):
        problems.append(f"transcriber note: {data.get('note')}")
    info["problems"] = problems
    info["valid"] = not problems
    return info


def _copy_db(data: Path, dest: Path) -> Path:
    """Copies polixor.db with its -wal/-shm so the originals are never opened for writing."""
    dest.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        src = data / f"polixor.db{suffix}"
        if src.exists():
            shutil.copy2(src, dest / src.name)
    return dest


def _enum(v: Any) -> str:
    return str(v or "").split(".")[-1].lower()


def _old_progress_place(stage: str, stage_progress: float, overall: float, completed: list[str]) -> str:
    if stage == "analyze":
        for limit, name in ANALYZE_SUBSTEPS:
            if stage_progress < limit:
                return f"analyze stage at {stage_progress * 100:.0f}% – {name}"
    return f"{stage or 'unknown'} stage at {stage_progress * 100:.0f}%"


def inspect_before(out: Path, media: Path, before_src: Optional[Path],
                   allow_fixture: bool = False) -> dict[str, Any]:
    """Read-only checkpoint report for an interrupted BEFORE run. Changes nothing."""
    import tempfile

    data = out / "before_data"
    rep: dict[str, Any] = {"data_dir": str(data), "problems": [], "warnings": [], "resumable": False}
    if (out / "before.json").exists():
        rep["problems"].append("before.json already exists – the BEFORE run finished; nothing to resume")
        return rep
    if not (data / "polixor.db").exists():
        rep["problems"].append(f"no database at {data / 'polixor.db'}")
        return rep
    # ---- exact BEFORE commit ----
    if before_src is not None:
        top = subprocess.run(["git", "-C", str(before_src), "rev-parse", "HEAD"], capture_output=True, text=True)
        want = subprocess.run(["git", "-C", str(ROOT), "rev-parse", f"{DEFAULT_BEFORE}^{{commit}}"],
                              capture_output=True, text=True)
        dirty = subprocess.run(["git", "-C", str(before_src), "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True)
        rep["before_commit"] = top.stdout.strip()
        if top.returncode or top.stdout.strip() != want.stdout.strip():
            rep["problems"].append(f"BEFORE code is at {top.stdout.strip() or '?'}, expected {want.stdout.strip()}")
        if dirty.stdout.strip():
            rep["problems"].append("BEFORE code has local modifications:\n" + dirty.stdout.strip())
    # ---- the job, from a copy of the database ----
    tmp = Path(tempfile.mkdtemp(prefix="pxinspect_"))
    try:
        copy = _copy_db(data, tmp)
        con = sqlite3.connect(str(copy / "polixor.db"))
        con.row_factory = sqlite3.Row
        jobs = con.execute("SELECT id, title, status, stage, stage_progress, overall_progress, message, "
                           "phase, run_scope, completed_stages, artifacts, settings_snapshot FROM jobs").fetchall()
        rep["jobs_in_db"] = len(jobs)
        if len(jobs) != 1:
            rep["problems"].append(f"expected exactly 1 project in the BEFORE database, found {len(jobs)}")
            return rep
        j = jobs[0]
        completed = json.loads(j["completed_stages"] or "[]")
        arts = json.loads(j["artifacts"] or "{}")
        rep["job"] = {"id": j["id"], "title": j["title"], "status": _enum(j["status"]), "phase": j["phase"],
                      "run_scope": j["run_scope"], "stage": _enum(j["stage"]),
                      "stage_progress": round(float(j["stage_progress"] or 0), 4),
                      "overall_progress": round(float(j["overall_progress"] or 0), 4),
                      "message": j["message"], "completed_stages": completed}
        rep["active_at_stop"] = _old_progress_place(_enum(j["stage"]), float(j["stage_progress"] or 0),
                                                    float(j["overall_progress"] or 0), completed)
        timings = [dict(r) for r in con.execute(
            "SELECT stage, seconds, media_seconds FROM stage_timings WHERE job_id=? ORDER BY id", (j["id"],))]
        rep["stage_timings"] = timings
        seg_rows = con.execute("SELECT COUNT(*) FROM transcript_segments WHERE job_id=?", (j["id"],)).fetchone()[0]
        con.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    # ---- the video ----
    src = Path(arts.get("source_path") or "")
    info = arts.get("source_info") or {}
    duration = float(info.get("duration") or 0.0)
    rep["source"] = {"path": str(src), "duration": duration, "size": info.get("size")}
    try:
        same = src.exists() and os.path.samefile(src, media)
    except OSError:
        same = False
    if not same:
        rep["problems"].append(f"the job's video {src} is not the file given with --media ({media})")
    elif info.get("size") and int(info["size"]) != media.stat().st_size:
        rep["problems"].append("the video's size differs from what the BEFORE run recorded")
    # ---- what completed ----
    if _enum(j["status"]) in ("completed",) or j["phase"] in ("configure", "done"):
        rep["warnings"].append(f"job status is {_enum(j['status'])} / phase {j['phase']}")
    if (j["run_scope"] or "") != "analyze" or "analyze" in completed:
        rep["problems"].append("only an interrupted ANALYSIS can be resumed by this tool "
                               f"(run_scope={j['run_scope']}, completed={completed}); generation must not "
                               "be stitched from partial outputs – tell Claude")
    for key in ("audio_path", "transcript_path", "source_path"):
        val = arts.get(key)
        if val and not Path(os.path.abspath(val)).is_relative_to(data.resolve()) and \
                not Path(os.path.abspath(val)).is_relative_to(Path(os.path.abspath(data))):
            rep["problems"].append(f"the job's {key} ({val}) is outside this BEFORE data folder ({data}) – "
                                   "it may belong to another run")
    rep["audio"] = wav_info(Path(arts.get("audio_path") or (data / "work" / j["id"] / "audio16k.wav")))
    if "audio" in completed:
        if not rep["audio"]["valid"]:
            rep["problems"].append(f"audio16k.wav is not reusable: {rep['audio'].get('problem')}")
        elif duration and abs(rep["audio"]["seconds"] - duration) > max(1.5, 0.002 * duration):
            rep["problems"].append(f"audio16k.wav covers {rep['audio']['seconds']:.1f}s but the video is "
                                   f"{duration:.1f}s")
    elif rep["audio"].get("exists"):
        rep["problems"].append("audio16k.wav exists but its stage never completed – the old code would reuse "
                               "a possibly partial file; refusing")
    tpath = Path(arts.get("transcript_path") or (data / "work" / j["id"] / "transcript.json"))
    snapshot = json.loads(j["settings_snapshot"] or "{}")
    rep["transcript"] = transcript_info(tpath, snapshot.get("transcript_provider") or "faster-whisper",
                                        allow_fixture=allow_fixture)
    rep["transcript"]["db_segments"] = seg_rows
    if "transcribe" in completed:
        t = rep["transcript"]
        if not t["valid"]:
            rep["problems"].append("transcript.json is not reusable: " + "; ".join(t.get("problems") or [t.get("problem", "")]))
        else:
            if t["segments"] != seg_rows:
                rep["problems"].append(f"transcript.json has {t['segments']} segments but the database has {seg_rows}")
            if duration and t["duration"] and abs(t["duration"] - duration) > max(2.0, 0.003 * duration):
                rep["problems"].append(f"transcript duration {t['duration']:.1f}s differs from the video {duration:.1f}s")
            if duration and t["last_end"] is not None and t["last_end"] > duration + 1.0:
                rep["problems"].append("transcript runs past the end of the video")
    elif rep["transcript"].get("exists"):
        rep["problems"].append("transcript.json exists but transcription never completed – refusing to reuse it")
    # ---- timing records for the stages that will not run again ----
    have = {t["stage"] for t in timings}
    missing = [st for st in ("audio", "transcribe") if st in completed and st not in have]
    if missing:
        rep["warnings"].append(f"no timing record for completed stage(s) {missing}: BEFORE analysis time "
                               "will be reported as unknown instead of estimated")
    stale = [p.name for p in (data / "work" / j["id"]).glob("*")
             if p.name not in ("audio16k.wav", "transcript.json")] if (data / "work" / j["id"]).exists() else []
    if stale:
        rep["warnings"].append(f"files from the interrupted analysis ({', '.join(sorted(stale))}) are not "
                               "referenced by the checkpoint and are not reused; the analysis rewrites them")
    rep["reuse"] = [st for st in ("probe", "audio", "transcribe") if st in completed]
    rep["rerun"] = (["probe (a few seconds, re-reads the file header)"] if "probe" in completed else ["probe"]) + \
        ([] if "audio" in completed else ["audio extraction"]) + \
        ([] if "transcribe" in completed else ["transcription"]) + \
        ["analysis – the whole stage from its start (the old version saves analysis only when it finishes)",
         "clip generation (selection + rendering)", "re-export timing"]
    rep["resumable"] = not rep["problems"]
    return rep


def print_inspection(rep: dict[str, Any]) -> None:
    j = rep.get("job") or {}
    print("\n=== BEFORE checkpoint inspection (read-only) ===")
    if j:
        print(f"Project/job: {j['id']}  status={j['status']}  phase={j['phase']}  scope={j['run_scope']}")
        print(f"Progress when it stopped: {j['overall_progress'] * 100:.0f}% overall; {rep.get('active_at_stop')}")
        print(f"1. Completed stages: {', '.join(j['completed_stages']) or 'none'}")
        print(f"2. Active stage at the stop: {rep.get('active_at_stop')}")
        for t in rep.get("stage_timings") or []:
            print(f"     recorded: {t['stage']:<11} {t['seconds']:>9.1f} s  (media {t['media_seconds']:.0f} s)")
    print(f"3. Resumable job in polixor.db: {'yes' if rep['resumable'] else 'NO'} "
          f"({rep.get('jobs_in_db', 0)} job(s) in the database)")
    t = rep.get("transcript") or {}
    if t:
        print(f"4. transcript.json: {'COMPLETE' if t.get('valid') else 'NOT usable'} – "
              f"{t.get('segments', 0)} segments / {t.get('words', 0)} words, provider {t.get('provider')}, "
              f"model {t.get('model')}, language {t.get('language')}, duration {t.get('duration')}, "
              f"last speech at {t.get('last_end')} s, database rows {t.get('db_segments')}"
              + (f"; problems: {t.get('problems') or t.get('problem')}" if not t.get('valid') else ""))
    a = rep.get("audio") or {}
    if a:
        print(f"5. audio16k.wav: {'COMPLETE' if a.get('valid') else 'NOT usable'} – {a.get('bytes', 0) / 1e6:.1f} MB, "
              f"{a.get('rate')} Hz x{a.get('channels')}, {a.get('seconds')} s of audio "
              f"(video {rep.get('source', {}).get('duration')} s)"
              + (f"; problem: {a.get('problem')}" if not a.get('valid') else ""))
    print("6. Would rerun: " + ("; ".join(rep.get("rerun") or []) if rep["resumable"] else "nothing yet – see problems"))
    if rep.get("reuse"):
        print("   Reused as is: " + ", ".join(rep["reuse"]))
    for w in rep["warnings"]:
        print(f"   note: {w}")
    for p in rep["problems"]:
        print(f"   PROBLEM: {p}")
    print("RESULT: " + ("safe to resume from the old version's own checkpoint." if rep["resumable"]
                        else "NOT safe to resume – nothing was changed."))


def _backup_before(data: Path) -> Path:
    dest = data / f"_backup_before_resume_{datetime.now():%Y%m%d_%H%M%S}"
    _copy_db(data, dest)
    for name in ("settings.json", "server.log"):
        if (data / name).exists():
            shutil.copy2(data / name, dest / name)
    return dest


def _stage_max(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Seconds per stage: the longest record (a skipped stage on resume records ~0 s)."""
    out: dict[str, float] = {}
    for r in rows:
        out[r["stage"]] = max(out.get(r["stage"], 0.0), float(r["seconds"] or 0.0))
    return out


def run_version(label: str, backend_dir: Path, media: Path, out: Path, *, language: str,
                settings: dict[str, Any], port: int, env_extra: dict[str, str],
                resume: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    data = out / f"{label}_data"
    data.mkdir(parents=True, exist_ok=True)
    token = _link_media(media, data / "sources")
    resumed_info: Optional[dict[str, Any]] = None
    if resume is None:
        (data / "settings.json").write_text(json.dumps(settings, ensure_ascii=False, indent=1), "utf-8")
    else:
        # the job keeps the settings snapshot it was created with; settings.json is left as is
        backup = _backup_before(data)
        reused = {k: Path(resume[k]["path"]) for k in ("audio", "transcript")
                  if resume.get(k, {}).get("valid")}
        resumed_info = {"from_checkpoint": resume["job"]["completed_stages"],
                        "stopped_at": resume.get("active_at_stop"), "backup": str(backup),
                        "reused": {k: {"path": str(p), "sha256": _sha256(p)} for k, p in reused.items()}}
        log(f"  {label}: database backed up to {backup}")
    log(f"{label.upper()}: starting Polixor from {backend_dir}")
    with Server(backend_dir, data, port, env_extra) as base:
        t0 = time.time()
        if resume is None:
            proj = _req(base, "POST", "/api/projects", {
                "source": {"type": "upload", "upload_token": token}, "title": f"{label}: {media.name}",
                "ui_language": "en", "content_language": language})
            pid = proj["id"]
        else:
            pid = resume["job"]["id"]
            state = _req(base, "GET", f"/api/projects/{pid}")
            # the old server marks a job that was running when it died as interrupted;
            # its own retry continues from the last completed stage (from_start=false)
            if state.get("status") not in ("queued", "running"):
                _req(base, "POST", f"/api/jobs/{pid}/retry?from_start=false")
            log(f"  {label}: resumed project {pid} from its checkpoint "
                f"(completed: {', '.join(resume['job']['completed_stages'])})")
        analysed = _wait_phase(base, pid, "configure", f"{label} analysis")
        t_an = time.time() - t0
        log(f"  {label}: analysis done in {t_an / 60:.1f} min")
        t1 = time.time()
        _req(base, "POST", f"/api/projects/{pid}/generate", {"mode": "short"})
        _wait_phase(base, pid, "done", f"{label} clip generation")
        t_gen = time.time() - t1
        log(f"  {label}: clips done in {t_gen / 60:.1f} min")
        clips = _req(base, "GET", f"/api/projects/{pid}/clips")
        for c in clips:
            c["cues"] = _req(base, "GET", f"/api/clips/{c['id']}/cues")
        reexport = _measure_reexport(base, clips)
    rows = _db_rows(data, "SELECT artifacts FROM jobs WHERE id=?", (pid,))
    arts = json.loads(rows[0]["artifacts"] or "{}") if rows else {}
    files = {r["id"]: r["file_path"] for r in _db_rows(data, "SELECT id, file_path FROM clips WHERE job_id=?", (pid,))}
    stages = [dict(r) for r in _db_rows(
        data, "SELECT stage, seconds, media_seconds FROM stage_timings WHERE job_id=? ORDER BY id", (pid,))]
    per_stage = _stage_max(stages)
    an_stages = [st for st in ("probe", "audio", "transcribe", "analyze") if st in per_stage]
    analysis_stage_seconds = round(sum(per_stage[st] for st in an_stages), 1)
    if resumed_info is not None:
        for k, v in resumed_info["reused"].items():
            v["unchanged_after_resume"] = _sha256(Path(v["path"])) == v["sha256"]
            if not v["unchanged_after_resume"]:
                log(f"  WARNING: {k} changed during the resumed run – the BEFORE result is not valid")
        needed = [st for st in ("probe", "audio", "transcribe", "analyze") if st in resume["job"]["completed_stages"]
                  or st == "analyze"]
        if all(st in per_stage for st in needed):
            # an honest analysis time: the recorded time of each stage, once; the interrupted
            # partial analysis attempt is not counted, and nothing is estimated
            t_an = analysis_stage_seconds
            resumed_info["analysis_time_basis"] = "sum of recorded stage times (interrupted attempt excluded)"
        else:
            t_an = None
            resumed_info["analysis_time_basis"] = f"unknown – missing timing records for {[st for st in needed if st not in per_stage]}"
    result = {
        "label": label, "project_id": pid, "backend": str(backend_dir), "data_dir": str(data),
        "media": str(media),
        "media_seconds": _media_seconds(media) or float((analysed.get("source") or {}).get("duration") or 0),
        "analysis_seconds": round(t_an, 1) if t_an is not None else None,
        "generation_seconds": round(t_gen, 1),
        "total_seconds": round(t_an + t_gen, 1) if t_an is not None else None,
        "analysis_stage_seconds": analysis_stage_seconds, "stage_seconds": per_stage,
        "resumed": resumed_info, "stages": stages, "reexport": reexport,
        "artifacts": arts,
        "clips": [{"id": c["id"], "title": c.get("title", ""), "status": c.get("status"),
                   "start": float(c.get("source_start") or 0), "end": float(c.get("source_end") or 0),
                   "duration": float(c.get("duration") or 0), "score": c.get("score"),
                   "reason": c.get("reason", ""), "file": files.get(c["id"], ""),
                   "cues": [{"start": q.get("start"), "end": q.get("end"), "text": q.get("text", "")}
                            for q in c.get("cues") or []]}
                  for c in clips if c.get("kind", "short") == "short"],
    }
    (out / f"{label}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), "utf-8")
    total = result["total_seconds"]
    log(f"  {label}: {len(result['clips'])} clips, total "
        + (f"{total / 60:.1f} min" if total is not None else "unknown (see resume note)"))
    return result


def _measure_reexport(base: str, clips: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Edits one subtitle line and re-exports that clip – the everyday fix-a-typo loop."""
    for c in clips:
        cues = c.get("cues") or []
        if c.get("status") not in ("ready", "needs_review") or not cues:
            continue
        edited = [{"id": q.get("id"), "start": q["start"], "end": q["end"], "text": q["text"]} for q in cues]
        edited[0]["text"] = edited[0]["text"].rstrip() + " !"
        _req(base, "PUT", f"/api/clips/{c['id']}/cues", edited)
        t0 = time.time()
        _req(base, "POST", f"/api/clips/{c['id']}/reexport", {}, timeout=3600)
        sec = time.time() - t0
        return {"clip_id": c["id"], "seconds": round(sec, 2), "clip_seconds": float(c.get("duration") or 0),
                "rtf": round(sec / max(0.1, float(c.get("duration") or 0)), 3)}
    return None


# ==========================================================================
# 2. one yardstick for both runs (the current engine, the same transcript)
# ==========================================================================
def load_transcript(arts: dict[str, Any], *, effective: bool = True):
    """The run's transcript; with its own corrections and timing fixes when it has them."""
    from polixor.pipeline import _load_transcript

    path = arts.get("transcript_path")
    if not path or not Path(path).exists():
        return None
    tr = _load_transcript(Path(path))
    if tr is None or not effective:
        return tr
    try:
        if arts.get("corrections_path") and Path(arts["corrections_path"]).exists():
            from polixor.services import transcript_correct as tc
            tr = tc.apply(tr, tc.load(Path(arts["corrections_path"])))
        if arts.get("timing_path") and Path(arts["timing_path"]).exists():
            from polixor.services import subtitle_align as sa
            tr = sa.apply(tr, sa.load(Path(arts["timing_path"])))
    except Exception as exc:                            # noqa: BLE001
        log(f"  (could not apply corrections/timing: {exc})")
    return tr


class Yardstick:
    """Scores any [start, end] span with the current clip engine, on one transcript."""

    def __init__(self, transcript, timeline, language: str, threshold: Optional[float] = None,
                 min_d: float = 15.0):
        from polixor.services.clip_intel.score import pick_threshold
        from polixor.services.clip_intel.units import build_units
        from polixor.services.scoring import Timeline

        self.tl = timeline if timeline is not None else Timeline()
        self.units = build_units(transcript, self.tl, language) if transcript is not None else []
        self.threshold = pick_threshold(threshold)
        self.min_d = min_d

    def span(self, start: float, end: float) -> dict[str, Any]:
        from polixor.services.clip_intel.score import score_proposal
        from polixor.services.clip_intel.story import Proposal, payoff_potential

        units = self.units
        inside = [i for i, u in enumerate(units) if u.end > start + 0.15 and u.start < end - 0.15]
        if not inside:
            return {"empty": True, "passed": False, "text": ""}
        h, e = inside[0], inside[-1]
        best, pi = -1.0, e
        for i in inside:
            pv, _ = payoff_potential(units[i], units[i + 1] if i + 1 < len(units) else None)
            if pv >= best:                              # ties → the later sentence
                best, pi = pv, i
        p = Proposal(hook_idx=h, payoff_idx=pi, end_idx=e, start=start, end=end)
        sc = score_proposal(p, units, self.tl, min_d=self.min_d, threshold=self.threshold)
        first, last = units[h], units[e]
        prev = units[h - 1] if h else None
        starts_mid = (first.start < start - 0.35) or (
            prev is not None and not prev.ends_sentence and start - prev.end < 0.3)
        ends_mid = (last.end > end + 0.35) or not last.ends_sentence
        return {
            "empty": False, "passed": bool(sc.passed), "final": round(float(sc.final), 3),
            "rejection": sc.rejection, "hook": sc.components.get("hook", 0.0),
            "payoff": sc.components.get("payoff", 0.0),
            "context": sc.components.get("context", sc.components.get("story", 0.0)),
            "components": {k: round(float(v), 3) for k, v in sc.components.items()},
            "penalties": {k: round(float(v), 3) for k, v in sc.penalties.items() if v},
            "hook_problems": list(sc.hook_problems), "starts_mid_sentence": bool(starts_mid),
            "ends_mid_sentence": bool(ends_mid),
            "tokens": [t for i in inside for t in units[i].tokens],
            "text": " ".join(units[i].text for i in inside)[:1200],
        }


def duplicate_pairs(clips: list[dict[str, Any]]) -> list[tuple[int, int, float]]:
    """Pairs of clips that tell the same story (content similarity) or mostly overlap in time."""
    from polixor.services.clip_intel.dedupe import SEMANTIC_DUPLICATE, TfIdf, cosine

    docs = [c.get("eval", {}).get("tokens") or [] for c in clips]
    tf = TfIdf(docs) if any(docs) else None
    vecs = [tf.vec(d) if tf is not None else {} for d in docs]
    out = []
    for i in range(len(clips)):
        for j in range(i + 1, len(clips)):
            a, b = clips[i], clips[j]
            inter = max(0.0, min(a["end"], b["end"]) - max(a["start"], b["start"]))
            shorter = max(0.1, min(a["end"] - a["start"], b["end"] - b["start"]))
            sim = cosine(vecs[i], vecs[j]) if vecs[i] and vecs[j] else 0.0
            if inter / shorter >= 0.5 or sim >= SEMANTIC_DUPLICATE:
                out.append((i, j, round(max(sim, inter / shorter), 3)))
    return out


def timing_metrics(transcript, audio_path: Optional[str], spans: list[tuple[float, float]]) -> Optional[dict]:
    if transcript is None or not spans:
        return None
    from polixor.services import subtitle_align as sa

    fn = None
    if audio_path and Path(audio_path).exists():
        fn = lambda a, b: sa.energy_for(Path(audio_path), a, b)   # noqa: E731
    return sa.measure(transcript, fn, spans)


# ---- subtitle text accuracy against a hand-corrected reference ----
_NIQQUD = re.compile(r"[֑-ׇ]")
_TOKEN = re.compile(r"[\w֐-׿']+", re.UNICODE)


def normalize_tokens(text: str) -> list[str]:
    text = _NIQQUD.sub("", text or "").replace("׳", "'").replace("״", '"').lower()
    return _TOKEN.findall(text)


def _edits(a: list[Any], b: list[Any]) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, y in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y))
        prev = cur
    return prev[-1]


def wer(ref: str, hyp: str) -> tuple[int, int]:
    r, h = normalize_tokens(ref), normalize_tokens(hyp)
    return _edits(r, h), len(r)


def cer(ref: str, hyp: str) -> tuple[int, int]:
    r, h = "".join(normalize_tokens(ref)), "".join(normalize_tokens(hyp))
    return _edits(list(r), list(h)), len(r)


def parse_reference(path: Path) -> list[tuple[float, float, str]]:
    """SRT or VTT: [(start, end, text)]."""
    raw = path.read_text("utf-8-sig")
    out = []
    ts = r"(\d+):(\d{2}):(\d{2})[,.](\d{3})"
    for block in re.split(r"\n\s*\n", raw.replace("\r\n", "\n")):
        m = re.search(ts + r"\s*-->\s*" + ts, block)
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        a = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        b = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        text = " ".join(line for line in block[m.end():].strip().splitlines() if line.strip())
        text = re.sub(r"<[^>]+>", "", text)
        if text:
            out.append((a, b, text))
    return out


def text_accuracy(reference: list[tuple[float, float, str]], transcript) -> Optional[dict[str, Any]]:
    if not reference or transcript is None:
        return None
    we = wn = ce = cn = 0
    for a, b, text in reference:
        hyp = transcript.text_between(a, b)
        e, n = wer(text, hyp)
        we, wn = we + e, wn + n
        e, n = cer(text, hyp)
        ce, cn = ce + e, cn + n
    return {"segments": len(reference), "words": wn, "wer": round(we / max(1, wn), 4),
            "cer": round(ce / max(1, cn), 4)}


# ---- evaluation of both runs ----
def evaluate(runs: dict[str, dict[str, Any]], language: str,
             reference: Optional[list[tuple[float, float, str]]] = None) -> dict[str, Any]:
    from polixor.services import analysis_store

    judge_run = runs.get("after") or next(iter(runs.values()))
    arts = judge_run.get("artifacts") or {}
    base_tr = load_transcript(arts)
    tl_path = arts.get("timeline_full_path") or arts.get("timeline_path")
    tl = analysis_store.load_timeline(Path(tl_path)) if tl_path and Path(tl_path).exists() else None
    yard = Yardstick(base_tr, tl, language)
    audio = arts.get("audio_path")
    out: dict[str, Any] = {"yardstick": {"transcript_from": judge_run["label"],
                                         "units": len(yard.units), "threshold": yard.threshold}}
    for label, run in runs.items():
        clips = run.get("clips") or []
        for c in clips:
            c["eval"] = yard.span(c["start"], c["end"])
        dups = duplicate_pairs(clips)
        tr = load_transcript(run.get("artifacts") or {})
        spans = [(c["start"], c["end"]) for c in clips]
        n = max(1, len(clips))
        ev = [c["eval"] for c in clips if not c["eval"].get("empty")]
        stats = {
            "clips": len(clips),
            "pass_quality_bar": sum(1 for e in ev if e["passed"]),
            "weak_or_random": sum(1 for c in clips if not c["eval"].get("passed")),
            "mean_score": round(sum(e["final"] for e in ev) / max(1, len(ev)), 3),
            "mean_hook": round(sum(e["hook"] for e in ev) / max(1, len(ev)), 3),
            "mean_payoff": round(sum(e["payoff"] for e in ev) / max(1, len(ev)), 3),
            "starts_mid_sentence": sum(1 for e in ev if e["starts_mid_sentence"]),
            "ends_mid_sentence": sum(1 for e in ev if e["ends_mid_sentence"]),
            "duplicate_pairs": len(dups),
            "clips_in_duplicates": len({i for p in dups for i in p[:2]}),
            "share_passing": round(sum(1 for e in ev if e["passed"]) / n, 3),
        }
        media_s = float(run.get("media_seconds") or 0)
        perf = {"analysis_seconds": run.get("analysis_seconds"),
                "generation_seconds": run.get("generation_seconds"),
                "total_seconds": run.get("total_seconds"),
                "total_rtf": (round(float(run["total_seconds"]) / media_s, 3)
                              if media_s and run.get("total_seconds") is not None else None),
                "analysis_stage_seconds": run.get("analysis_stage_seconds"),
                "resumed": run.get("resumed"),
                "stages": run.get("stages") or [], "reexport": run.get("reexport")}
        out[label] = {"stats": stats, "duplicates": dups, "performance": perf,
                      "timing": timing_metrics(tr, audio, spans),
                      "text_accuracy": text_accuracy(reference or [], tr)}
    return out


# ==========================================================================
# 3. report (blind review page + summary)
# ==========================================================================
def _rel(path: str, out: Path) -> str:
    """Relative link for the page, percent-encoded: clip names can contain #, % or spaces."""
    from urllib.parse import quote

    try:
        return quote(os.path.relpath(path, out).replace(os.sep, "/"))
    except ValueError:
        return Path(path).as_uri()


def subtitle_samples(runs: dict[str, dict[str, Any]], k: int = 12, seed: int = 7) -> list[dict[str, Any]]:
    """Windows where both runs have text – shown side by side, in random order."""
    if not {"before", "after"} <= set(runs):
        return []
    trs = {lab: load_transcript(runs[lab].get("artifacts") or {}) for lab in ("before", "after")}
    if any(v is None for v in trs.values()):
        return []
    media = float(runs["after"].get("media_seconds") or 0) or max(
        (s.end for s in trs["after"].segments), default=0.0)
    rnd = random.Random(seed)
    out = []
    step = max(20.0, media / (k * 3 or 1))
    t = rnd.uniform(0, step)
    while t < media - 8 and len(out) < k * 3:
        a, b = t, t + 8.0
        texts = {lab: trs[lab].text_between(a, b).strip() for lab in trs}
        if all(len(v) > 12 for v in texts.values()) and texts["before"] != texts["after"]:
            order = ["before", "after"]
            rnd.shuffle(order)
            out.append({"start": round(a, 1), "end": round(b, 1), "order": order,
                        "A": texts[order[0]], "B": texts[order[1]]})
        t += step
    rnd.shuffle(out)
    return out[:k]


def write_report(out: Path, runs: dict[str, dict[str, Any]], ev: dict[str, Any]) -> None:
    rnd = random.Random(11)
    cards = []
    for lab, run in runs.items():
        for c in run.get("clips") or []:
            cards.append({"key": f"{lab}:{c['id']}", "version": lab, "title": c.get("title", ""),
                          "start": c["start"], "end": c["end"], "file": _rel(c["file"], out) if c.get("file") else "",
                          "text": " ".join(q["text"] for q in c.get("cues") or [])[:900] or c["eval"].get("text", "")})
    rnd.shuffle(cards)
    samples = subtitle_samples(runs)
    summary = {lab: {**ev[lab]["stats"], **{k: ev[lab]["performance"][k] for k in
                                            ("analysis_seconds", "generation_seconds", "total_seconds", "total_rtf",
                                             "analysis_stage_seconds")},
                     "reexport_seconds": (ev[lab]["performance"]["reexport"] or {}).get("seconds"),
                     "timing_problem_rate": (ev[lab]["timing"] or {}).get("problem_rate"),
                     "wer": (ev[lab]["text_accuracy"] or {}).get("wer")}
               for lab in runs}
    payload = {"cards": [{k: v for k, v in c.items() if k != "version"} for c in cards],
               "versions": {c["key"]: c["version"] for c in cards}, "samples": samples,
               "summary": summary}
    (out / "compare.json").write_text(json.dumps({"summary": summary, "evaluation": ev,
                                                  "runs": {k: {x: y for x, y in v.items() if x != "artifacts"}
                                                           for k, v in runs.items()}},
                                                 ensure_ascii=False, indent=1, default=str), "utf-8")
    write_markdown(out, summary, ev)
    page = HTML_TEMPLATE.replace("__DATA__", json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"))
    (out / "compare.html").write_text(page, "utf-8")


ROWS = [
    ("clips", "Clips produced", ""), ("pass_quality_bar", "Clips that pass the quality bar", "higher"),
    ("weak_or_random", "Weak / random clips (fail the bar)", "lower"),
    ("mean_score", "Mean clip score (0–1)", "higher"), ("mean_hook", "Mean hook strength", "higher"),
    ("mean_payoff", "Mean payoff strength", "higher"),
    ("starts_mid_sentence", "Clips that start mid-sentence", "lower"),
    ("ends_mid_sentence", "Clips that end mid-sentence", "lower"),
    ("duplicate_pairs", "Duplicate pairs (same story twice)", "lower"),
    ("timing_problem_rate", "Subtitle words timed into silence/overlap", "lower"),
    ("wer", "Subtitle word error rate vs your reference", "lower"),
    ("analysis_seconds", "Analysis time (s)", "lower"), ("generation_seconds", "Clip generation time (s)", "lower"),
    ("total_seconds", "Total processing time (s)", "lower"), ("total_rtf", "Real-time factor (total / video length)", "lower"),
    ("reexport_seconds", "Re-export after a subtitle edit (s)", "lower"),
]


def write_markdown(out: Path, summary: dict[str, dict[str, Any]], ev: dict[str, Any]) -> None:
    labs = [lab for lab in ("before", "after") if lab in summary]
    lines = ["# Polixor – BEFORE vs AFTER", "",
             f"Created {datetime.now().isoformat(timespec='minutes')}. Clip scores use ONE yardstick for both "
             f"versions: the current engine on the {ev['yardstick']['transcript_from']} transcript "
             f"(quality bar {ev['yardstick']['threshold']}).", "",
             "| Metric | " + " | ".join(x.upper() for x in labs) + " |", "|---|" + "---|" * len(labs)]
    for key, name, _ in ROWS:
        vals = [summary[lab].get(key) for lab in labs]
        if all(v is None for v in vals):
            continue
        lines.append(f"| {name} | " + " | ".join("–" if v is None else str(v) for v in vals) + " |")
    for lab in labs:
        res = (ev[lab]["performance"].get("resumed") or {})
        if res:
            lines += ["", f"**{lab.upper()} was resumed** from the old version's own checkpoint "
                      f"(completed before the stop: {', '.join(res.get('from_checkpoint') or [])}; stopped in: "
                      f"{res.get('stopped_at')}). Its analysis time is the {res.get('analysis_time_basis')} – for the old "
                      "version this matches wall-clock time within ~3% (checked on an uninterrupted run); the "
                      "other version's analysis time is wall-clock. "
                      "Reused files, unchanged after the run: "
                      + ", ".join(f"{k} ({'yes' if v.get('unchanged_after_resume') else 'NO'})"
                                  for k, v in (res.get("reused") or {}).items()) + "."]
    lines += ["", "The automatic numbers are a proxy. Open **compare.html** and rate every clip "
              "(blind: you don't see which version made it) – then press *Reveal* for the number of "
              "genuinely usable clips per version, and download the ratings."]
    (out / "compare.md").write_text("\n".join(lines) + "\n", "utf-8")


HTML_TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Polixor blind review</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#16181d;--mute:#5b6270;--line:#e2e5ea;--brand:#5b4bdb;--ok:#1a7f4b;--bad:#b42318;--warn:#a15c00}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--ink:#e8eaee;--mute:#9aa1ad;--line:#2a2f3a;--brand:#8f82ff;--ok:#4cc38a;--bad:#ff6b5e;--warn:#f0b35a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Arial,sans-serif}
main{max-width:1100px;margin:0 auto;padding:20px 16px 80px}h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 10px}
.mute{color:var(--mute)}.grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fill,minmax(300px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px}
video{width:100%;max-height:420px;background:#000;border-radius:8px}
.q{margin-top:8px}.q b{display:block;font-size:13px;margin-bottom:4px}
.opts{display:flex;flex-wrap:wrap;gap:6px}.opts button{border:1px solid var(--line);background:transparent;color:var(--ink);border-radius:999px;padding:4px 10px;cursor:pointer;font:inherit;font-size:13px}
.opts button.on{background:var(--brand);border-color:var(--brand);color:#fff}
.text{font-size:13px;max-height:120px;overflow:auto;border-top:1px solid var(--line);margin-top:8px;padding-top:6px}
.bar{position:sticky;top:0;background:var(--bg);padding:10px 0;display:flex;flex-wrap:wrap;gap:8px;align-items:center;z-index:2;border-bottom:1px solid var(--line)}
.btn{border:0;background:var(--brand);color:#fff;border-radius:8px;padding:8px 14px;cursor:pointer;font:inherit}
.btn.sec{background:transparent;color:var(--ink);border:1px solid var(--line)}
table{border-collapse:collapse;width:100%;background:var(--card);border-radius:12px;overflow:hidden}td,th{border-bottom:1px solid var(--line);padding:8px;text-align:start;font-size:14px}
.pair{display:grid;gap:8px;grid-template-columns:1fr 1fr}@media (max-width:640px){.pair{grid-template-columns:1fr}}
.tag{font-size:12px;padding:2px 8px;border-radius:999px;border:1px solid var(--line)}
.hidden{display:none}
</style></head><body><main>
<h1>Polixor – blind review</h1>
<p class="mute">Clips from both versions are mixed in random order. Rate each one as a creator would: would you post it? Your answers stay in this browser; download them when done. Then press <b>Reveal</b>.</p>
<div class="bar"><span id="progress" class="mute"></span><button class="btn" id="reveal">Reveal results</button><button class="btn sec" id="download">Download ratings</button></div>
<section id="results" class="hidden"><h2>Results</h2><div id="resultsBody"></div></section>
<h2>Clips</h2><div class="grid" id="clips"></div>
<h2 id="subsTitle">Subtitle text – which is more accurate?</h2>
<p class="mute" id="subsHint">The same 8 seconds of the video as transcribed by each version (order is random). Listen at the given time in the source video.</p>
<div id="subs"></div>
<h2>Automatic measurements</h2><div id="auto"></div>
</main>
<script>
const DATA = __DATA__;
const KEY = 'polixor-blind-review';
let R = {}; try { R = JSON.parse(localStorage.getItem(KEY) || '{}') } catch (e) { R = {} }
const save = () => { try { localStorage.setItem(KEY, JSON.stringify(R)) } catch (e) {} ; progress() };
const fmt = s => { s = Math.max(0, Math.round(s)); const h = Math.floor(s/3600), m = Math.floor(s%3600/60), x = s%60; return (h? h+':'+String(m).padStart(2,'0') : m) + ':' + String(x).padStart(2,'0') };
function opts(id, field, choices) {
  const d = document.createElement('div'); d.className = 'opts';
  choices.forEach(([v, label]) => { const b = document.createElement('button'); b.type = 'button'; b.textContent = label;
    const sync = () => b.classList.toggle('on', (R[id]||{})[field] === v); sync();
    b.onclick = () => { R[id] = {...(R[id]||{}), [field]: v}; save(); d.querySelectorAll('button').forEach(x => x.classList.remove('on')); b.classList.add('on') };
    d.appendChild(b) }); return d }
function q(title, node) { const w = document.createElement('div'); w.className = 'q'; const b = document.createElement('b'); b.textContent = title; w.append(b, node); return w }
const clips = document.getElementById('clips');
DATA.cards.forEach((c, i) => {
  const el = document.createElement('div'); el.className = 'card';
  const h = document.createElement('div'); h.innerHTML = '<b>Clip ' + (i+1) + '</b> <span class="mute">' + fmt(c.start) + '–' + fmt(c.end) + '</span>'; el.append(h);
  if (c.file) { const v = document.createElement('video'); v.controls = true; v.preload = 'metadata'; v.src = c.file; el.append(v) }
  el.append(q('Would you post it?', opts(c.key, 'post', [['yes','Yes'],['maybe','With small fixes'],['no','No']])));
  el.append(q('Hook in the first seconds?', opts(c.key, 'hook', [['yes','Yes'],['no','No']])));
  el.append(q('Understandable without the stream? Has a payoff?', opts(c.key, 'story', [['yes','Yes'],['no','No']])));
  el.append(q('Subtitles', opts(c.key, 'subs', [['good','Accurate & in sync'],['text','Wrong words'],['timing','Out of sync']])));
  const t = document.createElement('div'); t.className = 'text'; t.dir = 'auto'; t.textContent = c.text || ''; el.append(t);
  clips.append(el) });
const subs = document.getElementById('subs');
if (!DATA.samples.length) { document.getElementById('subsTitle').classList.add('hidden'); document.getElementById('subsHint').classList.add('hidden') }
DATA.samples.forEach((s, i) => { const id = 'sub:' + i; const el = document.createElement('div'); el.className = 'card'; el.style.marginBottom = '10px';
  el.innerHTML = '<b>' + fmt(s.start) + '–' + fmt(s.end) + '</b>';
  const p = document.createElement('div'); p.className = 'pair';
  ['A','B'].forEach(k => { const d = document.createElement('div'); d.innerHTML = '<span class="tag">' + k + '</span> '; const t = document.createElement('span'); t.dir = 'auto'; t.textContent = s[k]; d.append(t); p.append(d) });
  el.append(p, q('More accurate', opts(id, 'pick', [['A','A'],['B','B'],['same','About the same']]))); subs.append(el) });
function progress() { const n = DATA.cards.filter(c => (R[c.key]||{}).post).length; document.getElementById('progress').textContent = n + ' / ' + DATA.cards.length + ' clips rated' }
progress();
function tally() {
  const t = {}; const add = (v, k) => { t[v] = t[v] || {clips:0, yes:0, maybe:0, no:0, hook:0, story:0, subs_good:0, sub_wins:0}; t[v][k]++ };
  DATA.cards.forEach(c => { const v = DATA.versions[c.key], r = R[c.key] || {}; add(v, 'clips');
    if (r.post) add(v, r.post); if (r.hook === 'yes') add(v, 'hook'); if (r.story === 'yes') add(v, 'story'); if (r.subs === 'good') add(v, 'subs_good') });
  DATA.samples.forEach((s, i) => { const r = R['sub:' + i] || {}; if (r.pick === 'A' || r.pick === 'B') add(s.order[r.pick === 'A' ? 0 : 1], 'sub_wins') });
  return t }
document.getElementById('reveal').onclick = () => {
  const t = tally(); const vs = ['before', 'after'].filter(v => t[v]);
  const rows = [['Clips','clips'],['Usable as is (Yes)','yes'],['Usable with small fixes','maybe'],['Not usable','no'],['Clear hook','hook'],['Complete story (context + payoff)','story'],['Accurate & in-sync subtitles','subs_good'],['Subtitle samples judged more accurate','sub_wins']];
  let h = '<table><tr><th></th>' + vs.map(v => '<th>' + v.toUpperCase() + '</th>').join('') + '</tr>';
  rows.forEach(([n, k]) => { h += '<tr><td>' + n + '</td>' + vs.map(v => '<td>' + (t[v][k]||0) + '</td>').join('') + '</tr>' });
  document.getElementById('resultsBody').innerHTML = h + '</table>'; document.getElementById('results').classList.remove('hidden');
  document.getElementById('results').scrollIntoView() };
document.getElementById('download').onclick = () => {
  const blob = new Blob([JSON.stringify({ratings: R, versions: DATA.versions, samples: DATA.samples.map(s => s.order), results: tally()}, null, 1)], {type: 'application/json'});
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'polixor_blind_ratings.json'; a.click() };
const S = DATA.summary, labs = Object.keys(S);
const names = {clips:'Clips produced', pass_quality_bar:'Pass the quality bar', weak_or_random:'Weak / random clips', mean_score:'Mean clip score', mean_hook:'Mean hook', mean_payoff:'Mean payoff', starts_mid_sentence:'Start mid-sentence', ends_mid_sentence:'End mid-sentence', duplicate_pairs:'Duplicate pairs', timing_problem_rate:'Subtitle words timed into silence/overlap', wer:'Subtitle WER vs reference', analysis_seconds:'Analysis (s)', generation_seconds:'Clip generation (s)', total_seconds:'Total (s)', total_rtf:'Real-time factor', reexport_seconds:'Re-export after a subtitle edit (s)'};
let a = '<p class="mute">Shown only after you press Reveal, so they don\'t bias the review.</p><table><tr><th></th>' + labs.map(l => '<th>' + l.toUpperCase() + '</th>').join('') + '</tr>';
Object.keys(names).forEach(k => { if (labs.every(l => S[l][k] == null)) return; a += '<tr><td>' + names[k] + '</td>' + labs.map(l => '<td>' + (S[l][k] == null ? '–' : S[l][k]) + '</td>').join('') + '</tr>' });
const auto = document.getElementById('auto'); auto.innerHTML = a + '</table>'; auto.classList.add('hidden');
document.getElementById('reveal').addEventListener('click', () => auto.classList.remove('hidden'));
</script></body></html>
"""


# ==========================================================================
def _before_src(args: argparse.Namespace, out: Path) -> Path:
    if args.before_src:
        return Path(args.before_src).resolve()
    wt = out / "_before_src"
    if not wt.exists():
        top = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run(["git", "-C", top, "worktree", "add", "--detach", str(wt), args.before_ref],
                       check=True, capture_output=True)
        sub = os.path.relpath(ROOT, top)
        return (wt / sub).resolve() if sub != "." else wt.resolve()
    top = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True).stdout.strip()
    sub = os.path.relpath(ROOT, top) if top else "."
    return (wt / sub).resolve() if sub != "." else wt.resolve()


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--media", type=Path, required=True)
    ap.add_argument("--language", choices=("auto", "he", "en"), default="he")
    ap.add_argument("--before-ref", default=DEFAULT_BEFORE)
    ap.add_argument("--before-src", type=Path)
    ap.add_argument("--only", choices=("before", "after"))
    ap.add_argument("--settings-from", type=Path)
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="setting for both runs, e.g. --set whisper_model=large-v3")
    ap.add_argument("--reference", type=Path, help="hand-corrected .srt/.vtt for subtitle text accuracy")
    ap.add_argument("--fixture-transcript", type=Path, help=argparse.SUPPRESS)   # offline self-test only
    ap.add_argument("--out", type=Path)
    ap.add_argument("--port", type=int, default=8871)
    ap.add_argument("--inspect-before", action="store_true",
                    help="report what an interrupted BEFORE run completed (read-only) and exit")
    ap.add_argument("--resume-before", action="store_true",
                    help="resume an interrupted BEFORE run from the old version's own checkpoint")
    ap.add_argument("--yes", action="store_true", help="don't ask before resuming")
    args = ap.parse_args(argv)

    media = args.media.resolve()
    if not media.exists():
        raise SystemExit(f"Video not found: {media}")
    out = (args.out or Path(f"acceptance_compare_{datetime.now():%Y%m%d_%H%M}")).resolve()
    out.mkdir(parents=True, exist_ok=True)
    settings: dict[str, Any] = {}
    if args.settings_from:
        settings.update(json.loads(args.settings_from.read_text("utf-8")))
    for kv in args.set:
        k, _, v = kv.partition("=")
        settings[k.strip()] = json.loads(v) if v[:1] in "[{0123456789-" or v in ("true", "false") else v
    env_extra: dict[str, str] = {}
    if args.fixture_transcript:
        settings["transcript_provider"] = "fixture"
        env_extra["POLIXOR_FIXTURE_TRANSCRIPT"] = str(args.fixture_transcript.resolve())

    resume_rep: Optional[dict[str, Any]] = None
    if args.inspect_before or args.resume_before:
        wt = out / "_before_src"
        src = None if not (wt.exists() or args.before_src) else _before_src(args, out)
        rep = inspect_before(out, media, src, allow_fixture=bool(args.fixture_transcript))
        if src is None:
            rep["problems"].append(f"BEFORE code folder {wt} is missing – cannot prove the commit")
            rep["resumable"] = False
        print_inspection(rep)
        (out / f"before_inspection_{datetime.now():%Y%m%d_%H%M%S}.json").write_text(
            json.dumps(rep, ensure_ascii=False, indent=1, default=str), "utf-8")
        if args.inspect_before or not rep["resumable"]:
            return 0 if rep["resumable"] else 3
        if not args.yes and input("Resume the BEFORE run from this checkpoint? [y/N] ").strip().lower() != "y":
            return 1
        resume_rep = rep

    runs: dict[str, dict[str, Any]] = {}
    for label in ("before", "after"):
        if label == "before" and resume_rep is not None:
            runs[label] = run_version(label, _before_src(args, out) / "backend", media, out,
                                      language=args.language, settings=settings, port=args.port,
                                      env_extra=env_extra, resume=resume_rep)
            continue
        if args.only and args.only != label:
            prev = out / f"{label}.json"
            if prev.exists():
                runs[label] = json.loads(prev.read_text("utf-8"))
                log(f"{label.upper()}: using the earlier run in {prev}")
            continue
        backend = (_before_src(args, out) if label == "before" else ROOT) / "backend"
        runs[label] = run_version(label, backend, media, out, language=args.language, settings=settings,
                                  port=args.port + (0 if label == "before" else 1), env_extra=env_extra)
    if not runs:
        raise SystemExit("Nothing to compare.")
    reference = parse_reference(args.reference) if args.reference else None
    log("Scoring both runs with one yardstick...")
    ev = evaluate(runs, args.language if args.language != "auto" else "he", reference)
    write_report(out, runs, ev)
    log(f"Done. Open {out / 'compare.html'} to rate the clips (blind), and see {out / 'compare.md'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
