"""הצפנת טוקנים בעמודות ה-DB, עם אותו מפתח מאסטר של SECRETS (Fernet)."""

from __future__ import annotations

from ...config import SECRETS


def encrypt(value: str) -> str:
    if not value:
        return ""
    return SECRETS._fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(blob: str) -> str:
    if not blob:
        return ""
    try:
        return SECRETS._fernet().decrypt(blob.encode("ascii")).decode("utf-8")
    except Exception:                                  # noqa: BLE001
        # מפתח שהוחלף או ערך פגום – כמו טוקן שפג: צריך להתחבר מחדש
        return ""
