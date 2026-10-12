"""
One switch for every paid AI call (Anthropic, OpenAI chat / images / cloud transcription).

POLIXOR_PAID_AI=off (or 0/false/no) refuses them all before any request leaves the machine;
answers already in a project's cache still work (they cost nothing). A refused call is
reported like an unreachable provider – "not evaluated", never a content rejection.
Used for infrastructure / UI testing so no credits are spent.

POLIXOR_PAID_AI_CAP_USD=2 is a hard spend cap for a validation run, per process: the editor's model
calls are priced as they return (costs.model_price) and no call starts once the cap is reached.
Calls already in flight finish, so a run can pass the cap by at most its parallel calls. Paid
services that do not report their cost (images, cloud transcription, the legacy text helper) are
refused outright while a cap is set – a cap that cannot be enforced is not offered.
"""

from __future__ import annotations

import os
import threading
from typing import Optional

_lock = threading.Lock()
_spent = 0.0


class PaidAIDisabled(RuntimeError):
    pass


def paid_ai_allowed() -> bool:
    return os.environ.get("POLIXOR_PAID_AI", "on").strip().lower() not in ("off", "0", "false", "no")


def cap_usd() -> Optional[float]:
    raw = os.environ.get("POLIXOR_PAID_AI_CAP_USD", "").strip()
    try:
        return max(0.0, float(raw)) if raw else None
    except ValueError:
        return 0.0                      # a cap that cannot be read allows nothing, never everything


def spent_usd() -> float:
    return _spent


def record(usd: float) -> None:
    global _spent
    with _lock:
        _spent += max(0.0, float(usd or 0.0))


def check(service: str, *, metered: bool = False) -> None:
    if not paid_ai_allowed():
        raise PaidAIDisabled(f"paid AI is switched off (POLIXOR_PAID_AI=off): {service} not called")
    cap = cap_usd()
    if cap is None:
        return
    if not metered:
        raise PaidAIDisabled(f"a spend cap is set (POLIXOR_PAID_AI_CAP_USD) and {service} does not report "
                             f"its cost: not called")
    if _spent >= cap:
        raise PaidAIDisabled(f"paid AI spend cap reached (${_spent:.2f} of ${cap:.2f}): {service} not called")
