"""עזרי מערכת קבצים: מקום בדיסק, שמות בטוחים, ניקוי קבצי עבודה."""

from __future__ import annotations

import re
import shutil
import unicodedata
from pathlib import Path
from typing import Iterable

from ..errors import DiskSpaceError

# תווים אסורים בשמות קבצים ב-Windows
_WIN_FORBIDDEN = r'<>:"/\\|?*'
_WIN_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_filename(name: str, *, max_length: int = 80, fallback: str = "clip") -> str:
    """
    שם קובץ בטוח לכל מערכות ההפעלה, כולל Windows.
    שומר על עברית ואנגלית, מסיר תווי בקרה ותווים אסורים.
    """
    if not name:
        return fallback
    name = unicodedata.normalize("NFC", str(name))
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C")
    name = "".join("-" if ch in _WIN_FORBIDDEN else ch for ch in name)
    name = re.sub(r"\s+", " ", name).strip(" .-_")
    if not name:
        return fallback
    if len(name) > max_length:
        name = name[:max_length].rstrip(" .-_")
    if name.upper().split(".")[0] in _WIN_RESERVED:
        name = f"_{name}"
    return name or fallback


def unique_path(path: Path) -> Path:
    """מוסיף סיומת מספרית אם הקובץ כבר קיים."""
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    for i in range(2, 10000):
        cand = parent / f"{stem} ({i}){suffix}"
        if not cand.exists():
            return cand
    raise DiskSpaceError("לא ניתן ליצור שם קובץ ייחודי.")


def free_bytes(path: Path) -> int:
    try:
        target = path if path.exists() else path.parent
        return shutil.disk_usage(target).free
    except OSError:
        return 0


def require_free_space(path: Path, needed_bytes: int) -> None:
    """זורק DiskSpaceError אם אין מספיק מקום, עם מספרים אמיתיים בהודעה."""
    free = free_bytes(path)
    if free and free < needed_bytes:
        raise DiskSpaceError(
            f"אין מספיק מקום בדיסק: נדרשים כ-{human_size(needed_bytes)}, "
            f"פנויים {human_size(free)}.",
        )


def human_size(n: int | float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024.0:
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024.0
    return f"{n:.1f} PB"


def rmtree_quiet(path: Path) -> None:
    try:
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass


def dir_size(path: Path) -> int:
    total = 0
    try:
        for p in path.rglob("*"):
            if p.is_file():
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def is_within(child: Path, parents: Iterable[Path]) -> bool:
    """
    מוודא שנתיב נמצא בתוך אחת מהתיקיות המותרות.
    משמש להגשת קבצים ב-API כדי למנוע path traversal.
    """
    try:
        c = child.resolve()
    except OSError:
        return False
    for parent in parents:
        try:
            c.relative_to(Path(parent).resolve())
            return True
        except (ValueError, OSError):
            continue
    return False
