"""
Checkpoints for the intelligence pipeline.

Every stage writes its result to <work_dir>/intel/<stage>.json together with
a key: a hash of everything the result depends on (the transcript's content
hash, the settings that matter, the model, the prompt version). On the next
run – after a crash, a regeneration or a re-export – a stage whose key still
matches is loaded instead of recomputed. Writes are atomic (tmp + rename), so
a crash mid-write never leaves a half file that looks valid.

The store also counts what happened (hits, misses, resumed stages) for the
run report.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any, Optional


def key_of(*parts: Any) -> str:
    raw = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


class StageStore:
    def __init__(self, work_dir: Optional[Path], name: str = "intel") -> None:
        self.dir = (Path(work_dir) / name) if work_dir is not None else None
        self.hits = 0
        self.misses = 0
        self.resumed: list[str] = []
        self.written: list[str] = []
        self._lock = threading.Lock()

    def path(self, stage: str) -> Optional[Path]:
        return (self.dir / f"{stage}.json") if self.dir is not None else None

    def get(self, stage: str, key: str) -> Optional[Any]:
        p = self.path(stage)
        if p is None or not p.exists():
            with self._lock:
                self.misses += 1
            _prof(stage, False)
            return None
        try:
            data = json.loads(p.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            with self._lock:
                self.misses += 1
            return None
        if data.get("key") != key:
            with self._lock:
                self.misses += 1
            _prof(stage, False)
            return None
        _prof(stage, True)
        with self._lock:
            self.hits += 1
            if stage not in self.resumed:
                self.resumed.append(stage)
        return data.get("value")

    def put(self, stage: str, key: str, value: Any) -> Optional[Path]:
        p = self.path(stage)
        if p is None:
            return None
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"key": key, "value": value}, ensure_ascii=False), "utf-8")
        tmp.replace(p)
        with self._lock:
            if stage not in self.written:
                self.written.append(stage)
        return p

    def stats(self) -> dict[str, Any]:
        return {"hits": self.hits, "misses": self.misses, "resumed_stages": list(self.resumed),
                "written_stages": list(self.written)}


def _prof(stage: str, hit: bool) -> None:
    from ...util import profiler

    profiler.cache_event(f"stage:{stage.split('/')[0].split('_')[0]}", hit)
