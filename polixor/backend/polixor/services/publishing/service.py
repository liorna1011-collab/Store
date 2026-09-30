"""
חשבונות, בקשות פרסום וביצוען.

זרימה: סרטון מוכן → בחירת חשבונות → בדיקה מוקדמת (preflight) → "פרסם עכשיו"
או "תזמן". כל יעד הוא PublishJob; פרסום לכמה חשבונות = קבוצה (group_id).

תזמון:
  * פלטפורמה שתומכת בתזמון (native_scheduling) – מעלים עכשיו עם זמן
    הפרסום, והפלטפורמה מפרסמת בזמן. Polixor לא צריך להיות פעיל.
  * אחרת – Polixor מפרסם בזמן שנקבע, ולכן **השרת חייב להיות פעיל** (VPS,
    ענן או מחשב שדלוק). פרסום שהוחמץ (השרת היה כבוי) מתבצע באיחור רק
    בתוך חלון `publish_missed_grace_minutes`; מעבר לו – נכשל עם התראה.

אמינות:
  * שגיאה זמנית – עד MAX_ATTEMPTS ניסיונות, בהמתנה הולכת וגדלה.
  * טוקן שפג – חידוש אוטומטי; אם החידוש נכשל, החשבון מסומן "צריך להתחבר
    מחדש" ונשלחת התראה.
  * העלאה שנקטעה (השרת נפל באמצעה) **לא** נשלחת שוב אוטומטית – ייתכן שהיא
    כבר פורסמה, ופרסום כפול ציבורי גרוע מפרסום שחסר. המשתמש בודק ולוחץ
    "נסה שוב".
  * כל ביצוע תופס "חכירה" (locked_until) – שני תהליכים לא יפרסמו אותו יעד.

אבטחה: טוקנים מוצפנים ולא יוצאים מהשרת; ביומן אין טוקנים; שגיאות נשמרות
כמפתח + פרמטרים (בלי תוכן תשובת השרת).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from ... import i18n
from ...config import AppSettings
from ...db import session_scope
from ...errors import PolixorError
from ...models import Clip, ClipKind, ClipStatus, OAuthState, PublishJob, SocialAccount, new_id
from .. import notifications
from . import oauth, registry, vault
from .base import PublishError, PublishRequest, TokenSet

log = logging.getLogger("polixor.publishing")

MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (60, 300, 900)
LEASE = timedelta(minutes=45)
MAX_SCHEDULE_AHEAD = timedelta(days=180)
REFRESH_MARGIN = timedelta(minutes=2)

ACTIVE = ("queued", "scheduled", "uploading", "processing")
DONE = ("published", "scheduled_on_platform", "failed", "cancelled")


def now() -> datetime:
    """UTC בלי אזור זמן – כך SQLite שומר ומחזיר."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _naive(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


def _err(key: str, **params: Any) -> dict[str, Any]:
    return {"key": key, "params": {k: str(v) for k, v in params.items()}}


def _record(err: Optional[dict[str, Any]]) -> dict[str, Any]:
    """שגיאת יעד → רשומה שההתראה יודעת לתרגם (PolixorError.to_record)."""
    if not err or not err.get("key"):
        return {}
    return {"code": "publish", "key": "", "message_key": err["key"],
            "params": dict(err.get("params") or {}), "hint_key": None,
            "message": None, "hint": None}


def _tr_err(err: Optional[dict[str, Any]]) -> str:
    if not err or not err.get("key"):
        return ""
    return i18n.tr(err["key"], **(err.get("params") or {}))


# --------------------------------------------------------------------------
# חשבונות
# --------------------------------------------------------------------------
def account_out(a: SocialAccount) -> dict[str, Any]:
    """מה שיוצא לממשק. לעולם בלי טוקנים."""
    return {"id": a.id, "platform": a.platform, "display_name": a.display_name,
            "handle": a.handle, "avatar_url": a.avatar_url, "status": a.status,
            "status_label": i18n.tr(f"publishing.account_status.{a.status}"),
            "scopes": list(a.scopes or []),
            # Instagram דרך Meta: לאיזה עמוד Facebook החשבון מקושר
            "linked_page": (a.meta or {}).get("page_name", "") if a.platform == "instagram" else "",
            "token_expires_at": _iso(a.token_expires_at),
            "connected_at": _iso(a.connected_at)}


def list_accounts() -> list[dict[str, Any]]:
    with session_scope() as s:
        return [account_out(a) for a in
                s.query(SocialAccount).order_by(SocialAccount.connected_at.asc()).all()]


def start_connect(platform: str, settings: AppSettings, *, redirect_uri: str,
                  return_to: str = "") -> str:
    prov = registry.get(platform, settings)
    if prov is None:
        raise PolixorError(message_key="publishing.error.platform_unavailable",
                           params={"platform": platform})
    if not prov.configured():
        raise PolixorError(message_key="publishing.error.not_configured",
                           params={"platform": prov.name})
    state, challenge = oauth.create_state(platform, redirect_uri=redirect_uri,
                                          return_to=return_to, with_pkce=prov.uses_pkce)
    return prov.authorize_url(state=state, redirect_uri=redirect_uri, code_challenge=challenge)


def finish_connect(platform: str, settings: AppSettings, *, state: str, code: str,
                   error: str = "") -> tuple[Optional[str], str, str]:
    """(account_id | None, return_to, error_key). לא זורק – תוצאה לדף החזרה."""
    st = oauth.consume_state(state, platform)
    if st is None:
        return None, "/publishing", "publishing.error.oauth_state"
    if error or not code:
        return None, st["return_to"], "publishing.error.oauth_denied"
    prov = registry.get(platform, settings)
    if prov is None:
        return None, st["return_to"], "publishing.error.platform_unavailable"
    try:
        tokens = prov.exchange_code(code=code, redirect_uri=st["redirect_uri"],
                                    code_verifier=st["verifier"])
        found = prov.accounts(tokens)
    except PublishError as exc:
        if exc.message_key in _CONNECT_ERRORS:
            return None, st["return_to"], exc.message_key
        log.warning("oauth exchange failed for %s: %s", platform, exc.detail or exc.message_key)
        return None, st["return_to"], "publishing.error.oauth_failed"
    except Exception as exc:                           # noqa: BLE001
        log.warning("oauth exchange failed for %s: %s", platform, type(exc).__name__)
        return None, st["return_to"], "publishing.error.oauth_failed"
    ids: dict[str, list[str]] = {}
    with session_scope() as s:
        for plat, info, toks in found:
            acc = (s.query(SocialAccount)
                   .filter(SocialAccount.platform == plat,
                           SocialAccount.external_id == info.external_id).first())
            if acc is None:
                acc = SocialAccount(id=new_id(), platform=plat, external_id=info.external_id)
                s.add(acc)
            acc.display_name, acc.handle, acc.avatar_url = info.display_name, info.handle, info.avatar_url
            acc.meta = dict(info.meta or {})
            _store_tokens(acc, toks)
            acc.status = "connected"
            acc.connected_at = acc.connected_at or now()
            ids.setdefault(plat, []).append(acc.id)
    if not ids.get(platform):
        # למשל: התחברות ל-Instagram בלי חשבון מקצועי שמקושר לעמוד
        missing = "publishing.error.meta_no_instagram" if platform == "instagram" \
            else "publishing.error.meta_no_pages" if platform == "facebook" \
            else "publishing.error.oauth_failed"
        first = next((v[0] for v in ids.values() if v), None)
        return first, st["return_to"], missing
    return ids[platform][0], st["return_to"], ""


_CONNECT_ERRORS = ("publishing.error.meta_no_pages", "publishing.error.meta_no_instagram",
                   "publishing.error.scope_missing", "publishing.error.youtube_no_channel")


def _store_tokens(acc: SocialAccount, t: TokenSet) -> None:
    acc.access_token_enc = vault.encrypt(t.access_token)
    if t.refresh_token:
        acc.refresh_token_enc = vault.encrypt(t.refresh_token)
    acc.token_expires_at = _naive(t.expires_at)
    acc.refresh_expires_at = _naive(t.refresh_expires_at) or acc.refresh_expires_at
    acc.scopes = list(t.scopes or acc.scopes or [])
    acc.updated_at = now()


def disconnect(account_id: str, settings: AppSettings) -> bool:
    """מבטל את ההרשאה אצל הפלטפורמה (כשאפשר) ומוחק את הטוקנים."""
    with session_scope() as s:
        acc = s.get(SocialAccount, account_id)
        if acc is None:
            return False
        prov = registry.get(acc.platform, settings)
        token = vault.decrypt(acc.refresh_token_enc) or vault.decrypt(acc.access_token_enc)
        # חיבור משותף (Meta): ביטול ההרשאה מנתק את כל העמודים והחשבונות – רק
        # כשזה החשבון האחרון שנשען עליו
        if prov is not None and prov.credential_group:
            siblings = [a for a in s.query(SocialAccount)
                        .filter(SocialAccount.id != account_id).all()
                        if registry.credential_group(a.platform) == prov.credential_group
                        and vault.decrypt(a.refresh_token_enc) == vault.decrypt(acc.refresh_token_enc)]
            if siblings:
                token = ""
        if prov is not None and token:
            try:
                prov.revoke(token)
            except Exception as exc:                   # noqa: BLE001
                log.info("revoke failed for %s (tokens deleted anyway): %s",
                         acc.platform, type(exc).__name__)
        # פרסומים מתוזמנים ב-Polixor לחשבון הזה – מבוטלים
        s.query(PublishJob).filter(PublishJob.account_id == account_id,
                                   PublishJob.status.in_(("queued", "scheduled"))) \
            .update({PublishJob.status: "cancelled",
                     PublishJob.error: _err("publishing.error.account_removed")},
                    synchronize_session=False)
        s.delete(acc)
    return True


def _mark_reconnect(account_id: str) -> None:
    with session_scope() as s:
        acc = s.get(SocialAccount, account_id)
        if acc is None or acc.status == "reconnect_required":
            return
        acc.status = "reconnect_required"
        platform, label = acc.platform, acc.display_name or acc.handle
    notifications.notify("account_reconnect",
                         params={"platform": _platform_name(platform), "account": label},
                         link="/publishing?tab=accounts", account_id=account_id,
                         group_key=f"account:{account_id}:reconnect")


def tokens_for(account_id: str, settings: AppSettings) -> TokenSet:
    """הטוקנים של החשבון, מחודשים אם פגו. PublishError("auth") כשאי אפשר."""
    with session_scope() as s:
        acc = s.get(SocialAccount, account_id)
        if acc is None:
            raise PublishError("permanent", "publishing.error.account_removed")
        if acc.status != "connected":
            raise PublishError("auth", "publishing.error.reconnect")
        access = vault.decrypt(acc.access_token_enc)
        refresh = vault.decrypt(acc.refresh_token_enc)
        exp = acc.token_expires_at
        platform = acc.platform
    if access and (exp is None or exp - REFRESH_MARGIN > now()):
        return TokenSet(access_token=access, refresh_token=refresh, expires_at=exp)
    prov = registry.get(platform, settings)
    if prov is None or not refresh:
        _mark_reconnect(account_id)
        raise PublishError("auth", "publishing.error.reconnect")
    try:
        fresh = prov.refresh(refresh)
    except PublishError as exc:
        if exc.kind == "auth":
            _mark_reconnect(account_id)
        raise
    with session_scope() as s:
        acc = s.get(SocialAccount, account_id)
        if acc is not None:
            _store_tokens(acc, fresh)
    return fresh


def _platform_name(pid: str) -> str:
    return registry.PLANNED.get(pid, (pid.capitalize(), 0))[0] if pid != "sandbox" else "Sandbox"


# --------------------------------------------------------------------------
# בדיקה מוקדמת ויצירה
# --------------------------------------------------------------------------
def _media(clip: Clip) -> dict[str, Any]:
    vertical = bool(clip.height and clip.width and clip.height > clip.width)
    fmt = "short" if (clip.kind == ClipKind.SHORT or vertical) else "long"
    return {"path": Path(clip.file_path) if clip.file_path else None, "format": fmt,
            "duration": float(clip.duration or 0.0), "width": int(clip.width or 0),
            "height": int(clip.height or 0), "size": int(clip.file_size or 0)}


def _issue(key: str, **params: Any) -> dict[str, Any]:
    return {"key": key, "params": {k: str(v) for k, v in params.items()},
            "text": i18n.tr(f"publishing.issue.{key}", **params)}


def _warning(key: str, **params: Any) -> dict[str, Any]:
    return {"key": key, "params": {k: str(v) for k, v in params.items()},
            "text": i18n.tr(f"publishing.warning.{key}", **params)}


def preflight(clip_id: str, targets: list[dict[str, Any]], settings: AppSettings, *,
              mode: str = "now", schedule_at: Optional[datetime] = None) -> dict[str, Any]:
    """בעיות לכל יעד (ולכל הבקשה). לא משנה כלום."""
    general: list[dict[str, Any]] = []
    out: list[dict[str, Any]] = []
    with session_scope() as s:
        clip = s.get(Clip, clip_id)
        if clip is None:
            return {"ok": False, "issues": [_issue("clip_missing")], "targets": []}
        media = _media(clip)
        if clip.status not in (ClipStatus.READY, ClipStatus.NEEDS_REVIEW):
            general.append(_issue("clip_not_ready"))
        if media["path"] is None or not media["path"].exists():
            general.append(_issue("file_missing"))
        if mode == "schedule":
            when = _naive(schedule_at)
            if when is None:
                general.append(_issue("schedule_missing"))
            elif when <= now() + timedelta(minutes=1):
                general.append(_issue("schedule_past"))
            elif when > now() + MAX_SCHEDULE_AHEAD:
                general.append(_issue("schedule_too_far", days=MAX_SCHEDULE_AHEAD.days))
        if not targets:
            general.append(_issue("no_targets"))
        seen: set[str] = set()
        for t in targets:
            aid = str(t.get("account_id") or "")
            issues: list[dict[str, Any]] = []
            acc = s.get(SocialAccount, aid)
            if acc is None:
                out.append({"account_id": aid, "ok": False, "issues": [_issue("account_missing")]})
                continue
            if aid in seen:
                issues.append(_issue("duplicate_target"))
            seen.add(aid)
            prov = registry.get(acc.platform, settings)
            if prov is None:
                issues.append(_issue("platform_unavailable", platform=_platform_name(acc.platform)))
                out.append({"account_id": aid, "ok": False, "issues": issues})
                continue
            if acc.status != "connected":
                issues.append(_issue("reconnect", platform=prov.name))
            cap = prov.capabilities()
            title = str(t.get("title") or "").strip()
            desc = str(t.get("description") or "")
            tags = [str(x) for x in (t.get("tags") or [])]
            privacy = str(t.get("privacy") or cap.privacy[0])
            if media["format"] not in cap.formats:
                issues.append(_issue("format_unsupported", platform=prov.name,
                                     format=i18n.tr(f"publishing.format.{media['format']}")))
            limit = cap.max_seconds.get(media["format"])
            if limit and media["duration"] > limit + 0.5:
                issues.append(_issue("too_long", seconds=int(limit)))
            if media["size"] > cap.max_bytes:
                issues.append(_issue("too_big", mb=cap.max_bytes // (1024 * 1024)))
            if not title:
                issues.append(_issue("title_missing"))
            if len(title) > cap.title_max:
                issues.append(_issue("title_too_long", max=cap.title_max))
            if len(desc) > cap.description_max:
                issues.append(_issue("description_too_long", max=cap.description_max))
            if len(tags) > cap.tags_max:
                issues.append(_issue("too_many_tags", max=cap.tags_max))
            if privacy not in cap.privacy:
                issues.append(_issue("privacy_unsupported", privacy=privacy))
            warnings: list[dict[str, Any]] = []
            if media["path"] is not None:
                req = PublishRequest(media_path=media["path"], format=media["format"], title=title,
                                     description=desc, tags=tags, privacy=privacy,
                                     duration=media["duration"], width=media["width"],
                                     height=media["height"], options=dict(t.get("options") or {}),
                                     publish_at=_naive(schedule_at) if mode == "schedule"
                                     and cap.native_scheduling else None,
                                     account={"external_id": acc.external_id, **(acc.meta or {})})
                if not issues:
                    for x in prov.validate(req):
                        issues.append(_issue(x["key"], **(x.get("params") or {})))
                for x in prov.warnings(req):
                    warnings.append(_warning(x["key"], **(x.get("params") or {})))
            schedule_by = ""
            if mode == "schedule":
                schedule_by = "platform" if cap.native_scheduling else "polixor"
            out.append({"account_id": aid, "platform": acc.platform, "ok": not issues,
                        "issues": issues, "warnings": warnings, "schedule_by": schedule_by,
                        "needs_server_online": schedule_by == "polixor",
                        "capabilities": cap.to_dict()})
    ok = not general and all(t["ok"] for t in out) and bool(out)
    return {"ok": ok, "issues": general, "targets": out, "format": media["format"]}


def create(clip_id: str, targets: list[dict[str, Any]], settings: AppSettings, *,
           mode: str = "now", schedule_at: Optional[datetime] = None) -> dict[str, Any]:
    """יוצר את בקשות הפרסום – הכול או כלום. מחזיר {group_id, jobs}."""
    if mode not in ("now", "schedule"):
        raise PolixorError(message_key="publishing.error.bad_mode")
    check = preflight(clip_id, targets, settings, mode=mode, schedule_at=schedule_at)
    if not check["ok"]:
        raise PolixorError(message_key="publishing.error.preflight")
    group = new_id()
    when = _naive(schedule_at) if mode == "schedule" else None
    ids: list[str] = []
    by_account = {t["account_id"]: t for t in check["targets"]}
    with session_scope() as s:
        for t in targets:
            aid = str(t["account_id"])
            acc = s.get(SocialAccount, aid)
            info = by_account[aid]
            status = "scheduled" if info["schedule_by"] == "polixor" else "queued"
            job = PublishJob(
                id=new_id(), group_id=group, clip_id=clip_id, account_id=aid,
                platform=acc.platform, format=check["format"],
                title=str(t.get("title") or "").strip(), description=str(t.get("description") or ""),
                tags=[str(x) for x in (t.get("tags") or [])],
                privacy=str(t.get("privacy") or info["capabilities"]["privacy"][0]),
                options={**dict(t.get("options") or {}),
                         "account_label": acc.display_name or acc.handle},
                mode=mode, schedule_by=info["schedule_by"], schedule_at=when, status=status,
                history=[{"at": _iso(now()), "status": status}])
            s.add(job)
            ids.append(job.id)
    for jid in ids:
        _notify_scheduled_if_polixor(jid)
    from . import scheduler

    scheduler.wake()
    return {"group_id": group, "jobs": ids}


def _notify_scheduled_if_polixor(job_id: str) -> None:
    with session_scope() as s:
        j = s.get(PublishJob, job_id)
        if j is None or j.status != "scheduled":
            return
        params = _job_params(j)
    notifications.notify("publish_scheduled", params=params, link="/publishing",
                         group_key=f"publish:{job_id}:scheduled")


def _job_params(j: PublishJob) -> dict[str, Any]:
    when = j.schedule_at.replace(tzinfo=timezone.utc).strftime("%Y-%m-%d %H:%M UTC") \
        if j.schedule_at else ""
    return {"title": j.title, "platform": _platform_name(j.platform),
            "account": (j.options or {}).get("account_label", ""), "when": when}


# --------------------------------------------------------------------------
# פעולות משתמש
# --------------------------------------------------------------------------
def cancel(job_id: str) -> None:
    with session_scope() as s:
        j = s.get(PublishJob, job_id)
        if j is None:
            raise PolixorError(message_key="publishing.error.job_missing")
        if j.status == "scheduled_on_platform":
            raise PolixorError(message_key="publishing.error.cancel_on_platform",
                               params={"platform": _platform_name(j.platform)})
        if j.status not in ("queued", "scheduled", "failed"):
            raise PolixorError(message_key="publishing.error.cannot_cancel")
        _set(j, "cancelled")


def retry(job_id: str) -> None:
    with session_scope() as s:
        j = s.get(PublishJob, job_id)
        if j is None:
            raise PolixorError(message_key="publishing.error.job_missing")
        if j.status != "failed":
            raise PolixorError(message_key="publishing.error.cannot_retry")
        # פרסום מתוזמן שזמנו עבר – מתפרסם עכשיו
        j.attempts, j.error, j.next_attempt_at, j.locked_until = 0, {}, None, None
        if j.schedule_by == "polixor" and j.schedule_at and j.schedule_at > now():
            _set(j, "scheduled")
        else:
            j.schedule_at = j.schedule_at if j.schedule_by == "platform" else None
            _set(j, "queued")
    from . import scheduler

    scheduler.wake()


def _set(j: PublishJob, status: str, **extra: Any) -> None:
    j.status = status
    j.updated_at = now()
    j.history = list(j.history or []) + [{"at": _iso(now()), "status": status, **extra}][-30:]


# --------------------------------------------------------------------------
# ביצוע
# --------------------------------------------------------------------------
def _claim(job_id: str) -> bool:
    """חכירה אטומית: רק מבצע אחד לכל יעד."""
    with session_scope() as s:
        t = now()
        n = (s.query(PublishJob)
             .filter(PublishJob.id == job_id,
                     (PublishJob.locked_until.is_(None)) | (PublishJob.locked_until < t))
             .update({PublishJob.locked_until: t + LEASE}, synchronize_session=False))
    return n == 1


def _release(job_id: str) -> None:
    with session_scope() as s:
        j = s.get(PublishJob, job_id)
        if j is not None:
            j.locked_until = None


def due(settings: AppSettings) -> list[str]:
    t = now()
    with session_scope() as s:
        q = s.query(PublishJob.id).filter(
            (PublishJob.status.in_(("queued", "processing")) &
             ((PublishJob.next_attempt_at.is_(None)) | (PublishJob.next_attempt_at <= t))) |
            ((PublishJob.status == "scheduled") & (PublishJob.schedule_at <= t)))
        return [r[0] for r in q.order_by(PublishJob.created_at.asc()).limit(20).all()]


def recover_interrupted() -> int:
    """
    העלאה שנקטעה (חכירה פגה בזמן uploading/processing): מסמנים ככשל שדורש
    בדיקה – לא מפרסמים שוב אוטומטית (ראו תיעוד המודול).
    """
    t = now()
    count = 0
    with session_scope() as s:
        rows = s.query(PublishJob).filter(
            PublishJob.status == "uploading",
            (PublishJob.locked_until.is_(None)) | (PublishJob.locked_until < t)).all()
        for j in rows:
            if (j.options or {}).get("checkpoint"):
                # הספק שומר נקודות ביניים (container / video_id / upload session) –
                # ממשיכים מהן, והספק בודק מה כבר פורסם לפני כל צעד. אין כפילות.
                j.locked_until = None
                j.next_attempt_at = None
                _set(j, "queued", resumed=True)
                continue
            j.error = _err("publishing.error.interrupted", platform=_platform_name(j.platform))
            j.locked_until = None
            _set(j, "failed")
            count += 1
            params = _job_params(j)
            jid = j.id
            notifications.notify("publish_failed", params=params,
                                 error=_record(j.error), link="/publishing",
                                 group_key=f"publish:{jid}:failed")
    return count


def _missed(j: PublishJob, settings: AppSettings) -> bool:
    if j.status != "scheduled" or j.schedule_at is None:
        return False
    return now() - j.schedule_at > timedelta(minutes=settings.publish_missed_grace_minutes)


def run(job_id: str, settings: AppSettings) -> str:
    """מבצע יעד אחד. מחזיר את הסטטוס החדש."""
    if not _claim(job_id):
        return "locked"
    try:
        return _run(job_id, settings)
    finally:
        _release(job_id)


def _run(job_id: str, settings: AppSettings) -> str:
    with session_scope() as s:
        j = s.get(PublishJob, job_id)
        if j is None or j.status not in ("queued", "scheduled", "processing"):
            return j.status if j else "missing"
        polling = j.status == "processing"
        if _missed(j, settings):
            j.error = _err("publishing.error.missed",
                           minutes=settings.publish_missed_grace_minutes)
            _set(j, "failed")
            params = _job_params(j)
            fail_err = _record(j.error)
            fail_kind = "schedule_failed"
        else:
            fail_kind = ""
            clip = s.get(Clip, j.clip_id)
            media = _media(clip) if clip is not None else None
            extra_opts: dict[str, Any] = {}
            if clip is not None and clip.thumbnail_path:
                extra_opts["thumbnail_path"] = clip.thumbnail_path
            if clip is not None and clip.job is not None:
                lang = (clip.job.content_language or "").strip()
                if lang and lang != "auto":
                    extra_opts["language"] = lang
            prov = registry.get(j.platform, settings)
            late = j.status == "scheduled"
            if not polling:
                _set(j, "uploading")
                j.attempts = int(j.attempts or 0) + 1
            acc = s.get(SocialAccount, j.account_id)
            account = {"external_id": acc.external_id, **(acc.meta or {})} if acc else {}
            checkpoint = dict((j.options or {}).get("checkpoint") or {})
            snap = {"account_id": j.account_id, "title": j.title, "description": j.description,
                    "tags": list(j.tags or []), "privacy": j.privacy, "format": j.format,
                    "options": {**extra_opts, **dict(j.options or {})},
                    "publish_at": j.schedule_at if j.schedule_by == "platform" else None,
                    "attempts": j.attempts, "late": late, "platform": j.platform,
                    "account": account, "checkpoint": checkpoint, "polling": polling,
                    "polls": int((j.options or {}).get("polls") or 0)}
    if fail_kind:
        notifications.notify(fail_kind, params=params, error=fail_err, link="/publishing",
                             group_key=f"publish:{job_id}:failed")
        return "failed"

    try:
        if media is None or media["path"] is None or not media["path"].exists():
            raise PublishError("permanent", "publishing.error.file_missing")
        if prov is None:
            raise PublishError("permanent", "publishing.error.platform_unavailable",
                               params={"platform": _platform_name(snap["platform"])})
        tokens = tokens_for(snap["account_id"], settings)
        opts = {k: v for k, v in snap["options"].items() if k not in ("checkpoint", "polls")}
        req = PublishRequest(media_path=media["path"], format=snap["format"], title=snap["title"],
                             description=snap["description"], tags=snap["tags"],
                             privacy=snap["privacy"], duration=media["duration"],
                             width=media["width"], height=media["height"],
                             publish_at=snap["publish_at"], options=opts,
                             account=snap["account"], checkpoint=snap["checkpoint"],
                             save_checkpoint=lambda cp: _save_checkpoint(job_id, cp))
        try:
            result = prov.publish(tokens, req)
        except PublishError as exc:
            if exc.kind != "auth":
                raise
            # טוקן שנדחה – חידוש אחד ואז ניסיון נוסף
            with session_scope() as s:
                acc = s.get(SocialAccount, snap["account_id"])
                if acc is not None:
                    acc.token_expires_at = now() - timedelta(seconds=1)
            tokens = tokens_for(snap["account_id"], settings)
            result = prov.publish(tokens, req)
    except PublishError as exc:
        return _failed(job_id, exc, snap["attempts"])
    except Exception as exc:                           # noqa: BLE001
        log.warning("publish %s crashed: %s", job_id, type(exc).__name__, exc_info=True)
        return _failed(job_id, PublishError("retry", "publishing.error.temporary"),
                       snap["attempts"])

    if result.state == "processing":
        return _processing(job_id, result, snap["polls"])
    with session_scope() as s:
        j = s.get(PublishJob, job_id)
        j.remote_id, j.remote_url, j.error = result.remote_id, result.url or j.remote_url, {}
        j.next_attempt_at = None
        if result.state == "published":
            j.published_at = now()
        _set(j, result.state)
        params = _job_params(j)
    if result.state == "scheduled_on_platform":
        notifications.notify("publish_scheduled", params=params, link="/publishing",
                             group_key=f"publish:{job_id}:scheduled")
    else:
        notifications.notify("publish_complete", params=params, link="/publishing",
                             group_key=f"publish:{job_id}:done")
    return result.state


POLL_SECONDS = 45
MAX_POLLS = 40                     # ~30 דקות של עיבוד בפלטפורמה


def _save_checkpoint(job_id: str, cp: dict[str, Any]) -> None:
    """נשמר מיד (בטרנזקציה נפרדת), כדי שנפילה באמצע לא תאבד את מה שכבר נעשה."""
    with session_scope() as s:
        j = s.get(PublishJob, job_id)
        if j is not None:
            j.options = {**dict(j.options or {}), "checkpoint": dict(cp)}


def _processing(job_id: str, result: Any, polls: int) -> str:
    """הפלטפורמה עוד מעבדת: בודקים שוב בעוד POLL_SECONDS (בלי לספור ניסיון)."""
    with session_scope() as s:
        j = s.get(PublishJob, job_id)
        if polls + 1 >= MAX_POLLS:
            j.error = _err("publishing.error.processing_timeout",
                           platform=_platform_name(j.platform))
            _set(j, "failed")
            params, rec = _job_params(j), _record(j.error)
            failed = True
        else:
            j.remote_id = result.remote_id or j.remote_id
            j.options = {**dict(j.options or {}), "polls": polls + 1}
            j.next_attempt_at = now() + timedelta(seconds=POLL_SECONDS)
            if j.status != "processing":
                _set(j, "processing")
            failed = False
    if failed:
        notifications.notify("publish_failed", params=params, error=rec, link="/publishing",
                             group_key=f"publish:{job_id}:failed")
        return "failed"
    return "processing"


def _failed(job_id: str, exc: PublishError, attempts: int) -> str:
    log.info("publish %s: %s (%s)", job_id, exc.message_key, exc.kind)
    with session_scope() as s:
        j = s.get(PublishJob, job_id)
        j.error = {"key": exc.message_key, "params": {k: str(v) for k, v in exc.params.items()}}
        if exc.kind == "retry" and attempts < MAX_ATTEMPTS:
            j.next_attempt_at = now() + timedelta(seconds=BACKOFF_SECONDS[min(attempts, 2) - 1])
            _set(j, "queued", retry_in=BACKOFF_SECONDS[min(attempts, 2) - 1])
            return "queued"
        _set(j, "failed")
        params = _job_params(j)
        rec = _record(j.error)
        kind = "schedule_failed" if j.mode == "schedule" and j.schedule_by == "polixor" \
            else "publish_failed"
    notifications.notify(kind, params=params, link="/publishing",
                         error=rec, group_key=f"publish:{job_id}:failed")
    return "failed"


def tick(settings: AppSettings) -> int:
    """סבב של המתזמן: שחזור, ואז כל מה שהגיע זמנו."""
    recover_interrupted()
    n = 0
    for jid in due(settings):
        run(jid, settings)
        n += 1
    return n


# --------------------------------------------------------------------------
# היסטוריה
# --------------------------------------------------------------------------
STATE_GROUP = {"failed": "needs_attention", "queued": "in_progress", "uploading": "in_progress",
               "processing": "in_progress", "scheduled": "scheduled",
               "scheduled_on_platform": "scheduled", "published": "published",
               "cancelled": "cancelled"}


def job_out(j: PublishJob, clip: Optional[Clip] = None) -> dict[str, Any]:
    return {
        "id": j.id, "group_id": j.group_id, "clip_id": j.clip_id, "account_id": j.account_id,
        "platform": j.platform, "platform_name": _platform_name(j.platform),
        "account_label": (j.options or {}).get("account_label", ""),
        "format": j.format, "title": j.title, "privacy": j.privacy, "mode": j.mode,
        "schedule_by": j.schedule_by, "schedule_at": _iso(j.schedule_at),
        "status": j.status, "status_label": i18n.tr(f"publishing.status.{j.status}"),
        "group": STATE_GROUP.get(j.status, "in_progress"),
        "attempts": j.attempts, "next_attempt_at": _iso(j.next_attempt_at),
        "remote_url": j.remote_url, "error": _tr_err(j.error),
        "can_cancel": j.status in ("queued", "scheduled", "failed"),
        "can_retry": j.status == "failed",
        "needs_server_online": j.status == "scheduled" and j.schedule_by == "polixor",
        "created_at": _iso(j.created_at), "published_at": _iso(j.published_at),
        "clip_title": clip.title if clip is not None else "",
        "thumbnail_url": f"/api/clips/{j.clip_id}/thumbnail" if clip is not None and clip.thumbnail_path else "",
    }


def history(limit: int = 100, clip_id: str = "") -> dict[str, Any]:
    with session_scope() as s:
        q = s.query(PublishJob)
        if clip_id:
            q = q.filter(PublishJob.clip_id == clip_id)
        rows = q.order_by(PublishJob.created_at.desc()).limit(max(1, min(500, limit))).all()
        clips = {c.id: c for c in s.query(Clip).filter(Clip.id.in_({r.clip_id for r in rows})).all()}
        items = [job_out(r, clips.get(r.clip_id)) for r in rows]
    order = ("needs_attention", "in_progress", "scheduled", "published", "cancelled")
    groups = [{"state": g, "title": i18n.tr(f"publishing.group.{g}"),
               "items": [i for i in items if i["group"] == g]} for g in order]
    return {"items": items, "groups": [g for g in groups if g["items"]]}


def cleanup_oauth_states() -> None:
    with session_scope() as s:
        s.query(OAuthState).filter(OAuthState.created_at < now() - timedelta(hours=1)).delete()
