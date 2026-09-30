"""
הפלטפורמות. ספק אמיתי נכנס לכאן בשלב שלו (YouTube – שלב 7, Meta – שלב 8,
TikTok – שלב 9) אחרי בדיקת התיעוד הרשמי העדכני. עד אז הן מוצגות בממשק
כ"בקרוב", כדי שהמבנה (חשבונות, יעדים, היסטוריה) יהיה אחיד כבר עכשיו.
"""

from __future__ import annotations

from typing import Any, Optional

from ...config import AppSettings
from .base import Provider
from .sandbox import SandboxProvider

# פלטפורמות מתוכננות: id → שם, שלב
PLANNED: dict[str, tuple[str, int]] = {
    "youtube": ("YouTube", 7),
    "instagram": ("Instagram", 8),
    "facebook": ("Facebook", 8),
    "tiktok": ("TikTok", 9),
}

_EXTRA: dict[str, Provider] = {}       # ספקים שנרשמו (שלבים הבאים / בדיקות)


def register(provider: Provider) -> None:
    _EXTRA[provider.id] = provider


def unregister(pid: str) -> None:
    _EXTRA.pop(pid, None)


def _builtin(pid: str, settings: AppSettings) -> Optional[Provider]:
    """ספקים אמיתיים שכבר מומשו (שלב 7 ואילך)."""
    if pid == "youtube":
        from .youtube import YouTubeProvider

        return YouTubeProvider(audited=bool(getattr(settings, "youtube_audited", False)))
    if pid == "instagram":
        from .meta import InstagramProvider

        return InstagramProvider()
    if pid == "facebook":
        from .meta import FacebookProvider

        return FacebookProvider()
    if pid == "tiktok":
        from .tiktok import TikTokProvider

        return TikTokProvider(audited=bool(getattr(settings, "tiktok_audited", False)))
    return None


def get(pid: str, settings: AppSettings) -> Optional[Provider]:
    if pid in _EXTRA:
        return _EXTRA[pid]
    builtin = _builtin(pid, settings)
    if builtin is not None:
        return builtin
    if pid == "sandbox" and settings.publish_sandbox:
        return SandboxProvider(native_scheduling=settings.publish_sandbox_native_scheduling)
    return None


def platforms(settings: AppSettings) -> list[dict[str, Any]]:
    out = []
    for pid, (name, stage) in PLANNED.items():
        p = _EXTRA.get(pid) or _builtin(pid, settings)
        out.append({"id": pid, "name": p.name if p else name, "available": p is not None,
                    "configured": bool(p and p.configured()), "sandbox": False,
                    "planned_stage": None if p else stage,
                    "capabilities": p.capabilities().to_dict() if p else None})
    for pid, p in _EXTRA.items():
        if pid not in PLANNED:
            out.append({"id": pid, "name": p.name, "available": True,
                        "configured": p.configured(), "sandbox": pid == "sandbox",
                        "planned_stage": None, "capabilities": p.capabilities().to_dict()})
    if settings.publish_sandbox and "sandbox" not in _EXTRA:
        sb = SandboxProvider(native_scheduling=settings.publish_sandbox_native_scheduling)
        out.append({"id": "sandbox", "name": sb.name, "available": True, "configured": True,
                    "sandbox": True, "planned_stage": None,
                    "capabilities": sb.capabilities().to_dict()})
    return out


# פרטי אפליקציית המפתחים לכל פלטפורמה (נשמרים מוצפנים בשרת, ב-SECRETS).
# Instagram ו-Facebook חולקות אפליקציית Meta אחת.
CREDENTIALS: dict[str, dict[str, Any]] = {
    "youtube": {"label": "Google Cloud (YouTube Data API v3)",
                "fields": ("youtube_client_id", "youtube_client_secret"),
                "platforms": ("youtube",)},
    "meta": {"label": "Meta for Developers (Instagram + Facebook)",
             "fields": ("meta_app_id", "meta_app_secret"),
             "platforms": ("instagram", "facebook")},
    "tiktok": {"label": "TikTok for Developers (Content Posting API)",
               "fields": ("tiktok_client_key", "tiktok_client_secret"),
               "platforms": ("tiktok",)},
}


def credential_group(platform: str) -> Optional[str]:
    for gid, g in CREDENTIALS.items():
        if platform in g["platforms"]:
            return gid
    return None
