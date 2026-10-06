"""
Job-scoped profiling: where a run's time goes, measured – not guessed.

One Profile per pipeline run (set with `activate(job_id, work_dir, source)`), carried through a
contextvar into every thread that is started with `spawn_context()` / `submit()` (the early
renderer, the semantic per-Short pool), so work in background threads is not lost.

It records:
  spans          wall + CPU seconds per named step (every timing.substage, every pipeline stage,
                 every semantic step), with media seconds → realtime factor
  subprocesses   every process started (ffmpeg, ffprobe, …) – counted by program – and every
                 FULL decode of the source file (an ffmpeg reading the source without a seek/limit),
                 which is the expensive thing to avoid repeating
  cache          hit / miss per cache (semantic stage store, model answers, render reuse …)
  model_wait     seconds spent waiting for a model answer, per task
  gpu            None unless a GPU path ran (then its seconds)

Saved as <work>/profile.json (merged across runs of the same job: analysis, generation, resume).
Admin diagnostics read it; the customer never sees it.
"""

from __future__ import annotations

import contextvars
import json
import os
import subprocess
import threading
import time
from collections import defaultdict
from concurrent.futures import Executor, Future
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

_CURRENT: contextvars.ContextVar[Optional["Profile"]] = contextvars.ContextVar("polixor_profile", default=None)
_installed = False
_install_lock = threading.Lock()


class Profile:
    def __init__(self, job_id: str, work_dir: Optional[Path] = None, source: str = "") -> None:
        self.job_id = job_id
        self.work_dir = Path(work_dir) if work_dir else None
        self.source = os.path.realpath(source) if source else ""
        self.lock = threading.Lock()
        self.spans: dict[str, dict[str, float]] = defaultdict(lambda: {"wall": 0.0, "cpu": 0.0, "count": 0,
                                                                        "media": 0.0})
        self.procs: dict[str, int] = defaultdict(int)
        self.full_decodes: list[dict[str, Any]] = []
        self.cache: dict[str, dict[str, int]] = defaultdict(lambda: {"hit": 0, "miss": 0})
        self.model_wait: dict[str, float] = defaultdict(float)
        self.gpu_seconds: Optional[float] = None
        self.started = time.time()

    # ---- recording ----
    def span(self, name: str, wall: float, cpu: float = 0.0, media: float = 0.0) -> None:
        with self.lock:
            s = self.spans[name]
            s["wall"] += wall
            s["cpu"] += cpu
            s["count"] += 1
            s["media"] += media

    def proc(self, argv: list[str]) -> None:
        prog = os.path.basename(str(argv[0])) if argv else "?"
        with self.lock:
            self.procs[prog] += 1
            if self.source and prog.startswith("ffmpeg"):
                full = _full_source_decode(argv, self.source)
                if full:
                    self.full_decodes.append({"t": round(time.time() - self.started, 2), "what": full})

    def cache_event(self, cache: str, hit: bool) -> None:
        with self.lock:
            self.cache[cache]["hit" if hit else "miss"] += 1

    def wait(self, task: str, seconds: float) -> None:
        with self.lock:
            self.model_wait[task] += seconds

    # ---- output ----
    def to_dict(self) -> dict[str, Any]:
        with self.lock:
            spans = {k: {kk: round(vv, 3) if isinstance(vv, float) else vv for kk, vv in v.items()}
                     for k, v in self.spans.items()}
            for v in spans.values():
                v["rtf"] = round(v["wall"] / v["media"], 4) if v.get("media") else None
            return {"job_id": self.job_id, "spans": spans, "subprocesses": dict(self.procs),
                    "full_source_decodes": list(self.full_decodes), "cache": {k: dict(v) for k, v in self.cache.items()},
                    "model_wait": {k: round(v, 2) for k, v in self.model_wait.items()},
                    "gpu_seconds": self.gpu_seconds}

    def save(self) -> None:
        if self.work_dir is None:
            return
        p = self.work_dir / "profile.json"
        old: dict[str, Any] = {}
        try:
            old = json.loads(p.read_text("utf-8"))
        except (OSError, ValueError):
            pass
        cur = self.to_dict()
        runs = list(old.get("runs") or [])[-20:]
        runs.append({"at": round(self.started, 1), **cur})
        merged = _merge([r for r in runs])
        try:
            self.work_dir.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps({"runs": runs, "total": merged}, ensure_ascii=False), "utf-8")
            os.replace(tmp, p)
        except OSError:
            pass


def _merge(runs: list[dict[str, Any]]) -> dict[str, Any]:
    spans: dict[str, dict[str, float]] = {}
    procs: dict[str, int] = defaultdict(int)
    cache: dict[str, dict[str, int]] = {}
    wait: dict[str, float] = defaultdict(float)
    decodes = 0
    for r in runs:
        for k, v in (r.get("spans") or {}).items():
            d = spans.setdefault(k, {"wall": 0.0, "cpu": 0.0, "count": 0, "media": 0.0})
            for kk in ("wall", "cpu", "count", "media"):
                d[kk] += v.get(kk) or 0
        for k, v in (r.get("subprocesses") or {}).items():
            procs[k] += v
        for k, v in (r.get("cache") or {}).items():
            c = cache.setdefault(k, {"hit": 0, "miss": 0})
            c["hit"] += v.get("hit", 0)
            c["miss"] += v.get("miss", 0)
        for k, v in (r.get("model_wait") or {}).items():
            wait[k] += v
        decodes += len(r.get("full_source_decodes") or [])
    for v in spans.values():
        v["wall"], v["cpu"], v["media"] = round(v["wall"], 2), round(v["cpu"], 2), round(v["media"], 2)
        v["rtf"] = round(v["wall"] / v["media"], 4) if v["media"] else None
    ranked = sorted(spans.items(), key=lambda kv: -kv[1]["wall"])
    return {"spans": spans, "bottlenecks": [{"name": k, **v} for k, v in ranked[:15]],
            "subprocesses": dict(procs), "full_source_decodes": decodes,
            "cache": cache, "model_wait": {k: round(v, 2) for k, v in wait.items()}}


def _full_source_decode(argv: list[str], source: str) -> str:
    """'' unless this ffmpeg reads the whole source file (an input of it with no seek/limit)."""
    args = [str(a) for a in argv]
    for i, a in enumerate(args):
        if a == "-i" and i + 1 < len(args):
            try:
                same = os.path.realpath(args[i + 1]) == source
            except (OSError, ValueError):
                same = False
            if not same:
                continue
            before = args[:i]
            if "-ss" in before or "-t" in before or "-to" in before:
                return ""
            after = args[i + 2:]
            if "-t" in after or "-to" in after or "-frames:v" in after or "-vframes" in after:
                return ""
            # what is it for: the output's flags (audio-only, filter, null …)
            if "-vn" in after:
                return "audio"
            if "null" in after:
                return "analysis"
            return "video"
    return ""


# --------------------------------------------------------------------------
# activation / propagation
# --------------------------------------------------------------------------
def install() -> None:
    """Counts every subprocess start (once per process). Only records; never changes a call."""
    global _installed
    with _install_lock:
        if _installed:
            return
        orig = subprocess.Popen.__init__

        def patched(self, args, *a, **kw):  # type: ignore[no-untyped-def]
            prof = _CURRENT.get()
            if prof is not None:
                try:
                    prof.proc(list(args) if isinstance(args, (list, tuple)) else [str(args)])
                except Exception:  # noqa: BLE001 – profiling never breaks a call
                    pass
            return orig(self, args, *a, **kw)

        subprocess.Popen.__init__ = patched  # type: ignore[method-assign]
        _installed = True


@contextmanager
def activate(job_id: str, work_dir: Optional[Path] = None, source: str = "") -> Iterator[Profile]:
    install()
    prof = Profile(job_id, work_dir, source)
    token = _CURRENT.set(prof)
    try:
        yield prof
    finally:
        _CURRENT.reset(token)
        prof.save()


def current() -> Optional[Profile]:
    return _CURRENT.get()


def set_source(path: str) -> None:
    p = _CURRENT.get()
    if p is not None and path:
        p.source = os.path.realpath(path)


def thread_target(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wraps a function so that, in whatever thread runs it, it records into the caller's profile."""
    prof = _CURRENT.get()

    def run(*a: Any, **kw: Any) -> Any:
        token = _CURRENT.set(prof)
        try:
            return fn(*a, **kw)
        finally:
            _CURRENT.reset(token)
    return run


def submit(ex: Executor, fn: Callable[..., Any], *a: Any, **kw: Any) -> Future:
    return ex.submit(thread_target(fn), *a, **kw)


def pmap(ex: Executor, fn: Callable[..., Any], items: Any) -> list[Any]:
    """`list(ex.map(fn, items))` with the caller's profile in every worker thread."""
    return list(ex.map(thread_target(fn), items))


@contextmanager
def span(name: str, media: float = 0.0) -> Iterator[None]:
    p = _CURRENT.get()
    t0, c0 = time.perf_counter(), time.thread_time()
    try:
        yield
    finally:
        if p is not None:
            p.span(name, time.perf_counter() - t0, time.thread_time() - c0, media)


def cache_event(cache: str, hit: bool) -> None:
    p = _CURRENT.get()
    if p is not None:
        p.cache_event(cache, hit)


def model_wait(task: str, seconds: float) -> None:
    p = _CURRENT.get()
    if p is not None:
        p.wait(task, seconds)


def load(work_dir: Path) -> dict[str, Any]:
    try:
        return json.loads((Path(work_dir) / "profile.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}
