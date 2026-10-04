"""מודלי SQLAlchemy – משימות, מקורות, תמלול, רגעים, קליפים וכתוביות."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex[:16]


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------
# מונים
# --------------------------------------------------------------------------
class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobStage(str, enum.Enum):
    PENDING = "pending"
    CAPTURE = "capture"
    DOWNLOAD = "download"
    PROBE = "probe"
    AUDIO = "audio"
    TRANSCRIBE = "transcribe"
    ANALYZE = "analyze"
    SELECT = "select"
    RENDER_LONG = "render_long"
    RENDER_SHORT = "render_short"
    DONE = "done"


STAGE_ORDER: list[JobStage] = [
    JobStage.PENDING,
    JobStage.CAPTURE,
    JobStage.DOWNLOAD,
    JobStage.PROBE,
    JobStage.AUDIO,
    JobStage.TRANSCRIBE,
    JobStage.ANALYZE,
    JobStage.SELECT,
    JobStage.RENDER_LONG,
    JobStage.RENDER_SHORT,
    JobStage.DONE,
]



class ProjectPhase(str, enum.Enum):
    """
    שלב הפרויקט מנקודת המבט של המשתמש.

    הפרויקט הוא שורת `Job`: `status` אומר אם משהו רץ עכשיו, ו-`phase`
    אומר איפה הפרויקט נמצא בזרימה Import → Analyze → Configure →
    Generate → Results.
    """

    IMPORTING = "importing"       # הורדה / הקלטה / קליטת הקובץ
    ANALYZING = "analyzing"       # אודיו, תמלול, ניתוח חזותי ופריסות
    CONFIGURE = "configure"       # הניתוח הושלם; המשתמש בוחר מצב והגדרות
    GENERATING = "generating"     # בחירת קטעים ויצירת הקליפים
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunScope(str, enum.Enum):
    """מה הריצה הבאה של המשימה אמורה לבצע."""

    ALL = "all"               # התנהגות קודמת: הכול בריצה אחת
    ANALYZE = "analyze"       # קליטה + ניתוח בלבד, ושמירת הניתוח לדיסק
    GENERATE = "generate"     # בחירה + רינדור מתוך ניתוח שמור


class ProjectMode(str, enum.Enum):
    SHORT = "short"
    LONGFORM = "longform"


class LiveState(str, enum.Enum):
    """מצבי מכונת המצבים של קליטת שידור חי."""

    IDLE = "idle"                 # לא לייב / טרם התחיל
    DETECTING = "detecting"       # בודק את הקישור מול הפלטפורמה
    CONNECTING = "connecting"     # מתחבר לזרם
    LIVE = "live"                 # מקליט
    RECONNECTING = "reconnecting"  # החיבור נפל, מנסה שוב
    STOPPING = "stopping"         # המשתמש ביקש לעצור, מסיים מקטע נוכחי
    COMPLETED = "completed"       # ההקלטה הסתיימה והחומר נשמר
    FAILED = "failed"             # נכשל לפני שנאסף חומר כלשהו



# התוויות המוצגות למשתמש נמצאות בקטלוג locales/system.py
# (pipeline.stage.*, system.live_state.*, system.image_role.*).

# מעברים חוקיים. כל מעבר אחר נדחה – כדי שה-UI לא יוכל להציג מצב
# שלא באמת קרה (למשל "LIVE" בלי שהתחברנו).
LIVE_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "idle": ("detecting", "connecting", "failed"),
    "detecting": ("connecting", "failed", "idle"),
    "connecting": ("live", "reconnecting", "failed", "stopping"),
    "live": ("reconnecting", "stopping", "completed", "failed"),
    "reconnecting": ("live", "connecting", "stopping", "completed", "failed"),
    "stopping": ("completed", "failed"),
    "completed": (),
    "failed": (),
}


def live_can_transition(current: str, nxt: str) -> bool:
    return nxt in LIVE_TRANSITIONS.get(str(current), ())


class ImageStatus(str, enum.Enum):
    QUEUED = "queued"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ImageRole(str, enum.Enum):
    """כיצד התמונה משולבת בווידאו."""

    INTRO = "intro"            # נוספת בתחילת הקליפ (מאריכה אותו)
    OUTRO = "outro"            # נוספת בסוף הקליפ (מאריכה אותו)
    INSERT = "insert"          # הכנסה מלאה באמצע (מאריכה את הקליפ)
    BROLL = "broll"            # מכסה את הווידאו, האודיו ממשיך (לא מאריך)
    OVERLAY = "overlay"        # שכבה חלקית מעל הווידאו (לא מאריך)
    BACKGROUND = "background"  # רקע מאחורי הווידאו בפריים אנכי
    THUMBNAIL = "thumbnail"    # תמונת שער – לא נכנסת לווידאו עצמו


# תפקידים שמוסיפים זמן לציר הזמן של הקליפ
TIMELINE_ROLES: frozenset[str] = frozenset({"intro", "outro", "insert"})
# תפקידים שמצוירים מעל/מתחת לווידאו הקיים בלי לשנות אורך
COMPOSITE_ROLES: frozenset[str] = frozenset({"broll", "overlay", "background"})



class ClipKind(str, enum.Enum):
    LONG = "long"
    SHORT = "short"
    HIGHLIGHTS = "highlights"


class ClipStatus(str, enum.Enum):
    PENDING = "pending"
    RENDERING = "rendering"
    READY = "ready"
    # הקובץ נוצר וניתן לניגון, אבל בדיקת האיכות מצאה בעיה. לפי
    # §25 מצב כזה אינו „הושלם": המשתמש צריך לראות מה נמצא לפני
    # שהוא מפרסם את הסרטון.
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"


class SourceKind(str, enum.Enum):
    YOUTUBE_VOD = "youtube_vod"
    YOUTUBE_LIVE = "youtube_live"
    TWITCH_VOD = "twitch_vod"
    TWITCH_LIVE = "twitch_live"
    KICK_VOD = "kick_vod"
    KICK_LIVE = "kick_live"
    GDRIVE = "gdrive"
    UPLOAD = "upload"
    DIRECT_URL = "direct_url"
    UNKNOWN = "unknown"


# --------------------------------------------------------------------------
# טבלאות
# --------------------------------------------------------------------------
class Source(Base):
    """שידור/קובץ מקור שהובא למערכת."""

    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    kind: Mapped[SourceKind] = mapped_column(Enum(SourceKind), default=SourceKind.UNKNOWN)
    url: Mapped[str] = mapped_column(Text, default="")
    title: Mapped[str] = mapped_column(Text, default="")
    uploader: Mapped[str] = mapped_column(Text, default="")
    file_path: Mapped[str] = mapped_column(Text, default="")
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    fps: Mapped[float] = mapped_column(Float, default=0.0)
    has_audio: Mapped[bool] = mapped_column(Boolean, default=True)
    is_live: Mapped[bool] = mapped_column(Boolean, default=False)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    jobs: Mapped[list["Job"]] = relationship(back_populates="source")


class Job(Base):
    """משימת עיבוד מלאה: הורדה → תמלול → ניתוח → בחירה → ייצוא."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    source_id: Mapped[Optional[str]] = mapped_column(ForeignKey("sources.id"), nullable=True)
    title: Mapped[str] = mapped_column(Text, default="")
    input_url: Mapped[str] = mapped_column(Text, default="")

    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.QUEUED)
    stage: Mapped[JobStage] = mapped_column(Enum(JobStage), default=JobStage.PENDING)
    stage_progress: Mapped[float] = mapped_column(Float, default=0.0)   # 0..1 בתוך השלב
    overall_progress: Mapped[float] = mapped_column(Float, default=0.0)  # 0..1 סה"כ
    message: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    error_code: Mapped[str] = mapped_column(String(64), default="")

    # צ'קפוינטים לחידוש משימה שנכשלה
    completed_stages: Mapped[list[str]] = mapped_column(JSON, default=list)
    artifacts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    settings_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    is_live_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    live_cycles: Mapped[int] = mapped_column(Integer, default=0)

    # --- מצב קליטת שידור חי (ראו LiveState) ---
    live_state: Mapped[str] = mapped_column(String(16), default=LiveState.IDLE.value)
    live_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    live_captured_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    live_reconnects: Mapped[int] = mapped_column(Integer, default=0)
    live_stop_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    # רשימת המקטעים שהוקלטו בפועל: [{path, seconds, started_at, ok}]
    # נשמרת אחרי כל מקטע כדי שנפילה לא תאבד חומר שכבר נאסף.
    live_segments: Mapped[list[Any]] = mapped_column(JSON, default=list)
    live_error: Mapped[str] = mapped_column(Text, default="")

    # --- פרויקט (ראו ProjectPhase / RunScope) ---
    # משימה ישנה שנוצרה דרך /api/jobs נשארת עם phase ריק ו-run_scope
    # "all"; השלב שלה מחושב מהסטטוס בעת ההצגה.
    mode: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    phase: Mapped[str] = mapped_column(String(16), default="")
    run_scope: Mapped[str] = mapped_column(String(16), default=RunScope.ALL.value)
    ui_language: Mapped[str] = mapped_column(String(8), default="he")
    content_language: Mapped[str] = mapped_column(String(8), default="auto")
    project_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    analysis: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    # השגיאה האחרונה בצורה שניתנת לתרגום מחדש (ראו PolixorError.to_record)
    error_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    source: Mapped[Optional[Source]] = relationship(back_populates="jobs")
    clips: Mapped[list["Clip"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    segments: Mapped[list["TranscriptSegment"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    moments: Mapped[list["Moment"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    timings: Mapped[list["StageTiming"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class TranscriptSegment(Base):
    """מקטע תמלול עם תזמון. `words` מכיל תזמון ברמת מילה כשזמין."""

    __tablename__ = "transcript_segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    idx: Mapped[int] = mapped_column(Integer, default=0)
    start: Mapped[float] = mapped_column(Float, default=0.0)
    end: Mapped[float] = mapped_column(Float, default=0.0)
    text: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str] = mapped_column(String(8), default="")
    avg_logprob: Mapped[float] = mapped_column(Float, default=0.0)
    no_speech_prob: Mapped[float] = mapped_column(Float, default=0.0)
    words: Mapped[list[Any]] = mapped_column(JSON, default=list)

    job: Mapped[Job] = relationship(back_populates="segments")


Index("ix_segment_job_start", TranscriptSegment.job_id, TranscriptSegment.start)


class Moment(Base):
    """רגע מעניין שזוהה – מועמד לקליפ, לפני הייצוא בפועל."""

    __tablename__ = "moments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    start: Mapped[float] = mapped_column(Float, default=0.0)
    end: Mapped[float] = mapped_column(Float, default=0.0)
    peak_time: Mapped[float] = mapped_column(Float, default=0.0)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    title: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(32), default="")
    signals: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source_of_truth: Mapped[str] = mapped_column(String(16), default="heuristic")  # heuristic|llm

    job: Mapped[Job] = relationship(back_populates="moments")


class Clip(Base):
    """קליפ מיוצא בפועל (קובץ MP4 על הדיסק)."""

    __tablename__ = "clips"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    moment_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    kind: Mapped[ClipKind] = mapped_column(Enum(ClipKind), default=ClipKind.SHORT)
    status: Mapped[ClipStatus] = mapped_column(Enum(ClipStatus), default=ClipStatus.PENDING)

    title: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    score: Mapped[float] = mapped_column(Float, default=0.0)

    source_start: Mapped[float] = mapped_column(Float, default=0.0)
    source_end: Mapped[float] = mapped_column(Float, default=0.0)
    duration: Mapped[float] = mapped_column(Float, default=0.0)

    # רצף מקטעים – לקליפ Highlights. ריק => קליפ רציף
    segments_json: Mapped[list[Any]] = mapped_column(JSON, default=list)

    file_path: Mapped[str] = mapped_column(Text, default="")
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    thumbnail_path: Mapped[str] = mapped_column(Text, default="")
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    aspect: Mapped[str] = mapped_column(String(16), default="16:9")
    layout: Mapped[str] = mapped_column(String(16), default="center")

    subtitles_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    subtitle_style: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    render_params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    job: Mapped[Job] = relationship(back_populates="clips")
    cues: Mapped[list["SubtitleCue"]] = relationship(
        back_populates="clip", cascade="all, delete-orphan"
    )


class SubtitleCue(Base):
    """כתובית אחת בתוך קליפ. זמנים יחסיים לתחילת הקליפ."""

    __tablename__ = "subtitle_cues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    clip_id: Mapped[str] = mapped_column(ForeignKey("clips.id"), index=True)
    idx: Mapped[int] = mapped_column(Integer, default=0)
    start: Mapped[float] = mapped_column(Float, default=0.0)
    end: Mapped[float] = mapped_column(Float, default=0.0)
    text: Mapped[str] = mapped_column(Text, default="")
    original_text: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str] = mapped_column(String(8), default="")
    words: Mapped[list[Any]] = mapped_column(JSON, default=list)
    edited: Mapped[bool] = mapped_column(Boolean, default=False)

    clip: Mapped[Clip] = relationship(back_populates="cues")


class GeneratedImage(Base):
    """
    תמונה שנוצרה באזור AI Images.

    `is_ai` מציין אם התמונה נוצרה על-ידי מודל אמיתי. ספק ה-placeholder
    המקומי מסמן False, והממשק מציג זאת במפורש – כדי שלא ייווצר רושם
    שתמונה מקומית היא תוצר של מודל.
    """

    __tablename__ = "generated_images"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    # ריק => התמונה בספרייה הכללית ולא שייכת לפרויקט מסוים
    job_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    parent_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    prompt: Mapped[str] = mapped_column(Text, default="")
    revised_prompt: Mapped[str] = mapped_column(Text, default="")
    aspect: Mapped[str] = mapped_column(String(8), default="16:9")

    status: Mapped[ImageStatus] = mapped_column(Enum(ImageStatus), default=ImageStatus.QUEUED)
    error: Mapped[str] = mapped_column(Text, default="")
    error_code: Mapped[str] = mapped_column(String(48), default="")

    provider: Mapped[str] = mapped_column(String(32), default="")
    model: Mapped[str] = mapped_column(String(48), default="")
    is_ai: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str] = mapped_column(Text, default="")

    file_path: Mapped[str] = mapped_column(Text, default="")
    thumb_path: Mapped[str] = mapped_column(Text, default="")
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)

    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


Index("ix_image_job_created", GeneratedImage.job_id, GeneratedImage.created_at)


class ImagePlacement(Base):
    """
    שיבוץ של תמונה בקליפ מסוים.

    `at_time` נמדד בשניות על **ציר הזמן של הפלט הערוך** – כלומר מה
    שהמשתמש רואה בנגן – ולא על זמני המקור. כך שיבוץ שנבחר מול הנגן
    נשאר במקום שנבחר גם אחרי שהעריכה הסירה אוויר מת.
    """

    __tablename__ = "image_placements"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    clip_id: Mapped[str] = mapped_column(
        ForeignKey("clips.id", ondelete="CASCADE"), index=True
    )
    image_id: Mapped[str] = mapped_column(
        ForeignKey("generated_images.id", ondelete="CASCADE"), index=True
    )

    role: Mapped[ImageRole] = mapped_column(Enum(ImageRole), default=ImageRole.INSERT)
    at_time: Mapped[float] = mapped_column(Float, default=0.0)
    duration: Mapped[float] = mapped_column(Float, default=3.0)
    opacity: Mapped[float] = mapped_column(Float, default=1.0)
    scale: Mapped[float] = mapped_column(Float, default=1.0)    # יחס מרוחב הפריים
    position: Mapped[str] = mapped_column(String(16), default="center")
    # top_left | top_right | bottom_left | bottom_right | center
    fit: Mapped[str] = mapped_column(String(12), default="cover")  # cover | contain
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    idx: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


Index("ix_placement_clip_time", ImagePlacement.clip_id, ImagePlacement.at_time)


class ImageThread(Base):
    """שיחה בסטודיו התמונות. התמונות עצמן הן GeneratedImage רגילות."""

    __tablename__ = "image_threads"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    job_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    title: Mapped[str] = mapped_column(Text, default="")
    cover_image_id: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class ImageMessage(Base):
    """הודעה בשיחה: של המשתמש (טקסט + תמונות מצורפות) או תשובה (תמונה)."""

    __tablename__ = "image_messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    thread_id: Mapped[str] = mapped_column(
        ForeignKey("image_threads.id", ondelete="CASCADE"), index=True)
    idx: Mapped[int] = mapped_column(Integer, default=0)
    role: Mapped[str] = mapped_column(String(12), default="user")      # user | assistant
    text: Mapped[str] = mapped_column(Text, default="")
    attachments: Mapped[list[str]] = mapped_column(JSON, default=list)
    image_id: Mapped[str] = mapped_column(String(32), default="")
    mode: Mapped[str] = mapped_column(String(12), default="")
    aspect: Mapped[str] = mapped_column(String(8), default="")
    background: Mapped[str] = mapped_column(String(16), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class StageTiming(Base):
    """מדידות זמן אמיתיות לכל שלב – הבסיס היחיד להערכות זמן בממשק."""

    __tablename__ = "stage_timings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    stage: Mapped[str] = mapped_column(String(32), default="")
    seconds: Mapped[float] = mapped_column(Float, default=0.0)
    media_seconds: Mapped[float] = mapped_column(Float, default=0.0)  # אורך החומר שעובד
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    job: Mapped[Job] = relationship(back_populates="timings")


class ClipReview(Base):
    """
    A human review of one finished clip (Studio QA mode) and the user's decision.

    `features` is a snapshot of what the system knew when the clip was made
    (profile, moment type, editor checks, publish verdict, subtitle timing QA,
    duration…), so review data can later inform ranking without re-deriving
    anything. Nothing is learned automatically from it.
    """

    __tablename__ = "clip_reviews"

    clip_id: Mapped[str] = mapped_column(ForeignKey("clips.id"), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(32), index=True)
    post: Mapped[str] = mapped_column(String(16), default="")          # yes | small_fix | no
    hook: Mapped[str] = mapped_column(String(8), default="")           # yes | no
    story: Mapped[str] = mapped_column(String(8), default="")          # yes | no
    subtitles: Mapped[str] = mapped_column(String(16), default="")     # good | text | timing
    edit: Mapped[str] = mapped_column(String(16), default="")          # good | cut | pacing | framing | other
    note: Mapped[str] = mapped_column(Text, default="")
    decision: Mapped[str] = mapped_column(String(16), default="")      # approved | rejected
    features: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Notification(Base):
    """
    התראה למשתמש (פעמון בסרגל העליון). הטקסט לא נשמר – רק סוג, פרמטרים
    ושגיאה בצורה שניתנת לתרגום – כדי שכל התראה תוצג בשפת הממשק הנוכחית,
    גם אם נוצרה בשפה אחרת. ראו services/notifications.py.
    """

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(48), index=True)
    level: Mapped[str] = mapped_column(String(12), default="info")   # info|success|warning|error
    # התראות עם אותו מפתח קבוצה שעוד לא נקראו מתאחדות לאחת (count עולה)
    group_key: Mapped[str] = mapped_column(String(160), default="", index=True)
    count: Mapped[int] = mapped_column(Integer, default=1)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    link: Mapped[str] = mapped_column(Text, default="")
    job_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    clip_id: Mapped[str] = mapped_column(String(32), default="")
    account_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


# --------------------------------------------------------------------------
# פרסום לרשתות (services/publishing)
# --------------------------------------------------------------------------
class SocialAccount(Base):
    """
    חשבון מחובר בפלטפורמה. הטוקנים מוצפנים (Fernet, מפתח המאסטר המקומי)
    ולעולם לא יוצאים מהשרת; ה-API מחזיר רק שם, סטטוס ותוקף.
    """

    __tablename__ = "social_accounts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    platform: Mapped[str] = mapped_column(String(24), index=True)
    external_id: Mapped[str] = mapped_column(String(128), default="")
    display_name: Mapped[str] = mapped_column(Text, default="")
    handle: Mapped[str] = mapped_column(Text, default="")
    avatar_url: Mapped[str] = mapped_column(Text, default="")
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    # connected | reconnect_required | revoked
    status: Mapped[str] = mapped_column(String(24), default="connected")
    access_token_enc: Mapped[str] = mapped_column(Text, default="")
    refresh_token_enc: Mapped[str] = mapped_column(Text, default="")
    token_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    refresh_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    connected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class OAuthState(Base):
    """`state` חד-פעמי לחיבור חשבון (10 דקות), עם PKCE verifier מוצפן."""

    __tablename__ = "oauth_states"

    state: Mapped[str] = mapped_column(String(96), primary_key=True)
    platform: Mapped[str] = mapped_column(String(24))
    verifier_enc: Mapped[str] = mapped_column(Text, default="")
    redirect_uri: Mapped[str] = mapped_column(Text, default="")
    return_to: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class PublishJob(Base):
    """
    פרסום של סרטון אחד לחשבון אחד. פרסום לכמה חשבונות = כמה שורות עם אותו
    group_id. אותה מערכת לקליפים קצרים ולסרטונים ארוכים (format).
    """

    __tablename__ = "publish_jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    group_id: Mapped[str] = mapped_column(String(32), index=True, default="")
    clip_id: Mapped[str] = mapped_column(String(32), index=True, default="")
    account_id: Mapped[str] = mapped_column(String(32), index=True, default="")
    platform: Mapped[str] = mapped_column(String(24), default="")
    format: Mapped[str] = mapped_column(String(12), default="short")      # short | long
    title: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    privacy: Mapped[str] = mapped_column(String(32), default="public")
    options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # now | schedule
    mode: Mapped[str] = mapped_column(String(12), default="now")
    # platform – הפלטפורמה מפרסמת בזמן שנקבע; polixor – Polixor מפרסם בזמן שנקבע
    schedule_by: Mapped[str] = mapped_column(String(12), default="")
    schedule_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # queued | scheduled | uploading | processing | scheduled_on_platform |
    # published | failed | cancelled
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    remote_id: Mapped[str] = mapped_column(Text, default="")
    remote_url: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    history: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
