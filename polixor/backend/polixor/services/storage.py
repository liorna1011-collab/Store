"""
Storage of a project, by class, and safe cleanup.

Every file of a project falls in one class:

  source      the uploaded / imported video (kept)
  outputs     finished clips, their thumbnails and subtitle files (kept)
  artifacts   checkpoints a resume or a re-edit needs: transcripts, the
              semantic stages, timelines, analysis (kept)
  caches      language-model answers (kept: they make re-runs free)
  temp        reconstructable leftovers: render part folders, *.tmp files,
              partial subtitle files, renders of clips that failed, export
              files no clip points to (duplicates of older renders)

cleanup() deletes ONLY temp. It never touches a file a clip points to, a
checkpoint, the source or a cache, and it skips a project that is being
processed. dry_run=True reports what it would free.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Iterable, Optional

from ..config import PATHS
from ..db import session_scope
from ..models import Clip, ClipStatus, Job, JobStatus

log = logging.getLogger("polixor.storage")

CLASSES = ("source", "outputs", "artifacts", "caches", "temp")
_TEMP_NAME = re.compile(r"(\.tmp$|\.part$|_part_?\d+.*\.ass$|_re\.ass$|^concat\.txt$)")


def _files(root: Path) -> Iterable[Path]:
    if root.exists():
        yield from (p for p in root.rglob("*") if p.is_file())


def _size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def classify(job: Job, clips: list[Clip]) -> dict[str, list[Path]]:
    out: dict[str, list[Path]] = {k: [] for k in CLASSES}
    arts = job.artifacts or {}
    src = Path(arts.get("source_path") or "")
    if src.is_file():
        out["source"].append(src)
    kept_files, kept_stems = set(), set()
    for c in clips:
        for p in (c.file_path, c.thumbnail_path):
            if p and c.status != ClipStatus.FAILED:
                kept_files.add(Path(p).resolve())
                kept_stems.add(Path(p).stem)
    failed = {Path(c.file_path).resolve() for c in clips if c.file_path and c.status == ClipStatus.FAILED}
    work = PATHS.work / job.id
    for p in _files(work):
        rel = p.relative_to(work).parts
        if "llm" in rel and "intel" in rel:
            out["caches"].append(p)
        elif _TEMP_NAME.search(p.name) or any(x.startswith(".") and x.endswith("_parts") for x in rel[:-1]):
            out["temp"].append(p)
        else:
            out["artifacts"].append(p)
    for root in {PATHS.exports / job.id}:
        for p in _files(root):
            r = p.resolve()
            if r in kept_files or p.stem in kept_stems:
                out["outputs"].append(p)
            elif r in failed or _TEMP_NAME.search(p.name) or p.suffix.lower() in (".mp4", ".jpg", ".png", ".srt"):
                out["temp"].append(p)           # a failed render, or a file no clip points to any more
            else:
                out["outputs"].append(p)        # unknown files are kept
    # outputs written outside the project's export folder (custom export dir) are outputs too
    for p in kept_files:
        if p.exists() and p not in {x.resolve() for x in out["outputs"]}:
            out["outputs"].append(p)
    return out


def usage(job_id: str) -> Optional[dict[str, Any]]:
    with session_scope() as s:
        job = s.get(Job, job_id)
        if job is None:
            return None
        clips = s.query(Clip).filter(Clip.job_id == job_id).all()
        classes = classify(job, clips)
        busy = job.status in (JobStatus.QUEUED, JobStatus.RUNNING)
    return {"job_id": job_id, "busy": busy,
            "bytes": {k: sum(_size(p) for p in v) for k, v in classes.items()},
            "files": {k: len(v) for k, v in classes.items()},
            "reclaimable_bytes": sum(_size(p) for p in classes["temp"])}


def cleanup(job_id: str, *, dry_run: bool = False) -> dict[str, Any]:
    """Deletes the project's temp files only (see the module doc). Never while it is processing."""
    with session_scope() as s:
        job = s.get(Job, job_id)
        if job is None:
            return {"job_id": job_id, "error": "not_found", "deleted": 0, "freed_bytes": 0}
        if job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
            return {"job_id": job_id, "error": "busy", "deleted": 0, "freed_bytes": 0}
        clips = s.query(Clip).filter(Clip.job_id == job_id).all()
        temp = classify(job, clips)["temp"]
    freed, deleted = 0, 0
    for p in temp:
        n = _size(p)
        if not dry_run:
            try:
                p.unlink()
            except OSError:
                continue
        freed += n
        deleted += 1
    if not dry_run:
        for d in sorted({p.parent for p in temp}, key=lambda x: -len(x.parts)):
            try:
                if d.exists() and not any(d.iterdir()) and d.name.endswith("_parts"):
                    d.rmdir()
            except OSError:
                pass
        log.info("storage cleanup of %s: %d files, %.1f MB", job_id, deleted, freed / 1e6)
    return {"job_id": job_id, "dry_run": dry_run, "deleted": deleted, "freed_bytes": freed}


def overview() -> dict[str, Any]:
    with session_scope() as s:
        ids = [j.id for j in s.query(Job).all()]
    rows = [u for u in (usage(i) for i in ids) if u]
    total = {k: sum(r["bytes"][k] for r in rows) for k in CLASSES}
    return {"projects": rows, "bytes": total, "reclaimable_bytes": sum(r["reclaimable_bytes"] for r in rows),
            "free_bytes": PATHS.free_bytes()}
