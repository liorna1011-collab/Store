"""סכמות Pydantic ל-API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------
# מקורות
# --------------------------------------------------------------------------
class ResolveRequest(BaseModel):
    url: str


class ResolveResponse(BaseModel):
    kind: str
    platform: str
    is_live: bool
    normalized_url: str
    notes: list[str] = Field(default_factory=list)
    platform_key: str = ""
    content: str = "video"
    live_certain: bool = False
    start_hint: Optional[float] = None
    video_id: str = ""


class ProbeResponse(BaseModel):
    platform: str
    kind: str
    title: str = ""
    uploader: str = ""
    duration: float = 0.0
    is_live: bool = False
    live_status: str = ""
    thumbnail: str = ""
    filesize_approx: int = 0
    notes: list[str] = Field(default_factory=list)
    webpage_url: str = ""
    platform_key: str = ""
    was_live: bool = False
    start_hint: Optional[float] = None
    id: str = ""
    extractor: str = ""
    availability: str = ""
    chapters: list[dict[str, Any]] = Field(default_factory=list)
    max_source_hours: float = 12.0
    needs_section: bool = False


# --------------------------------------------------------------------------
# משימות
# --------------------------------------------------------------------------
class CreateJobRequest(BaseModel):
    url: str = ""
    upload_token: str = ""          # מזהה שמוחזר מ-/api/upload
    title: str = ""
    live_mode: bool = False
    settings_override: dict[str, Any] = Field(default_factory=dict)


class StageTimingOut(BaseModel):
    stage: str
    seconds: float
    media_seconds: float


class JobOut(BaseModel):
    id: str
    title: str
    input_url: str
    status: str
    stage: str
    stage_label: str
    stage_progress: float
    overall_progress: float
    message: str
    error: str
    error_code: str
    is_live_mode: bool
    live_cycles: int
    completed_stages: list[str]
    notes: list[str] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    source: Optional[dict[str, Any]] = None
    clip_counts: dict[str, int] = Field(default_factory=dict)
    timings: list[StageTimingOut] = Field(default_factory=list)
    eta_seconds: Optional[float] = None
    eta_basis: str = ""


# --------------------------------------------------------------------------
# קליפים
# --------------------------------------------------------------------------
class CueOut(BaseModel):
    id: int
    idx: int
    start: float
    end: float
    text: str
    original_text: str
    language: str
    words: list[Any] = Field(default_factory=list)
    edited: bool


class CueIn(BaseModel):
    id: Optional[int] = None
    start: float
    end: float
    text: str


class ClipOut(BaseModel):
    id: str
    job_id: str
    kind: str
    status: str
    title: str
    description: str
    reason: str
    score: float
    source_start: float
    source_end: float
    duration: float
    segments: list[list[float]] = Field(default_factory=list)
    width: int
    height: int
    aspect: str
    layout: str
    file_size: int
    has_file: bool
    has_thumbnail: bool
    subtitles_enabled: bool
    subtitle_style: dict[str, Any] = Field(default_factory=dict)
    render_params: dict[str, Any] = Field(default_factory=dict)
    error: str
    cue_count: int = 0
    created_at: Optional[datetime] = None


class ClipPatch(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None


class ReExportRequest(BaseModel):
    source_start: Optional[float] = None
    source_end: Optional[float] = None
    aspect: Optional[str] = None            # "16:9" | "9:16"
    layout: Optional[str] = None            # center | auto_face | split
    subtitles_enabled: Optional[bool] = None
    subtitle_style: Optional[dict[str, Any]] = None
    camera_region: Optional[dict[str, float]] = None
    title_card: Optional[bool] = None
    quality: Optional[str] = None
    edit_style: Optional[str] = None          # raw | clean | dynamic | hype
    caption_animation: Optional[str] = None   # none | pop | punch


class ZipRequest(BaseModel):
    clip_ids: list[str]
    include_subtitles: bool = True


# --------------------------------------------------------------------------
# שידור חי
# --------------------------------------------------------------------------
class LiveDetectRequest(BaseModel):
    url: str
    probe_media: bool = True        # לבדוק את הזרם עצמו, לא רק מטא-דאטה


class LiveDetectResponse(BaseModel):
    url: str = ""
    platform: str = ""
    kind: str = ""
    title: str = ""
    uploader: str = ""
    is_live: bool = False
    live_status: str = ""
    available: bool = False
    width: int = 0
    height: int = 0
    fps: float = 0.0
    has_audio: bool = False
    audio_checked: bool = False     # False => לפי מטא-דאטה בלבד
    resolution_label: str = ""
    thumbnail: str = ""
    notes: list[str] = Field(default_factory=list)
    reason: str = ""
    ffmpeg_ready: bool = True


class LiveStatusOut(BaseModel):
    job_id: str
    is_live_mode: bool = False
    state: str = "idle"
    state_label: str = ""
    capturing: bool = False
    stop_requested: bool = False
    captured_seconds: float = 0.0
    segments: int = 0
    reconnects: int = 0
    started_at: Optional[datetime] = None
    error: str = ""
    job_status: str = ""
    note: str = ""


# --------------------------------------------------------------------------
# תמונות (AI Images)
# --------------------------------------------------------------------------
class ImageCreateRequest(BaseModel):
    prompt: str
    aspect: str = "16:9"
    job_id: str = ""


class ImagePromptPatch(BaseModel):
    prompt: str


class ImageOut(BaseModel):
    id: str
    job_id: str = ""
    parent_id: str = ""
    prompt: str
    revised_prompt: str = ""
    aspect: str
    status: str
    error: str = ""
    error_code: str = ""
    provider: str = ""
    model: str = ""
    is_ai: bool = True
    note: str = ""
    width: int = 0
    height: int = 0
    file_size: int = 0
    has_file: bool = False
    url: str = ""
    thumb_url: str = ""
    created_at: Optional[datetime] = None


class ImageProvidersOut(BaseModel):
    providers: list[dict[str, Any]] = Field(default_factory=list)
    selected: str = ""
    ready: bool = False
    # הסבר קריא למשתמש כשאין ספק AI זמין – מוצג בממשק במקום
    # להעמיד פנים שהכול תקין.
    reason: str = ""


class PlacementRequest(BaseModel):
    image_id: str
    role: str = "insert"
    at_time: float = 0.0
    duration: float = 3.0
    opacity: float = 1.0
    scale: float = 1.0
    position: str = "center"
    fit: str = "cover"


class PlacementOut(BaseModel):
    id: str
    clip_id: str
    image_id: str
    role: str
    role_label: str = ""
    at_time: float
    duration: float
    opacity: float = 1.0
    scale: float = 1.0
    position: str = "center"
    fit: str = "cover"
    enabled: bool = True
    extends_timeline: bool = False
    image: Optional[ImageOut] = None


class VisualSuggestion(BaseModel):
    start: float
    end: float
    text: str = ""
    prompt: str
    aspect: str = "16:9"
    reason: str = ""
    role: str = "insert"
    duration: float = 3.0
    score: float = 0.0
    source: str = "heuristic"       # heuristic | llm


class SuggestVisualsOut(BaseModel):
    clip_id: str = ""
    job_id: str = ""
    suggestions: list[VisualSuggestion] = Field(default_factory=list)
    source: str = "heuristic"
    note: str = ""


# --------------------------------------------------------------------------
# הגדרות
# --------------------------------------------------------------------------
class SettingsOut(BaseModel):
    values: dict[str, Any]
    secrets: dict[str, Any]
    ai_status: dict[str, Any]


class SecretIn(BaseModel):
    name: str                                # anthropic_api_key | openai_api_key | cookiefile_path
    value: str


# --------------------------------------------------------------------------
# מערכת
# --------------------------------------------------------------------------
class SystemOut(BaseModel):
    app: dict[str, Any]
    python: str
    platform: str
    cpu_count: int
    ffmpeg: dict[str, Any]
    ffprobe: dict[str, Any]
    modules: dict[str, Any]
    gpu: dict[str, Any]
    data_dir: str
    free_disk_bytes: int
    warnings: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# פרויקטים
# --------------------------------------------------------------------------
class SectionIn(BaseModel):
    start: float
    end: float


class ProjectSourceIn(BaseModel):
    type: str                               # upload | url
    upload_token: str = ""
    url: str = ""
    section: Optional[SectionIn] = None
    live_capture_seconds: Optional[float] = None


class ProjectPreviewIn(BaseModel):
    title: Optional[str] = None
    thumbnail: Optional[str] = None
    duration: Optional[float] = None
    platform: Optional[str] = None
    uploader: Optional[str] = None
    is_live: Optional[bool] = None


class CreateProjectBody(BaseModel):
    source: ProjectSourceIn
    title: str = ""
    ui_language: str = "he"
    content_language: str = "auto"
    preview: Optional[ProjectPreviewIn] = None
    # שמות, כינויים ומונחים להטיית התמלול (רשימה או טקסט עם שורות/פסיקים)
    vocabulary: Optional[Any] = None
    # Polixor Studio: the goal (generation starts after the analysis by itself), profile,
    # quality mode, overlay, how many Shorts at most – see project_config.clamp_studio
    goal: Optional[str] = None              # package | short | longform
    content_profile: str = "auto"
    quality: str = "premium"
    editorial_overlay: bool = False
    clip_count: Optional[int] = None
    clip_length: Optional[str] = None       # short | medium | long (preset of min/max seconds)


class ProjectPatch(BaseModel):
    title: Optional[str] = None
    mode: Optional[str] = None
    config: Optional[dict[str, Any]] = None
    ui_language: Optional[str] = None
    content_language: Optional[str] = None


class GenerateBody(BaseModel):
    mode: Optional[str] = None
    config: Optional[dict[str, Any]] = None


class ProjectErrorOut(BaseModel):
    code: str
    message: str
    hint: str = ""


class ProjectSourceOut(BaseModel):
    kind: str = "unknown"
    platform: str = ""
    url: Optional[str] = None
    title: Optional[str] = None
    uploader: Optional[str] = None
    duration: Optional[float] = None
    thumbnail_url: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    is_live: bool = False
    section: Optional[dict[str, float]] = None


class ProjectOut(BaseModel):
    id: str
    title: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    phase: str
    status: str
    stage: str
    stage_label: str
    stage_progress: float
    overall_progress: float
    message: Optional[str] = None
    # a worker of THIS server is processing the job right now (False with status
    # queued/running = a stale record from a process that is gone)
    worker_active: Optional[bool] = None
    eta_seconds: Optional[float] = None
    error: Optional[ProjectErrorOut] = None
    source: ProjectSourceOut
    mode: Optional[str] = None
    ui_language: str = "he"
    content_language: str = "auto"
    config: dict[str, Any] = Field(default_factory=dict)
    analysis: Optional[dict[str, Any]] = None
    clip_counts: dict[str, int] = Field(default_factory=dict)
    is_live: bool = False
    legacy: bool = False
    notes: list[str] = Field(default_factory=list)
    performance: Optional[dict[str, Any]] = None


class ProjectListOut(BaseModel):
    items: list[ProjectOut]
