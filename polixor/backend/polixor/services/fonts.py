"""
גופנים מותקנים: רשימה, תמיכה בעברית/לטינית, ואיתור קובץ הגופן.

המקור הוא fontconfig (`fc-list` / `fc-match`) – אותו מנגנון ש-libass
משתמש בו ברינדור, ולכן מה שמוצג כאן הוא מה שבאמת יופיע בווידאו.
ב-Windows, כשאין fontconfig, נקראת רשימת הגופנים מהרישום.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("polixor.fonts")

_CACHE: dict[str, Any] = {}
_LOCK = threading.RLock()

# משקל CSS ← משקל fontconfig
_FC_WEIGHT = {400: 80, 500: 100, 600: 180, 700: 200, 800: 205, 900: 210}


def _fc(args: list[str], timeout: float = 20.0) -> str:
    exe = shutil.which(args[0])
    if not exe:
        return ""
    try:
        res = subprocess.run([exe, *args[1:]], capture_output=True, text=True,
                             timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return res.stdout if res.returncode == 0 else ""


def _families_from(output: str) -> set[str]:
    out: set[str] = set()
    for line in output.splitlines():
        for fam in line.split(","):
            fam = fam.strip().replace("\\-", "-")
            if fam:
                out.add(fam)
    return out


def installed_families() -> set[str]:
    """כל משפחות הגופנים המותקנות."""
    with _LOCK:
        if "all" in _CACHE:
            return _CACHE["all"]
        fams = _families_from(_fc(["fc-list", "--format", "%{family}\n"]))
        if not fams and platform.system() == "Windows":
            fams = set(_windows_registry_fonts().keys())
        _CACHE["all"] = fams
        return fams


def families_for(lang: str) -> set[str]:
    """משפחות שיש להן כיסוי לשפה (לפי fontconfig)."""
    key = f"lang:{lang}"
    with _LOCK:
        if key in _CACHE:
            return _CACHE[key]
        fams = _families_from(_fc(["fc-list", f":lang={lang}", "--format", "%{family}\n"]))
        _CACHE[key] = fams
        return fams


def supports(family: str, lang: str) -> Optional[bool]:
    """True/False כשידוע, None כשאין fontconfig (למשל Windows)."""
    fams = families_for(lang)
    if not fams:
        return None
    return family in fams


# גופנים שמוצגים בבורר. רק מה שמותקן בפועל נכנס לרשימה.
PREFERRED_FAMILIES = (
    "Noto Sans Hebrew", "Heebo", "Rubik", "Assistant", "Alef", "Arial",
    "Segoe UI", "Tahoma", "David", "Noto Sans", "Inter", "Roboto",
    "Open Sans", "Montserrat", "Liberation Sans", "DejaVu Sans", "FreeSans",
    "Noto Serif Hebrew", "Noto Serif", "Liberation Serif", "DejaVu Serif",
)


# משקל fontconfig ← משקל CSS (לקריאת המשקלים שמותקנים בפועל)
_CSS_WEIGHT = {0: 100, 40: 200, 50: 300, 80: 400, 100: 500, 180: 600,
               200: 700, 205: 800, 210: 900}


def family_weights(family: str) -> list[int]:
    """
    המשקלים (CSS) שמותקנים בפועל למשפחה, בלי נטוי. רשימה ריקה כשאין
    מידע (למשל Windows בלי fontconfig).
    """
    key = f"weights:{family}"
    with _LOCK:
        if key in _CACHE:
            return _CACHE[key]
    out = _fc(["fc-list", f"{family}:slant=0", "--format", "%{weight}\n"])
    weights: set[int] = set()
    for line in out.splitlines():
        for part in line.replace("[", " ").replace("]", " ").split():
            try:
                fc = int(float(part))
            except ValueError:
                continue
            weights.add(_CSS_WEIGHT[min(_CSS_WEIGHT, key=lambda k: abs(k - fc))])
    result = sorted(weights)
    with _LOCK:
        _CACHE[key] = result
    return result


def available_fonts() -> list[dict[str, Any]]:
    """
    הגופנים שאפשר לבחור לכתוביות: רק גופנים שמותקנים במחשב, עם
    סימון אם הם מכסים עברית ולטינית ואילו משקלים מותקנים בפועל.
    """
    installed = installed_families()
    he = families_for("he")
    en = families_for("en")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for fam in PREFERRED_FAMILIES:
        if fam in installed and fam not in seen:
            seen.add(fam)
            out.append({"family": fam,
                        "hebrew": (fam in he) if he else True,
                        "latin": (fam in en) if en else True,
                        "weights": family_weights(fam)})
    return out


def font_file(family: str, weight: int = 400) -> Optional[Path]:
    """
    נתיב קובץ הגופן שישמש לרינדור – כפי ש-fontconfig בוחר אותו.

    משמש למדידת רוחב טקסט (PIL) על אותו קובץ ש-libass ישתמש בו.
    """
    fc_weight = _FC_WEIGHT.get(min(_FC_WEIGHT, key=lambda w: abs(w - int(weight or 400))), 80)
    key = f"file:{family}:{fc_weight}"
    with _LOCK:
        if key in _CACHE:
            return _CACHE[key]
    path: Optional[Path] = None
    out = _fc(["fc-match", "-f", "%{file}", f"{family}:weight={fc_weight}"])
    if out.strip():
        p = Path(out.strip())
        if p.exists():
            path = p
    if path is None and platform.system() == "Windows":
        path = _windows_font_file(family, weight)
    with _LOCK:
        _CACHE[key] = path
    return path


def matched_family(family: str) -> str:
    """המשפחה ש-fontconfig באמת יבחר עבור השם המבוקש."""
    out = _fc(["fc-match", "-f", "%{family}", family])
    return out.split(",")[0].strip() if out.strip() else family


def charset(path: Optional[Path]) -> Optional[tuple[tuple[int, int], ...]]:
    """
    טווחי התווים שקובץ הגופן מכסה (לפי fontconfig), או None כשאין מידע.

    libass עובר לגופן חלופי לכל תו שחסר בגופן שנבחר – למשל ספרות וסימני
    פיסוק שחסרים בגופנים עבריים רבים – ולכן המדידה צריכה לדעת זאת.
    """
    if path is None:
        return None
    key = f"charset:{path}"
    with _LOCK:
        if key in _CACHE:
            return _CACHE[key]
    out = _fc(["fc-query", "-f", "%{charset}\n", str(path)])
    ranges: list[tuple[int, int]] = []
    first = out.splitlines()[0] if out.strip() else ""
    for part in first.split():
        lo, _, hi = part.partition("-")
        try:
            ranges.append((int(lo, 16), int(hi or lo, 16)))
        except ValueError:
            continue
    result = tuple(sorted(ranges)) or None
    with _LOCK:
        _CACHE[key] = result
    return result


# תו מייצג לכל כתב – לשאלה „איזה גופן מכסה את הכתב הזה"
SCRIPT_PROBE = {"en": 0x41, "he": 0x05D0}


def fallback_family(family: str, weight: int, script: str) -> str:
    """
    הגופן ש-libass ישתמש בו לאותיות של `script` כשב-`family` אין אותן.

    libass ממיין את הגופנים לפי fontconfig ולוקח את הראשון שיש בו את
    התו; `fc-match` עם `charset` מחזיר בדיוק את אותו גופן.
    """
    probe = SCRIPT_PROBE.get(script)
    if probe is None:
        return family
    fc_weight = _FC_WEIGHT.get(min(_FC_WEIGHT, key=lambda w: abs(w - int(weight or 400))), 80)
    key = f"fallback:{family}:{fc_weight}:{script}"
    with _LOCK:
        if key in _CACHE:
            return _CACHE[key]
    out = _fc(["fc-match", "-f", "%{family[0]}",
               f"{family}:weight={fc_weight}:charset={probe:x}"])
    fam = out.strip() or family
    with _LOCK:
        _CACHE[key] = fam
    return fam


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()


# --------------------------------------------------------------------------
# Windows
# --------------------------------------------------------------------------
def _windows_registry_fonts() -> dict[str, list[tuple[str, str]]]:
    """{family: [(style_name, file)]} מתוך רישום הגופנים של Windows."""
    out: dict[str, list[tuple[str, str]]] = {}
    try:
        import winreg  # type: ignore

        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts")
        for i in range(winreg.QueryInfoKey(key)[1]):
            name, value, _ = winreg.EnumValue(key, i)
            label = name.split(" (")[0].strip()
            family = label
            for suffix in (" Bold Italic", " Bold", " Italic", " Light",
                           " Semibold", " Black", " Medium"):
                if family.endswith(suffix):
                    family = family[: -len(suffix)]
                    break
            out.setdefault(family, []).append((label, str(value)))
    except Exception:                                  # noqa: BLE001
        pass
    return out


def _windows_font_file(family: str, weight: int) -> Optional[Path]:
    fonts = _windows_registry_fonts().get(family) or []
    if not fonts:
        return None
    windir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    want_bold = int(weight or 400) >= 600
    ranked = sorted(fonts, key=lambda it: (("Bold" in it[0]) != want_bold,
                                           "Italic" in it[0]))
    for _label, file in ranked:
        p = Path(file)
        if not p.is_absolute():
            p = windir / file
        if p.exists():
            return p
    return None
