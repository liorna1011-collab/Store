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
  performance_profile: 'auto' | 'fast' | 'quality'
  long_source_minutes: number
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
  selection_engine: 'intel' | 'legacy'
  clip_min_quality: number
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
  platform_key?: string
  content?: string
  live_certain?: boolean
  start_hint?: number | null
  video_id?: string
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
  platform_key?: string
  was_live?: boolean
  start_hint?: number | null
  max_source_hours?: number
  needs_section?: boolean
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

// ==========================================================================
// פרויקטים (Import → Analyze → Mode → Settings → Generate → Results)
// ==========================================================================
export type ProjectPhase =
  | 'importing' | 'analyzing' | 'configure' | 'generating' | 'done' | 'failed'
export type ProjectMode = 'short' | 'longform'

export interface SubtitleStyle {
  preset: string | null
  font: string
  size: number
  weight: number
  color: string
  highlight_color: string
  background: 'none' | 'box' | 'bar'
  background_color: string
  background_opacity: number
  outline: number
  outline_color: string
  shadow: number
  shadow_color: string
  shadow_opacity: number
  position: 'top' | 'middle' | 'bottom'
  offset: number
  words_per_line: number
  max_lines: number
  animation: 'none' | 'fade' | 'pop' | 'karaoke' | 'word' | 'bounce'
  uppercase: boolean
}

export interface ProjectConfig {
  mode: ProjectMode | null
  aspect_ratio: '9:16' | '1:1' | '4:5' | '16:9'
  clip_min_seconds: number
  clip_max_seconds: number
  clip_count: number
  longform_target_seconds: number
  layout: 'auto' | 'reaction' | 'face' | 'center' | 'blur'
  subtitles: { enabled: boolean; style: SubtitleStyle }
  content_language: 'auto' | 'he' | 'en'
}

export interface ProjectOptions {
  modes: string[]
  aspect_ratios: string[]
  layouts: string[]
  clip_lengths: { min: number; max: number }[]
  clip_counts: number[]
  longform_targets: number[]
  content_languages: string[]
}

export interface ProjectDefaults {
  config: ProjectConfig
  options: ProjectOptions
}

export interface LayoutSegmentInfo {
  start: number
  end: number
  kind: 'reaction' | 'camera' | 'screen'
  facecam?: { x: number; y: number; w: number; h: number } | null
  confidence?: number
}

export interface ProjectAnalysis {
  duration: number
  language: string | null
  transcript: { available: boolean; words: number; segments: number; provider: string; note: string | null }
  audio: { available: boolean; speech_ratio: number; silence_seconds: number; loudness_lufs: number | null }
  speakers: { available: boolean; count: number | null; note: string }
  faces: { detected: boolean; seconds: number; ratio: number }
  facecam: { detected: boolean; segments: number; box: { x: number; y: number; w: number; h: number } | null; seconds: number }
  screen: { layouts: Record<string, number>; segments?: LayoutSegmentInfo[] }
  layout_note: string | null
  moments: { count: number; top: { start: number; end: number; score: number; title: string }[] }
  timeline_available: boolean
}

export interface Project {
  id: string
  title: string
  created_at: string | null
  updated_at: string | null
  phase: ProjectPhase
  status: JobStatus
  stage: string
  stage_label: string
  stage_progress: number
  overall_progress: number
  message: string | null
  eta_seconds: number | null
  error: { code: string; message: string; hint: string } | null
  source: {
    kind: string; platform: string; url: string | null; title: string | null
    uploader: string | null; duration: number | null; thumbnail_url: string | null
    width: number | null; height: number | null; is_live: boolean
    section: { start: number; end: number } | null
  }
  mode: ProjectMode | null
  ui_language: string
  content_language: string
  config: ProjectConfig
  analysis: ProjectAnalysis | null
  clip_counts: Record<string, number>
  is_live: boolean
  legacy: boolean
  notes: string[]
}

export interface SubtitlePreset {
  id: string
  label: Record<string, string>
  description: Record<string, string>
  style: SubtitleStyle
}

export interface PresetsResponse {
  presets: SubtitlePreset[]
  default: SubtitleStyle
  limits: {
    size: [number, number]; outline: [number, number]; shadow: [number, number]
    offset: [number, number]; words_per_line: [number, number]; max_lines: [number, number]
    weights: number[]; backgrounds: string[]; positions: string[]; animations: string[]
  }
}

export interface FontInfo {
  family: string
  hebrew: boolean
  latin: boolean
  weights: number[]
}

export interface FontsResponse {
  fonts: FontInfo[]
  default: { he: string; en: string }
}

export interface SubtitlePreview {
  image: string
  width: number
  height: number
  target_width: number
  target_height: number
  at: number
  lines: string[][]
  language: string
  style: SubtitleStyle
  background: 'source_frame' | 'plain'
  background_note: string
  font_weights: number[]
  elapsed_ms: number
}

export interface LongformChapter {
  start: number
  title: string
  source_start: number
}

// ---- דוח הבחירה של מנוע הקליפים (/api/projects/{id}/clip-review) ----
export interface ReviewReason { key: string; text: string }
export interface ReviewRecord {
  id: string
  status: 'selected' | 'near_miss' | 'duplicate'
  start: number
  end: number
  duration: number
  final_score: number
  proposed_by: ReviewReason[]
  hook: { text: string; start: number; end: number; score: number; reasons: ReviewReason[]; problems: ReviewReason[] }
  context: { text: string; sentences: number; seconds: number }
  payoff: { text: string; start: number; end: number; score: number; reasons: ReviewReason[]; tail: string }
  components: { key: string; label: string; value: number }[]
  penalties: { key: string; label: string; value: number }[]
  boundaries: { start: number; end: number; start_reason: ReviewReason; end_reason: ReviewReason }
  low_confidence_words: number
  rejection: ReviewReason | null
  duplicate_of: string | null
  overlapping_alternatives?: { start: number; end: number; final_score: number; payoff: string }[]
}
export interface ClipReview {
  available: boolean
  threshold?: number
  stats?: Record<string, number>
  selected?: ReviewRecord[]
  near_misses?: ReviewRecord[]
  duplicates?: ReviewRecord[]
}
