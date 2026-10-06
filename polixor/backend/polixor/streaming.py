"""
Streaming: publishable Shorts while the source is still being transcribed.

The strong speech model transcribes in ~5-minute chunks and each chunk is final when it ends
(services/transcribe.py → on_chunk). Once enough of the source is final, an EARLY WINDOW runs the
same semantic pipeline on that part – topic map → candidates → ranking → the editor gate (with
its one reconstruction) → the two-model final transcript → the title – and renders every Short
it ships right away. The person sees the first publishable Short while the rest is transcribed.

    the standard does not change  the same editor, the same checks, the same final transcript;
                                  a window can ship nothing
    the payoff must be heard      a window's candidates must end HORIZON_GUARD seconds before
                                  the last final second (else the full pass decides)
    no quota, room for the best   a window ships at most one Short per 10 minutes it covers, all
                                  windows together at most half of the wanted Shorts
    global reconciliation         the full pass (after ASR) sees the early Shorts as shipped:
                                  they count toward the limit, their moments are duplicates, and
                                  it picks the rest across the whole source (salvage / near-pass
                                  stay for a run where nothing shipped at all)

When: a first processing (not a re-edit – that has its transcript), Shorts wanted, a usable
language model, a source of at least STREAM_MIN_SOURCE seconds. POLIXOR_STREAMING=off disables it.

Cost: a window's topic map / candidates / ranking are paid again by the full pass over the same
text, so discovery costs up to about twice; the editor is not repeated for the early Shorts
(see docs/ENGINE.md → Streaming; the run report has the windows' own usage).

Resume: the windows have their own checkpoints (<work>/stream/w<i>/) and the state of the early
Shorts is in <work>/stream.json; a restarted ASR replays its finished chunks, the windows are
answered from their checkpoints and a Short that is already rendered is not rendered again.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("polixor.streaming")

STREAM_MIN_SOURCE = 600.0        # the strong ASR is chunked from 10 minutes (STRONG_CHUNK_MIN_TOTAL)
FIRST_WINDOW = 240.0             # the first window once this much is final
NEXT_WINDOW = 1200.0             # later windows: this much new final transcript …
TAIL_MIN = 600.0                 # … while at least this much is still to come (else the full pass is near)
CONTEXT = 120.0                  # a later window starts this much before the previous one ended
HORIZON_GUARD = 30.0             # a window's candidate ends at least this long before the last final second
STATE_FILE = "stream.json"


def _settings_key(s: Any) -> list[Any]:
    """What makes an early Short valid for a generation: the same Short settings."""
    return [int(s.short_count), float(s.short_min_seconds), float(s.short_max_seconds), str(s.short_resolution),
            str(s.short_layout), str(getattr(s, "content_profile", "auto")), bool(s.subtitles_enabled)]


def stream_settings(ctx: Any) -> Optional[Any]:
    """The settings the Shorts will be made with, when this run should stream; else None."""
    from .models import RunScope

    if os.environ.get("POLIXOR_STREAMING", "").lower() in ("0", "off", "false", "no"):
        return None
    if getattr(ctx, "is_live", False) or ctx.settings.transcript_provider == "none":
        return None
    duration = float(ctx.source_info.get("duration") or 0.0)
    if duration < STREAM_MIN_SOURCE:
        return None
    scope = getattr(ctx, "scope", RunScope.ALL.value) or RunScope.ALL.value
    if scope == RunScope.ALL.value:
        settings = ctx.settings
    elif scope == RunScope.ANALYZE.value:
        # a Studio project: analysis first, then the goal it was created with (auto_generate)
        goal = ((getattr(ctx, "config", None) or {}).get("studio") or {}).get("auto_generate")
        if goal not in ("short", "package"):
            return None
        settings = _goal_settings(ctx, goal, duration)
    else:
        return None
    if settings is None or not settings.short_enabled or int(settings.short_count) <= 0:
        return None
    from .services.semantic.provider import resolve

    provider, _why = resolve(settings)
    return settings if provider is not None else None


def _goal_settings(ctx: Any, goal: str, duration: float) -> Optional[Any]:
    from .config import AppSettings
    from .db import session_scope
    from .models import Job
    from .project_config import settings_for_project

    with session_scope() as s:
        job = s.get(Job, ctx.job_id)
        if job is None:
            return None
        cfg = dict(job.project_config or {})
        cfg["mode"] = goal
        return settings_for_project(AppSettings.from_dict(job.settings_snapshot or {}), cfg,
                                    content_language=job.content_language, duration=duration)


def load_state(work_dir: Path) -> dict[str, Any]:
    p = Path(work_dir) / STATE_FILE
    try:
        return json.loads(p.read_text("utf-8")) if p.exists() else {}
    except (OSError, ValueError):
        return {}


def preshipped(work_dir: Path, settings: Any) -> list[dict[str, Any]]:
    """The early Shorts a generation with these settings keeps (empty when the settings changed)."""
    from .services.selection import Candidate

    st = load_state(work_dir)
    if not st or st.get("settings_key") != _settings_key(settings) or st.get("consumed"):
        return []
    out = []
    for x in st.get("shorts") or []:
        if not x.get("clip_id"):
            continue
        item = dict(x["cand"])
        item["segments"] = [tuple(s) for s in item.get("segments") or []]
        try:
            out.append({"cand": Candidate(**item), "final": x.get("final") or {}, "key": x["key"],
                        "window": x.get("window"), "clip_id": x["clip_id"]})
        except TypeError:
            log.warning("bad early Short record – ignored")
    return out


def mark_consumed(work_dir: Path) -> None:
    """The generation took the early Shorts: a later re-edit starts clean."""
    st = load_state(work_dir)
    if st:
        st["consumed"] = True
        _write(Path(work_dir), st)


def mark_reported(work_dir: Path) -> None:
    st = load_state(work_dir)
    if st:
        st["usage_reported"] = True
        _write(Path(work_dir), st)


def _write(work_dir: Path, st: dict[str, Any]) -> None:
    p = work_dir / STATE_FILE
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, default=str), "utf-8")
    tmp.replace(p)


class _WindowView:
    """The job context as an early window sees it: its own (partial) transcript, timeline, settings
    and a quiet reporter; everything else – visual windows, artifacts, the clip store – is the job's."""

    LOCAL = ("transcript", "transcript_original", "timeline", "settings", "reporter", "language",
             "audio_feats", "silences")

    def __init__(self, ctx: Any, **local: Any) -> None:
        object.__setattr__(self, "_ctx", ctx)
        for k, v in local.items():
            object.__setattr__(self, k, v)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._ctx, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in self.LOCAL:
            object.__setattr__(self, name, value)
        else:
            setattr(self._ctx, name, value)


class StreamingWindows:
    """Runs early windows on a background thread while the ASR continues (see the module doc)."""

    def __init__(self, ctx: Any, settings: Any) -> None:
        self.ctx, self.settings = ctx, settings
        self.total = float(ctx.source_info.get("duration") or 0.0)
        self.cap = max(1, int(settings.short_count) // 2)
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.closing = False
        self.latest: Optional[tuple[float, list, str]] = None
        self.last_end = 0.0                       # where the previous window ended
        self.audio: Optional[tuple[Any, list]] = None
        self.error: Optional[str] = None
        old = load_state(ctx.work_dir)
        fresh = {"settings_key": _settings_key(settings), "windows": [], "shorts": [], "usage": {}}
        self.state = old if old.get("settings_key") == fresh["settings_key"] and not old.get("consumed") else fresh
        self.thread = threading.Thread(target=self._loop, name="polixor-stream", daemon=True)
        self.thread.start()

    # ---- called by the ASR (its own thread) ----
    def feed(self, until: float, segments: list, language: str) -> None:
        with self.lock:
            self.latest = (until, segments, language)
        self.wake.set()

    def finish(self) -> dict[str, Any]:
        """The ASR finished: no new window starts; the running one completes (its Shorts render)."""
        self.closing = True
        self.wake.set()
        while self.thread.is_alive():
            self.thread.join(1.0)
            if self.ctx.cancel_event.is_set():
                break
        return self.state

    # ---- the window thread ----
    def _loop(self) -> None:
        from .errors import JobCancelledError

        try:
            while True:
                self.wake.wait(2.0)
                self.wake.clear()
                if self.ctx.cancel_event.is_set():
                    return
                win = self._next()
                if win is not None:
                    self._run_window(*win)
                elif self.closing:
                    return
        except JobCancelledError:
            return
        except Exception as exc:                  # noqa: BLE001 – never breaks the job: the full pass follows
            self.error = str(exc)
            log.warning("streaming window failed – the full pass continues", exc_info=True)

    def _next(self) -> Optional[tuple[float, float, list, str]]:
        if self.closing:
            return None
        with self.lock:
            latest = self.latest
        if latest is None:
            return None
        until, segs, lang = latest
        shipped = len(self.state["shorts"])
        if shipped >= self.cap:
            return None
        if not self.state["windows"] and self.last_end == 0.0:
            if until < FIRST_WINDOW:
                return None
            return 0.0, until, segs, lang
        if until - self.last_end < NEXT_WINDOW or self.total - until < TAIL_MIN:
            return None
        return max(0.0, self.last_end - CONTEXT), until, segs, lang

    def _run_window(self, w0: float, until: float, segs: list, lang: str) -> None:
        from . import pipeline
        from .services import scoring
        from .services.semantic import run as semantic_run
        from .services.transcribe import TranscriptResult

        ctx, s = self.ctx, self.settings
        index = len(self.state["windows"])
        self.last_end = until
        part = [x for x in segs if x.start >= w0 - 0.01 and x.end <= until + 0.01]
        if not part:
            return
        t0 = time.time()
        pipeline._metric(ctx, "first_window_at")
        strong = str(getattr(s, "discovery_asr", "strong") or "strong") == "strong"
        tr = TranscriptResult(segments=part, language=lang or (part[0].language if part else ""), duration=until,
                              provider=s.transcript_provider, model="stream",
                              meta={"discovery": "strong" if strong else "fast", "stream_window": index})
        audio_feats, silences = self._audio()
        language = lang or getattr(ctx, "language", None) or ""
        timeline = scoring.build_timeline(audio=audio_feats, visual=None, transcript=tr, duration=self.total,
                                          settings=s, language=language or None)
        view = _WindowView(ctx, transcript=tr, transcript_original=None, timeline=timeline, settings=s,
                           reporter=pipeline._QuietReporter(ctx.reporter), language=language,
                           audio_feats=audio_feats, silences=silences)
        earlier = preshipped_from(self.state)
        want = min(max(1, round((until - w0) / 600.0)), self.cap - len(self.state["shorts"]))
        done_spans = [(x["cand"]["start"], x["cand"]["end"]) for x in self.state["shorts"] if x.get("clip_id")]
        renderer = pipeline.EarlyRenderer(ctx, want, base=view)
        shipped: list[dict[str, Any]] = []

        def on_ship(cand: Any, final: dict[str, Any]) -> None:
            from .services.analysis_store import _cand_to_dict

            if any(abs(cand.start - a) < 0.05 and abs(cand.end - b) < 0.05 for a, b in done_spans):
                return                              # resumed: this Short is already rendered
            shipped.append({"cand": cand, "final": final})
            renderer.submit(cand, final)

        def on_candidates(n: int) -> None:
            pipeline._metric(ctx, "first_candidates_at")
            pipeline._milestone(ctx, "candidates", n=n, minutes=round(until / 60))

        inp = semantic_run.Inputs(
            transcript=tr, settings=s, work_dir=ctx.work_dir / "stream" / f"w{index}", language=language,
            duration=until - w0, audio_path=ctx.audio_path, limit=want + len(earlier), want_longform=False,
            cancel_event=ctx.cancel_event, vocabulary=list(ctx.artifacts.get("project_vocabulary") or []),
            discovery_strong=strong, on_ship=on_ship, preshipped=earlier,
            horizon=until - HORIZON_GUARD if until < self.total - 1.0 else None, on_candidates=on_candidates)
        try:
            out = semantic_run.run(inp)
        finally:
            renderer.close()
        new_ids = [cid for _c, cid in renderer.pairs]
        by_cand = {id(c): cid for c, cid in renderer.pairs}
        from .services.analysis_store import _cand_to_dict

        for x in shipped:
            c = x["cand"]
            key = f"w{index}:{(c.quality or {}).get('key') or round(c.start, 1)}"
            # a Short whose render failed is still shipped: the full pass renders it with the rest
            self.state["shorts"].append({"key": key, "window": index, "cand": _cand_to_dict(c),
                                         "final": x["final"], "clip_id": by_cand.get(id(c))})
        usage = (out.report or {}).get("usage") or {}
        self.state["usage"] = _add_usage(self.state.get("usage") or {}, usage)
        self.state["windows"].append({"index": index, "start": round(w0, 1), "end": round(until, 1),
                                      "mode": out.mode, "reason": out.reason, "pool": len((out.report or {}).get("pool") or []),
                                      "shipped": len(shipped), "rendered": len(new_ids),
                                      "seconds": round(time.time() - t0, 1), "calls": usage.get("calls", 0)})
        _write(Path(ctx.work_dir), self.state)
        ctx.artifacts["stream"] = {"windows": len(self.state["windows"]),
                                   "shorts": sum(1 for x in self.state["shorts"] if x.get("clip_id"))}
        ctx._persist()
        log.info("early window %d (%.0f–%.0f s): %s, %d Short(s) rendered in %.0f s", index, w0, until,
                 out.mode, len(new_ids), time.time() - t0)

    def _audio(self) -> tuple[Any, list]:
        """Loudness / energy of the whole source for the early renders (once; the analysis reuses it)."""
        if self.audio is None:
            from .services.audio import analyze_audio

            feats, sil = None, []
            if self.ctx.audio_path is not None:
                feats = analyze_audio(self.ctx.audio_path, cancel_event=self.ctx.cancel_event)
                sil = list(feats.silences) if feats is not None and feats.silences is not None else []
            self.audio = (feats, sil)
        return self.audio


def preshipped_from(state: dict[str, Any]) -> list[dict[str, Any]]:
    from .services.selection import Candidate

    out = []
    for x in state.get("shorts") or []:
        item = dict(x["cand"])
        item["segments"] = [tuple(s) for s in item.get("segments") or []]
        out.append({"cand": Candidate(**item), "final": x.get("final") or {}, "key": x["key"],
                    "window": x.get("window")})
    return out


def _add_usage(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, (int, float)):
            out[k] = round(out.get(k, 0) + v, 3)
        elif k == "by_task" and isinstance(v, dict):
            bt = {t: dict(x) for t, x in (out.get("by_task") or {}).items()}
            for t, x in v.items():
                cur = bt.setdefault(t, {})
                for kk, vv in x.items():
                    cur[kk] = round(cur.get(kk, 0) + vv, 3)
            out["by_task"] = bt
    return out


def add_usage(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    return _add_usage(a, b)
