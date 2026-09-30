"""ממשק ספק פרסום. כל פלטפורמה (YouTube, Instagram, Facebook, TikTok) מממשת אותו."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional


@dataclass
class Capabilities:
    formats: tuple[str, ...] = ("short", "long")        # short (אנכי) | long
    max_seconds: dict[str, float] = field(default_factory=lambda: {"short": 180.0, "long": 43200.0})
    max_bytes: int = 4 * 1024 ** 3
    title_max: int = 100
    description_max: int = 5000
    tags_max: int = 30
    privacy: tuple[str, ...] = ("public", "unlisted", "private")
    # הפלטפורמה עצמה מפרסמת בזמן שנקבע (ואז Polixor לא צריך להיות פעיל)
    native_scheduling: bool = False
    thumbnail: bool = False
    # פלטפורמה שדורשת בדיקת אפליקציה לפני פרסום ציבורי
    public_requires_audit: bool = False
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in self.__dict__.items()}


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str = ""
    expires_at: Optional[datetime] = None
    refresh_expires_at: Optional[datetime] = None
    scopes: tuple[str, ...] = ()


@dataclass
class AccountInfo:
    external_id: str
    display_name: str
    handle: str = ""
    avatar_url: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class PublishRequest:
    media_path: Path
    format: str
    title: str
    description: str
    tags: list[str]
    privacy: str
    duration: float
    width: int
    height: int
    publish_at: Optional[datetime] = None          # רק כשהתזמון בפלטפורמה
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class PublishResult:
    remote_id: str
    url: str = ""
    # published | processing | scheduled_on_platform
    state: str = "published"


class PublishError(Exception):
    """שגיאה מהספק. `kind` קובע מה עושים: retry | auth | permanent."""

    def __init__(self, kind: str, message_key: str, *, params: Optional[dict[str, Any]] = None,
                 detail: str = "") -> None:
        super().__init__(message_key)
        assert kind in ("retry", "auth", "permanent")
        self.kind = kind
        self.message_key = message_key
        self.params = params or {}
        # פירוט טכני ליומן – לעולם בלי טוקנים (הספק אחראי לנקות)
        self.detail = detail[:500]


ProgressFn = Optional[Callable[[float], None]]


class Provider:
    """בסיס לספק. ספק אמיתי דורש client id/secret של אפליקציית מפתחים."""

    id = "base"
    name = "Base"
    # כותרת מדינה ל-PKCE: ספקים שלא תומכים (למשל TikTok web) מגדירים False
    uses_pkce = True
    scopes: tuple[str, ...] = ()

    def capabilities(self) -> Capabilities:
        return Capabilities()

    def configured(self) -> bool:
        """יש פרטי אפליקציית מפתחים (בשרת)."""
        return False

    def authorize_url(self, *, state: str, redirect_uri: str, code_challenge: str) -> str:
        raise NotImplementedError

    def exchange_code(self, *, code: str, redirect_uri: str, code_verifier: str) -> TokenSet:
        raise NotImplementedError

    def refresh(self, refresh_token: str) -> TokenSet:
        raise PublishError("auth", "publishing.error.reconnect")

    def revoke(self, token: str) -> None:
        return None

    def account_info(self, tokens: TokenSet) -> AccountInfo:
        raise NotImplementedError

    def validate(self, req: PublishRequest) -> list[dict[str, Any]]:
        """בעיות שמונעות פרסום, כ-[{key, params}] (מתורגם בממשק)."""
        return []

    def publish(self, tokens: TokenSet, req: PublishRequest, *,
                on_progress: ProgressFn = None) -> PublishResult:
        raise NotImplementedError

    def status(self, tokens: TokenSet, remote_id: str) -> PublishResult:
        return PublishResult(remote_id=remote_id)
