"""
שמירה וטעינה של תוצרי הניתוח על הדיסק.

שלב הניתוח (run_scope="analyze") שומר כאן את כל מה ששלב היצירה צריך,
כך שיצירה חוזרת (Regenerate) או המשך אחרי נפילה אינם מורידים, מתמללים
או מנתחים מחדש:

    audio_features.npz   AudioFeatures מלא (כל הערוצים)
    silences.json        קטעי שקט מ-silencedetect
    timeline.npz         ציר הזמן המלא (0.1 שנ') כולל ערוצים ומסכת דיבור
    visual.npz           אותות חזותיים (סצנה, תנועה, בהירות, הבזקים)
    faces.json           מלבני פנים לכל פריים שנדגם
    layouts.json         פריסות המסך (תגובה / מצלמה / מסך) לאורך הזמן
    candidates.json      המועמדים שנבחרו (לשחזור אחרי נפילה באמצע רינדור)

קבצים חסרים או פגומים לא מפילים דבר: הטוען מחזיר None, והקורא מחליט
אם לנתח מחדש.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

import numpy as np

from .audio import AudioFeatures
from .scoring import Timeline
from .visual import VisualFeatures

log = logging.getLogger("polixor.analysis_store")

AUDIO_FILE = "audio_features.npz"
SILENCES_FILE = "silences.json"
TIMELINE_FILE = "timeline.npz"
VISUAL_FILE = "visual.npz"
FACES_FILE = "faces.json"
LAYOUTS_FILE = "layouts.json"
CANDIDATES_FILE = "candidates.json"
CLIP_REVIEW_FILE = "clip_review.json"

_AUDIO_ARRAYS = ("times", "rms_db", "energy", "flux", "centroid", "zcr",
                 "jump", "silence", "laughter")
_AUDIO_SCALARS = ("sample_rate", "hop", "duration", "noise_floor_db",
                  "peak_db", "speech_ratio")
_TIMELINE_ARRAYS = ("times", "vocal", "speech", "visual", "pause", "chat",
                    "score", "speech_mask")
_VISUAL_ARRAYS = ("times", "scene", "motion", "brightness", "flash")


def _atomic_write_bytes(path: Path, save_fn) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    save_fn(tmp)
    tmp.replace(path)


def _write_json(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    tmp.replace(path)
    return path


def _read_json(path: Optional[Path]) -> Any:
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("unreadable analysis file %s: %s", path, exc)
        return None


# --------------------------------------------------------------------------
# אודיו
# --------------------------------------------------------------------------
def save_audio(work_dir: Path, feats: Optional[AudioFeatures]) -> Optional[Path]:
    if feats is None or feats.n == 0:
        return None
    path = work_dir / AUDIO_FILE
    arrays = {name: np.asarray(getattr(feats, name)) for name in _AUDIO_ARRAYS}
    scalars = {name: np.asarray(getattr(feats, name)) for name in _AUDIO_SCALARS}

    def _save(tmp: Path) -> None:
        with open(tmp, "wb") as fh:
            np.savez_compressed(fh, **arrays, **{f"s_{k}": v for k, v in scalars.items()})

    _atomic_write_bytes(path, _save)
    return path


def load_audio(path: Optional[Path]) -> Optional[AudioFeatures]:
    if path is None or not Path(path).exists():
        return None
    try:
        with np.load(path, allow_pickle=False) as data:
            feats = AudioFeatures()
            for name in _AUDIO_ARRAYS:
                if name in data:
                    arr = data[name]
                    setattr(feats, name, arr.astype(bool) if name == "silence"
                            else arr.astype(np.float32))
            for name in _AUDIO_SCALARS:
                key = f"s_{name}"
                if key in data:
                    value = data[key].item()
                    setattr(feats, name, int(value) if name == "sample_rate"
                            else float(value))
        return feats
    except Exception as exc:                          # noqa: BLE001
        log.warning("could not load audio features from %s: %s", path, exc)
        return None


# --------------------------------------------------------------------------
# שקט
# --------------------------------------------------------------------------
def save_silences(work_dir: Path, silences: list[tuple[float, float]]) -> Path:
    return _write_json(work_dir / SILENCES_FILE,
                       [[round(float(a), 3), round(float(b), 3)] for a, b in silences or []])


def load_silences(path: Optional[Path]) -> Optional[list[tuple[float, float]]]:
    data = _read_json(Path(path) if path else None)
    if not isinstance(data, list):
        return None
    out: list[tuple[float, float]] = []
    for item in data:
        try:
            out.append((float(item[0]), float(item[1])))
        except (TypeError, ValueError, IndexError):
            continue
    return out


# --------------------------------------------------------------------------
# ציר זמן
# --------------------------------------------------------------------------
def save_timeline(work_dir: Path, tl: Optional[Timeline]) -> Optional[Path]:
    if tl is None or tl.n == 0:
        return None
    path = work_dir / TIMELINE_FILE
    arrays = {name: np.asarray(getattr(tl, name)) for name in _TIMELINE_ARRAYS}

    def _save(tmp: Path) -> None:
        with open(tmp, "wb") as fh:
            np.savez_compressed(fh, **arrays, s_hop=np.asarray(tl.hop),
                                s_duration=np.asarray(tl.duration))

    _atomic_write_bytes(path, _save)
    return path


def load_timeline(path: Optional[Path]) -> Optional[Timeline]:
    if path is None or not Path(path).exists():
        return None
    try:
        with np.load(path, allow_pickle=False) as data:
            tl = Timeline(hop=float(data["s_hop"].item()),
                          duration=float(data["s_duration"].item()))
            for name in _TIMELINE_ARRAYS:
                if name in data:
                    arr = data[name]
                    setattr(tl, name, arr.astype(bool) if name == "speech_mask"
                            else arr.astype(np.float32))
        return tl if tl.n else None
    except Exception as exc:                          # noqa: BLE001
        log.warning("could not load timeline from %s: %s", path, exc)
        return None


# --------------------------------------------------------------------------
# וידאו
# --------------------------------------------------------------------------
def save_visual(work_dir: Path, vf: Optional[VisualFeatures]) -> dict[str, str]:
    """שומר אותות חזותיים ומלבני פנים. מחזיר את הנתיבים שנכתבו."""
    out: dict[str, str] = {}
    if vf is None or not vf.analyzed:
        return out
    path = work_dir / VISUAL_FILE
    arrays = {name: np.asarray(getattr(vf, name)) for name in _VISUAL_ARRAYS}

    def _save(tmp: Path) -> None:
        with open(tmp, "wb") as fh:
            np.savez_compressed(fh, **arrays, s_fps=np.asarray(vf.fps),
                                s_duration=np.asarray(vf.duration),
                                s_width=np.asarray(vf.width),
                                s_height=np.asarray(vf.height),
                                s_coverage=np.asarray(vf.coverage or [],
                                                      dtype=np.float64).reshape(-1, 2))

    _atomic_write_bytes(path, _save)
    out["visual_path"] = str(path)
    faces = {
        "fps": vf.fps, "duration": vf.duration,
        "width": vf.width, "height": vf.height,
        "faces": [[[round(v, 4) for v in box] for box in frame]
                  for frame in vf.faces],
    }
    out["faces_path"] = str(_write_json(work_dir / FACES_FILE, faces))
    return out


def load_visual(visual_path: Optional[Path],
                faces_path: Optional[Path]) -> Optional[VisualFeatures]:
    """טוען אותות חזותיים. בלי visual.npz – רק מלבני הפנים (תאימות לאחור)."""
    faces_data = _read_json(Path(faces_path) if faces_path else None)
    faces = []
    if isinstance(faces_data, dict):
        faces = [[tuple(box) for box in frame] for frame in faces_data.get("faces", [])]

    vf: Optional[VisualFeatures] = None
    if visual_path and Path(visual_path).exists():
        try:
            with np.load(visual_path, allow_pickle=False) as data:
                vf = VisualFeatures(
                    fps=float(data["s_fps"].item()),
                    duration=float(data["s_duration"].item()),
                    width=int(data["s_width"].item()),
                    height=int(data["s_height"].item()),
                    analyzed=True)
                for name in _VISUAL_ARRAYS:
                    if name in data:
                        setattr(vf, name, data[name].astype(np.float32))
                if "s_coverage" in data:
                    vf.coverage = [(float(a), float(b)) for a, b in data["s_coverage"]]
        except Exception as exc:                      # noqa: BLE001
            log.warning("could not load visual features: %s", exc)
            vf = None

    if vf is None:
        if not isinstance(faces_data, dict) or not faces:
            return None
        vf = VisualFeatures(
            fps=float(faces_data.get("fps") or 1.0),
            duration=float(faces_data.get("duration") or 0.0),
            width=int(faces_data.get("width") or 0),
            height=int(faces_data.get("height") or 0),
            analyzed=True)
        vf.times = np.arange(len(faces), dtype=np.float32) / max(1e-6, vf.fps)
    vf.faces = faces if faces else [[] for _ in range(vf.n)]
    if vf.n == 0 and faces:
        vf.times = np.arange(len(faces), dtype=np.float32) / max(1e-6, vf.fps)
    return vf


# --------------------------------------------------------------------------
# פריסות
# --------------------------------------------------------------------------
def save_layouts(work_dir: Path, layouts: Any) -> Optional[Path]:
    if layouts is None:
        return None
    data = layouts.to_dict() if hasattr(layouts, "to_dict") else layouts
    return _write_json(work_dir / LAYOUTS_FILE, data)


def load_layouts_data(path: Optional[Path]) -> Optional[dict[str, Any]]:
    data = _read_json(Path(path) if path else None)
    return data if isinstance(data, dict) else None


# --------------------------------------------------------------------------
# מועמדים
# --------------------------------------------------------------------------
def save_clip_review(work_dir: Path, review: dict[str, Any]) -> Path:
    """דוח הבחירה של clip_intel: למה כל קליפ נבחר ולמה כל "כמעט" נדחה."""
    return _write_json(work_dir / CLIP_REVIEW_FILE, review)


def load_clip_review(path: Optional[Path]) -> Optional[dict[str, Any]]:
    data = _read_json(Path(path) if path else None)
    return data if isinstance(data, dict) else None


def save_candidates(work_dir: Path, groups: dict[str, list[Any]]) -> Path:
    data = {kind: [_cand_to_dict(c) for c in cands]
            for kind, cands in (groups or {}).items()}
    return _write_json(work_dir / CANDIDATES_FILE, data)


def load_candidates(path: Optional[Path]) -> Optional[dict[str, list[Any]]]:
    from .selection import Candidate

    data = _read_json(Path(path) if path else None)
    if not isinstance(data, dict):
        return None
    out: dict[str, list[Any]] = {}
    for kind, items in data.items():
        cands = []
        for item in items or []:
            try:
                item = dict(item)
                item["segments"] = [tuple(s) for s in item.get("segments") or []]
                cands.append(Candidate(**item))
            except TypeError as exc:
                log.warning("bad candidate record: %s", exc)
                return None
        out[kind] = cands
    return out


def _cand_to_dict(c: Any) -> dict[str, Any]:
    d = asdict(c)
    d["segments"] = [[float(a), float(b)] for a, b in d.get("segments") or []]
    return d
