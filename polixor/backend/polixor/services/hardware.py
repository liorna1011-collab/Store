"""
What this machine can do, measured once, and the worker counts derived from it.

  profile()   cpus (the container's quota, not the host's core count), RAM, GPU, free disk,
              load average
  tuning()    how many re-export renders, ASR threads, browser upload connections and
              background jobs this machine runs at once

Every number can be pinned with an environment variable (POLIXOR_RENDER_WORKERS,
POLIXOR_UPLOAD_CONCURRENCY_MAX, POLIXOR_ASR_THREADS). Nothing here lowers quality: it only
decides how much runs side by side. A busy machine (load above its cores) gets fewer
parallel renders so the web server keeps answering.
"""

from __future__ import annotations

import os
import shutil
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

_lock = threading.Lock()


def _cgroup_cpus() -> float:
    """A container's CPU quota (cgroup v2 cpu.max / v1 cfs) – os.cpu_count() reports the host."""
    try:
        q = Path("/sys/fs/cgroup/cpu.max").read_text().split()
        if q and q[0] != "max":
            return int(q[0]) / int(q[1])
    except (OSError, ValueError, IndexError):
        pass
    try:
        quota = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read_text())
        period = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text())
        if quota > 0 and period > 0:
            return quota / period
    except (OSError, ValueError):
        pass
    return 0.0


def cpus() -> int:
    try:
        n = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        n = os.cpu_count() or 2
    q = _cgroup_cpus()
    if q:
        n = min(n, max(1, int(q + 0.5)))
    return max(1, n)


def ram_gb() -> float:
    try:
        lim = Path("/sys/fs/cgroup/memory.max").read_text().strip()
        if lim != "max":
            return round(int(lim) / 1024 ** 3, 1)
    except (OSError, ValueError):
        pass
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return round(int(line.split()[1]) / 1024 ** 2, 1)
    except (OSError, ValueError):
        pass
    return 0.0


@lru_cache(maxsize=1)
def gpus() -> int:
    try:
        import ctranslate2

        return int(ctranslate2.get_cuda_device_count())
    except Exception:                                 # noqa: BLE001
        return 0


def load() -> float:
    try:
        return round(os.getloadavg()[0], 2)
    except OSError:
        return 0.0


def profile() -> dict[str, Any]:
    from ..config import PATHS

    try:
        free = shutil.disk_usage(PATHS.data).free
    except OSError:
        free = 0
    return {"cpus": cpus(), "host_cpus": os.cpu_count(), "ram_gb": ram_gb(), "gpus": gpus(),
            "disk_free_gb": round(free / 1024 ** 3, 1), "load_1m": load()}


def _env_int(name: str) -> int:
    v = os.environ.get(name, "").strip()
    return int(v) if v.isdigit() and int(v) > 0 else 0


def render_workers() -> int:
    """Concurrent re-export renders. FFmpeg already uses several threads per render."""
    pinned = _env_int("POLIXOR_RENDER_WORKERS")
    if pinned:
        return min(8, pinned)
    n = cpus()
    workers = 1 if n <= 4 else (2 if n <= 12 else 3)
    if ram_gb() and ram_gb() < 6:
        workers = 1
    return workers


def upload_concurrency_max() -> int:
    """Upper bound for parallel chunk requests from one browser (the browser adapts within 2..this)."""
    pinned = _env_int("POLIXOR_UPLOAD_CONCURRENCY_MAX")
    if pinned:
        return max(2, min(6, pinned))
    return 6 if cpus() >= 4 else 4


def busy() -> bool:
    """The machine is saturated (1-minute load above its cores)."""
    return load() > cpus() * 1.0


def tuning() -> dict[str, Any]:
    from ..config import SETTINGS
    from .transcribe import cpu_threads

    return {"render_workers": render_workers(), "asr_threads": cpu_threads(),
            "asr_device": "cuda" if gpus() else "cpu",
            "upload_concurrency": {"min": 2, "max": upload_concurrency_max()},
            "background_jobs": max(1, SETTINGS.get().concurrent_jobs), "busy": busy()}
