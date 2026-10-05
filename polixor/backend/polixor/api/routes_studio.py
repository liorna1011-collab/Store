"""
Polixor Studio: results, review (QA mode), downloads, storage and quality metrics.

  GET  /api/studio/projects/{pid}/results     Shorts and long-form of a project, grouped
                                               publish-ready / needs attention / not verified,
                                               with the user's review of each clip
  PUT  /api/clips/{cid}/review                 save a QA rating and/or approve / reject
  GET  /api/studio/projects/{pid}/reviews.json export the project's reviews (with what the
                                               system knew about each clip)
  GET  /api/studio/projects/{pid}/download     zip: kind=shorts | long | package
  GET  /api/studio/projects/{pid}/storage      storage by class; POST …/cleanup removes temp only
  GET  /api/studio/metrics                     quality metrics across projects (approval rate…)

Media plays from /api/clips/{cid}/file (range requests) – never from local paths.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import PATHS
from ..models import Clip, ClipKind, ClipReview, ClipStatus, Job
from ..services import storage
from ..util.fs import safe_filename
from ..db import db_dependency
from .http import api_error

router = APIRouter(prefix="/api", tags=["studio"])

POST_VALUES = ("yes", "small_fix", "no")


class ReviewIn(BaseModel):
    post: Optional[Literal["yes", "small_fix", "no", ""]] = None
    hook: Optional[Literal["yes", "no", ""]] = None
    story: Optional[Literal["yes", "no", ""]] = None
    subtitles: Optional[Literal["good", "text", "timing", ""]] = None
    edit: Optional[Literal["good", "cut", "pacing", "framing", "other", ""]] = None
    note: Optional[str] = None
    decision: Optional[Literal["approved", "rejected", ""]] = None


def _features(clip: Clip) -> dict[str, Any]:
    """What the system knew about the clip when it was made (for later analysis; never learned from live)."""
    rp = clip.render_params or {}
    q = rp.get("quality") or {}
    ed = q.get("editor") or {}
    last = (ed.get("history") or [{}])[-1] if ed.get("history") else {}
    return {"kind": clip.kind.value, "duration": round(clip.duration or 0, 2), "score": clip.score,
            "type": q.get("type"), "topic": q.get("topic"), "engine": q.get("engine"),
            "editor_verdict": ed.get("verdict"), "editor_checks": last.get("checks"),
            "editor_scores": last.get("scores"), "publish": rp.get("publish"),
            "subtitle_timing": {k: v for k, v in (rp.get("subtitle_timing") or {}).items() if k != "examples"},
            "final_transcript": q.get("final_transcript"), "profile": q.get("profile"),
            "segments": len(clip.segments_json or []) or 1, "status": clip.status.value}


def _review_out(r: Optional[ClipReview]) -> dict[str, Any]:
    if r is None:
        return {}
    return {"post": r.post, "hook": r.hook, "story": r.story, "subtitles": r.subtitles, "edit": r.edit,
            "note": r.note, "decision": r.decision,
            "updated_at": (r.updated_at or r.created_at).isoformat() if (r.updated_at or r.created_at) else None}


def _group(clip: Clip) -> str:
    if clip.status in (ClipStatus.PENDING, ClipStatus.RENDERING):
        return "in_progress"
    if clip.status == ClipStatus.FAILED or not clip.file_path or not Path(clip.file_path).exists():
        return "failed"
    pub = (clip.render_params or {}).get("publish")
    if pub is None:                       # made before publish verdicts existed
        return "ready" if clip.status == ClipStatus.READY else "attention"
    if pub.get("ready"):
        return "ready"
    return "attention" if pub.get("verified") else "unverified"


def _reason(clip: Clip) -> str:
    rp = clip.render_params or {}
    q = rp.get("quality") or {}
    ed = (q.get("editorial") or {})
    return (clip.reason or q.get("reason") or "") + (f" — {ed.get('content_hook')}" if ed.get("content_hook") else "")


def _clip_out(clip: Clip, review: Optional[ClipReview]) -> dict[str, Any]:
    rp = clip.render_params or {}
    q = rp.get("quality") or {}
    ed = q.get("editorial") or {}
    pub = rp.get("publish") or {}
    return {
        "id": clip.id, "kind": clip.kind.value, "status": clip.status.value, "group": _group(clip),
        "title": clip.title, "description": clip.description, "why": _reason(clip),
        "duration": round(clip.duration or 0, 2), "width": clip.width, "height": clip.height,
        "source_start": clip.source_start, "source_end": clip.source_end,
        "publish": {"ready": bool(pub.get("ready")), "verified": bool(pub.get("verified")),
                    "reasons": pub.get("reasons") or []},
        "social": {"caption": ed.get("hook") or "", "titles": [ed.get("title")] if ed.get("title") else []},
        "error": clip.error,
        "media": {"video": f"/api/clips/{clip.id}/file", "thumbnail": f"/api/clips/{clip.id}/thumbnail",
                  "download": f"/api/clips/{clip.id}/download", "srt": f"/api/clips/{clip.id}/subtitles.srt"},
        "review": _review_out(review),
    }


@router.get("/studio/projects/{pid}/results")
def results(pid: str, db: Session = Depends(db_dependency)) -> dict[str, Any]:
    job = db.get(Job, pid)
    if job is None:
        raise api_error("job_not_found", 404)
    clips = db.query(Clip).filter(Clip.job_id == pid).all()
    reviews = {r.clip_id: r for r in db.query(ClipReview).filter(ClipReview.job_id == pid).all()}
    shorts = [_clip_out(c, reviews.get(c.id)) for c in clips if c.kind != ClipKind.LONG]
    longs = [_clip_out(c, reviews.get(c.id)) for c in clips if c.kind == ClipKind.LONG]
    order = {"ready": 0, "attention": 1, "unverified": 2, "in_progress": 3, "failed": 4}
    shorts.sort(key=lambda c: (order.get(c["group"], 9), -float(c.get("duration") and 0 or 0)))
    longs.sort(key=lambda c: (order.get(c["group"], 9), c["source_start"]))
    arts = job.artifacts or {}
    review_doc = _read_json(arts.get("clip_review_path")) or {}
    intel = _read_json(arts.get("intel_report_path")) or {}
    others = [{"start": r.get("start"), "end": r.get("end"), "title": (r.get("hook") or {}).get("text", "")[:140],
               "reason": (r.get("rejection") or {}).get("text") or "", "status": r.get("status")}
              for r in (review_doc.get("near_misses") or [])[:40]]
    return {"project_id": pid, "shorts": shorts, "long": longs,
            "summary": {"publish_ready": sum(1 for c in shorts + longs if c["group"] == "ready"),
                        "shorts_ready": sum(1 for c in shorts if c["group"] == "ready"),
                        "long_ready": sum(1 for c in longs if c["group"] == "ready"),
                        "mode": intel.get("mode") or review_doc.get("mode") or "",
                        "mode_reason": intel.get("reason") or review_doc.get("mode_reason") or "",
                        "profile": intel.get("profile") or {},
                        "candidates": len(intel.get("pool") or []),
                        "rejected_by_editor": len(intel.get("editor_rejected") or [])},
            "other_candidates": others}


def _read_json(path: Any) -> Any:
    try:
        return json.loads(Path(path).read_text("utf-8")) if path else None
    except (OSError, ValueError):
        return None


@router.put("/clips/{cid}/review")
def put_review(cid: str, payload: ReviewIn, db: Session = Depends(db_dependency)) -> dict[str, Any]:
    clip = db.get(Clip, cid)
    if clip is None:
        raise api_error("clip_not_found", 404)
    r = db.get(ClipReview, cid)
    if r is None:
        r = ClipReview(clip_id=cid, job_id=clip.job_id, features=_features(clip))
        db.add(r)
    for k, v in payload.model_dump(exclude_none=True).items():
        setattr(r, k, v.strip()[:2000] if isinstance(v, str) and k == "note" else v)
    if not r.features:
        r.features = _features(clip)
    db.commit()
    db.refresh(r)
    return {"clip_id": cid, "review": _review_out(r)}


@router.get("/studio/projects/{pid}/reviews.json")
def export_reviews(pid: str, db: Session = Depends(db_dependency)):
    job = db.get(Job, pid)
    if job is None:
        raise api_error("job_not_found", 404)
    clips = {c.id: c for c in db.query(Clip).filter(Clip.job_id == pid).all()}
    rows = []
    for r in db.query(ClipReview).filter(ClipReview.job_id == pid).all():
        c = clips.get(r.clip_id)
        rows.append({"clip_id": r.clip_id, "title": c.title if c else "", "kind": c.kind.value if c else "",
                     "source_start": c.source_start if c else None, "source_end": c.source_end if c else None,
                     "review": _review_out(r), "features": r.features or (_features(c) if c else {})})
    doc = {"project_id": pid, "title": job.title, "reviews": rows, "summary": _summary(rows)}
    return JSONResponse(doc, headers={"Content-Disposition":
                                      f'attachment; filename="polixor_review_{pid}.json"'})


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    rated = [r for r in rows if r["review"].get("post")]
    n = len(rated)

    def share(key: str, value: str) -> Optional[float]:
        k = [r for r in rows if r["review"].get(key)]
        return round(sum(1 for r in k if r["review"][key] == value) / len(k), 3) if k else None
    return {"rated": n,
            "approval_rate": round(sum(1 for r in rated if r["review"]["post"] == "yes") / n, 3) if n else None,
            "small_fix_rate": round(sum(1 for r in rated if r["review"]["post"] == "small_fix") / n, 3) if n else None,
            "rejection_rate": round(sum(1 for r in rated if r["review"]["post"] == "no") / n, 3) if n else None,
            "hook_failure_rate": share("hook", "no"), "story_failure_rate": share("story", "no"),
            "subtitle_issue_rate": (lambda k: round(sum(1 for r in k if r["review"]["subtitles"] != "good") / len(k), 3)
                                    if k else None)([r for r in rows if r["review"].get("subtitles")])}


@router.get("/studio/metrics")
def metrics(db: Session = Depends(db_dependency)) -> dict[str, Any]:
    """Quality across projects. The key number: how many SURFACED (publish-ready) clips the user approves."""
    per: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    for job in db.query(Job).order_by(Job.created_at.desc()).limit(200).all():
        clips = db.query(Clip).filter(Clip.job_id == job.id).all()
        if not clips:
            continue
        reviews = {r.clip_id: r for r in db.query(ClipReview).filter(ClipReview.job_id == job.id).all()}
        intel = _read_json((job.artifacts or {}).get("intel_report_path")) or {}
        surfaced = [c for c in clips if _group(c) == "ready"]
        rows = [{"review": _review_out(reviews[c.id])} for c in surfaced if c.id in reviews]
        all_rows += rows
        per.append({"project_id": job.id, "title": job.title, "candidates": len(intel.get("pool") or []),
                    "finalists": len(intel.get("shipped") or []) + len(intel.get("editor_rejected") or []),
                    "rejected_by_editor": len(intel.get("editor_rejected") or []),
                    "surfaced": len(surfaced), "reviewed": len(rows), **_summary(rows)})
    return {"overall": _summary(all_rows), "projects": per,
            "note": "Rates are over surfaced (publish-ready) clips the user rated. Nothing is learned "
                    "automatically from these numbers."}


@router.get("/studio/projects/{pid}/download")
def download(pid: str, kind: Literal["shorts", "long", "package"] = Query("package"),
             db: Session = Depends(db_dependency)):
    job = db.get(Job, pid)
    if job is None:
        raise api_error("job_not_found", 404)
    clips = [c for c in db.query(Clip).filter(Clip.job_id == pid).all()
             if c.file_path and Path(c.file_path).exists() and c.status != ClipStatus.FAILED]
    if kind == "shorts":
        clips = [c for c in clips if c.kind != ClipKind.LONG]
    elif kind == "long":
        clips = [c for c in clips if c.kind == ClipKind.LONG]
    else:
        clips = [c for c in clips if _group(c) in ("ready", "attention", "unverified")]
    if not clips:
        raise api_error("no_files", 404)
    from .routes_clips import _cues_for_render
    from ..services import subtitles as sub_svc

    tmp = PATHS.work / f"polixor_{kind}_{pid}.zip"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    manifest = []
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_STORED) as zf:
        used: set[str] = set()
        for c in sorted(clips, key=lambda x: (x.kind.value, x.source_start)):
            folder = "long" if c.kind == ClipKind.LONG else "shorts"
            base = safe_filename(c.title or c.id, max_length=60)
            name, i = f"{folder}/{base}.mp4", 2
            while name in used:
                name, i = f"{folder}/{base} ({i}).mp4", i + 1
            used.add(name)
            zf.write(c.file_path, arcname=name)
            cues = _cues_for_render(db, c.id)
            if cues:
                srt = PATHS.work / f"{c.id}.zip.srt"
                sub_svc.write_srt(cues, srt)
                zf.write(srt, arcname=name[:-4] + ".srt")
                srt.unlink(missing_ok=True)
            out = _clip_out(c, None)
            manifest.append({"file": name, "title": c.title, "description": c.description, "why": out["why"],
                             "duration": out["duration"], "publish_ready": out["group"] == "ready",
                             "caption": out["social"]["caption"], "source_start": c.source_start,
                             "source_end": c.source_end})
        if kind == "package":
            zf.writestr("package.json", json.dumps({"project": job.title, "clips": manifest},
                                                   ensure_ascii=False, indent=1))

    def _iter():
        with tmp.open("rb") as fh:
            while chunk := fh.read(1024 * 1024):
                yield chunk
        tmp.unlink(missing_ok=True)

    fname = f"polixor_{safe_filename(job.title or pid, max_length=40)}_{kind}.zip"
    from .routes_clips import content_disposition
    return StreamingResponse(_iter(), media_type="application/zip",
                             headers={"Content-Disposition": content_disposition(fname)})


@router.get("/studio/projects/{pid}/storage")
def project_storage(pid: str) -> dict[str, Any]:
    u = storage.usage(pid)
    if u is None:
        raise api_error("job_not_found", 404)
    return u


@router.post("/studio/projects/{pid}/storage/cleanup")
def project_cleanup(pid: str, dry_run: bool = Query(False)) -> dict[str, Any]:
    out = storage.cleanup(pid, dry_run=dry_run)
    if out.get("error") == "not_found":
        raise api_error("job_not_found", 404)
    if out.get("error") == "busy":
        raise api_error("project_busy", 409)
    return out


@router.get("/studio/storage")
def storage_overview() -> dict[str, Any]:
    return storage.overview()


@router.get("/studio/projects/{pid}/diagnostics")
def project_diagnostics(pid: str, db: Session = Depends(db_dependency)) -> dict[str, Any]:
    """Timing profile and the editor gate's outcome, from what the run recorded (nothing is re-run)."""
    from ..services import diagnostics

    job = db.get(Job, pid)
    if job is None:
        raise api_error("job_not_found", 404)
    return {"performance": diagnostics.profile(job), "gate": diagnostics.gate(job)}
