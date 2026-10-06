"""
Which H.264 encoder this machine should use – decided by a real test encode, not by a list.

FFmpeg builds often *list* h264_nvenc / h264_qsv / h264_videotoolbox although the machine has no
such hardware (or no driver): the list says what was compiled in, not what works. So every
candidate is tried once with a one-second test encode at the Shorts size; only encoders that
produce a file are usable. The result is cached for the process and on disk (data/encoders.json,
keyed by FFmpeg binary + host), and a hardware encoder that fails during a real render is
dropped for the rest of the process (the render is retried on the CPU – the user never sees it).

`hw_accel` setting: "auto" → the first working of nvenc, qsv, videotoolbox, else the CPU;
"nvenc" | "qsv" | "videotoolbox" → that one when it works, else the CPU; "none" → the CPU.

Quality mapping (the quality bar is the same as the CPU path; the hardware is only faster):
  libx264        -crf C (preset by quality)
  NVENC          -preset p6 -tune hq, VBR constant quality -cq C+1, spatial+temporal AQ,
                 B-frames as reference – visually on par with x264 "medium" at equal size
  QuickSync      -global_quality C+1 with look-ahead
  VideoToolbox   -q:v mapped from C, high profile
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("polixor.encoders")

HW = ("nvenc", "qsv", "videotoolbox")
NAMES = {"nvenc": "h264_nvenc", "qsv": "h264_qsv", "videotoolbox": "h264_videotoolbox", "cpu": "libx264"}

_lock = threading.Lock()
_probe: Optional[dict[str, Any]] = None
_broken: set[str] = set()


def _listed() -> set[str]:
    from ..util.ffmpeg import ffmpeg_bin

    try:
        out = subprocess.run([ffmpeg_bin(), "-hide_banner", "-encoders"], capture_output=True, text=True,
                             timeout=20).stdout
    except Exception:                                     # noqa: BLE001
        return set()
    return {k for k, n in NAMES.items() if f" {n} " in out}


def _test_encode(kind: str) -> tuple[bool, float, str]:
    """A 1-second 1080x1920 encode: (works, seconds, error)."""
    from ..util.ffmpeg import ffmpeg_bin

    with tempfile.TemporaryDirectory(prefix="pxenc_") as d:
        out = Path(d) / "t.mp4"
        cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
               "testsrc2=s=1080x1920:r=30:d=1", *codec_args(kind, 20, "medium"), "-an", str(out)]
        t0 = time.time()
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except Exception as exc:                          # noqa: BLE001
            return False, 0.0, str(exc)[:200]
        ok = r.returncode == 0 and out.exists() and out.stat().st_size > 1000
        return ok, round(time.time() - t0, 3), ("" if ok else (r.stderr or "")[-300:])


def _cache_path() -> Path:
    from ..config import PATHS

    return PATHS.data / "encoders.json"


def _key() -> str:
    from ..util.ffmpeg import ffmpeg_bin

    b = ffmpeg_bin()
    try:
        m = int(Path(b).stat().st_mtime) if os.path.isabs(b) else 0
    except OSError:
        m = 0
    return f"{socket.gethostname()}|{b}|{m}"


def probe(force: bool = False) -> dict[str, Any]:
    """{'usable': [...hardware that works...], 'listed': [...], 'tests': {kind: {...}}, 'cpu': {...}}"""
    global _probe
    with _lock:
        if _probe is not None and not force:
            return _probe
        if not force:
            try:
                saved = json.loads(_cache_path().read_text("utf-8"))
                if saved.get("key") == _key() and time.time() - float(saved.get("at") or 0) < 7 * 86400:
                    _probe = saved
                    return _probe
            except (OSError, ValueError):
                pass
        listed = _listed()
        tests: dict[str, Any] = {}
        usable: list[str] = []
        for kind in HW:
            if kind not in listed:
                continue
            ok, secs, err = _test_encode(kind)
            tests[kind] = {"works": ok, "seconds": secs, "error": err}
            if ok:
                usable.append(kind)
        ok, secs, err = _test_encode("cpu")
        _probe = {"key": _key(), "at": time.time(), "listed": sorted(listed), "usable": usable, "tests": tests,
                  "cpu": {"works": ok, "seconds": secs, "error": err}}
        try:
            _cache_path().write_text(json.dumps(_probe), "utf-8")
        except OSError:
            pass
        log.info("video encoders: listed %s, working hardware %s (cpu test %.2fs)", sorted(listed), usable, secs)
        return _probe


def choose(hw_accel: str) -> str:
    """'cpu' or the hardware kind to use for this setting on this machine."""
    want = (hw_accel or "auto").lower()
    if want == "none":
        return "cpu"
    usable = [k for k in probe()["usable"] if k not in _broken]
    if want == "auto":
        return usable[0] if usable else "cpu"
    return want if want in usable else "cpu"


def mark_broken(kind: str, reason: str = "") -> None:
    """A hardware encoder failed during a real render: the CPU is used for the rest of this process."""
    if kind != "cpu":
        _broken.add(kind)
        log.warning("hardware encoder %s failed (%s) – using the CPU from now on", kind, reason[:200])


def codec_args(kind: str, crf: int, preset: str) -> list[str]:
    if kind == "nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p6", "-tune", "hq", "-rc", "vbr", "-cq", str(crf + 1),
                "-b:v", "0", "-spatial-aq", "1", "-temporal-aq", "1", "-bf", "3", "-b_ref_mode", "middle",
                "-profile:v", "high", "-pix_fmt", "yuv420p"]
    if kind == "qsv":
        return ["-c:v", "h264_qsv", "-global_quality", str(crf + 1), "-look_ahead", "1", "-preset", "slower",
                "-profile:v", "high", "-pix_fmt", "nv12"]
    if kind == "videotoolbox":
        return ["-c:v", "h264_videotoolbox", "-q:v", str(max(1, min(100, 100 - crf * 2))), "-profile:v", "high",
                "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-profile:v", "high", "-level", "4.1",
            "-pix_fmt", "yuv420p"]


def summary() -> dict[str, Any]:
    p = probe()
    return {"listed": p.get("listed"), "working_hardware": p.get("usable"), "broken_this_run": sorted(_broken),
            "auto_choice": choose("auto"), "tests": p.get("tests"), "cpu_test_seconds": (p.get("cpu") or {}).get("seconds")}


__all__ = ["probe", "choose", "codec_args", "mark_broken", "summary", "HW", "NAMES"]
