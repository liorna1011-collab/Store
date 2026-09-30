"""
ניתוח חזותי מדגמי עם OpenCV.

הפריימים נשאבים דרך צינור FFmpeg בקצב דגימה נמוך וברזולוציה מוקטנת,
כך שאין טעינה של הווידאו לזיכרון ואין seek איטי. כל פריים מעובד ומשוחרר.

אותות:
  scene       – שינוי סצנה (מרחק היסטוגרמות בין פריימים עוקבים)
  motion      – עוצמת תנועה (הפרש אבסולוטי ממוצע)
  brightness  – בהירות ממוצעת (זיהוי חשכה/הבזקים)
  flash       – קפיצת בהירות פתאומית
  faces       – מלבני פנים מנורמלים לכל פריים שנדגם (ל-Smart Reframe)
"""

from __future__ import annotations

import logging
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from .. import i18n
from ..errors import JobCancelledError, PolixorError
from ..util.ffmpeg import ffmpeg_bin

log = logging.getLogger("polixor.visual")

ProgressFn = Optional[Callable[[float], None]]

# מלבן פנים מנורמל: (x, y, w, h) בטווח 0..1 ביחס לפריים
FaceBox = tuple[float, float, float, float]


@dataclass
class VisualFeatures:
    fps: float = 1.0
    duration: float = 0.0
    width: int = 0
    height: int = 0
    times: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    scene: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    motion: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    brightness: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    flash: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    faces: list[list[FaceBox]] = field(default_factory=list)
    analyzed: bool = True
    note: str = ""
    # חלונות הזמן שנותחו בפועל. ריק = כל הסרטון (ניתוח מלא). במצב חלונות
    # (FAST לשידורים ארוכים) המערכים באורך הסרטון, ומחוץ לחלונות הם אפסים.
    coverage: list[tuple[float, float]] = field(default_factory=list)

    def covers(self, start: float, end: float) -> bool:
        if not self.coverage:
            return self.analyzed
        got = sum(max(0.0, min(end, b) - max(start, a)) for a, b in self.coverage)
        return got >= (end - start) * 0.9

    @property
    def n(self) -> int:
        return int(self.times.shape[0])

    def index_at(self, t: float) -> int:
        if self.n == 0:
            return 0
        step = 1.0 / max(1e-6, self.fps)
        return int(min(self.n - 1, max(0, round(t / step))))

    def slice_mean(self, name: str, start: float, end: float) -> float:
        arr = getattr(self, name, None)
        if arr is None or self.n == 0:
            return 0.0
        i0, i1 = self.index_at(start), max(self.index_at(end), self.index_at(start) + 1)
        seg = np.asarray(arr[i0:i1], dtype=np.float32)
        return float(seg.mean()) if seg.size else 0.0

    def faces_between(self, start: float, end: float) -> list[tuple[float, FaceBox]]:
        out: list[tuple[float, FaceBox]] = []
        if not self.faces:
            return out
        i0, i1 = self.index_at(start), min(self.n, self.index_at(end) + 1)
        step = 1.0 / max(1e-6, self.fps)
        for i in range(i0, min(i1, len(self.faces))):
            for box in self.faces[i]:
                out.append((i * step, box))
        return out


def analyze_video(
    video_path: str | Path,
    *,
    sample_fps: float = 1.0,
    duration: float = 0.0,
    analysis_width: int = 320,
    detect_faces: bool = True,
    face_every: int = 2,
    on_progress: ProgressFn = None,
    cancel_event: Optional[threading.Event] = None,
    start: float = 0.0,
    end: Optional[float] = None,
    normalize: bool = True,
) -> VisualFeatures:
    """
    מנתח וידאו בדגימה. `face_every` – כל כמה פריימים שנדגמו להריץ זיהוי פנים
    (זיהוי פנים הוא החלק היקר; דילוג מקטין זמן בלי לפגוע במעקב).

    `start`/`end` – ניתוח של טווח בלבד (קפיצה ישירה אליו, בלי לפענח את מה
    שלפניו). הזמנים בתוצאה מוחלטים. `normalize=False` מחזיר תנועה ומעברי
    סצנה בסקאלה קבועה (ראו FIXED_REFS) – כך חלונות שונים ברי השוואה.
    """
    path = Path(video_path)
    if not path.exists():
        raise PolixorError(message_key="processing.visual.missing")

    sample_fps = max(0.1, float(sample_fps))
    cascade = _load_face_cascade() if detect_faces else None

    # קובעים רוחב/גובה יעד מהמקור כדי לשמור יחס
    from ..util.ffmpeg import probe

    info = probe(path)
    if not info.has_video:
        return VisualFeatures(fps=sample_fps, duration=duration or info.duration,
                              analyzed=False, note=i18n.tr("processing.visual.no_video"))

    src_w, src_h = info.width or 1280, info.height or 720
    w = min(analysis_width, src_w)
    h = max(2, int(round(w * src_h / max(1, src_w))))
    h -= h % 2
    start = max(0.0, float(start or 0.0))
    ranged = start > 0 or end is not None
    total_dur = (float(end) - start) if end is not None else (duration or info.duration or 0.0)

    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-v", "error"]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(path)]
    if end is not None and end > start:
        cmd += ["-t", f"{end - start:.3f}"]
    cmd += [
        "-an", "-sn", "-dn",
        "-vf", f"fps={sample_fps},scale={w}:{h}:flags=fast_bilinear",
        "-pix_fmt", "bgr24", "-f", "rawvideo", "-",
    ]

    frame_bytes = w * h * 3
    times: list[float] = []
    scene: list[float] = []
    motion: list[float] = []
    bright: list[float] = []
    faces: list[list[FaceBox]] = []

    prev_hist: Optional[np.ndarray] = None
    prev_gray: Optional[np.ndarray] = None
    idx = 0

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            bufsize=frame_bytes * 2)
    try:
        assert proc.stdout is not None
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise JobCancelledError()

            buf = _read_exact(proc.stdout, frame_bytes)
            if buf is None:
                break

            frame = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 3)
            t = start + idx / sample_fps
            times.append(t)

            gray = _to_gray(frame)
            bright.append(float(gray.mean()) / 255.0)

            hist = _hist(gray)
            if prev_hist is not None:
                # מרחק Bhattacharyya מקורב – עמיד לשינויי בהירות קלים
                d = float(np.sqrt(np.maximum(0.0, 1.0 - np.sum(np.sqrt(prev_hist * hist)))))
                scene.append(d)
            else:
                scene.append(0.0)
            prev_hist = hist

            if prev_gray is not None:
                motion.append(float(np.abs(gray.astype(np.int16) -
                                           prev_gray.astype(np.int16)).mean()) / 255.0)
            else:
                motion.append(0.0)
            prev_gray = gray

            if cascade is not None and (idx % max(1, face_every) == 0):
                faces.append(_detect_faces(cascade, gray, w, h))
            else:
                faces.append(faces[-1] if faces else [])

            idx += 1
            if on_progress and total_dur > 0:
                on_progress(min(0.99, (t - start) / total_dur))
    finally:
        try:
            if proc.stdout:
                proc.stdout.close()
        except Exception:
            pass
        err = b""
        try:
            if proc.stderr:
                err = proc.stderr.read() or b""
                proc.stderr.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        if proc.returncode not in (0, None) and idx == 0:
            raise PolixorError(message_key="processing.visual.failed",
                               detail=err.decode("utf-8", "ignore")[-800:])

    if idx == 0:
        return VisualFeatures(fps=sample_fps, duration=total_dur, width=w, height=h,
                              analyzed=False, note=i18n.tr("processing.visual.no_frames"))

    norm = _normalize if normalize else _fixed_scale
    feats = VisualFeatures(
        fps=sample_fps, duration=total_dur, width=w, height=h,
        times=np.asarray(times, dtype=np.float32),
        scene=norm(np.asarray(scene, dtype=np.float32), "scene"),
        motion=norm(np.asarray(motion, dtype=np.float32), "motion"),
        brightness=np.asarray(bright, dtype=np.float32),
        faces=faces,
    )
    feats.flash = _flash_signal(feats.brightness, fixed=not normalize)
    if ranged:
        feats.coverage = [(start, start + total_dur)]
    if on_progress:
        on_progress(1.0)
    log.info("visual features: %d sampled frames at %.2f fps (%dx%d)",
             idx, sample_fps, w, h)
    return feats


def _read_exact(stream, n: int) -> Optional[bytes]:
    chunks: list[bytes] = []
    remaining = n
    while remaining > 0:
        c = stream.read(remaining)
        if not c:
            return None if not chunks else None
        chunks.append(c)
        remaining -= len(c)
    return b"".join(chunks)


def _to_gray(frame: np.ndarray) -> np.ndarray:
    try:
        import cv2

        return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    except Exception:
        return (frame[:, :, 0] * 0.114 + frame[:, :, 1] * 0.587 +
                frame[:, :, 2] * 0.299).astype(np.uint8)


def _hist(gray: np.ndarray, bins: int = 64) -> np.ndarray:
    h, _ = np.histogram(gray, bins=bins, range=(0, 256))
    total = h.sum()
    return (h / total).astype(np.float32) if total else h.astype(np.float32)


# סקאלה קבועה לניתוח בחלונות: ערך גולמי שמתאים ל-1.0. תנועה = הפרש מוחלט
# ממוצע (0..1) בין פריימים; סצנה = מרחק היסטוגרמות; הבזק = קפיצת בהירות.
FIXED_REFS = {"motion": 0.05, "scene": 0.35, "flash": 0.12}


def _fixed_scale(x: np.ndarray, kind: str) -> np.ndarray:
    return np.clip(x / FIXED_REFS[kind], 0.0, 1.5).astype(np.float32)


def _normalize(x: np.ndarray, kind: str = "") -> np.ndarray:
    if x.size == 0:
        return x
    ref = float(np.percentile(x, 95))
    if ref <= 1e-6:
        return np.zeros_like(x)
    return np.clip(x / ref, 0.0, 1.5).astype(np.float32)


def _flash_signal(brightness: np.ndarray, fixed: bool = False) -> np.ndarray:
    if brightness.size < 2:
        return np.zeros_like(brightness)
    d = np.abs(np.diff(brightness, prepend=brightness[0])).astype(np.float32)
    return _fixed_scale(d, "flash") if fixed else _normalize(d)


def merge_windows(windows: list[tuple[float, float]], gap: float = 1.0
                  ) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted((float(a), float(b)) for a, b in windows if b > a):
        if out and a <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def subtract_windows(want: list[tuple[float, float]], have: list[tuple[float, float]]
                     ) -> list[tuple[float, float]]:
    """החלקים של `want` שעדיין לא נותחו."""
    out: list[tuple[float, float]] = []
    for a, b in merge_windows(want):
        pieces = [(a, b)]
        for c, d in have:
            nxt = []
            for x, y in pieces:
                if d <= x or c >= y:
                    nxt.append((x, y))
                    continue
                if c > x:
                    nxt.append((x, c))
                if d < y:
                    nxt.append((d, y))
            pieces = nxt
        out += [(x, y) for x, y in pieces if y - x >= 0.5]
    return out


def analyze_video_windows(
    video_path: str | Path,
    windows: list[tuple[float, float]],
    *,
    sample_fps: float,
    duration: float,
    base: Optional[VisualFeatures] = None,
    detect_faces: bool = True,
    on_progress: ProgressFn = None,
    cancel_event: Optional[threading.Event] = None,
) -> VisualFeatures:
    """
    ניתוח חזותי של חלונות זמן בלבד (מצב FAST לשידורים ארוכים): קופצים
    ישירות לכל חלון ומפענחים רק אותו. התוצאה באורך הסרטון המלא (אפסים
    מחוץ לחלונות) ו-`coverage` אומר מה נותח. `base` – ניתוח חלונות קודם:
    רק מה שעוד לא כוסה מנותח עכשיו.
    """
    fps = max(0.1, float(sample_fps))
    n = int(np.ceil(max(duration, 0.0) * fps)) + 1
    reuse = base is not None and base.coverage and abs(base.fps - fps) < 1e-6 and base.n == n
    if reuse:
        out = VisualFeatures(fps=fps, duration=duration, width=base.width, height=base.height,
                             times=base.times.copy(), scene=base.scene.copy(),
                             motion=base.motion.copy(), brightness=base.brightness.copy(),
                             flash=base.flash.copy(), faces=[list(f) for f in base.faces],
                             coverage=list(base.coverage))
    else:
        z = np.zeros(n, dtype=np.float32)
        out = VisualFeatures(fps=fps, duration=duration, times=np.arange(n, dtype=np.float32) / fps,
                             scene=z.copy(), motion=z.copy(), brightness=z.copy(), flash=z.copy(),
                             faces=[[] for _ in range(n)], coverage=[])
    todo = subtract_windows(windows, out.coverage)
    total = sum(b - a for a, b in todo) or 1.0
    done = 0.0
    for a, b in todo:
        part = analyze_video(
            video_path, sample_fps=fps, duration=duration, detect_faces=detect_faces,
            start=a, end=min(duration, b), normalize=False, cancel_event=cancel_event,
            on_progress=(lambda f, a=a, b=b: on_progress((done + f * (b - a)) / total))
            if on_progress else None)
        if not part.analyzed or part.n == 0:
            continue
        out.width, out.height = part.width, part.height
        for k in range(part.n):
            i = int(round(float(part.times[k]) * fps))
            if 0 <= i < n:
                out.scene[i] = part.scene[k]
                out.motion[i] = part.motion[k]
                out.brightness[i] = part.brightness[k]
                out.flash[i] = part.flash[k]
                out.faces[i] = list(part.faces[k]) if k < len(part.faces) else []
        out.coverage.append((a, min(duration, b)))
        done += b - a
    out.coverage = merge_windows(out.coverage, gap=0.0)
    out.analyzed = bool(out.coverage)
    if on_progress:
        on_progress(1.0)
    return out


_CASCADE_CACHE: dict[str, object] = {}
_CASCADE_LOCK = threading.Lock()


def _load_face_cascade():
    """מסווג Haar לפנים – מגיע עם OpenCV, לא דורש הורדה."""
    with _CASCADE_LOCK:
        if "c" in _CASCADE_CACHE:
            return _CASCADE_CACHE["c"]
        try:
            import cv2

            xml = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
            if not xml.exists():
                log.warning("face cascade not found at %s", xml)
                _CASCADE_CACHE["c"] = None
                return None
            cascade = cv2.CascadeClassifier(str(xml))
            if cascade.empty():
                _CASCADE_CACHE["c"] = None
                return None
            _CASCADE_CACHE["c"] = cascade
            return cascade
        except Exception as exc:
            log.warning("face detection unavailable: %s", exc)
            _CASCADE_CACHE["c"] = None
            return None


def _detect_faces(cascade, gray: np.ndarray, w: int, h: int) -> list[FaceBox]:
    try:
        import cv2

        # CLAHE: איזון מקומי. equalizeHist גלובלי מחליש פנים קטנות
        # בפריים צבעוני, והגלאי מפספס אותן.
        eq = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
        rects = cascade.detectMultiScale(
            eq, scaleFactor=1.15, minNeighbors=5,
            minSize=(max(16, w // 24), max(16, h // 24)),
        )
        return [
            (float(x) / w, float(y) / h, float(fw) / w, float(fh) / h)
            for (x, y, fw, fh) in rects
        ]
    except Exception:
        return []


# --------------------------------------------------------------------------
# זיהוי אזור מצלמת הסטרימר
# --------------------------------------------------------------------------
def estimate_camera_region(feats: VisualFeatures,
                           min_hits: int = 12,
                           layouts=None) -> Optional[dict[str, float]]:
    """
    מנחש את אזור מצלמת הסטרימר: אשכול הפנים הנפוץ ביותר לאורך השידור.
    מחזיר מלבן מנורמל מורחב, או None אם אין מספיק ראיות.

    כשיש ציר פריסות (`layout_detect`) עם חלון מצלמה שזוהה מגבולותיו,
    הוא עדיף: זה המלבן האמיתי של המצלמה ולא הרחבה של מלבן הפנים.

    זהו ניחוש – הממשק מאפשר לתקן ידנית ולשמור את האזור לשידורים הבאים.
    """
    if layouts is not None:
        cam = layouts.dominant_facecam() if hasattr(layouts, "dominant_facecam") else None
        if cam is not None:
            secs = [s.duration for s in layouts.segments
                    if s.kind == "reaction" and s.facecam is not None
                    and s.facecam.iou(cam) > 0.6]
            total = max(1e-6, layouts.duration or sum(s.duration for s in layouts.segments))
            return {**cam.to_dict(),
                    "confidence": round(min(1.0, sum(secs) / total), 3),
                    "hits": len(secs), "source": "layout"}
    centers: list[tuple[float, float, float]] = []
    for frame_faces in feats.faces:
        for (x, y, w, h) in frame_faces:
            centers.append((x + w / 2.0, y + h / 2.0, max(w, h)))
    if len(centers) < min_hits:
        return None

    pts = np.asarray([(c[0], c[1]) for c in centers], dtype=np.float32)
    sizes = np.asarray([c[2] for c in centers], dtype=np.float32)

    # רשת 8x8 – מוצאים את התא עם הכי הרבה פנים
    gx = np.clip((pts[:, 0] * 8).astype(int), 0, 7)
    gy = np.clip((pts[:, 1] * 8).astype(int), 0, 7)
    flat = gy * 8 + gx
    counts = np.bincount(flat, minlength=64)
    best = int(counts.argmax())
    if counts[best] < min_hits:
        return None

    mask = flat == best
    cx = float(pts[mask, 0].mean())
    cy = float(pts[mask, 1].mean())
    size = float(np.median(sizes[mask]))

    # מרחיבים סביב הפנים כדי לכלול כתפיים ומסגרת המצלמה
    half_w = min(0.30, max(0.10, size * 1.9))
    half_h = min(0.34, max(0.12, size * 2.1))
    x0 = max(0.0, cx - half_w)
    y0 = max(0.0, cy - half_h)
    x1 = min(1.0, cx + half_w)
    y1 = min(1.0, cy + half_h)

    return {
        "x": round(x0, 4), "y": round(y0, 4),
        "w": round(x1 - x0, 4), "h": round(y1 - y0, 4),
        "confidence": round(float(counts[best]) / max(1, len(centers)), 3),
        "hits": int(counts[best]),
    }
