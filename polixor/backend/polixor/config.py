"""
Polixor – הגדרות מערכת, נתיבים ואחסון מאובטח של מפתחות API.

עקרונות:
  * כל הנתונים נשמרים מקומית כברירת מחדל (וידאו, DB, קליפים).
  * מפתחות API מוצפנים במנוחה בקובץ מקומי עם הרשאות מוגבלות,
    ולעולם אינם נשלחים לדפדפן – ה-API מחזיר רק מסכה ("sk-...abcd").
  * אין תלות ב-pydantic-settings כדי לצמצם תלויות.
"""

from __future__ import annotations

import base64
import json
import os
import platform
import shutil
import stat
import sys
import threading
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Optional

APP_NAME = "Polixor"
APP_VERSION = "1.0.0"


# --------------------------------------------------------------------------
# נתיבים
# --------------------------------------------------------------------------
def _default_data_dir() -> Path:
    """תיקיית הנתונים הראשית, לפי מערכת ההפעלה."""
    override = os.environ.get("POLIXOR_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()

    system = platform.system()
    if system == "Windows":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / APP_NAME
        return Path.home() / "AppData" / "Local" / APP_NAME
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    # Linux / אחר
    base = os.environ.get("XDG_DATA_HOME")
    if base:
        return Path(base) / APP_NAME.lower()
    return Path.home() / ".local" / "share" / APP_NAME.lower()


class Paths:
    """כל נתיבי הדיסק של האפליקציה, נוצרים בעת ההפעלה."""

    def __init__(self, data_dir: Optional[Path] = None) -> None:
        self.data = Path(data_dir) if data_dir else _default_data_dir()
        self.sources = self.data / "sources"        # וידאו מקור שהורד/הועלה
        self.work = self.data / "work"              # קבצי עבודה זמניים לכל משימה
        self.exports = self.data / "exports"        # קליפים מיוצאים
        self.models = self.data / "models"          # מטמון מודלים (whisper)
        self.images = self.data / "images"          # תמונות שנוצרו ב-AI Images
        self.captures = self.data / "captures"      # מקטעי הקלטה של שידור חי
        self.logs = self.data / "logs"
        self.db_path = self.data / "polixor.db"
        self.secrets_path = self.data / "secrets.enc"
        self.keyfile_path = self.data / ".master.key"
        self.settings_path = self.data / "settings.json"

    def ensure(self) -> None:
        for p in (self.data, self.sources, self.work, self.exports, self.models,
                  self.images, self.captures, self.logs):
            p.mkdir(parents=True, exist_ok=True)

    def capture_dir(self, job_id: str) -> Path:
        d = self.captures / job_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def job_work_dir(self, job_id: str) -> Path:
        d = self.work / job_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def job_export_dir(self, job_id: str) -> Path:
        d = self.exports / job_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def free_bytes(self) -> int:
        try:
            return shutil.disk_usage(self.data).free
        except OSError:
            return 0


PATHS = Paths()


# --------------------------------------------------------------------------
# הגדרות שניתנות לעריכה מהממשק
# --------------------------------------------------------------------------
@dataclass
class AppSettings:
    """הגדרות משתמש. נשמרות ב-settings.json (ללא סודות)."""

    # ---- ניתוח ----
    whisper_model: str = "small"            # tiny/base/small/medium/large-v3
    whisper_device: str = "auto"            # auto/cpu/cuda
    whisper_compute_type: str = "auto"      # auto/int8/float16/float32
    transcribe_language: str = "auto"       # auto/he/en
    transcript_provider: str = "faster-whisper"   # faster-whisper | none | fixture
    sensitivity: float = 0.5                # 0..1 – רגישות לזיהוי רגעים
    visual_sample_fps: float = 1.0          # כמה פריימים בשנייה לנתח ויזואלית
    use_chat_signal: bool = False           # אות מצ'אט הלייב (כשיש הרשאה)

    # ---- שידור חי ----
    live_segment_seconds: int = 300         # אורך מקטע הקלטה
    live_max_minutes: int = 0               # 0 = עד שהמשתמש עוצר
    live_keep_segments: bool = False        # לשמור מקטעים אחרי האיחוד

    # ---- AI ----
    # heuristic – מנוע מקומי ללא מודל שפה (תמיד זמין)
    # ollama    – מודל שפה מקומי דרך Ollama (ללא עלות, דורש חומרה)
    # cloud     – Anthropic/OpenAI עם מפתח API
    ai_mode: str = "heuristic"              # heuristic | ollama | cloud
    ai_provider: str = "anthropic"          # anthropic | openai | ollama
    ai_model: str = "claude-sonnet-4-5-20250929"
    ai_discover_moments: bool = True        # לתת למודל להציע רגעים שקטים

    # ---- יצירת תמונות (AI Images) ----
    # openai      – יצירת תמונות אמיתית דרך OpenAI (דורש מפתח API בצד השרת)
    # placeholder – כרטיס גרפי מקומי שנוצר במחשב. **אינו AI** ומסומן ככזה
    #               בכל מקום בממשק; קיים כדי שניתן יהיה לבדוק את שרשרת
    #               ההכנסה לווידאו גם ללא מפתח, ולא כדי להתחזות ליצירה.
    image_provider: str = "openai"          # openai | placeholder
    image_model: str = "gpt-image-1"        # gpt-image-1 | dall-e-3 | dall-e-2
    image_quality: str = "medium"           # low | medium | high
    image_timeout_seconds: int = 120
    image_retries: int = 2                  # ניסיונות חוזרים על תקלה זמנית בלבד

    # ---- סגנון עריכה ----
    # raw     – חיתוך ישיר, ללא עריכה
    # clean   – הסרת אוויר מת + הידוק ראש/זנב + ליטוש אודיו
    # dynamic – בנוסף: שינויי זווית, דחיפה על השיא, צבע
    # hype    – הכי הדוק, לשורטים
    edit_style_long: str = "clean"
    edit_style_short: str = "dynamic"
    remove_silence: bool = True             # עקיפה גלובלית לכיבוי
    silence_min_gap: float = 0.0            # 0 = לפי הסגנון
    max_removed_ratio: float = 0.0          # 0 = לפי הסגנון
    angle_changes: bool = True              # עקיפה גלובלית לכיבוי

    # ---- במאי ה-AI ----
    # כשדולק, העריכה נגזרת מתכנית מלאה שנבנית לפני הביצוע:
    # הבנת המבנה, גיזום פתיחה, הסרת היסוסים, קצב לפי תפקיד ומסגור
    # מוצדק. כשכבוי, חוזרים לעורך ההיוריסטי הקודם.
    director_enabled: bool = True
    # ריק => נגזר מסגנון העריכה. אחרת אחד מפרופילי הקצב.
    director_style: str = ""
    # ריק => נגזר מסגנון הקצב שהבמאי בחר
    caption_preset: str = ""

    # ---- מוזיקת רקע ----
    # המערכת אינה מספקת מוזיקה. `music_path` הוא קובץ שהמשתמש
    # בחר ושיש לו זכות להשתמש בו.
    music_enabled: bool = False
    music_path: str = ""
    music_profile: str = "balanced"     # minimal | balanced | energetic

    # ---- מאסטרינג אודיו ----
    mastering_enabled: bool = True
    mastering_target: str = "social"        # social | podcast | broadcast
    mastering_denoise: bool = True
    mastering_compress: bool = True

    # ---- קליפים ארוכים ----
    long_enabled: bool = True
    long_min_seconds: int = 120
    long_max_seconds: int = 900
    long_mode: str = "continuous"           # continuous | highlights
    long_count: int = 3

    # ---- שורטים ----
    short_enabled: bool = True
    short_min_seconds: int = 15
    short_max_seconds: int = 60
    short_count: int = 5
    # center | auto_face | split | blur_pad
    short_layout: str = "auto_face"
    short_resolution: str = "1080x1920"
    # אזור מצלמת הסטרימר שהוגדר ידנית ונשמר לשידורים הבאים (x,y,w,h ב-0..1)
    camera_region: dict[str, float] = field(default_factory=dict)

    # ---- ייצוא ----
    export_dir: str = ""                    # ריק => ברירת המחדל של האפליקציה
    video_quality: str = "high"             # low | medium | high
    long_resolution: str = "1920x1080"
    audio_normalize: bool = True
    hw_accel: str = "none"                  # none | nvenc | qsv | videotoolbox

    # ---- כתוביות ----
    subtitles_enabled: bool = True
    subtitle_language: str = "auto"         # auto | he | en
    subtitle_font: str = "DejaVu Sans"
    subtitle_size: int = 54
    subtitle_color: str = "#FFFFFF"
    subtitle_outline_color: str = "#000000"
    subtitle_position: str = "bottom"       # top | middle | bottom
    subtitle_word_level: bool = True
    subtitle_animation: str = "pop"         # none | pop | punch
    title_card_enabled: bool = False

    # ---- כללי ----
    context_pad_before: float = 2.0         # שניות הקשר לפני השיא
    context_pad_after: float = 1.5          # שניות הקשר אחרי השיא
    max_clips_total: int = 12
    concurrent_jobs: int = 1

    def clamp(self) -> "AppSettings":
        """אימות ותיקון ערכים כדי למנוע הגדרות לא חוקיות."""
        self.sensitivity = min(1.0, max(0.0, float(self.sensitivity)))
        self.visual_sample_fps = min(8.0, max(0.1, float(self.visual_sample_fps)))
        self.long_min_seconds = max(10, int(self.long_min_seconds))
        self.long_max_seconds = max(self.long_min_seconds + 5, int(self.long_max_seconds))
        self.short_min_seconds = max(3, int(self.short_min_seconds))
        self.short_max_seconds = max(self.short_min_seconds + 1, int(self.short_max_seconds))
        self.long_count = min(50, max(0, int(self.long_count)))
        self.short_count = min(50, max(0, int(self.short_count)))
        self.max_clips_total = min(100, max(1, int(self.max_clips_total)))
        self.concurrent_jobs = min(4, max(1, int(self.concurrent_jobs)))
        self.subtitle_size = min(160, max(12, int(self.subtitle_size)))
        self.context_pad_before = min(15.0, max(0.0, float(self.context_pad_before)))
        self.context_pad_after = min(15.0, max(0.0, float(self.context_pad_after)))
        if self.long_mode not in ("continuous", "highlights"):
            self.long_mode = "continuous"
        if self.short_layout not in ("center", "auto_face", "split", "blur_pad"):
            self.short_layout = "auto_face"
        if self.ai_mode == "local":          # תאימות לאחור
            self.ai_mode = "heuristic"
        if self.ai_mode not in ("heuristic", "ollama", "cloud"):
            self.ai_mode = "heuristic"
        if self.ai_provider not in ("anthropic", "openai", "ollama"):
            self.ai_provider = "anthropic"
        if self.transcript_provider not in ("faster-whisper", "none", "fixture"):
            self.transcript_provider = "faster-whisper"
        if self.subtitle_position not in ("top", "middle", "bottom"):
            self.subtitle_position = "bottom"
        if self.subtitle_animation not in ("none", "pop", "punch"):
            self.subtitle_animation = "pop"
        for attr in ("edit_style_long", "edit_style_short"):
            if getattr(self, attr) not in ("raw", "clean", "dynamic", "hype"):
                setattr(self, attr, "clean")
        # שם פרופיל לא מוכר נופל חזרה לגזירה מסגנון העריכה, ולא
        # מפיל את העבודה.
        from .services.pacing_engine import PROFILES as _PACING
        from .services.caption_engine import PRESETS as _CAPTIONS

        if self.director_style and self.director_style not in _PACING:
            self.director_style = ""
        if self.caption_preset and self.caption_preset not in _CAPTIONS:
            self.caption_preset = ""
        if self.mastering_target not in ("social", "podcast", "broadcast"):
            self.mastering_target = "social"
        from .services.music_engine import PROFILES as _MUSIC

        if self.music_profile not in _MUSIC:
            self.music_profile = "balanced"
        # מוזיקה בלי קובץ אינה מוזיקה
        if self.music_enabled and not self.music_path.strip():
            self.music_enabled = False
        self.silence_min_gap = min(2.0, max(0.0, float(self.silence_min_gap)))
        self.max_removed_ratio = min(0.7, max(0.0, float(self.max_removed_ratio)))
        if self.hw_accel not in ("none", "nvenc", "qsv", "videotoolbox"):
            self.hw_accel = "none"
        # ---- תמונות ----
        if self.image_provider not in ("openai", "placeholder"):
            self.image_provider = "openai"
        if self.image_model not in ("gpt-image-1", "dall-e-3", "dall-e-2"):
            self.image_model = "gpt-image-1"
        if self.image_quality not in ("low", "medium", "high"):
            self.image_quality = "medium"
        self.image_timeout_seconds = min(600, max(15, int(self.image_timeout_seconds)))
        self.image_retries = min(5, max(0, int(self.image_retries)))
        # ---- שידור חי ----
        self.live_segment_seconds = min(1800, max(30, int(self.live_segment_seconds)))
        self.live_max_minutes = min(1440, max(0, int(self.live_max_minutes)))
        return self

    def resolved_export_dir(self) -> Path:
        if self.export_dir.strip():
            p = Path(self.export_dir).expanduser()
            try:
                p.mkdir(parents=True, exist_ok=True)
                return p
            except OSError:
                pass
        return PATHS.exports

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppSettings":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        clean = {k: v for k, v in (data or {}).items() if k in known}
        return cls(**clean).clamp()


class SettingsStore:
    """קריאה/כתיבה של ההגדרות לדיסק, בטוח לשימוש מכמה תהליכונים."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._cache: Optional[AppSettings] = None

    def get(self) -> AppSettings:
        with self._lock:
            if self._cache is None:
                self._cache = self._load()
            # מחזירים עותק כדי שקוד קורא לא ישנה את המטמון בטעות
            return AppSettings.from_dict(self._cache.to_dict())

    def _load(self) -> AppSettings:
        if self._path.exists():
            try:
                return AppSettings.from_dict(json.loads(self._path.read_text("utf-8")))
            except (json.JSONDecodeError, OSError, TypeError):
                pass
        return AppSettings().clamp()

    def update(self, patch: dict[str, Any]) -> AppSettings:
        with self._lock:
            current = self.get().to_dict()
            current.update(patch or {})
            new = AppSettings.from_dict(current)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(new.to_dict(), ensure_ascii=False, indent=2), "utf-8")
            tmp.replace(self._path)
            self._cache = new
            return self.get()


SETTINGS = SettingsStore(PATHS.settings_path)


# --------------------------------------------------------------------------
# אחסון מאובטח של סודות (מפתחות API)
# --------------------------------------------------------------------------
class SecretStore:
    """
    מצפין סודות עם Fernet (AES-128-CBC + HMAC) באמצעות מפתח מאסטר מקומי.

    המפתח נשמר בקובץ נפרד עם הרשאות 0600 (ב-Windows ההרשאות נשלטות
    על-ידי ACL של תיקיית המשתמש). זו הגנה מפני קריאה מקרית ושיתוף של
    קובץ ההגדרות – לא מפני תוקף שכבר השיג הרשאות מלאות למחשב.
    """

    def __init__(self, secrets_path: Path, keyfile_path: Path) -> None:
        self._secrets_path = secrets_path
        self._keyfile_path = keyfile_path
        self._lock = threading.RLock()

    # -- מפתח מאסטר --
    def _master_key(self) -> bytes:
        if self._keyfile_path.exists():
            return self._keyfile_path.read_bytes().strip()
        self._keyfile_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            from cryptography.fernet import Fernet

            key = Fernet.generate_key()
        except ImportError:
            key = base64.urlsafe_b64encode(os.urandom(32))
        self._keyfile_path.write_bytes(key)
        try:
            os.chmod(self._keyfile_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass  # Windows – מסתמכים על ACL של הפרופיל
        return key

    def _fernet(self):
        from cryptography.fernet import Fernet

        return Fernet(self._master_key())

    # -- קריאה/כתיבה --
    def _read_all(self) -> dict[str, str]:
        if not self._secrets_path.exists():
            return {}
        try:
            raw = self._secrets_path.read_bytes()
            plain = self._fernet().decrypt(raw)
            return json.loads(plain.decode("utf-8"))
        except Exception:
            # קובץ פגום או מפתח שהוחלף – מתחילים מחדש במקום לקרוס
            return {}

    def _write_all(self, data: dict[str, str]) -> None:
        blob = self._fernet().encrypt(json.dumps(data).encode("utf-8"))
        self._secrets_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._secrets_path.with_suffix(".tmp")
        tmp.write_bytes(blob)
        tmp.replace(self._secrets_path)
        try:
            os.chmod(self._secrets_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    def set(self, name: str, value: str) -> None:
        with self._lock:
            data = self._read_all()
            if value:
                data[name] = value
            else:
                data.pop(name, None)
            self._write_all(data)

    def get(self, name: str) -> Optional[str]:
        """מחזיר את הסוד בצד השרת בלבד. אסור להחזיר זאת ל-API."""
        env_name = f"POLIXOR_{name.upper()}"
        if os.environ.get(env_name):
            return os.environ[env_name]
        with self._lock:
            return self._read_all().get(name)

    def has(self, name: str) -> bool:
        return bool(self.get(name))

    def mask(self, name: str) -> Optional[str]:
        """תצוגה בטוחה לממשק: 4 תווים אחרונים בלבד."""
        val = self.get(name)
        if not val:
            return None
        if len(val) <= 8:
            return "•" * len(val)
        return f"{val[:3]}{'•' * 6}{val[-4:]}"

    def delete(self, name: str) -> None:
        self.set(name, "")


SECRETS = SecretStore(PATHS.secrets_path, PATHS.keyfile_path)


# --------------------------------------------------------------------------
# איתור כלים חיצוניים
# --------------------------------------------------------------------------
def find_ffmpeg() -> Optional[str]:
    override = os.environ.get("POLIXOR_FFMPEG")
    if override and Path(override).exists():
        return override
    return shutil.which("ffmpeg")


def find_ffprobe() -> Optional[str]:
    override = os.environ.get("POLIXOR_FFPROBE")
    if override and Path(override).exists():
        return override
    return shutil.which("ffprobe")


def system_report() -> dict[str, Any]:
    """דוח מצב סביבה – מוצג במסך ההגדרות ובבדיקת התקינות."""
    ffmpeg = find_ffmpeg()
    ffprobe = find_ffprobe()

    def _mod(name: str) -> dict[str, Any]:
        try:
            import importlib

            m = importlib.import_module(name)
            return {"available": True, "version": str(getattr(m, "__version__", "") or "")}
        except Exception:
            return {"available": False, "version": ""}

    gpu = _detect_gpu()
    return {
        "app": {"name": APP_NAME, "version": APP_VERSION},
        "python": sys.version.split()[0],
        "platform": f"{platform.system()} {platform.release()}",
        "cpu_count": os.cpu_count() or 1,
        "ffmpeg": {"available": bool(ffmpeg), "path": ffmpeg or ""},
        "ffprobe": {"available": bool(ffprobe), "path": ffprobe or ""},
        "modules": {
            "faster_whisper": _mod("faster_whisper"),
            "yt_dlp": _mod("yt_dlp"),
            "cv2": _mod("cv2"),
            "numpy": _mod("numpy"),
        },
        "gpu": gpu,
        "data_dir": str(PATHS.data),
        "free_disk_bytes": PATHS.free_bytes(),
    }


def _detect_gpu() -> dict[str, Any]:
    """זיהוי GPU ל-NVENC/CUDA. לא מפיל את השרת אם nvidia-smi חסר."""
    import subprocess

    info: dict[str, Any] = {"cuda": False, "name": "", "nvenc": False}
    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            out = subprocess.run(
                [smi, "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=8,
            )
            if out.returncode == 0 and out.stdout.strip():
                info["cuda"] = True
                info["name"] = out.stdout.strip().splitlines()[0]
        except Exception:
            pass
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        try:
            out = subprocess.run([ffmpeg, "-hide_banner", "-encoders"],
                                 capture_output=True, text=True, timeout=15)
            info["nvenc"] = "h264_nvenc" in out.stdout
        except Exception:
            pass
    return info
