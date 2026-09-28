// טיפוסים המשקפים את תשובות ה-API של השרת.

export type JobStatus =
  | 'queued' | 'running' | 'paused' | 'completed' | 'failed' | 'cancelled'

export type StageKey =
  | 'pending' | 'download' | 'probe' | 'audio' | 'transcribe'
  | 'analyze' | 'select' | 'render_long' | 'render_short' | 'done'

export interface StageTiming {
  stage: string
  seconds: number
  media_seconds: number
}

export interface SourceInfo {
  id?: string
  kind?: string
  title?: string
  uploader?: string
  duration?: number
  file_size?: number
  width?: number
  height?: number
  fps?: number
  has_audio?: boolean
  is_live?: boolean
  url?: string
}

export interface Job {
  id: string
  title: string
  input_url: string
  status: JobStatus
  stage: StageKey
  stage_label: string
  stage_progress: number
  overall_progress: number
  message: string
  error: string
  error_code: string
  is_live_mode: boolean
  live_cycles: number
  completed_stages: string[]
  notes: string[]
  created_at: string | null
  started_at: string | null
  finished_at: string | null
  source: SourceInfo | null
  clip_counts: Record<string, number>
  timings: StageTiming[]
  eta_seconds: number | null
  eta_basis: string
}

export type ClipKind = 'long' | 'short' | 'highlights'
// needs_review: הקובץ קיים וניתן לניגון, אבל בדיקת האיכות מצאה
// בעיה. הוא נפתח ומתנגן כרגיל — רק מסומן.
export type ClipStatus =
  | 'pending' | 'rendering' | 'ready' | 'needs_review' | 'failed'

/** קליפ שיש לו קובץ אפשר לנגן ולפתוח, גם אם הוא מסומן לבדיקה. */
export function clipIsPlayable(status: ClipStatus): boolean {
  return status === 'ready' || status === 'needs_review'
}

export interface QaFinding {
  code: string
  severity: 'error' | 'warning'
  message: string
  detail: Record<string, unknown>
}

export interface QaReport {
  passed: boolean
  needs_review: boolean
  findings: QaFinding[]
  measurements: Record<string, unknown>
  checks_run: string[]
  checks_skipped: Record<string, string>
  summary: string
}

export interface Clip {
  id: string
  job_id: string
  kind: ClipKind
  status: ClipStatus
  title: string
  description: string
  reason: string
  score: number
  source_start: number
  source_end: number
  duration: number
  segments: number[][]
  width: number
  height: number
  aspect: string
  layout: string
  file_size: number
  has_file: boolean
  has_thumbnail: boolean
  subtitles_enabled: boolean
  subtitle_style: Record<string, unknown>
  render_params: Record<string, unknown>
  error: string
  cue_count: number
  created_at: string | null
}

export interface Cue {
  id: number
  idx: number
  start: number
  end: number
  text: string
  original_text: string
  language: string
  words: { start: number; end: number; text: string }[]
  edited: boolean
}

export interface AppSettings {
  whisper_model: string
  whisper_device: string
  whisper_compute_type: string
  transcribe_language: string
  transcript_provider: string
  sensitivity: number
  visual_sample_fps: number
  use_chat_signal: boolean

  ai_mode: 'heuristic' | 'ollama' | 'cloud'
  ai_provider: 'anthropic' | 'openai' | 'ollama'
  ai_model: string
  ai_discover_moments: boolean

  // AI Images
  image_provider: 'openai' | 'placeholder'
  image_model: string
  image_quality: 'low' | 'medium' | 'high'
  image_timeout_seconds: number
  image_retries: number

  // שידור חי
  live_segment_seconds: number
  live_max_minutes: number
  live_keep_segments: boolean

  edit_style_long: EditStyleName
  edit_style_short: EditStyleName
  remove_silence: boolean
  silence_min_gap: number
  max_removed_ratio: number
  angle_changes: boolean

  long_enabled: boolean
  long_min_seconds: number
  long_max_seconds: number
  long_mode: 'continuous' | 'highlights'
  long_count: number

  short_enabled: boolean
  short_min_seconds: number
  short_max_seconds: number
  short_count: number
  short_layout: 'center' | 'auto_face' | 'split' | 'blur_pad'
  short_resolution: string
  camera_region: Record<string, number>

  export_dir: string
  video_quality: 'low' | 'medium' | 'high'
  long_resolution: string
  audio_normalize: boolean
  hw_accel: string

  subtitles_enabled: boolean
  subtitle_language: string
  subtitle_font: string
  subtitle_size: number
  subtitle_color: string
  subtitle_outline_color: string
  subtitle_position: 'top' | 'middle' | 'bottom'
  subtitle_word_level: boolean
  subtitle_animation: CaptionAnimation
  title_card_enabled: boolean

  // במאי ה-AI
  director_enabled: boolean
  director_style: string
  caption_preset: string

  // מוזיקת רקע — התוכנה אינה מספקת קבצים
  music_enabled: boolean
  music_path: string
  music_profile: 'minimal' | 'balanced' | 'energetic'

  // מאסטרינג אודיו
  mastering_enabled: boolean
  mastering_target: 'social' | 'podcast' | 'broadcast'
  mastering_denoise: boolean
  mastering_compress: boolean

  context_pad_before: number
  context_pad_after: number
  max_clips_total: number
  concurrent_jobs: number
}

export interface SettingsResponse {
  values: AppSettings
  secrets: Record<string, { configured: boolean; masked: string }>
  ai_status: Record<string, unknown>
}

export interface SystemInfo {
  app: { name: string; version: string }
  python: string
  platform: string
  cpu_count: number
  ffmpeg: { available: boolean; path: string }
  ffprobe: { available: boolean; path: string }
  modules: Record<string, { available: boolean; version: string }>
  gpu: { cuda: boolean; name: string; nvenc: boolean }
  data_dir: string
  free_disk_bytes: number
  warnings: string[]
}

export interface ResolveResult {
  kind: string
  platform: string
  is_live: boolean
  normalized_url: string
  notes: string[]
}

export interface ProbeResult {
  platform: string
  kind: string
  title: string
  uploader: string
  duration: number
  is_live: boolean
  live_status: string
  thumbnail: string
  filesize_approx: number
  notes: string[]
  webpage_url: string
}

export interface TimelineData {
  available: boolean
  reason?: string
  hop?: number
  duration?: number
  times?: number[]
  score?: number[]
  vocal?: number[]
  speech?: number[]
  visual?: number[]
  pause?: number[]
}

export interface ApiError {
  code: string
  message: string
  hint: string
  detail?: string
}

export interface WsEvent {
  type: string
  job_id: string
  data: Record<string, any>
  ts: number
}


export type EditStyleName = 'raw' | 'clean' | 'dynamic' | 'hype'
export type CaptionAnimation = 'none' | 'pop' | 'punch'

export interface EditStyleInfo {
  name: EditStyleName
  label: string
  description: string
  removes_silence: boolean
  angle_changes: boolean
  caption_animation: CaptionAnimation
  color_punch: number
}

export interface CaptionAnimationInfo {
  name: CaptionAnimation
  label: string
  description: string
}

export interface EditStylesResponse {
  styles: EditStyleInfo[]
  caption_animations: CaptionAnimationInfo[]
}

/** ביט אחד בתכנית העריכה – קטע רציף עם גודל פריים ומהירות משלו. */
export interface EditBeat {
  start: number
  end: number
  zoom: number
  speed: number
  reason: string
}

export interface EditStats {
  style: string
  beats: number
  cuts: number
  raw_duration: number
  out_duration: number
  removed_seconds: number
  removed_percent: number
  dramatic_pauses_kept: number
  zoom_changes: number
  notes: string[]
}


// --------------------------------------------------------------------------
// AI Images
// --------------------------------------------------------------------------
export type ImageAspect = '1:1' | '16:9' | '9:16'
export type ImageStatus = 'queued' | 'generating' | 'ready' | 'failed' | 'cancelled'
export type ImageRole =
  'intro' | 'outro' | 'insert' | 'broll' | 'overlay' | 'background' | 'thumbnail'

export interface GeneratedImage {
  id: string
  job_id: string
  parent_id: string
  prompt: string
  revised_prompt: string
  aspect: ImageAspect
  status: ImageStatus
  error: string
  error_code: string
  provider: string
  model: string
  /** false => לא נוצרה על-ידי מודל AI. הממשק חייב לומר זאת. */
  is_ai: boolean
  note: string
  width: number
  height: number
  file_size: number
  has_file: boolean
  url: string
  thumb_url: string
  created_at: string | null
}

export interface ImageProvider {
  name: string
  label: string
  is_ai: boolean
  available: boolean
  reason: string
  selected: boolean
  key_configured: boolean
  key_masked: string
}

export interface ImageProvidersResponse {
  providers: ImageProvider[]
  selected: string
  ready: boolean
  reason: string
}

export interface ImagePlacement {
  id: string
  clip_id: string
  image_id: string
  role: ImageRole
  role_label: string
  at_time: number
  duration: number
  opacity: number
  scale: number
  position: string
  fit: string
  enabled: boolean
  extends_timeline: boolean
  image: GeneratedImage | null
}

export interface VisualSuggestion {
  start: number
  end: number
  text: string
  prompt: string
  aspect: ImageAspect
  reason: string
  role: ImageRole
  duration: number
  score: number
  source: string
}

export interface SuggestVisualsResponse {
  clip_id: string
  job_id: string
  suggestions: VisualSuggestion[]
  source: string
  note: string
}

// --------------------------------------------------------------------------
// שידור חי
// --------------------------------------------------------------------------
export type LiveState =
  'idle' | 'detecting' | 'connecting' | 'live' | 'reconnecting'
  | 'stopping' | 'completed' | 'failed'

export interface LiveDetectResult {
  url: string
  platform: string
  kind: string
  title: string
  uploader: string
  is_live: boolean
  live_status: string
  available: boolean
  width: number
  height: number
  fps: number
  has_audio: boolean
  /** false => נוכחות האודיו לפי מטא-דאטה בלבד, לא נבדקה בזרם. */
  audio_checked: boolean
  resolution_label: string
  thumbnail: string
  notes: string[]
  reason: string
  ffmpeg_ready: boolean
}

export interface LiveStatus {
  job_id: string
  is_live_mode: boolean
  state: LiveState
  state_label: string
  capturing: boolean
  stop_requested: boolean
  captured_seconds: number
  segments: number
  reconnects: number
  started_at: string | null
  error: string
  job_status: string
  note: string
}
