"""
שגיאות ממוקדות עם הודעות מתורגמות לממשק.

כל שגיאה נושאת `code` יציב (לשימוש ה-UI), והודעה + רמז קריאים למשתמש.
ההודעות עצמן נמצאות בקטלוג `locales/errors.py` (מפתחות
`errors.<code>.message` ו-`errors.<code>.hint`) ומתורגמות לשפת הבקשה
או לשפת הפרויקט. הטקסט העברי שבמחלקה הוא ברירת מחדל בלבד, לשגיאה
שעוד אין לה רשומה בקטלוג.

שלוש דרכים להעביר הודעה:
  * כלום                       – ההודעה לפי הקוד מהקטלוג.
  * `message_key` + `params`   – הודעה ספציפית מהקטלוג, עם פרמטרים.
  * `message` (מחרוזת)         – טקסט מפורש. נשמר כמו שהוא, בלי תרגום.
"""

from __future__ import annotations

from typing import Any, Optional

from . import i18n


def _jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class PolixorError(Exception):
    code = "unknown_error"
    # מפתח בקטלוג כשהוא שונה מהקוד (כמה מחלקות חולקות קוד אחד)
    key: Optional[str] = None
    _default_message = "אירעה שגיאה לא צפויה."
    _default_hint = ""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        # תת-מחלקות מגדירות `message = "..."` ו-`hint = "..."` כתכונות
        # מחלקה. מעבירים אותן לברירות המחדל, כדי שהמאפיינים המתורגמים
        # של מחלקת הבסיס לא יוסתרו.
        super().__init_subclass__(**kwargs)
        for attr in ("message", "hint"):
            value = cls.__dict__.get(attr)
            if isinstance(value, str):
                setattr(cls, f"_default_{attr}", value)
                delattr(cls, attr)

    def __init__(self, message: Optional[str] = None, *, hint: Optional[str] = None,
                 detail: Optional[str] = None, message_key: Optional[str] = None,
                 params: Optional[dict[str, Any]] = None,
                 hint_key: Optional[str] = None,
                 code_hint_key: Optional[str] = None) -> None:
        self._message: Optional[str] = message or None
        self._hint: Optional[str] = hint
        self.detail = detail or ""
        self.message_key = message_key
        self.params: dict[str, Any] = {k: _jsonable(v) for k, v in (params or {}).items()}
        self.hint_key = hint_key or code_hint_key
        super().__init__(self.message)

    # ---- טקסט מתורגם ----
    @property
    def catalog_base(self) -> str:
        return f"errors.{self.key or self.code}"

    def localized(self, lang: Optional[str] = None) -> dict[str, str]:
        """ההודעה והרמז בשפה נתונה (ברירת מחדל: השפה הפעילה)."""
        base = self.catalog_base
        if self._message is not None:
            message = self._message
        elif self.message_key and i18n.has(self.message_key):
            message = i18n.tr(self.message_key, lang, **self.params)
        elif i18n.has(f"{base}.message"):
            message = i18n.tr(f"{base}.message", lang, **self.params)
        else:
            message = type(self)._default_message

        if self._hint is not None:
            hint = self._hint
        elif self.hint_key and i18n.has(self.hint_key):
            hint = i18n.tr(self.hint_key, lang, **self.params)
        elif i18n.has(f"{base}.hint"):
            hint = i18n.tr(f"{base}.hint", lang, **self.params)
        else:
            hint = type(self)._default_hint
        return {"code": self.code, "message": message, "hint": hint}

    @property
    def message(self) -> str:
        return self.localized()["message"]

    @message.setter
    def message(self, value: str) -> None:
        self._message = value or None

    @property
    def hint(self) -> str:
        return self.localized()["hint"]

    @hint.setter
    def hint(self, value: str) -> None:
        self._hint = value

    def __str__(self) -> str:
        return self.message

    def to_dict(self, lang: Optional[str] = None) -> dict[str, str]:
        out = self.localized(lang)
        out["detail"] = self.detail
        return out

    # ---- שמירה ב-DB ותרגום מאוחר ----
    def to_record(self) -> dict[str, Any]:
        """
        ייצוג שנשמר עם המשימה, כדי שאפשר יהיה להציג את השגיאה בכל
        שפה גם אחרי שהמשימה הסתיימה (ולא רק בשפה שבה היא נכשלה).
        """
        return {
            "code": self.code,
            "key": self.key,
            "message_key": self.message_key,
            "params": dict(self.params),
            "hint_key": self.hint_key,
            "message": self._message,
            "hint": self._hint,
            "default_message": type(self)._default_message,
            "default_hint": type(self)._default_hint,
        }

    @staticmethod
    def localize_record(record: Optional[dict[str, Any]],
                        lang: Optional[str] = None) -> Optional[dict[str, str]]:
        """מתרגם שגיאה שנשמרה עם `to_record`."""
        if not record or not record.get("code"):
            return None
        err = PolixorError(
            record.get("message") or None,
            hint=record.get("hint"),
            message_key=record.get("message_key"),
            params=record.get("params") or {},
            hint_key=record.get("hint_key"),
        )
        err.code = str(record.get("code"))
        err.key = record.get("key")
        err._default_message = record.get("default_message") or PolixorError._default_message
        err._default_hint = record.get("default_hint") or ""
        return err.localized(lang)


class InvalidUrlError(PolixorError):
    code = "invalid_url"
    message = "הקישור שהוזן אינו תקין או אינו נתמך."
    hint = "נסה להדביק קישור מלא ל-YouTube, Twitch, Kick או Google Drive."


class UnsupportedPlatformError(PolixorError):
    code = "unsupported_platform"
    message = "הפלטפורמה הזו אינה נתמכת כרגע."


class PrivateOrUnavailableError(PolixorError):
    code = "private_or_unavailable"
    message = "הסרטון פרטי, הוסר, או אינו זמין להורדה."
    hint = "ודא שהסרטון ציבורי. Polixor לא עוקף הגבלות גישה או הרשאות."


class DrmProtectedError(PolixorError):
    code = "drm_protected"
    message = "התוכן מוגן ב-DRM ולכן לא ניתן לעבד אותו."
    hint = "Polixor לא עוקף מנגנוני הגנה. יש להשתמש במקור שיש לך זכות להוריד."


class GeoOrAgeRestrictedError(PolixorError):
    code = "restricted"
    message = "הגישה לסרטון מוגבלת (אזור גאוגרפי, גיל או התחברות)."
    hint = "ניתן להוריד את הקובץ ידנית ולהעלות אותו לתוכנה."


class LiveNotStartedError(PolixorError):
    code = "live_not_started"
    message = "השידור החי טרם התחיל או שאינו זמין כרגע."


class NoAudioError(PolixorError):
    code = "no_audio"
    message = "לא נמצא ערוץ אודיו בסרטון."
    hint = "ניתן עדיין לנתח ויזואלית: הפעל 'ניתוח חזותי בלבד' בהגדרות."


class FFmpegMissingError(PolixorError):
    code = "ffmpeg_missing"
    message = "FFmpeg לא נמצא במערכת."
    hint = "התקן FFmpeg והוסף אותו ל-PATH, או הרץ scripts/install_windows.ps1."


class FFmpegFailedError(PolixorError):
    code = "ffmpeg_failed"
    message = "פעולת FFmpeg נכשלה."


class DiskSpaceError(PolixorError):
    code = "disk_space"
    message = "אין מספיק מקום פנוי בדיסק."
    hint = "פנה מקום או בחר תיקיית ייצוא בכונן אחר."


class TranscriptionError(PolixorError):
    code = "transcription_failed"
    message = "התמלול נכשל."
    hint = "בדוק שהמודל הורד בהצלחה ושיש מספיק זיכרון פנוי."


class ModelUnavailableError(PolixorError):
    code = "model_unavailable"
    message = "מודל התמלול אינו זמין."
    hint = "נדרשת גישה לאינטרנט להורדת המודל בפעם הראשונה."


class AiProviderError(PolixorError):
    code = "ai_provider_failed"
    message = "הפנייה למודל ה-AI בענן נכשלה."
    hint = "בדוק את מפתח ה-API בהגדרות. ניתן להמשיך במצב AI מקומי."


class NoMomentsFoundError(PolixorError):
    code = "no_moments"
    message = "לא נמצאו רגעים מתאימים ליצירת קליפים."
    hint = "נסה להעלות את רמת הרגישות בהגדרות או להאריך את טווח אורך הקליפ."


class JobCancelledError(PolixorError):
    code = "cancelled"
    message = "המשימה בוטלה."


class LongformDeferred(Exception):
    """The Shorts are done; the long-form continues as its own lower-priority task (worker processes)."""


class JobNotFoundError(PolixorError):
    code = "job_not_found"
    message = "המשימה לא נמצאה."


class ClipNotFoundError(PolixorError):
    code = "clip_not_found"
    message = "הקליפ לא נמצא."


class NetworkError(PolixorError):
    code = "network_error"
    message = "לא ניתן להגיע לשרת של הפלטפורמה."
    hint = ("בדוק את החיבור לאינטרנט. אם אתה מאחורי חומת אש או פרוקסי, "
            "ייתכן שהגישה לפלטפורמה חסומה מהמחשב הזה.")


class SourceTooShortError(PolixorError):
    code = "source_too_short"
    message = "הסרטון קצר מדי לניתוח."
    hint = "נדרש סרטון באורך של לפחות מספר שניות."


# --------------------------------------------------------------------------
# ייבוא מקישור
# --------------------------------------------------------------------------
class NotAVideoError(PolixorError):
    code = "not_a_video"
    message = "הקישור אינו מצביע על סרטון או שידור."
    hint = "פתח את הסרטון עצמו בדפדפן והעתק את הקישור שלו."


class PlaylistNotSupportedError(PolixorError):
    code = "playlist_not_supported"
    message = "זהו קישור לרשימת השמעה ולא לסרטון בודד."
    hint = "פתח סרטון אחד מהרשימה והעתק את הקישור שלו."


class BlockedAddressError(PolixorError):
    code = "blocked_address"
    message = "הקישור מפנה לכתובת פנימית או מקומית, ולכן נחסם."
    hint = "ניתן לייבא רק קישורים לאתרים ציבוריים. קובץ מהמחשב – השתמש בהעלאה."


class LiveRequiresCaptureError(PolixorError):
    code = "live_requires_capture"
    message = "זהו שידור חי. יש לבחור כמה דקות להקליט ממנו."
    hint = "שידור חי אינו מורד בלי הגבלה: ההקלטה מתחילה מעכשיו ונמשכת לפי הזמן שבחרת."


class SourceTooLongError(PolixorError):
    code = "source_too_long"
    message = "הסרטון ארוך מהמגבלה ({hours} שעות)."
    hint = "בחר טווח זמן (התחלה וסיום) לייבוא, או העלה את המגבלה בהגדרות."


class QuotaExceededError(PolixorError):
    code = "quota_exceeded"
    message = "נותרו לך {remaining} דקות. הסרטון הזה הוא {video} דקות."
    hint = "אפשר לבחור קטע קצר יותר מהסרטון, לחכות לתקופת החיוב הבאה או לשדרג את החבילה."


class JobStalledError(PolixorError):
    code = "stalled"
    message = "העיבוד נעצר בשלב: {stage}."
    hint = "לחצו \"המשך\" – העיבוד ימשיך מהנקודה האחרונה שנשמרה, בלי לחייב שוב."


class InvalidSectionError(PolixorError):
    code = "invalid_section"
    message = "טווח הזמן שנבחר אינו תקין."
    hint = "זמן הסיום צריך להיות אחרי זמן ההתחלה ובתוך אורך הסרטון."


class SignInRequiredError(PolixorError):
    code = "sign_in_required"
    message = "הפלטפורמה דורשת התחברות כדי לאשר שאינך רובוט."
    hint = ("Polixor אינו עוקף בדיקות כאלה. הורד את הסרטון בדרך מורשית "
            "והעלה את הקובץ, או נסה שוב מאוחר יותר.")


class MembersOnlyError(PolixorError):
    code = "members_only"
    message = "הסרטון זמין לחברי ערוץ בלבד."
    hint = "Polixor אינו עוקף הגבלות גישה. אם יש לך זכות לתוכן, העלה את הקובץ ידנית."


class RateLimitedError(PolixorError):
    code = "rate_limited"
    message = "הפלטפורמה הגבילה זמנית את מספר הבקשות."
    hint = "המתן כמה דקות ונסה שוב."


class ExtractorFailedError(PolixorError):
    code = "extractor_failed"
    message = "לא ניתן היה לקרוא את פרטי הסרטון מהפלטפורמה."
    hint = ("ייתכן שהאתר השתנה. עדכן את yt-dlp (pip install -U yt-dlp) "
            "או הורד את הסרטון ידנית והעלה אותו.")


class DownloadFailedError(PolixorError):
    code = "download_failed"
    message = "ההורדה נכשלה."
    hint = "בדוק את הקישור ואת החיבור לרשת."


# --------------------------------------------------------------------------
# פרויקטים
# --------------------------------------------------------------------------
class ProjectNotFoundError(PolixorError):
    code = "project_not_found"
    message = "הפרויקט לא נמצא."


class ProjectBusyError(PolixorError):
    code = "project_busy"
    message = "הפרויקט בעיבוד כרגע."
    hint = "המתן לסיום העיבוד או בטל אותו לפני שינוי ההגדרות."


class AnalysisIncompleteError(PolixorError):
    code = "analysis_incomplete"
    message = "ניתוח התוכן עדיין לא הושלם."
    hint = "המתן לסיום הניתוח ואז נסה שוב."


class JobInterruptedError(PolixorError):
    code = "interrupted"
    message = "השרת נסגר באמצע העיבוד."
    hint = "ניתן להמשיך מהשלב האחרון שהושלם."


class UploadMissingError(PolixorError):
    code = "upload_missing"
    message = "הקובץ שהועלה לא נמצא. נסה להעלות שוב."


class MissingInputError(PolixorError):
    code = "missing_input"
    message = "יש להזין קישור או להעלות קובץ."


def classify_download_error(raw: str) -> PolixorError:
    """
    ממפה הודעת שגיאה של yt-dlp לשגיאה מובנית.

    הסדר חשוב: בודקים מהספציפי לכללי. לדוגמה, ההודעה
    "Sign in to confirm your age" מכילה גם "sign in" וגם "age restricted",
    והאבחנה הנכונה היא הגבלת גיל – זו המידע השימושי למשתמש.
    """
    low = (raw or "").lower().replace("’", "'")

    # --- משאבים ותשתית ---
    if "no space left" in low or "errno 28" in low:
        return DiskSpaceError(detail=raw)

    # --- הגנות תוכן ---
    if "drm" in low or ("protected" in low and "content" in low):
        return DrmProtectedError(detail=raw)

    # --- בדיקת רובוט: לא עוקפים, מסבירים ---
    if "not a bot" in low or "confirm you're not a robot" in low \
            or "captcha" in low:
        return SignInRequiredError(detail=raw)

    # --- הגבלות גיל/אזור (לפני "פרטי", כי ההודעות חופפות) ---
    if ("age" in low and ("restrict" in low or "confirm your age" in low)) \
            or "age-restricted" in low:
        return GeoOrAgeRestrictedError(detail=raw)
    # "in your country" מכסה את כל הניסוחים של חסימה גאוגרפית, כולל
    # "The uploader has not made this video available in your country"
    if "geo" in low or "in your country" in low or "in your region" in low:
        return GeoOrAgeRestrictedError(detail=raw)

    # --- תוכן לחברים בלבד ---
    if "members-only" in low or "members only" in low \
            or "join this channel" in low or "subscriber-only" in low \
            or "subscribers only" in low:
        return MembersOnlyError(detail=raw)

    # --- שידור שטרם התחיל ---
    if "this live event will begin" in low or "premieres in" in low \
            or "live event will begin" in low:
        return LiveNotStartedError(detail=raw)

    # --- הגבלת קצב ---
    if "429" in low or "too many requests" in low or "rate limit" in low \
            or "rate-limit" in low:
        return RateLimitedError(detail=raw)

    # --- פרטי / הוסר / לא זמין ---
    if "private video" in low or "sign in" in low or "login required" in low:
        return PrivateOrUnavailableError(detail=raw)
    if "video unavailable" in low or "not available" in low \
            or "has been removed" in low or "account associated" in low:
        return PrivateOrUnavailableError(detail=raw)
    if "404" in low or "not found" in low:
        return PrivateOrUnavailableError(detail=raw)

    # --- פלטפורמה לא נתמכת ---
    if "unsupported url" in low or "no video formats" in low \
            or "no suitable formats" in low:
        return UnsupportedPlatformError(detail=raw)

    # --- כשל פענוח של האתר ---
    if "unable to extract" in low or "extractorerror" in low \
            or "please report this issue" in low:
        return ExtractorFailedError(detail=raw)

    # --- תקלת רשת / חסימה ברמת החיבור ---
    # מופיע אחרון כי מילים כמו "timed out" יכולות להופיע גם בשגיאות
    # ספציפיות יותר שכבר טופלו למעלה.
    if any(k in low for k in (
            "connection refused", "connection reset", "connection aborted",
            "timed out", "timeout", "network is unreachable",
            "temporary failure in name resolution", "name or service not known",
            "failed to resolve", "ssl", "certificate verify",
            "proxy", "tunnel connection failed", "407", "403 forbidden",
            "getaddrinfo", "urlopen error", "unable to download webpage")):
        return NetworkError(detail=raw)

    return DownloadFailedError(detail=raw)
