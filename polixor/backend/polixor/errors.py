"""
שגיאות ממוקדות עם הודעות בעברית לממשק.

כל שגיאה נושאת `code` יציב (לשימוש ה-UI) ו-`message` קריא למשתמש.
"""

from __future__ import annotations


class PolixorError(Exception):
    code = "unknown_error"
    message = "אירעה שגיאה לא צפויה."
    hint = ""

    def __init__(self, message: str | None = None, *, hint: str | None = None,
                 detail: str | None = None) -> None:
        self.message = message or self.__class__.message
        self.hint = hint if hint is not None else self.__class__.hint
        self.detail = detail or ""
        super().__init__(self.message)

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "detail": self.detail,
        }


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


def classify_download_error(raw: str) -> PolixorError:
    """
    ממפה הודעת שגיאה של yt-dlp לשגיאה מובנית בעברית.

    הסדר חשוב: בודקים מהספציפי לכללי. לדוגמה, ההודעה
    "Sign in to confirm your age" מכילה גם "sign in" וגם "age restricted",
    והאבחנה הנכונה היא הגבלת גיל – זו המידע השימושי למשתמש.
    """
    low = (raw or "").lower()

    # --- משאבים ותשתית ---
    if "no space left" in low or "errno 28" in low:
        return DiskSpaceError(detail=raw)

    # --- הגנות תוכן ---
    if "drm" in low or ("protected" in low and "content" in low):
        return DrmProtectedError(detail=raw)

    # --- הגבלות גיל/אזור (לפני "פרטי", כי ההודעות חופפות) ---
    if ("age" in low and ("restrict" in low or "confirm your age" in low)) \
            or "age-restricted" in low:
        return GeoOrAgeRestrictedError(detail=raw)
    # "in your country" מכסה את כל הניסוחים של חסימה גאוגרפית, כולל
    # "The uploader has not made this video available in your country"
    if "geo" in low or "in your country" in low or "in your region" in low:
        return GeoOrAgeRestrictedError(detail=raw)

    # --- שידור שטרם התחיל ---
    if "this live event will begin" in low or "premieres in" in low \
            or "live event will begin" in low:
        return LiveNotStartedError(detail=raw)

    # --- פרטי / הוסר / לא זמין ---
    if "private video" in low or "members-only" in low or "members only" in low \
            or "sign in" in low or "login required" in low:
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

    return PolixorError("ההורדה נכשלה.", hint="בדוק את הקישור ואת החיבור לרשת.", detail=raw)
