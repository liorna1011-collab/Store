"""
בדיקת איכות אוטומטית אחרי הרינדור.

הכלל שמנחה את הקובץ: **`exit code 0` של FFmpeg אינו הוכחה לכלום.**
FFmpeg מחזיר 0 גם כשהפלט שחור, גם כשהתמונה קפאה באמצע, גם כשפס
הקול נעלם וגם כשהאורך יצא חצי ממה שתוכנן. לכן אחרי כל רינדור
בודקים את הקובץ עצמו.

מה שנבדק כאן נמדד בפועל ולא מוערך:
  • אורך מול מה שהתכנית הבטיחה
  • קיום זרם וידאו וזרם אודיו
  • פריימים שחורים (`blackdetect`)
  • קטעים קפואים (`freezedetect`)
  • דגימת פריימים אמיתית — לא רק Metadata
  • כתוביות שחורגות מגבולות הקליפ
  • טקסט שנופל מחוץ לאזור הבטוח

הבדיקות מסווגות ל-`error` ו-`warning`. ממצא ברמת `error` מסמן את
הקליפ כ-`needs_review` ולא כ„הושלם" — הקובץ קיים וניתן לניגון,
אבל אסור להציג אותו כתקין.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .. import i18n
from ..util.ffmpeg import ffmpeg_bin, probe

log = logging.getLogger("polixor.qa")

# --- ספים ---
DURATION_TOLERANCE = 0.45   # שניות מול התכנית
BLACK_MAX_RATIO = 0.08      # חלק מהסרטון שמותר שיהיה שחור
BLACK_MIN_RUN = 0.6         # רצף שחור קצר מזה הוא מעבר, לא תקלה
FREEZE_MIN_RUN = 2.0        # קיפאון קצר מזה יכול להיות שוט סטטי
FREEZE_MAX_RATIO = 0.25
SAMPLE_COUNT = 7            # כמה פריימים לדגום
DARK_FRAME_MEAN = 6.0       # ממוצע בהירות שמתחתיו הפריים שחור


@dataclass
class QAFinding:
    code: str
    severity: str           # error | warning
    message: str
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "severity": self.severity,
                "message": self.message, "detail": self.detail}


@dataclass
class QAReport:
    findings: list[QAFinding] = field(default_factory=list)
    measurements: dict[str, Any] = field(default_factory=dict)
    checks_run: list[str] = field(default_factory=list)
    checks_skipped: dict[str, str] = field(default_factory=dict)

    @property
    def errors(self) -> list[QAFinding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[QAFinding]:
        return [f for f in self.findings if f.severity == "warning"]

    @property
    def needs_review(self) -> bool:
        return bool(self.errors)

    @property
    def passed(self) -> bool:
        return not self.findings

    def add(self, code: str, severity: str, message: str, **detail) -> None:
        self.findings.append(QAFinding(code, severity, message, detail))

    def summary(self) -> str:
        if self.passed:
            return i18n.tr("qa.summary.passed", count=len(self.checks_run))
        parts = []
        if self.errors:
            parts.append(i18n.tr("qa.summary.errors", count=len(self.errors)))
        if self.warnings:
            parts.append(i18n.tr("qa.summary.warnings", count=len(self.warnings)))
        return " · ".join(parts) + i18n.tr("qa.summary.of", count=len(self.checks_run))

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "needs_review": self.needs_review,
            "findings": [f.to_dict() for f in self.findings],
            "measurements": self.measurements,
            "checks_run": self.checks_run,
            "checks_skipped": self.checks_skipped,
            "summary": self.summary(),
        }


# --------------------------------------------------------------------------
def check_render(
    path: str | Path,
    *,
    expected_duration: float = 0.0,
    expect_audio: bool = True,
    vertical: bool = False,
    cues: Optional[list] = None,
    safe_margin_v: int = 0,
    timeout: float = 900.0,
) -> QAReport:
    """
    בודק קובץ שיצא מהרינדור. לא משנה אותו.

    `expected_duration` הוא האורך שתכנית העריכה הבטיחה. אם הוא 0,
    בדיקת האורך מסומנת כ„לא בוצעה" ולא עוברת בשקט.
    """
    report = QAReport()
    p = Path(path)

    if not p.exists() or p.stat().st_size < 1024:
        report.add("missing_output", "error",
                   i18n.tr("qa.missing_output"), path=str(p))
        return report

    try:
        info = probe(p)
    except Exception as exc:                            # noqa: BLE001
        report.add("unreadable", "error",
                   i18n.tr("qa.unreadable", error=exc), path=str(p))
        return report

    report.measurements.update({
        "duration": round(info.duration, 3),
        "width": info.width, "height": info.height,
        "fps": round(info.fps, 3),
        "has_video": info.has_video, "has_audio": info.has_audio,
        "size_bytes": info.size_bytes,
    })

    _check_streams(report, info, expect_audio)
    _check_duration(report, info, expected_duration)
    _check_black(report, p, info, timeout)
    _check_freeze(report, p, info, timeout)
    _check_samples(report, p, info, timeout)
    _check_cues(report, info, cues)
    _check_safe_area(report, info, safe_margin_v, vertical)
    return report


# --------------------------------------------------------------------------
def _check_streams(report: QAReport, info, expect_audio: bool) -> None:
    report.checks_run.append("streams")
    if not info.has_video:
        report.add("no_video", "error", i18n.tr("qa.no_video"))
    if expect_audio and not info.has_audio:
        report.add("no_audio", "error",
                   i18n.tr("qa.no_audio"))
    if info.width <= 0 or info.height <= 0:
        report.add("bad_dimensions", "error",
                   i18n.tr("qa.bad_dimensions", width=info.width, height=info.height))


def _check_duration(report: QAReport, info, expected: float) -> None:
    if expected <= 0:
        report.checks_skipped["duration"] = i18n.tr("qa.skipped.duration")
        return
    report.checks_run.append("duration")
    report.measurements["expected_duration"] = round(expected, 3)
    delta = info.duration - expected
    report.measurements["duration_delta"] = round(delta, 3)
    if abs(delta) > DURATION_TOLERANCE:
        report.add(
            "duration_mismatch", "error",
            i18n.tr("qa.duration_mismatch", actual=f"{info.duration:.2f}",
                    expected=f"{expected:.2f}", delta=f"{delta:+.2f}"),
            actual=round(info.duration, 3), expected=round(expected, 3))


def _check_black(report: QAReport, p: Path, info, timeout: float) -> None:
    """פריימים שחורים — `blackdetect` מדווח עליהם עם זמנים."""
    runs = _detect_runs(p, f"blackdetect=d={BLACK_MIN_RUN}:pix_th=0.10",
                        "black_start", "black_end", info.duration, timeout)
    if runs is None:
        report.checks_skipped["black_frames"] = i18n.tr("qa.skipped.blackdetect")
        return
    report.checks_run.append("black_frames")
    total = sum(b - a for a, b in runs)
    ratio = total / max(0.01, info.duration)
    report.measurements["black_seconds"] = round(total, 2)
    if not runs:
        return
    # שחור בקצוות הוא פייד לגיטימי; שחור באמצע אינו
    middle = [(a, b) for a, b in runs
              if a > 0.6 and b < info.duration - 0.6]
    if middle:
        report.add(
            "black_frames", "error",
            i18n.tr("qa.black_frames", count=len(middle),
                    seconds=f"{sum(b - a for a, b in middle):.1f}"),
            spans=[[round(a, 2), round(b, 2)] for a, b in middle[:6]])
    elif ratio > BLACK_MAX_RATIO:
        report.add(
            "black_ratio", "warning",
            i18n.tr("qa.black_ratio", percent=f"{ratio * 100:.0f}"),
            ratio=round(ratio, 3))


def _check_freeze(report: QAReport, p: Path, info, timeout: float) -> None:
    """קטעים קפואים — תמונה שלא משתנה לאורך זמן."""
    runs = _detect_runs(
        p, f"freezedetect=n=-60dB:d={FREEZE_MIN_RUN}",
        "freeze_start", "freeze_end", info.duration, timeout)
    if runs is None:
        report.checks_skipped["frozen"] = i18n.tr("qa.skipped.freezedetect")
        return
    report.checks_run.append("frozen")
    total = sum(b - a for a, b in runs)
    report.measurements["frozen_seconds"] = round(total, 2)
    if not runs:
        return
    ratio = total / max(0.01, info.duration)
    if ratio > FREEZE_MAX_RATIO:
        report.add(
            "frozen_sections", "error",
            i18n.tr("qa.frozen_error", percent=f"{ratio * 100:.0f}", seconds=f"{total:.1f}"),
            spans=[[round(a, 2), round(b, 2)] for a, b in runs[:6]])
    else:
        report.add(
            "frozen_sections", "warning",
            i18n.tr("qa.frozen_warning", count=len(runs), seconds=f"{total:.1f}"),
            spans=[[round(a, 2), round(b, 2)] for a, b in runs[:6]])


def _detect_runs(p: Path, flt: str, start_key: str, end_key: str,
                 duration: float,
                 timeout: float) -> Optional[list[tuple[float, float]]]:
    """
    מריץ פילטר זיהוי ומחזיר רצפים.

    נקודה שעלתה בבדיקה: כשהתקלה נמשכת עד סוף הקובץ, הפילטר מדווח
    `start` ולעולם לא `end`. פרסר שדורש את שניהם מפספס בדיוק את
    המקרה הגרוע ביותר — סרטון שקפא ונשאר קפוא. לכן רצף פתוח
    נסגר באורך הקובץ.
    """
    try:
        res = subprocess.run(
            [ffmpeg_bin(), "-hide_banner", "-nostdin", "-i", str(p),
             "-vf", flt, "-an", "-f", "null", "-"],
            capture_output=True, text=True, timeout=timeout)
    except Exception as exc:                            # noqa: BLE001
        log.debug("detect %s failed: %s", flt, exc)
        return None
    if res.returncode != 0:
        return None

    pattern = re.compile(
        rf"(?P<kind>{re.escape(start_key)}|{re.escape(end_key)})"
        rf"\s*[:=]\s*(?P<value>[\d.]+)")
    runs: list[tuple[float, float]] = []
    open_at: Optional[float] = None
    for m in pattern.finditer(res.stderr):
        try:
            value = float(m.group("value"))
        except (TypeError, ValueError):
            continue
        if m.group("kind") == start_key:
            if open_at is None:
                open_at = value
        elif open_at is not None:
            if value > open_at:
                runs.append((open_at, value))
            open_at = None
    if open_at is not None and duration > open_at:
        runs.append((open_at, duration))
    return runs


def _check_samples(report: QAReport, p: Path, info, timeout: float) -> None:
    """
    דגימת פריימים אמיתית: מחלצים תמונות ובודקים שהן קיימות ולא
    שחורות. Metadata תקין אינו מעיד שיש מה לראות.
    """
    if info.duration <= 0.3:
        report.checks_skipped["frame_samples"] = i18n.tr("qa.skipped.too_short")
        return
    report.checks_run.append("frame_samples")
    points = [info.duration * f
              for f in [0.02, 0.15, 0.3, 0.45, 0.6, 0.78, 0.95][:SAMPLE_COUNT]]
    means: list[float] = []
    failed = 0
    for t in points:
        mean = _frame_mean(p, t, timeout=min(60.0, timeout))
        if mean is None:
            failed += 1
        else:
            means.append(mean)
    report.measurements["frame_samples"] = len(means)
    report.measurements["frame_mean_min"] = (round(min(means), 1)
                                             if means else None)
    if failed:
        report.add("frame_decode_failed", "error",
                   i18n.tr("qa.frame_decode_failed", failed=failed, total=len(points)),
                   failed=failed, total=len(points))
    dark = [m for m in means if m < DARK_FRAME_MEAN]
    if means and len(dark) == len(means):
        report.add("all_frames_black", "error",
                   i18n.tr("qa.all_frames_black"))
    elif len(dark) > len(means) / 2 and means:
        report.add("mostly_dark", "warning",
                   i18n.tr("qa.mostly_dark", dark=len(dark), total=len(means)))


def _frame_mean(p: Path, t: float, *, timeout: float) -> Optional[float]:
    """בהירות ממוצעת של פריים בודד, או None אם לא ניתן לחלץ אותו."""
    try:
        res = subprocess.run(
            [ffmpeg_bin(), "-hide_banner", "-loglevel", "error",
             "-ss", f"{max(0.0, t):.3f}", "-i", str(p), "-frames:v", "1",
             "-vf", "scale=64:36", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
            capture_output=True, timeout=timeout)
    except Exception:                                   # noqa: BLE001
        return None
    if res.returncode != 0 or not res.stdout:
        return None
    data = res.stdout
    return sum(data) / float(len(data))


def _check_cues(report: QAReport, info, cues) -> None:
    """כתובית שמסתיימת אחרי סוף הקליפ לא תוצג — וזו תקלה שקטה."""
    if not cues:
        report.checks_skipped["caption_bounds"] = i18n.tr("qa.skipped.no_captions")
        return
    report.checks_run.append("caption_bounds")
    over = [c for c in cues if float(getattr(c, "end", 0.0))
            > info.duration + 0.3]
    negative = [c for c in cues if float(getattr(c, "start", 0.0)) < -0.01]
    report.measurements["cue_count"] = len(cues)
    if over:
        last = max(float(getattr(c, "end", 0.0)) for c in over)
        report.add(
            "caption_out_of_bounds", "error",
            i18n.tr("qa.caption_out_of_bounds", count=len(over), last=f"{last:.2f}",
                    duration=f"{info.duration:.2f}"),
            count=len(over), last_end=round(last, 3))
    if negative:
        report.add("caption_negative_start", "error",
                   i18n.tr("qa.caption_negative_start", count=len(negative)),
                   count=len(negative))


def _check_safe_area(report: QAReport, info, margin_v: int,
                     vertical: bool) -> None:
    """
    האם הכתובית יושבת בתוך האזור הבטוח של הפריים.

    זו בדיקה של הפרמטר ולא של הפיקסלים: השוליים ב-ASS נמדדים
    ביחידות הפלט, ולכן אפשר להשוות אותם ישירות לאזור השמור.
    """
    if margin_v <= 0 or info.height <= 0:
        report.checks_skipped["safe_area"] = i18n.tr("qa.skipped.no_margin")
        return
    report.checks_run.append("safe_area")
    from .caption_engine import SafeZone

    zone = SafeZone.for_frame(vertical=vertical)
    required = int(info.height * zone.bottom)
    report.measurements["margin_v"] = margin_v
    report.measurements["safe_margin_required"] = required
    if margin_v + 2 < required:
        report.add(
            "caption_outside_safe_area", "warning",
            i18n.tr("qa.caption_outside_safe_area", margin=margin_v, required=required),
            margin_v=margin_v, required=required)
