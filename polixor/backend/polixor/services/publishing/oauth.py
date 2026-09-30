"""
OAuth: `state` חד-פעמי (מונע CSRF והתחברות לחשבון של מישהו אחר) ו-PKCE
(S256) כשהספק תומך. state נשמר ב-DB, תקף 10 דקות ונמחק אחרי שימוש.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import timedelta
from typing import Callable, Optional

from ...db import session_scope
from ...models import OAuthState, utcnow
from . import vault

STATE_TTL = timedelta(minutes=10)


def pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).decode("ascii").rstrip("=")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    return verifier, challenge


def create_state(platform: str, *, redirect_uri: str, return_to: str = "",
                 with_pkce: bool = True,
                 challenge_fn: Optional[Callable[[str], str]] = None) -> tuple[str, str]:
    """
    מחזיר (state, code_challenge). `challenge_fn` – לספק שמחשב את ה-challenge
    אחרת מהתקן (TikTok: SHA-256 ב-hex במקום base64url).
    """
    state = secrets.token_urlsafe(32)
    verifier, challenge = pkce_pair() if with_pkce else ("", "")
    if with_pkce and challenge_fn is not None:
        challenge = challenge_fn(verifier)
    with session_scope() as s:
        cutoff = (utcnow() - STATE_TTL * 6).replace(tzinfo=None)
        s.query(OAuthState).filter(OAuthState.created_at < cutoff).delete()
        s.add(OAuthState(state=state, platform=platform, verifier_enc=vault.encrypt(verifier),
                         redirect_uri=redirect_uri, return_to=_safe_return(return_to)))
    return state, challenge


def consume_state(state: str, platform: str) -> Optional[dict[str, str]]:
    """בודק ומוחק. None כשה-state לא קיים, שייך לפלטפורמה אחרת, פג או כבר נוצל."""
    if not state or len(state) > 96:
        return None
    with session_scope() as s:
        row = s.get(OAuthState, state)
        if row is None or row.used_at is not None or row.platform != platform:
            return None
        created = row.created_at.replace(tzinfo=None)
        if utcnow().replace(tzinfo=None) - created > STATE_TTL:
            s.delete(row)
            return None
        out = {"verifier": vault.decrypt(row.verifier_enc), "redirect_uri": row.redirect_uri,
               "return_to": row.return_to}
        s.delete(row)
        return out


def _safe_return(path: str) -> str:
    """רק נתיב פנימי (מונע open redirect)."""
    if path and path.startswith("/") and not path.startswith("//") and "\\" not in path:
        return path[:300]
    return "/publishing"
