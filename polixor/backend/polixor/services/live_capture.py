"""
לולאת קליטת השידור החי ומכונת המצבים שלה.

הלולאה נכתבה כך שניתן להזריק לה את פותר הכתובת ואת מקליט המקטע
(`resolve` / `record`). בייצור אלה הפונקציות האמיתיות מול הרשת;
בבדיקות ניתן להחליף אותן ולבדוק מעברי מצב, חיבור מחדש ושמירת חומר
חלקי בלי תלות בשידור חיצוני.

עיקרון מרכזי: **חומר שהוקלט לא הולך לאיבוד.** כל מקטע שנסגר נמסר
מיד ל-`on_segment`, שאחראי לשמור אותו, עוד לפני שמנסים להתחבר שוב.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import Any, Callable, Optional

from .. import i18n
from ..config import AppSettings
from ..errors import PolixorError
from ..models import LiveState, live_can_transition
from .live import (
    MAX_CONSECUTIVE_FAILURES,
    MIN_USABLE_SEGMENT,
    RECONNECT_BACKOFF,
    SEGMENT_SECONDS,
    LiveUnavailableError,
    SegmentResult,
    record_segment,
    resolve_manifest,
)

log = logging.getLogger("polixor.live.capture")

StateFn = Callable[[str, str], None]
SegmentFn = Callable[[SegmentResult], None]
TickFn = Callable[[float, float], None]


class StateMachine:
    """
    אוכפת את המעברים המותרים בין מצבי ההקלטה.

    מעבר לא חוקי נרשם ונדחה, כדי שהממשק לא יוכל להציג מצב שלא
    באמת קרה – למשל "משדר" בלי שהתחברנו.
    """

    def __init__(self, on_change: Optional[StateFn] = None,
                 initial: str = LiveState.IDLE.value) -> None:
        self.state = initial
        self.history: list[tuple[float, str, str]] = [(time.time(), initial, "")]
        self._on_change = on_change

    def to(self, state: str, detail: str = "") -> bool:
        target = state.value if isinstance(state, LiveState) else str(state)
        if target == self.state:
            return True
        if not live_can_transition(self.state, target):
            log.warning("rejected live transition %s -> %s", self.state, target)
            return False
        self.state = target
        self.history.append((time.time(), target, detail))
        if self._on_change:
            try:
                self._on_change(target, detail)
            except Exception:              # pragma: no cover
                log.debug("state callback failed", exc_info=True)
        return True

    @property
    def is_terminal(self) -> bool:
        return self.state in (LiveState.COMPLETED.value, LiveState.FAILED.value)


@dataclass
class CaptureConfig:
    url: str
    work_dir: Path
    segment_seconds: float = SEGMENT_SECONDS
    max_seconds: float = 0.0            # 0 => ללא הגבלת זמן
    max_failures: int = MAX_CONSECUTIVE_FAILURES
    backoff: tuple[float, ...] = RECONNECT_BACKOFF


@dataclass
class CaptureOutcome:
    state: str
    segments: list[dict[str, Any]] = field(default_factory=list)
    reconnects: int = 0
    seconds: float = 0.0
    error: str = ""
    error_code: str = ""
    stopped_by_user: bool = False

    @property
    def paths(self) -> list[Path]:
        return [Path(s["path"]) for s in self.segments]


def run_capture(
    config: CaptureConfig,
    *,
    settings: AppSettings,
    cancel_event: Event,
    stop_event: Event,
    machine: Optional[StateMachine] = None,
    resolve: Callable[[str, AppSettings], str] = resolve_manifest,
    record: Callable[..., SegmentResult] = record_segment,
    sleep: Callable[[float], None] = time.sleep,
    on_state: Optional[StateFn] = None,
    on_segment: Optional[SegmentFn] = None,
    on_tick: Optional[TickFn] = None,
) -> CaptureOutcome:
    """
    מקליטה שידור עד שהמשתמש עוצר, השידור נגמר, או נגמרו הניסיונות.

    `stop_event`  – עצירה יזומה: מסיימת את המקטע הנוכחי ושומרת אותו.
    `cancel_event`– ביטול המשימה כולה: גם כאן המקטע נסגר ונשמר, אבל
                    לא מתחילים מקטע נוסף.
    """
    sm = machine or StateMachine(on_state)
    config.work_dir.mkdir(parents=True, exist_ok=True)

    outcome = CaptureOutcome(state=sm.state)
    failures = 0
    index = 0
    started = time.time()

    sm.to(LiveState.CONNECTING.value, i18n.tr("live.capture.connecting"))

    while True:
        if cancel_event.is_set():
            outcome.stopped_by_user = True
            break
        if stop_event.is_set():
            outcome.stopped_by_user = True
            break
        if config.max_seconds and outcome.seconds >= config.max_seconds:
            break

        # --- פתרון כתובת הזרם. זהו גם מנגנון החיבור מחדש ---
        try:
            manifest = resolve(config.url, settings)
        except PolixorError as exc:
            failures += 1
            outcome.error, outcome.error_code = exc.message, exc.code
            if outcome.segments:
                # כבר יש חומר: מדובר בנפילה, לא בכשל התחלתי
                if not _retry_wait(sm, config, failures, exc.message,
                                   cancel_event, stop_event, sleep, outcome):
                    break
                continue
            if failures >= config.max_failures:
                sm.to(LiveState.FAILED.value, exc.message)
                break
            if not _retry_wait(sm, config, failures, exc.message,
                               cancel_event, stop_event, sleep, outcome):
                sm.to(LiveState.FAILED.value, exc.message)
                break
            continue

        # --- הקלטת מקטע ---
        remaining = config.segment_seconds
        if config.max_seconds:
            remaining = min(remaining, config.max_seconds - outcome.seconds)
            if remaining <= 0:
                break

        dest = config.work_dir / f"seg_{index:04d}.ts"
        index += 1

        # לא עוברים ל-LIVE רק מפני שהתהליך עלה. המצב משתנה כשבאמת
        # מגיעים בתים מהזרם – כך שהממשק לא מציג "משדר" בזמן שהזרם
        # למעשה נפול.
        seg_started = time.time()
        flowing = {"seen": False}

        def _tick(elapsed: float, written: int) -> None:
            if written > 0 and not flowing["seen"]:
                flowing["seen"] = True
                sm.to(LiveState.LIVE.value, i18n.tr("live.capture.recording"))
            if on_tick:
                on_tick(outcome.seconds + elapsed, elapsed)

        try:
            result = record(
                manifest, dest, seconds=remaining,
                cancel_event=cancel_event, stop_event=stop_event,
                on_tick=_tick,
            )
        except PolixorError as exc:
            failures += 1
            outcome.error, outcome.error_code = exc.message, exc.code
            if not _retry_wait(sm, config, failures, exc.message,
                               cancel_event, stop_event, sleep, outcome):
                sm.to(LiveState.FAILED.value if not outcome.segments
                      else LiveState.STOPPING.value, exc.message)
                break
            continue

        # --- שמירת מה שהוקלט, תמיד, לפני כל החלטה אחרת ---
        if result.seconds >= MIN_USABLE_SEGMENT and result.path.exists():
            # מקטע קצר עלול להסתיים לפני הדגימה הראשונה; ההוכחה
            # שהזרם זרם היא החומר עצמו.
            if not flowing["seen"]:
                sm.to(LiveState.LIVE.value, i18n.tr("live.capture.recording"))
            entry = {
                "path": str(result.path),
                "seconds": float(result.seconds),
                "started_at": seg_started,
                "complete": bool(result.complete),
            }
            outcome.segments.append(entry)
            outcome.seconds = round(outcome.seconds + result.seconds, 2)
            failures = 0
            if on_segment:
                on_segment(result)
        else:
            failures += 1
            if result.path.exists():
                try:
                    result.path.unlink()      # מקטע ריק – לא שומרים
                except OSError:
                    pass

        if cancel_event.is_set() or stop_event.is_set():
            outcome.stopped_by_user = True
            break

        if result.complete:
            continue      # מקטע מלא – ממשיכים ישר לבא

        # מקטע נקטע לפני הזמן => הזרם נפל
        message = result.error or i18n.tr("live.record.dropped")
        outcome.error = message
        if failures >= config.max_failures:
            outcome.error = i18n.tr("live.capture.failed_repeatedly", count=failures) + message
            break
        if not _retry_wait(sm, config, failures, message,
                           cancel_event, stop_event, sleep, outcome):
            break

    # --- סיום ---
    if sm.state not in (LiveState.FAILED.value,):
        sm.to(LiveState.STOPPING.value, i18n.tr("live.capture.stopping"))
        if outcome.segments:
            sm.to(LiveState.COMPLETED.value, i18n.tr("live.capture.completed"))
        else:
            sm.to(LiveState.FAILED.value,
                  outcome.error or i18n.tr("live.capture.nothing"))

    outcome.state = sm.state
    outcome.seconds = round(sum(s["seconds"] for s in outcome.segments), 2)
    log.info("capture finished: state=%s segments=%d seconds=%.1f reconnects=%d",
             outcome.state, len(outcome.segments), outcome.seconds,
             outcome.reconnects)
    return outcome


def _retry_wait(sm: StateMachine, config: CaptureConfig, failures: int,
                message: str, cancel_event: Event, stop_event: Event,
                sleep: Callable[[float], None],
                outcome: CaptureOutcome) -> bool:
    """
    ממתין לפני ניסיון חוזר, עם השהיה עולה.

    מחזיר False אם אין טעם לנסות שוב (ביטול, עצירה יזומה, או שנגמרו
    הניסיונות) – ואז הלולאה מסיימת בלי לאבד את מה שכבר הוקלט.
    """
    if cancel_event.is_set() or stop_event.is_set():
        return False
    if failures > config.max_failures:
        return False

    outcome.reconnects += 1
    idx = min(failures - 1, len(config.backoff) - 1)
    wait = config.backoff[max(0, idx)]
    sm.to(LiveState.RECONNECTING.value,
          message + i18n.tr("live.capture.retrying", seconds=int(wait),
                            attempt=failures, attempts=config.max_failures))

    waited = 0.0
    step = 0.25
    while waited < wait:
        if cancel_event.is_set() or stop_event.is_set():
            return False
        sleep(step)
        waited += step
    return True


def describe_outcome(outcome: CaptureOutcome) -> str:
    """משפט אחד בעברית שמסכם את ההקלטה – לרישום ביומן ולממשק."""
    minutes = outcome.seconds / 60.0
    parts = [i18n.tr("live.capture.collected", minutes=f"{minutes:.1f}",
                     segments=len(outcome.segments))]
    if outcome.reconnects:
        parts.append(i18n.tr("live.capture.reconnects", count=outcome.reconnects))
    if outcome.stopped_by_user:
        parts.append(i18n.tr("live.capture.stopped_by_user"))
    if outcome.error and outcome.state == LiveState.FAILED.value:
        parts.append(outcome.error)
    return " · ".join(parts)
