"""
זיהוי פריסת המסך לאורך הזמן: מצלמת תגובה (facecam), תוכן, ומסך בלבד.

הבעיה: בשידורי תגובה האדם צופה בתוכן (YouTube, TikTok, משחק, אתר)
ומופיע בחלון מצלמה קטן. חיתוך אנכי שעוקב רק אחרי הפנים מאבד את
התוכן, וחיתוך מרכזי מאבד את הפנים. כדי להרכיב פריים שמראה את שניהם
צריך לדעת **איפה** כל אחד מהם נמצא – ולכל קטע בזמן בנפרד, כי
הסטרימר מחליף סצנות.

שיטה (בלי מודל מאומן, רק OpenCV):
  1. דגימת פריימים כל `sample_every` שניות ברוחב 960px. במקור ארוך
     מאוד – פענוח פריימי מפתח בלבד (`-skip_frame nokey`).
  2. זיהוי פנים (Haar חזיתי + פרופיל) בכל דגימה, ובניית „מסלולים":
     אותן פנים לאורך זמן. פנים של מצלמת תגובה כמעט לא זזות; פנים
     בתוך התוכן זזות, מתחלפות ונעלמות.
  3. מלבן המצלמה נמצא מקצוות ישרים **קבועים לאורך זמן** סביב
     הפנים: מפת „התמדה" של קצוות אנכיים ואופקיים על פני כל הדגימות
     שבהן המסלול נוכח. גבול של חלון מצלמה קיים בכל פריים; קצוות
     של התוכן זזים ונמחקים בממוצע.
  4. כל חלון של 8 שניות מסווג: "reaction" (מצלמה + תוכן), "camera"
     (המצלמה ממלאת את הפריים) או "screen" (תוכן בלבד).
  5. חלונות רצופים עם אותו סיווג ומלבן דומה (IoU) מתמזגים לקטעים,
     עם השהיה נגד הבהובים, והגבול בין קטעים מעודן לדיוק של בערך
     0.2 שנייה לפי חיתוך הסצנה בפועל.

כל הקואורדינטות מנורמלות ל-0..1 ביחס לפריים המקור.
"""

from __future__ import annotations

import logging
import math
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Optional

import numpy as np

from ..errors import JobCancelledError
from ..util.ffmpeg import ffmpeg_bin, probe

log = logging.getLogger("polixor.layout")

ProgressFn = Optional[Callable[[float], None]]

KINDS = ("reaction", "camera", "screen")

# ספים
LARGE_FACE_H = 0.16          # פנים בגובה כזה מהפריים = מצלמה מלאה
MIN_FACE_PRESENCE = 0.30     # שיעור הדגימות בחלון שבהן יש פנים
LINE_SCORE_MIN = 0.42        # התמדה מינימלית לקו של גבול מצלמה
MAX_CAM_AREA = 0.50          # מלבן מצלמה גדול מזה אינו "חלון מצלמה"
MIN_CAM_AREA = 0.012
STATIC_SPREAD = 0.035        # פיזור מרכז פנים שנחשב „לא זז"


# --------------------------------------------------------------------------
# מבני נתונים
# --------------------------------------------------------------------------
@dataclass
class Box:
    """מלבן מנורמל: x,y פינה שמאלית-עליונה; w,h רוחב וגובה."""

    x: float
    y: float
    w: float
    h: float

    @property
    def x1(self) -> float:
        return self.x + self.w

    @property
    def y1(self) -> float:
        return self.y + self.h

    @property
    def cx(self) -> float:
        return self.x + self.w / 2.0

    @property
    def cy(self) -> float:
        return self.y + self.h / 2.0

    @property
    def area(self) -> float:
        return max(0.0, self.w) * max(0.0, self.h)

    def intersection(self, other: "Box") -> float:
        iw = min(self.x1, other.x1) - max(self.x, other.x)
        ih = min(self.y1, other.y1) - max(self.y, other.y)
        return max(0.0, iw) * max(0.0, ih)

    def iou(self, other: Optional["Box"]) -> float:
        if other is None:
            return 0.0
        inter = self.intersection(other)
        union = self.area + other.area - inter
        return inter / union if union > 0 else 0.0

    def contains(self, other: "Box", tol: float = 0.0) -> bool:
        return (other.x >= self.x - tol and other.y >= self.y - tol
                and other.x1 <= self.x1 + tol and other.y1 <= self.y1 + tol)

    def clamp(self) -> "Box":
        x = min(1.0, max(0.0, self.x))
        y = min(1.0, max(0.0, self.y))
        return Box(x, y, max(0.0, min(1.0 - x, self.w)), max(0.0, min(1.0 - y, self.h)))

    def expand(self, fx: float, fy: Optional[float] = None) -> "Box":
        fy = fx if fy is None else fy
        nw, nh = self.w * fx, self.h * fy
        return Box(self.cx - nw / 2.0, self.cy - nh / 2.0, nw, nh).clamp()

    def aspect(self, src_w: int = 1, src_h: int = 1) -> float:
        return (self.w * src_w) / max(1e-6, self.h * src_h)

    def to_dict(self) -> dict[str, float]:
        return {"x": round(self.x, 4), "y": round(self.y, 4),
                "w": round(self.w, 4), "h": round(self.h, 4)}

    @classmethod
    def from_dict(cls, d: Optional[dict[str, Any]]) -> Optional["Box"]:
        if not d:
            return None
        try:
            return cls(float(d["x"]), float(d["y"]), float(d["w"]), float(d["h"]))
        except (KeyError, TypeError, ValueError):
            return None


@dataclass
class LayoutSegment:
    start: float
    end: float
    kind: str                           # reaction | camera | screen
    facecam: Optional[Box] = None       # מלבן חלון המצלמה (תגובה)
    face: Optional[Box] = None          # הפנים (חציון לאורך הקטע)
    content: Optional[Box] = None       # אזור התוכן, בלי פסים שחורים
    confidence: float = 0.0
    focus: Optional[tuple[float, float]] = None
    face_path: list[tuple[float, float, float]] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": round(self.start, 3), "end": round(self.end, 3),
            "kind": self.kind,
            "facecam": self.facecam.to_dict() if self.facecam else None,
            "face": self.face.to_dict() if self.face else None,
            "content": self.content.to_dict() if self.content else None,
            "confidence": round(self.confidence, 3),
            "focus": list(self.focus) if self.focus else None,
            "face_path": [[round(t, 2), round(x, 4), round(y, 4)]
                          for t, x, y in self.face_path],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LayoutSegment":
        focus = d.get("focus")
        return cls(
            start=float(d.get("start", 0.0)), end=float(d.get("end", 0.0)),
            kind=str(d.get("kind") or "screen"),
            facecam=Box.from_dict(d.get("facecam")),
            face=Box.from_dict(d.get("face")),
            content=Box.from_dict(d.get("content")),
            confidence=float(d.get("confidence") or 0.0),
            focus=(float(focus[0]), float(focus[1])) if focus else None,
            face_path=[(float(a), float(b), float(c))
                       for a, b, c in (d.get("face_path") or [])],
        )


@dataclass
class LayoutTimeline:
    segments: list[LayoutSegment] = field(default_factory=list)
    src_w: int = 0
    src_h: int = 0
    sample_every: float = 2.0
    duration: float = 0.0
    analyzed: bool = False
    note: str = ""
    samples: int = 0
    keyframes_only: bool = False

    def at(self, t: float) -> Optional[LayoutSegment]:
        for seg in self.segments:
            if seg.start - 1e-6 <= t < seg.end + 1e-6:
                return seg
        return self.segments[-1] if self.segments and t >= self.segments[-1].end else None

    def slice(self, t0: float, t1: float) -> "LayoutTimeline":
        """הקטעים שבתוך [t0, t1], חתוכים לגבולות ובזמנים יחסיים ל-t0."""
        out: list[LayoutSegment] = []
        for seg in self.segments:
            s, e = max(seg.start, t0), min(seg.end, t1)
            if e - s <= 1e-3:
                continue
            path = [(t - t0, x, y) for t, x, y in seg.face_path if s <= t <= e]
            out.append(LayoutSegment(
                start=s - t0, end=e - t0, kind=seg.kind, facecam=seg.facecam,
                face=seg.face, content=seg.content, confidence=seg.confidence,
                focus=seg.focus, face_path=path))
        return LayoutTimeline(segments=out, src_w=self.src_w, src_h=self.src_h,
                              sample_every=self.sample_every,
                              duration=max(0.0, t1 - t0), analyzed=self.analyzed,
                              note=self.note, samples=self.samples,
                              keyframes_only=self.keyframes_only)

    def seconds_by_kind(self) -> dict[str, float]:
        out = {k: 0.0 for k in KINDS}
        for seg in self.segments:
            out[seg.kind] = out.get(seg.kind, 0.0) + seg.duration
        return {k: round(v, 2) for k, v in out.items()}

    def dominant_facecam(self) -> Optional[Box]:
        """מלבן המצלמה שמכסה הכי הרבה זמן (למשל לפריסה מפוצלת ידנית)."""
        best: Optional[tuple[float, Box]] = None
        for seg in self.segments:
            if seg.kind != "reaction" or seg.facecam is None:
                continue
            total = sum(s.duration for s in self.segments
                        if s.facecam is not None and s.facecam.iou(seg.facecam) > 0.6)
            if best is None or total > best[0]:
                best = (total, seg.facecam)
        return best[1] if best else None

    def summary(self) -> dict[str, Any]:
        secs = self.seconds_by_kind()
        cam = self.dominant_facecam()
        facecam_segments = [s for s in self.segments if s.kind == "reaction" and s.facecam]
        return {
            "analyzed": self.analyzed,
            "seconds": secs,
            "segments": len(self.segments),
            "facecam_detected": cam is not None,
            "facecam_segments": len(facecam_segments),
            "facecam_box": cam.to_dict() if cam else None,
            "facecam_seconds": round(sum(s.duration for s in facecam_segments), 2),
            "samples": self.samples,
            "keyframes_only": self.keyframes_only,
            "note": self.note,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "segments": [s.to_dict() for s in self.segments],
            "src_w": self.src_w, "src_h": self.src_h,
            "sample_every": self.sample_every, "duration": round(self.duration, 3),
            "analyzed": self.analyzed, "note": self.note, "samples": self.samples,
            "keyframes_only": self.keyframes_only,
        }

    @classmethod
    def from_dict(cls, d: Optional[dict[str, Any]]) -> Optional["LayoutTimeline"]:
        if not d:
            return None
        return cls(
            segments=[LayoutSegment.from_dict(x) for x in d.get("segments") or []],
            src_w=int(d.get("src_w") or 0), src_h=int(d.get("src_h") or 0),
            sample_every=float(d.get("sample_every") or 2.0),
            duration=float(d.get("duration") or 0.0),
            analyzed=bool(d.get("analyzed")), note=str(d.get("note") or ""),
            samples=int(d.get("samples") or 0),
            keyframes_only=bool(d.get("keyframes_only")))


# --------------------------------------------------------------------------
# קריאת פריימים
# --------------------------------------------------------------------------
@dataclass
class _Sample:
    t: float
    faces: list[Box]
    gray_small: np.ndarray          # 240px, להשוואות מהירות
    edge_v: np.ndarray              # קצוות אנכיים (בינארי, חצי רזולוציה)
    edge_h: np.ndarray              # קצוות אופקיים
    luma: np.ndarray                # בהירות לחצי רזולוציה (לזיהוי פסים שחורים)


def _analysis_size(src_w: int, src_h: int, width: int) -> tuple[int, int]:
    w = min(width, max(64, src_w))
    h = max(2, int(round(w * src_h / max(1, src_w))))
    return w - w % 2, h - h % 2


def iter_frames(path: Path, *, width: int, height: int, interval: float,
                keyframes_only: bool = False, start: float = 0.0,
                end: Optional[float] = None,
                cancel_event: Optional[threading.Event] = None
                ) -> Iterator[tuple[float, np.ndarray]]:
    """
    מחזיר (זמן, פריים BGR) בדגימה כל `interval` שניות.

    במצב פריימי מפתח הזמן נקרא מ-showinfo, כי מרווחי פריימי המפתח
    אינם קבועים.
    """
    vf: list[str] = []
    if keyframes_only:
        vf.append(f"select='isnan(prev_selected_t)+gte(t-prev_selected_t\\,{interval:.3f})'")
        vf.append("showinfo")
    else:
        vf.append(f"fps=1/{max(0.01, interval):.5f}")
    vf.append(f"scale={width}:{height}:flags=area")

    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin",
           "-loglevel", "info" if keyframes_only else "error"]
    if keyframes_only:
        cmd += ["-skip_frame", "nokey"]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(path)]
    if end is not None and end > start:
        cmd += ["-t", f"{end - start:.3f}"]
    cmd += ["-an", "-sn", "-dn", "-vf", ",".join(vf)]
    if keyframes_only:
        cmd += ["-fps_mode", "passthrough"]
    cmd += ["-pix_fmt", "bgr24", "-f", "rawvideo", "-"]

    frame_bytes = width * height * 3
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            bufsize=frame_bytes * 2)
    times: list[float] = []
    cond = threading.Condition()
    done = {"stderr": False}

    def _drain() -> None:
        assert proc.stderr is not None
        for raw in iter(proc.stderr.readline, b""):
            line = raw.decode("utf-8", "ignore")
            if keyframes_only and "pts_time:" in line:
                try:
                    value = float(line.split("pts_time:", 1)[1].split()[0])
                except (ValueError, IndexError):
                    continue
                with cond:
                    times.append(value)
                    cond.notify_all()
        with cond:
            done["stderr"] = True
            cond.notify_all()

    reader = threading.Thread(target=_drain, daemon=True)
    reader.start()
    idx = 0
    try:
        assert proc.stdout is not None
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise JobCancelledError()
            buf = _read_exact(proc.stdout, frame_bytes)
            if buf is None:
                break
            if keyframes_only:
                with cond:
                    while len(times) <= idx and not done["stderr"]:
                        cond.wait(timeout=5.0)
                    t = times[idx] if idx < len(times) else start + idx * interval
            else:
                t = start + idx * interval
            idx += 1
            yield t, np.frombuffer(buf, dtype=np.uint8).reshape(height, width, 3)
    finally:
        for pipe in (proc.stdout,):
            try:
                if pipe:
                    pipe.close()
            except Exception:                         # noqa: BLE001
                pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        reader.join(timeout=2)


def _read_exact(stream, n: int) -> Optional[bytes]:
    chunks: list[bytes] = []
    remaining = n
    while remaining > 0:
        c = stream.read(remaining)
        if not c:
            return None
        chunks.append(c)
        remaining -= len(c)
    return b"".join(chunks)


# --------------------------------------------------------------------------
# גלאי פנים
# --------------------------------------------------------------------------
_CASCADES: dict[str, Any] = {}
_CASCADE_LOCK = threading.Lock()


def _cascade(name: str):
    with _CASCADE_LOCK:
        if name in _CASCADES:
            return _CASCADES[name]
        try:
            import cv2

            xml = Path(cv2.data.haarcascades) / name
            c = cv2.CascadeClassifier(str(xml)) if xml.exists() else None
            if c is not None and c.empty():
                c = None
        except Exception as exc:                      # noqa: BLE001
            log.warning("cascade %s unavailable: %s", name, exc)
            c = None
        _CASCADES[name] = c
        return c


def detectors_available() -> bool:
    return _cascade("haarcascade_frontalface_default.xml") is not None


def detect_faces(gray: np.ndarray) -> list[Box]:
    """פנים חזיתיות ופרופיל בפריים אפור. מחזיר מלבנים מנורמלים."""
    import cv2

    h, w = gray.shape[:2]
    # CLAHE ולא equalizeHist: איזון גלובלי על פריים צבעוני „מכבה" את
    # הניגודיות של פנים קטנות בחלון מצלמה, והגלאי מפספס אותן.
    eq = _clahe(gray)
    min_side = max(20, int(min(w, h) * 0.035))
    boxes: list[tuple[float, float, float, float]] = []
    frontal = _cascade("haarcascade_frontalface_default.xml")
    if frontal is not None:
        for (x, y, fw, fh) in frontal.detectMultiScale(
                eq, scaleFactor=1.1, minNeighbors=5, minSize=(min_side, min_side)):
            boxes.append((x, y, fw, fh))
    if not boxes:
        alt = _cascade("haarcascade_frontalface_alt2.xml")
        if alt is not None:
            for (x, y, fw, fh) in alt.detectMultiScale(
                    eq, scaleFactor=1.1, minNeighbors=5, minSize=(min_side, min_side)):
                boxes.append((x, y, fw, fh))
    # פרופיל – רק כשאין פנים חזיתיות. שני מעברי הפרופיל (רגיל והפוך) הם
    # כשני שלישים מזמן הזיהוי, ובפריים עם פנים חזיתיות הם מוצאים בעיקר את
    # אותן פנים שוב (ראו בדיקת הביצועים ב-TESTING_GUIDE)
    profile = _cascade("haarcascade_profileface.xml") if not boxes else None
    if profile is not None:
        for flip in (False, True):
            img = cv2.flip(eq, 1) if flip else eq
            for (x, y, fw, fh) in profile.detectMultiScale(
                    img, scaleFactor=1.1, minNeighbors=6,
                    minSize=(min_side, min_side)):
                if flip:
                    x = w - x - fw
                boxes.append((x, y, fw, fh))
    # איחוד כפילויות בין הגלאים
    merged: list[Box] = []
    for (x, y, fw, fh) in sorted(boxes, key=lambda b: -b[2] * b[3]):
        b = Box(x / w, y / h, fw / w, fh / h)
        if any(b.iou(m) > 0.3 or m.contains(b, 0.01) for m in merged):
            continue
        merged.append(b)
    return merged


_CLAHE: dict[str, Any] = {}


def _clahe(gray: np.ndarray) -> np.ndarray:
    import cv2

    c = _CLAHE.get("c")
    if c is None:
        c = _CLAHE["c"] = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return c.apply(gray)


def _edges(gray: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """קצוות אנכיים ואופקיים חדים, כמפות בוליאניות."""
    import cv2

    g = cv2.GaussianBlur(gray, (3, 3), 0).astype(np.int16)
    gx = np.zeros_like(g)
    gy = np.zeros_like(g)
    gx[:, 1:-1] = np.abs(g[:, 2:] - g[:, :-2])
    gy[1:-1, :] = np.abs(g[2:, :] - g[:-2, :])
    thr = 18
    ev = (gx > thr) & (gx > 2 * gy)
    eh = (gy > thr) & (gy > 2 * gx)
    return ev, eh


def _analyse_frame(t: float, frame: np.ndarray) -> _Sample:
    import cv2

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    faces = detect_faces(gray)
    half = cv2.resize(gray, (gray.shape[1] // 2, gray.shape[0] // 2),
                      interpolation=cv2.INTER_AREA)
    ev, eh = _edges(half)
    small_w = 240
    small = cv2.resize(gray, (small_w, max(2, int(small_w * gray.shape[0] / gray.shape[1]))),
                       interpolation=cv2.INTER_AREA)
    return _Sample(t=t, faces=faces, gray_small=small, edge_v=ev, edge_h=eh,
                   luma=half)


# --------------------------------------------------------------------------
# מסלולי פנים
# --------------------------------------------------------------------------
@dataclass
class _Track:
    boxes: list[tuple[int, Box]] = field(default_factory=list)   # (sample idx, box)

    @property
    def median(self) -> Box:
        arr = np.array([[b.x, b.y, b.w, b.h] for _, b in self.boxes], dtype=np.float32)
        m = np.median(arr, axis=0)
        return Box(float(m[0]), float(m[1]), float(m[2]), float(m[3]))

    @property
    def spread(self) -> float:
        """פיזור מרכז הפנים, כשבר מרוחב הפריים."""
        if len(self.boxes) < 2:
            return 0.0
        c = np.array([[b.cx, b.cy] for _, b in self.boxes], dtype=np.float32)
        return float(np.sqrt(((c - c.mean(axis=0)) ** 2).sum(axis=1)).mean())

    def matches(self, box: Box) -> bool:
        m = self.boxes[-1][1]
        ref = max(m.w, m.h, 1e-3)
        dist = math.hypot(box.cx - m.cx, box.cy - m.cy)
        size = max(box.w, m.w) / max(1e-3, min(box.w, m.w))
        return dist < 0.8 * ref and size < 1.8


def _build_tracks(samples: list[_Sample]) -> list[_Track]:
    tracks: list[_Track] = []
    for i, s in enumerate(samples):
        for box in s.faces:
            target = None
            for tr in tracks:
                if tr.boxes and tr.boxes[-1][0] >= i - 3 and tr.matches(box):
                    if target is None or tr.boxes[-1][1].iou(box) > target.boxes[-1][1].iou(box):
                        target = tr
            if target is None or any(j == i for j, _ in target.boxes):
                tracks.append(_Track(boxes=[(i, box)]))
            else:
                target.boxes.append((i, box))
    tracks = [t for t in tracks if len(t.boxes) >= 2]
    return _merge_tracks(tracks)


def _merge_tracks(tracks: list[_Track]) -> list[_Track]:
    """
    מאחד מסלולים של אותן פנים שנקטעו (פספוס של הגלאי כמה דגימות
    ברצף): מלבנים חופפים וטווחי זמן שאינם חופפים.
    """
    tracks = sorted(tracks, key=lambda t: t.boxes[0][0])
    merged: list[_Track] = []
    for tr in tracks:
        target = None
        for m in merged:
            if m.boxes[-1][0] < tr.boxes[0][0] and m.median.iou(tr.median) > 0.45:
                target = m
                break
        if target is None:
            merged.append(_Track(boxes=list(tr.boxes)))
        else:
            target.boxes.extend(tr.boxes)
    return merged


# --------------------------------------------------------------------------
# מלבן המצלמה מקצוות קבועים
# --------------------------------------------------------------------------
def _best_line(profile: np.ndarray, lo: int, hi: int, *,
               outward: int = 0) -> tuple[int, float]:
    """
    הקו החזק ביותר בטווח. `outward` (-1 שמאלה/למעלה, +1 ימינה/למטה):
    מבין הקווים שחזקים כמעט כמו החזק ביותר, נבחר החיצוני – גבול חלון
    המצלמה עוטף את מה שבתוכו (משקוף דלת בחדר הוא קו פנימי).
    """
    lo, hi = max(0, lo), min(profile.size, hi)
    if hi <= lo:
        return -1, 0.0
    seg = profile[lo:hi]
    i = int(np.argmax(seg))
    best = float(seg[i])
    if outward and best >= LINE_SCORE_MIN:
        strong = np.flatnonzero(seg >= max(LINE_SCORE_MIN, 0.85 * best))
        if strong.size:
            i = int(strong.min() if outward < 0 else strong.max())
    return lo + i, float(seg[i])


def find_facecam_box(samples: list[_Sample], indices: list[int], face: Box
                     ) -> tuple[Optional[Box], float]:
    """
    מאתר את מלבן חלון המצלמה סביב הפנים, מתוך מפת התמדה של קצוות.

    מחזיר (מלבן, ביטחון). None כשאין גבול ישר שמחזיק לאורך זמן –
    כלומר הפנים אינן בתוך חלון מצלמה (למשל מצלמה במסך מלא).
    """
    if len(indices) < 2:
        return None, 0.0
    ev = np.mean([samples[i].edge_v for i in indices], axis=0)
    eh = np.mean([samples[i].edge_h for i in indices], axis=0)
    H, W = ev.shape

    fx0, fx1 = int(face.x * W), int(face.x1 * W)
    fy0, fy1 = int(face.y * H), int(face.y1 * H)
    fw, fh = max(2, fx1 - fx0), max(2, fy1 - fy0)

    # שלב 1: טווח שורות משוער (הפנים ומעט מסביב) → גבולות אנכיים
    r0, r1 = max(0, fy0 - fh // 2), min(H, fy1 + fh)
    # הטווחים נכנסים מעט לתוך מלבן הפנים: במצלמה צמודה הראש נחתך
    # בגבול החלון, ומלבן הפנים של הגלאי חורג מעט מעבר לגבול.
    col = ev[r0:r1, :].mean(axis=0)
    left, ls = _best_line(col, fx0 - int(4.5 * fw), fx0 + int(0.2 * fw), outward=-1)
    right, rs = _best_line(col, fx1 - int(0.2 * fw), fx1 + int(4.5 * fw), outward=1)
    left_ok, right_ok = ls >= LINE_SCORE_MIN, rs >= LINE_SCORE_MIN
    x0 = left if left_ok else 0
    x1 = right if right_ok else W - 1

    # שלב 2: גבולות אופקיים בתוך העמודות שנמצאו
    c0, c1 = max(0, x0 + 1), min(W, x1)
    row = eh[:, c0:c1].mean(axis=1) if c1 > c0 else np.zeros(H)
    top, ts = _best_line(row, fy0 - int(4.0 * fh), fy0 + int(0.2 * fh), outward=-1)
    bottom, bs = _best_line(row, fy1 - int(0.1 * fh), fy1 + int(4.0 * fh), outward=1)
    top_ok, bottom_ok = ts >= LINE_SCORE_MIN, bs >= LINE_SCORE_MIN
    y0 = top if top_ok else 0
    y1 = bottom if bottom_ok else H - 1

    # שלב 3: עידון הגבולות האנכיים לפי השורות האמיתיות של החלון
    if y1 - y0 > 8:
        col = ev[y0 + 2:y1 - 1, :].mean(axis=0)
        if left_ok:
            left, ls = _best_line(col, x0 - 3, x0 + 4)
        if right_ok:
            right, rs = _best_line(col, x1 - 3, x1 + 4)
        x0 = left if left_ok else 0
        x1 = right if right_ok else W - 1

    lines = sum((left_ok, right_ok, top_ok, bottom_ok))
    if lines == 0:
        return None, 0.0
    # צד בלי קו חייב להיות צמוד לשולי הפריים – כך נראה חלון מצלמה בפינה
    box = Box(x0 / W, y0 / H, (x1 - x0) / W, (y1 - y0) / H).clamp()
    if not (MIN_CAM_AREA <= box.area <= MAX_CAM_AREA):
        return None, 0.0
    if not box.contains(face, tol=max(0.02, 0.25 * face.h)):
        return None, 0.0
    inside, outside = _activity_contrast(samples, indices, box)
    if lines == 1 or not ((left_ok or right_ok) and (top_ok or bottom_ok)):
        # קו אחד בלבד (למשל מצלמה בחצי המסך): מקבלים רק כשהצד השני
        # באמת נראה כמו תוכן – הרבה יותר תנועה מאשר בתוך המצלמה.
        # כך משקוף דלת מאחורי מצלמה במסך מלא אינו הופך ל„חלון מצלמה".
        if not (outside > 2.0 * inside and outside > 2.5):
            return None, 0.0
    scores = [s for s, ok in ((ls, left_ok), (rs, right_ok), (ts, top_ok), (bs, bottom_ok)) if ok]
    confidence = float(np.clip(np.mean(scores) * (0.7 + 0.075 * lines), 0.0, 1.0))
    return box, confidence


def _activity_contrast(samples: list[_Sample], indices: list[int], box: Box
                       ) -> tuple[float, float]:
    """שינוי ממוצע בין דגימות עוקבות: בתוך המלבן ומחוצה לו."""
    idx = sorted(indices)
    diffs = [np.abs(samples[b].gray_small.astype(np.int16)
                    - samples[a].gray_small.astype(np.int16))
             for a, b in zip(idx, idx[1:]) if b == a + 1]
    if not diffs:
        return 0.0, 0.0
    act = np.mean(diffs, axis=0)
    H, W = act.shape
    x0, x1 = int(box.x * W), int(math.ceil(box.x1 * W))
    y0, y1 = int(box.y * H), int(math.ceil(box.y1 * H))
    mask = np.zeros_like(act, dtype=bool)
    mask[y0:y1, x0:x1] = True
    inside = float(act[mask].mean()) if mask.any() else 0.0
    outside = float(act[~mask].mean()) if (~mask).any() else 0.0
    return inside, outside


def _content_box(samples: list[_Sample], indices: list[int],
                 cam: Optional[Box]) -> Box:
    """
    אזור התוכן: המלבן הגדול ביותר שאינו חופף את חלון המצלמה, בלי
    פסים שחורים בשולי הפריים.
    """
    luma = np.mean([samples[i].luma for i in indices], axis=0) if indices else None
    bars = Box(0.0, 0.0, 1.0, 1.0)
    if luma is not None:
        H, W = luma.shape
        rows = luma.mean(axis=1)
        cols = luma.mean(axis=0)
        dark_r = rows < 14
        dark_c = cols < 14
        top = 0
        while top < H // 3 and dark_r[top]:
            top += 1
        bottom = H - 1
        while bottom > H * 2 // 3 and dark_r[bottom]:
            bottom -= 1
        left = 0
        while left < W // 3 and dark_c[left]:
            left += 1
        right = W - 1
        while right > W * 2 // 3 and dark_c[right]:
            right -= 1
        bars = Box(left / W, top / H, (right + 1 - left) / W, (bottom + 1 - top) / H)

    if cam is None:
        return bars
    candidates = [
        Box(bars.x, bars.y, max(0.0, cam.x - bars.x), bars.h),                 # משמאל
        Box(cam.x1, bars.y, max(0.0, bars.x1 - cam.x1), bars.h),              # מימין
        Box(bars.x, bars.y, bars.w, max(0.0, cam.y - bars.y)),                 # מעל
        Box(bars.x, cam.y1, bars.w, max(0.0, bars.y1 - cam.y1)),              # מתחת
    ]
    candidates = [c.clamp() for c in candidates if c.w > 0.15 and c.h > 0.15]
    if not candidates:
        return bars
    src_aspect = bars.w / max(1e-6, bars.h)
    best = max(candidates, key=lambda c: c.area)
    close = [c for c in candidates if c.area >= best.area * 0.9]
    return min(close, key=lambda c: abs(math.log(max(1e-6, c.w / max(1e-6, c.h)) / src_aspect)))


# --------------------------------------------------------------------------
# הזיהוי עצמו
# --------------------------------------------------------------------------
def detect_layouts(
    video_path: str | Path,
    *,
    duration: float,
    src_w: int,
    src_h: int,
    sample_every: float = 2.0,
    analysis_width: int = 960,
    window_seconds: float = 8.0,
    cancel_event: Optional[threading.Event] = None,
    on_progress: ProgressFn = None,
    keyframes_only: Optional[bool] = None,
    refine_boundaries: bool = True,
    max_samples: int = 1500,
    min_segment_seconds: Optional[float] = None,
    start: float = 0.0,
    frames: Optional[Iterable[tuple[float, np.ndarray]]] = None,
    frame_size: Optional[tuple[int, int]] = None,
) -> LayoutTimeline:
    """
    מזהה את פריסת המסך לאורך הווידאו. ראו תיעוד המודול.

    `frames` – פריימים מפענוח משותף (services/frame_scan) במקום פענוח
    נפרד; `frame_size` – הרוחב והגובה שלהם.

    `start` > 0: ניתוח של הטווח [start, start+duration] בלבד; הזמנים
    בתוצאה יחסיים ל-start (detect_layouts_range מזיז אותם למוחלטים).
    """
    path = Path(video_path)
    tl = LayoutTimeline(src_w=src_w, src_h=src_h, duration=duration,
                        sample_every=sample_every)
    if not detectors_available():
        tl.note = "face_detector_unavailable"
        return tl
    if not src_w or not src_h or duration <= 0:
        info = probe(path)
        src_w, src_h = info.width or src_w, info.height or src_h
        duration = duration or info.duration
        tl.src_w, tl.src_h, tl.duration = src_w, src_h, duration
    if duration <= 0:
        tl.note = "no_duration"
        return tl

    interval = max(0.25, float(sample_every))
    if duration / interval > max_samples:
        interval = duration / max_samples
    if keyframes_only is None:
        keyframes_only = duration > 45 * 60
    tl.sample_every = round(interval, 3)
    tl.keyframes_only = bool(keyframes_only)

    w, h = frame_size or _analysis_size(src_w, src_h, analysis_width)
    samples: list[_Sample] = []
    t_start = time.time()
    source = frames if frames is not None else iter_frames(
        path, width=w, height=h, interval=interval, keyframes_only=keyframes_only,
        start=start, end=(start + duration) if start > 0 else None, cancel_event=cancel_event)
    for t, frame in source:
        # בטווח: זמנים יחסיים ל-start (החלונות למטה נבנים מ-0)
        t_rel = t - start if start > 0 else t
        samples.append(_analyse_frame(t_rel, frame))
        if on_progress and duration > 0:
            on_progress(min(0.92, t_rel / duration * 0.92))
    tl.samples = len(samples)
    if not samples:
        tl.note = "no_frames"
        return tl
    log.info("layout: %d samples in %.1fs (%s)", len(samples), time.time() - t_start,
             "keyframes" if keyframes_only else f"every {interval:.2f}s")

    tracks = _build_tracks(samples)
    track_info = _describe_tracks(samples, tracks)

    # ---- סיווג חלונות ----
    win = max(interval, float(window_seconds))
    n_win = max(1, int(math.ceil(duration / win)))
    windows: list[dict[str, Any]] = []
    for k in range(n_win):
        w0, w1 = k * win, min(duration, (k + 1) * win)
        idx = [i for i, s in enumerate(samples) if w0 <= s.t < w1]
        windows.append(_classify_window(samples, idx, tracks, track_info, w0, w1))

    _hysteresis(windows)
    segments = _merge_windows(windows, samples, tracks, track_info)

    if refine_boundaries and len(segments) > 1:
        _refine(path, segments, samples, src_w, src_h, cancel_event)

    # קטע קצר מאוד (שארית של חלון שסווג בשוגג) נבלע בשכנו
    min_len = min_segment_seconds if min_segment_seconds is not None else \
        max(1.0, interval * 0.75)
    segments = _absorb_short(segments, min_len)
    for seg in segments:
        _finalise_segment(seg, samples, tracks)

    tl.segments = segments
    tl.analyzed = True
    if on_progress:
        on_progress(1.0)
    return tl


def detect_layouts_range(video_path: str | Path, start: float, end: float, *,
                         src_w: int, src_h: int, sample_every: float = 2.0,
                         cancel_event: Optional[threading.Event] = None) -> LayoutTimeline:
    """זיהוי פריסה מפורט לחלון זמן אחד; זמני הקטעים מוחלטים."""
    tl = detect_layouts(video_path, duration=max(0.5, end - start), src_w=src_w, src_h=src_h,
                        sample_every=sample_every, keyframes_only=False,
                        refine_boundaries=False, start=start, cancel_event=cancel_event)
    for seg in tl.segments:
        seg.start += start
        seg.end += start
        seg.face_path = [(t + start, x, y) for t, x, y in seg.face_path]
    tl.duration = end
    return tl


def merge_layout_windows(base: Optional[LayoutTimeline],
                         parts: list[tuple[float, float, LayoutTimeline]],
                         duration: float) -> Optional[LayoutTimeline]:
    """
    ציר פריסות אחד: הקטעים המפורטים של החלונות, ומחוצה להם – הציר הגס
    (אם יש). משמש במצב FAST לשידורים ארוכים.
    """
    if base is None and not parts:
        return None
    ref = base or parts[0][2]
    segs: list[LayoutSegment] = []
    spans = sorted((a, b) for a, b, _ in parts)
    for seg in (base.segments if base else []):
        pieces = [(seg.start, seg.end)]
        for a, b in spans:
            nxt = []
            for x, y in pieces:
                if b <= x or a >= y:
                    nxt.append((x, y))
                    continue
                if a > x:
                    nxt.append((x, a))
                if b < y:
                    nxt.append((b, y))
            pieces = nxt
        for x, y in pieces:
            if y - x > 1e-3:
                segs.append(LayoutSegment(
                    start=x, end=y, kind=seg.kind, facecam=seg.facecam, face=seg.face,
                    content=seg.content, confidence=seg.confidence, focus=seg.focus,
                    face_path=[p for p in seg.face_path if x <= p[0] <= y]))
    for a, b, tl in parts:
        segs += [s for s in tl.segments if s.end > a and s.start < b]
    segs.sort(key=lambda s: s.start)
    return LayoutTimeline(segments=segs, src_w=ref.src_w, src_h=ref.src_h,
                          sample_every=ref.sample_every, duration=duration,
                          analyzed=any(t.analyzed for _, _, t in parts) or bool(base and base.analyzed),
                          note=ref.note, samples=(base.samples if base else 0)
                          + sum(t.samples for _, _, t in parts),
                          keyframes_only=bool(base and base.keyframes_only))


def _describe_tracks(samples: list[_Sample], tracks: list[_Track]) -> list[dict[str, Any]]:
    """לכל מסלול: חציון, פיזור, ומלבן מצלמה אם יש."""
    out = []
    for tr in tracks:
        face = tr.median
        idx = sorted({i for i, _ in tr.boxes})
        # הדגימות שבהן המסלול זוהה, ועוד השכנות הצמודות אליהן: פספוס
        # בודד של הגלאי לא מוריד את הדגימה ממפת ההתמדה, אבל קטע ארוך
        # בלי פנים (מסך בלבד) לא נכנס אליה
        span = sorted({j for i in idx for j in (i - 1, i, i + 1)
                       if 0 <= j < len(samples) and idx[0] <= j <= idx[-1]})
        cam, conf = (None, 0.0)
        if tr.spread <= STATIC_SPREAD:
            cam, conf = find_facecam_box(samples, span, face)
        out.append({"face": face, "spread": tr.spread, "cam": cam, "conf": conf,
                    "span": (idx[0], idx[-1])})
    return out


def _classify_window(samples: list[_Sample], idx: list[int], tracks: list[_Track],
                     info: list[dict[str, Any]], w0: float, w1: float) -> dict[str, Any]:
    base = {"start": w0, "end": w1, "kind": "screen", "track": None,
            "confidence": 0.5, "samples": idx}
    if not idx:
        return base
    idx_set = set(idx)
    best, best_hits = None, 0
    for ti, tr in enumerate(tracks):
        hits = sum(1 for i, _ in tr.boxes if i in idx_set)
        if hits == 0 or hits < 0.2 * len(idx):
            continue
        lo, hi = info[ti]["span"]
        # בתוך טווח המסלול – גם דגימות שבהן הגלאי פספס נחשבות נוכחות
        covered = sum(1 for i in idx if lo <= i <= hi)
        presence = max(hits, covered)
        if presence > best_hits or (presence == best_hits and best is not None
                                    and info[ti]["face"].area > info[best]["face"].area):
            best, best_hits = ti, presence
    if best is None or best_hits / max(1, len(idx)) < MIN_FACE_PRESENCE:
        faces_seen = sum(1 for i in idx if samples[i].faces)
        base["confidence"] = float(np.clip(1.0 - faces_seen / max(1, len(idx)), 0.5, 1.0))
        return base
    ti = info[best]
    face: Box = ti["face"]
    if ti["cam"] is not None:
        return {**base, "kind": "reaction", "track": best,
                "confidence": max(0.5, ti["conf"])}
    if face.h >= LARGE_FACE_H or (face.h >= 0.10 and ti["spread"] > STATIC_SPREAD):
        return {**base, "kind": "camera", "track": best,
                "confidence": float(np.clip(face.h / LARGE_FACE_H, 0.5, 1.0))}
    if ti["spread"] <= STATIC_SPREAD:
        # פנים קטנות וקבועות בלי גבול ישר: כנראה מצלמה עם שוליים רכים
        return {**base, "kind": "reaction", "track": best, "confidence": 0.45}
    return base


def _hysteresis(windows: list[dict[str, Any]]) -> None:
    """חלון בודד שחורג משני שכניו הזהים מאומץ לסיווג שלהם."""
    for i in range(1, len(windows) - 1):
        a, b, c = windows[i - 1], windows[i], windows[i + 1]
        if a["kind"] == c["kind"] != b["kind"] and b["confidence"] < 0.8:
            b["kind"] = a["kind"]
            b["track"] = a["track"] if a["kind"] != "screen" else None


def _segment_from(windows: list[dict[str, Any]], samples: list[_Sample],
                  info: list[dict[str, Any]]) -> LayoutSegment:
    first, last = windows[0], windows[-1]
    idx = [i for w in windows for i in w["samples"]]
    kind = first["kind"]
    track = first["track"]
    cam = info[track]["cam"] if (track is not None and kind == "reaction") else None
    face = info[track]["face"] if track is not None else None
    if kind == "reaction" and cam is None and face is not None:
        cam = face.expand(2.4, 2.6)
    content = _content_box(samples, idx, cam if kind == "reaction" else None)
    conf = float(np.mean([w["confidence"] for w in windows]))
    return LayoutSegment(start=first["start"], end=last["end"], kind=kind,
                         facecam=cam, face=face, content=content, confidence=conf,
                         focus=(content.cx, content.cy) if content else None)


def _merge_windows(windows: list[dict[str, Any]], samples: list[_Sample],
                   tracks: list[_Track], info: list[dict[str, Any]]) -> list[LayoutSegment]:
    groups: list[list[dict[str, Any]]] = []
    for w in windows:
        if groups:
            prev = groups[-1][-1]
            same = prev["kind"] == w["kind"]
            if same and w["kind"] in ("reaction", "camera"):
                a, b = prev["track"], w["track"]
                if a is not None and b is not None and a != b:
                    box_a = info[a]["cam"] or info[a]["face"]
                    box_b = info[b]["cam"] or info[b]["face"]
                    same = box_a.iou(box_b) > 0.6
            if same:
                groups[-1].append(w)
                continue
        groups.append([w])
    return [_segment_from(g, samples, info) for g in groups]


def _finalise_segment(seg: LayoutSegment, samples: list[_Sample],
                      tracks: list[_Track]) -> None:
    """
    אחרי עידון הגבולות: פנים, מסלול פנים ואזור תוכן לפי הדגימות
    שבאמת נמצאות בתוך הקטע (ולא לפי החלונות של 8 שניות).
    """
    inside = [i for i, smp in enumerate(samples) if seg.start <= smp.t < seg.end]
    if seg.face is not None:
        boxes = [b for tr in tracks for i, b in tr.boxes
                 if i in set(inside) and (b.iou(seg.face) > 0.2
                                          or (seg.facecam is not None
                                              and seg.facecam.contains(b, 0.02)))]
        if boxes:
            arr = np.array([[b.x, b.y, b.w, b.h] for b in boxes], dtype=np.float32)
            m = np.median(arr, axis=0)
            seg.face = Box(float(m[0]), float(m[1]), float(m[2]), float(m[3]))
        seg.face_path = [(samples[i].t, b.cx, b.cy)
                         for tr in tracks for i, b in tr.boxes
                         if i in set(inside) and seg.face is not None
                         and b.iou(seg.face) > 0.2]
    if inside:
        seg.content = _content_box(samples, inside,
                                   seg.facecam if seg.kind == "reaction" else None)
        seg.focus = (seg.content.cx, seg.content.cy)


def _absorb_short(segments: list[LayoutSegment], min_len: float) -> list[LayoutSegment]:
    """קטע קצר מ-min_len מצורף לשכן הארוך מבין שניהם."""
    segs = list(segments)
    changed = True
    while changed and len(segs) > 1:
        changed = False
        for i, seg in enumerate(segs):
            if seg.duration >= min_len:
                continue
            prev = segs[i - 1] if i > 0 else None
            nxt = segs[i + 1] if i + 1 < len(segs) else None
            target = max((x for x in (prev, nxt) if x is not None),
                         key=lambda x: x.duration)
            if target is prev:
                prev.end = seg.end
            else:
                nxt.start = seg.start
            segs.pop(i)
            changed = True
            break
    # שכנים זהים אחרי הבליעה מתאחדים
    out: list[LayoutSegment] = []
    for seg in segs:
        if out and out[-1].kind == seg.kind and (
                seg.kind == "screen"
                or (out[-1].facecam is not None and seg.facecam is not None
                    and out[-1].facecam.iou(seg.facecam) > 0.6)
                or (seg.kind == "camera")):
            out[-1].end = seg.end
            continue
        out.append(seg)
    return out


# --------------------------------------------------------------------------
# עידון גבולות
# --------------------------------------------------------------------------
def _sample_label(s: _Sample, seg: LayoutSegment) -> bool:
    """האם הדגימה נראית כמו הקטע (לפי הפנים שלו)."""
    if seg.kind == "screen":
        return not s.faces
    if seg.face is None:
        return bool(s.faces)
    return any(f.iou(seg.face) > 0.25 or seg.face.contains(f, 0.03) for f in s.faces)


def _refine(path: Path, segments: list[LayoutSegment], samples: list[_Sample],
            src_w: int, src_h: int, cancel_event: Optional[threading.Event]) -> None:
    """
    מזיז כל גבול בין קטעים למקום שבו הפריסה באמת מתחלפת.

    1. בין הדגימה האחרונה שנראית כמו הקטע הקודם לראשונה שנראית כמו
       הבא (ושתי הבאות אחריה מאשרות) – שם נמצא המעבר.
    2. בתוך המרווח הזה מפענחים ב-10 פריימים לשנייה ברזולוציה נמוכה,
       ומחפשים את הקפיצה החדה ביותר בין פריימים עוקבים (חיתוך סצנה).
    """
    for k in range(len(segments) - 1):
        a, b = segments[k], segments[k + 1]
        boundary = a.end
        lo_t, hi_t = max(a.start, boundary - 12.0), min(b.end, boundary + 12.0)
        cand = [i for i, smp in enumerate(samples) if lo_t <= smp.t <= hi_t]
        labels = []
        for i in cand:
            in_a, in_b = _sample_label(samples[i], a), _sample_label(samples[i], b)
            labels.append(1 if (in_b and not in_a) else (-1 if (in_a and not in_b) else 0))
        first_b = None
        for pos, lab in enumerate(labels):
            if lab != 1:
                continue
            following = [x for x in labels[pos + 1:pos + 3] if x != 0]
            if all(x == 1 for x in following):
                first_b = pos
                break
        if first_b is None:
            t0, t1 = max(a.start, boundary - 4.0), min(b.end, boundary + 4.0)
        else:
            t1 = samples[cand[first_b]].t
            prev_a = [pos for pos in range(first_b) if labels[pos] == -1]
            t0 = samples[cand[prev_a[-1]]].t if prev_a else max(lo_t, t1 - 8.0)
        if t1 <= t0:
            t0, t1 = max(a.start, boundary - 4.0), min(b.end, boundary + 4.0)
        cut = _scene_cut(path, t0, t1, cancel_event)
        new_boundary = cut if cut is not None else (t0 + t1) / 2.0
        new_boundary = float(np.clip(new_boundary, a.start + 0.2, b.end - 0.2))
        a.end = b.start = round(new_boundary, 2)


def _scene_cut(path: Path, t0: float, t1: float,
               cancel_event: Optional[threading.Event]) -> Optional[float]:
    """זמן הקפיצה החדה ביותר בין פריימים בטווח, או None אם אין קפיצה ברורה."""
    if t1 - t0 < 0.15:
        return (t0 + t1) / 2.0
    fps = 10.0
    frames: list[tuple[float, np.ndarray]] = []
    try:
        for t, frame in iter_frames(path, width=160, height=90, interval=1.0 / fps,
                                    start=max(0.0, t0 - 0.1), end=t1 + 0.1,
                                    cancel_event=cancel_event):
            frames.append((t, frame.astype(np.int16).mean(axis=2)))
    except JobCancelledError:
        raise
    except Exception as exc:                          # noqa: BLE001
        log.debug("scene-cut refine failed: %s", exc)
        return None
    if len(frames) < 3:
        return None
    diffs = [float(np.abs(frames[i][1] - frames[i - 1][1]).mean())
             for i in range(1, len(frames))]
    k = int(np.argmax(diffs))
    peak = diffs[k]
    rest = np.median(diffs) if len(diffs) > 2 else 0.0
    if peak < 6.0 or peak < 3.0 * max(1.0, rest):
        return None
    return frames[k + 1][0]


def frame_layout_probe(frame_bgr: np.ndarray) -> dict[str, Any]:
    """כלי אבחון: פנים וקצוות בפריים בודד (משמש בבדיקות)."""
    s = _analyse_frame(0.0, frame_bgr)
    return {"faces": [f.to_dict() for f in s.faces]}
