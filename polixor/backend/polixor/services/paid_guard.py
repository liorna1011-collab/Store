"""
One switch for every paid AI call (Anthropic, OpenAI chat / images / cloud transcription).

POLIXOR_PAID_AI=off (or 0/false/no) refuses them all before any request leaves the machine;
answers already in a project's cache still work (they cost nothing). A refused call is
reported like an unreachable provider – "not evaluated", never a content rejection.
Used for infrastructure / UI testing so no credits are spent.
"""

from __future__ import annotations

import os


class PaidAIDisabled(RuntimeError):
    pass


def paid_ai_allowed() -> bool:
    return os.environ.get("POLIXOR_PAID_AI", "on").strip().lower() not in ("off", "0", "false", "no")


def check(service: str) -> None:
    if not paid_ai_allowed():
        raise PaidAIDisabled(f"paid AI is switched off (POLIXOR_PAID_AI=off): {service} not called")
