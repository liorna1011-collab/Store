"""
פרסום לרשתות: פלטפורמות, חשבונות (OAuth), בדיקה מוקדמת, תור והיסטוריה.

אבטחה:
  * כל בקשה שמשנה משהו חייבת את הכותרת `X-Polixor-Request: 1` (הממשק שולח
    אותה תמיד). אתר זר לא יכול לשלוח כותרת מותאמת בלי preflight של CORS,
    ו-CORS מאשר רק את מקורות Polixor – כך נחסם CSRF גם כשיש עוגיית כניסה.
  * החזרה מ-OAuth מאומתת ב-state חד-פעמי (ובקוד PKCE כשהספק תומך).
  * טוקנים וסודות לא יוצאים מהשרת; פרטי אפליקציה מוצגים רק כ"מוגדר" + 4 תווים.
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any, Optional
from urllib.parse import quote, urlencode, urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from .. import i18n
from ..config import SECRETS, SETTINGS
from ..errors import PolixorError
from ..services.publishing import registry, scheduler, service
from ..services.publishing.sandbox import SandboxProvider, recorded_posts

router = APIRouter(prefix="/api/publish", tags=["publishing"])


def _guard(request: Request) -> None:
    if request.headers.get("x-polixor-request") != "1":
        raise HTTPException(status_code=403, detail={
            "code": "csrf", "message": i18n.tr("publishing.error.csrf"), "hint": "", "detail": ""})


def _http(exc: PolixorError, status: int = 400) -> HTTPException:
    d = exc.to_dict()
    d["code"] = (exc.message_key or "error").split(".")[-1]
    return HTTPException(status_code=status, detail=d)


def _base_url(request: Request) -> str:
    base = SETTINGS.get().public_base_url
    return base or str(request.base_url).rstrip("/")


def _redirect_uri(request: Request, platform: str) -> str:
    return f"{_base_url(request)}/api/publish/oauth/{platform}/callback"


# --------------------------------------------------------------------------
@router.get("/platforms")
def platforms() -> dict[str, Any]:
    return {"platforms": registry.platforms(SETTINGS.get()), "scheduler": scheduler.status()}


@router.get("/accounts")
def accounts() -> dict[str, Any]:
    return {"accounts": service.list_accounts()}


class ConnectIn(BaseModel):
    return_to: str = Field(default="/publishing", max_length=300)


@router.post("/accounts/{platform}/connect")
def connect(platform: str, body: ConnectIn, request: Request) -> dict[str, str]:
    _guard(request)
    try:
        url = service.start_connect(platform, SETTINGS.get(),
                                    redirect_uri=_redirect_uri(request, platform),
                                    return_to=body.return_to)
    except PolixorError as exc:
        raise _http(exc) from exc
    return {"auth_url": url}


@router.get("/oauth/{platform}/callback")
def oauth_callback(platform: str, state: str = "", code: str = "", error: str = ""):
    aid, return_to, err = service.finish_connect(platform, SETTINGS.get(), state=state,
                                                 code=code, error=error)
    sep = "&" if "?" in return_to else "?"
    if aid and err:
        # חיבור חלקי (למשל עמודי Facebook נמצאו, אבל אין Instagram מקושר)
        return RedirectResponse(f"{return_to}{sep}connected={quote(platform)}"
                                f"&publish_error={quote(err.split('.')[-1])}", status_code=303)
    if aid:
        return RedirectResponse(f"{return_to}{sep}connected={quote(platform)}", status_code=303)
    return RedirectResponse(f"{return_to}{sep}publish_error={quote(err.split('.')[-1])}",
                            status_code=303)


@router.post("/accounts/{account_id}/disconnect")
def disconnect(account_id: str, request: Request) -> dict[str, bool]:
    _guard(request)
    if not service.disconnect(account_id, SETTINGS.get()):
        raise HTTPException(status_code=404, detail={
            "code": "account_missing", "message": i18n.tr("publishing.error.account_removed"),
            "hint": "", "detail": ""})
    return {"disconnected": True}


# ---- פרטי אפליקציות מפתחים ----
@router.get("/config")
def config(request: Request) -> dict[str, Any]:
    out = []
    for gid, g in registry.CREDENTIALS.items():
        fields = [{"name": f, "configured": SECRETS.has(f), "masked": SECRETS.mask(f)}
                  for f in g["fields"]]
        out.append({"id": gid, "label": g["label"], "platforms": list(g["platforms"]),
                    "fields": fields, "configured": all(x["configured"] for x in fields),
                    "redirect_uris": [_redirect_uri(request, p) for p in g["platforms"]]})
    return {"groups": out, "public_base_url": SETTINGS.get().public_base_url,
            "base_url": _base_url(request)}


class CredentialsIn(BaseModel):
    values: dict[str, str] = Field(default_factory=dict)


@router.post("/config/{group}")
def save_config(group: str, body: CredentialsIn, request: Request) -> dict[str, bool]:
    _guard(request)
    g = registry.CREDENTIALS.get(group)
    if g is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": group,
                                                     "hint": "", "detail": ""})
    for name, value in body.values.items():
        if name not in g["fields"]:
            continue
        value = (value or "").strip()
        if len(value) > 500 or any(c in value for c in "\r\n\t "):
            raise HTTPException(status_code=400, detail={
                "code": "bad_credential", "message": i18n.tr("publishing.error.bad_credential"),
                "hint": "", "detail": ""})
        SECRETS.set(name, value)
    return {"saved": True}


# ---- בקשות פרסום ----
class TargetIn(BaseModel):
    account_id: str = Field(max_length=32)
    title: str = Field(default="", max_length=2200)
    description: str = Field(default="", max_length=10000)
    tags: list[str] = Field(default_factory=list, max_length=100)
    privacy: str = Field(default="", max_length=16)
    options: dict[str, Any] = Field(default_factory=dict)


class PublishIn(BaseModel):
    clip_id: str = Field(max_length=32)
    targets: list[TargetIn] = Field(default_factory=list, max_length=20)
    mode: str = Field(default="now", pattern="^(now|schedule)$")
    schedule_at: Optional[datetime] = None


def _targets(body: PublishIn) -> list[dict[str, Any]]:
    return [t.model_dump() for t in body.targets]


def _schedule_at(body: PublishIn) -> Optional[datetime]:
    if body.schedule_at is not None and body.schedule_at.tzinfo is None:
        raise HTTPException(status_code=400, detail={
            "code": "timezone_required", "message": i18n.tr("publishing.error.timezone_required"),
            "hint": "", "detail": ""})
    return body.schedule_at


@router.post("/preflight")
def preflight(body: PublishIn, request: Request) -> dict[str, Any]:
    _guard(request)
    return service.preflight(body.clip_id, _targets(body), SETTINGS.get(),
                             mode=body.mode, schedule_at=_schedule_at(body))


@router.post("/jobs", status_code=201)
def create_jobs(body: PublishIn, request: Request):
    _guard(request)
    settings = SETTINGS.get()
    when = _schedule_at(body)
    check = service.preflight(body.clip_id, _targets(body), settings, mode=body.mode,
                              schedule_at=when)
    if not check["ok"]:
        return JSONResponse(status_code=422, content={"detail": {
            "code": "preflight", "message": i18n.tr("publishing.error.preflight"),
            "hint": "", "detail": "", "preflight": check}})
    try:
        return service.create(body.clip_id, _targets(body), settings, mode=body.mode,
                              schedule_at=when)
    except PolixorError as exc:
        raise _http(exc, 422) from exc


@router.get("/jobs")
def jobs(limit: int = 100, clip_id: str = "") -> dict[str, Any]:
    return service.history(limit=limit, clip_id=clip_id)


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: str, request: Request) -> dict[str, bool]:
    _guard(request)
    try:
        service.cancel(job_id)
    except PolixorError as exc:
        raise _http(exc, 409) from exc
    return {"cancelled": True}


@router.post("/jobs/{job_id}/retry")
def retry(job_id: str, request: Request) -> dict[str, bool]:
    _guard(request)
    try:
        service.retry(job_id)
    except PolixorError as exc:
        raise _http(exc, 409) from exc
    return {"queued": True}


# ---- ארגז חול ----
def _sandbox_on() -> None:
    if not SETTINGS.get().publish_sandbox:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "",
                                                     "hint": "", "detail": ""})


def _own_callback(request: Request, redirect_uri: str) -> bool:
    """מונע הפניה פתוחה: רק לכתובת החזרה של ארגז החול עצמו."""
    want = urlsplit(_redirect_uri(request, "sandbox"))
    got = urlsplit(redirect_uri)
    return (got.scheme, got.netloc, got.path) == (want.scheme, want.netloc, want.path) \
        and not got.query and not got.fragment


@router.get("/sandbox/authorize", response_class=HTMLResponse)
def sandbox_authorize(request: Request, state: str = "", redirect_uri: str = "",
                      code_challenge: str = "") -> HTMLResponse:
    _sandbox_on()
    if not _own_callback(request, redirect_uri):
        raise HTTPException(status_code=400, detail={"code": "bad_redirect", "message": "",
                                                     "hint": "", "detail": ""})
    q = urlencode({"state": state, "redirect_uri": redirect_uri, "code_challenge": code_challenge})
    t = lambda k: html.escape(i18n.tr(f"publishing.sandbox.{k}"))    # noqa: E731
    lang = i18n.get_lang()
    page = f"""<!doctype html><html lang="{lang}" dir="{i18n.direction(lang)}"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{t('title')}</title><style>
body{{font-family:system-ui,sans-serif;background:#0f1115;color:#e7e9ee;display:grid;place-items:center;min-height:100vh;margin:0;padding:16px}}
main{{max-width:420px;background:#171a21;border:1px solid #2a2f3a;border-radius:14px;padding:24px}}
a{{display:inline-block;margin-top:14px;margin-inline-end:10px;padding:10px 16px;border-radius:10px;text-decoration:none}}
.ok{{background:#4f6bff;color:#fff}} .no{{border:1px solid #3a4150;color:#e7e9ee}}
</style></head><body><main><h1>{t('title')}</h1><p>{t('body')}</p>
<a class="ok" id="approve" href="/api/publish/sandbox/approve?{html.escape(q)}">{t('approve')}</a>
<a class="no" id="deny" href="{html.escape(redirect_uri)}?error=access_denied&amp;state={html.escape(quote(state))}">{t('deny')}</a>
</main></body></html>"""
    return HTMLResponse(page)


@router.get("/sandbox/approve")
def sandbox_approve(request: Request, state: str = "", redirect_uri: str = "",
                    code_challenge: str = ""):
    _sandbox_on()
    if not _own_callback(request, redirect_uri):
        raise HTTPException(status_code=400, detail={"code": "bad_redirect", "message": "",
                                                     "hint": "", "detail": ""})
    code = SandboxProvider.grant(code_challenge)
    return RedirectResponse(f"{redirect_uri}?{urlencode({'code': code, 'state': state})}",
                            status_code=303)


@router.get("/sandbox/posts")
def sandbox_posts() -> dict[str, Any]:
    _sandbox_on()
    return {"posts": recorded_posts()}
