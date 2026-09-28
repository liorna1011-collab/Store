"""
בדיקות לסקריפטי Windows — ההתקנה וההפעלה.

הסקריפטים האלה נכתבו על Linux ומעולם לא הורצו על Windows. הבדיקה
הזו נכתבה אחרי שנמצא שסקריפט ההתקנה **לא עבר אפילו ניתוח תחבירי**
ב-PowerShell 5.1 — ברירת המחדל בכל Windows 10/11. היא בודקת את
הדברים שאפשר לבדוק בלי Windows:

  קידוד      PowerShell 5.1 קורא קובץ בלי BOM בקידוד ANSI של המערכת.
             האותיות „ד" (D7 93) ו„ה" (D7 94) הופכות שם לתווים
             “ ו-” — ש-PowerShell מתייחס אליהם כמירכאות. בלי BOM,
             כל מחרוזת עם ד או ה נחתכת באמצע.
  cmd.exe    קובץ .bat עם BOM נשבר כבר בשורה הראשונה, ושורות LF
             בלבד גורמות לניתוח שגוי. ASCII + CRLF הוא הכי עמיד.
  קונסולה    קונסולת Windows אינה תומכת בכיווניות עברית ומציגה
             אותה הפוכה. הודעות שמוצגות בחלון — באנגלית.
  תחביר     אם PowerShell זמין (pwsh), כל .ps1 עובר ניתוח תחבירי
             בדיוק כפי ש-PowerShell 5.1 יקרא אותו.

הרצה:  python3 tests/test_windows_scripts.py
        POLIXOR_PWSH=/path/to/pwsh python3 tests/test_windows_scripts.py
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
BOM = b"\xef\xbb\xbf"
HEB = re.compile(r"[֐-׿]")

PS1 = sorted(SCRIPTS.glob("*.ps1"))
BAT = sorted(SCRIPTS.glob("*.bat")) + [ROOT / "Polixor.bat"]

skipped: list[str] = []


def _pwsh() -> str | None:
    cand = os.environ.get("POLIXOR_PWSH") or shutil.which("pwsh")
    return cand if cand and Path(cand).exists() else None


# ==========================================================================
# קידוד
# ==========================================================================
def test_every_ps1_has_a_bom():
    """בלי BOM, PowerShell 5.1 קורא את הקובץ ב-ANSI ושובר את העברית."""
    for f in PS1:
        assert f.read_bytes()[:3] == BOM, f"{f.name}: חסר BOM"


def test_no_bat_has_a_bom():
    """cmd.exe קורא את ה-BOM כחלק מ-@echo off ונכשל בשורה הראשונה."""
    for f in BAT:
        assert f.read_bytes()[:3] != BOM, f"{f.name}: יש BOM"


def test_bat_files_are_pure_ascii():
    """ASCII הוא הקידוד היחיד ש-cmd.exe קורא נכון בכל תצורה."""
    for f in BAT:
        bad = [i for i, b in enumerate(f.read_bytes()) if b > 127]
        assert not bad, f"{f.name}: תווים שאינם ASCII בבית {bad[0]}"


def test_bat_files_use_crlf():
    for f in BAT:
        data = f.read_bytes()
        lf_only = data.count(b"\n") - data.count(b"\r\n")
        assert lf_only == 0, f"{f.name}: {lf_only} שורות LF בלבד"


def test_ps1_has_no_hebrew_quote_bytes_outside_comments_without_bom():
    """
    הגנה כפולה: גם אם ה-BOM יאבד, הודעות לא אמורות להכיל עברית.
    „ד" ו„ה" הן בדיוק האותיות שהופכות למירכאות ב-cp1252/cp1255.
    """
    for f in PS1:
        for n, line in enumerate(_code_lines(f), 1):
            if HEB.search(line):
                raise AssertionError(f"{f.name}: עברית בקוד (לא בהערה): {line.strip()[:60]}")


# ==========================================================================
# הודעות קונסולה
# ==========================================================================
def test_console_messages_are_not_hebrew():
    """קונסולת Windows מציגה עברית הפוכה — בדיוק כשצריך לקרוא שגיאה."""
    for f in PS1 + BAT:
        for line in _code_lines(f):
            if re.search(r"(Write-\w+|echo |throw |print\()", line) and HEB.search(line):
                raise AssertionError(f"{f.name}: הודעה בעברית: {line.strip()[:60]}")


def _code_lines(f: Path) -> list[str]:
    """שורות קוד בלבד — בלי הערות, בלי בלוק <# #>, בלי REM."""
    text = f.read_bytes().decode("utf-8-sig")
    out, in_block = [], False
    for line in text.splitlines():
        s = line.strip()
        if f.suffix == ".ps1":
            if s.startswith("<#"):
                in_block = True
            if in_block:
                if "#>" in s:
                    in_block = False
                continue
            out.append(s.split("#", 1)[0] if not s.startswith("#") else "")
        else:
            out.append("" if s.upper().startswith("REM") else s)
    return out


# ==========================================================================
# לוגיקה שנמצאה שבורה
# ==========================================================================
def test_winget_output_does_not_leak_into_the_return_value():
    """
    רגרסיה: פלט של פקודה חיצונית בתוך פונקציה הופך לחלק מערך ההחזרה.
    מערך לא-ריק הוא TRUE ב-PowerShell — ולכן התקנה שנכשלה דווחה
    כהצלחה. נבדק ב-PowerShell אמיתי.
    """
    text = (SCRIPTS / "install_windows.ps1").read_bytes().decode("utf-8-sig")
    fn = re.search(r"function Install-WithWinget.*?\n}", text, re.S)
    assert fn, "הפונקציה Install-WithWinget לא נמצאה"
    body = fn.group(0)
    assert "| Out-Host" in body, "הפלט של winget לא מופנה ל-Out-Host"
    assert "$LASTEXITCODE" in body, "ההצלחה לא נקבעת לפי קוד היציאה"
    assert "return $?" not in body, "עדיין מחזיר $? במקום קוד יציאה"


def test_installer_does_not_exit_zero_before_it_is_done():
    """
    רגרסיה: אחרי התקנת Python הסקריפט עצר עם `exit 0` — שמסמן הצלחה.
    משגר שבודק את קוד היציאה היה ממשיך להפעיל שרת בלי סביבה.
    """
    text = (SCRIPTS / "install_windows.ps1").read_bytes().decode("utf-8-sig")
    code = "\n".join(_code_lines(SCRIPTS / "install_windows.ps1"))
    exits = [m.start() for m in re.finditer(r"\bexit 0\b", code)]
    assert len(exits) == 1, f"צפוי exit 0 אחד בלבד, בסוף — נמצאו {len(exits)}"
    assert code.rstrip().endswith("exit 0"), "exit 0 אינו בסוף הסקריפט"
    assert "Find-Python" in text and "Update-SessionPath" in text


def test_installer_skips_node_when_ui_is_prebuilt():
    """הממשק מגיע בנוי — Node.js הוא רק עוד נקודת כשל."""
    text = (SCRIPTS / "install_windows.ps1").read_bytes().decode("utf-8-sig")
    assert 'frontend\\dist\\index.html' in text
    assert "$SkipFrontend = $true" in text


def test_installer_uses_python_dash_m_pip():
    """pip.exe עלול להיות נעול ב-Windows אחרי שדרוג עצמי."""
    code = "\n".join(_code_lines(SCRIPTS / "install_windows.ps1"))
    assert "pip.exe" not in code
    assert "-m pip install -r" in code


def test_installer_rechecks_ffmpeg_after_installing():
    code = "\n".join(_code_lines(SCRIPTS / "install_windows.ps1"))
    block = code[code.index('"Checking FFmpeg"'):code.index('"Creating the Python')]
    assert block.count('Test-Command "ffmpeg"') >= 2, "FFmpeg לא נבדק שוב"


def test_launcher_checks_it_was_extracted_from_the_zip():
    text = (ROOT / "Polixor.bat").read_text(encoding="ascii")
    assert "install_windows.ps1" in text and "Extract All" in text


def test_launcher_installs_then_starts():
    # קריאה כבתים: read_text ממיר CRLF ל-LF ומסתיר בדיוק את מה שנבדק
    text = (ROOT / "Polixor.bat").read_bytes().decode("ascii")
    i_install = text.index("install_windows.ps1\"\r\n    if errorlevel 1")
    i_start = text.index("start.bat")
    assert i_install < i_start
    # errorlevel ולא %ERRORLEVEL% — בתוך בלוק סוגריים %ERRORLEVEL%
    # מוערך פעם אחת לפני שהפקודה רצה, ותמיד יהיה 0.
    assert "%ERRORLEVEL%" not in text.upper()


def test_browser_opens_only_after_the_server_answers():
    """פתיחה לפני שהשרת עלה מציגה „לא ניתן להתחבר"."""
    text = (SCRIPTS / "start.bat").read_text(encoding="ascii")
    assert "/api/health" in text
    i_poll = text.index("Invoke-WebRequest")
    i_open = text.index("Start-Process")
    assert i_poll < i_open


def test_echo_lines_inside_blocks_have_no_bare_parentheses():
    """
    בתוך בלוק `if (...)`, סוגר `)` בשורת echo סוגר את הבלוק מוקדם
    וכל מה שאחריו רץ בלי תנאי.
    """
    for f in BAT:
        depth = 0
        for n, raw in enumerate(f.read_text(encoding="ascii").splitlines(), 1):
            s = raw.strip()
            if s.upper().startswith("REM"):
                continue
            if depth and s.lower().startswith("echo"):
                msg = s[4:]
                assert ")" not in msg and "(" not in msg, \
                    f"{f.name}:{n}: סוגריים בשורת echo בתוך בלוק"
            if s.endswith("("):
                depth += 1
            if s == ")":
                depth -= 1


# ==========================================================================
# ניתוח תחבירי אמיתי
# ==========================================================================
def test_every_ps1_parses_as_powershell_51_would_read_it():
    """
    הבדיקה החזקה: מפענחים את הבתים בדיוק כמו PowerShell 5.1 (BOM →
    UTF-8, בלי BOM → ANSI) ומריצים את המנתח התחבירי של PowerShell.
    """
    pwsh = _pwsh()
    if not pwsh:
        skipped.append("ניתוח תחבירי: pwsh לא נמצא (הגדר POLIXOR_PWSH)")
        return

    parser = (
        "param([string]$Path)\n"
        "$t = [IO.File]::ReadAllText($Path, [Text.Encoding]::UTF8)\n"
        "$tok=$null; $err=$null\n"
        "[void][Management.Automation.Language.Parser]::ParseInput($t,[ref]$tok,[ref]$err)\n"
        "if ($err.Count) { $err | % { \"L$($_.Extent.StartLineNumber): $($_.Message)\" } ; exit 1 }\n"
    )
    with tempfile.TemporaryDirectory(prefix="pxps_") as tmp:
        tmpd = Path(tmp)
        (tmpd / "parse.ps1").write_text(parser, encoding="utf-8")
        for f in PS1:
            raw = f.read_bytes()
            for cp in ("cp1255", "cp1252"):
                text = (raw[3:].decode("utf-8") if raw[:3] == BOM
                        else raw.decode(cp, errors="replace"))
                sim = tmpd / f"{f.stem}.{cp}.ps1"
                sim.write_text(text, encoding="utf-8")
                r = subprocess.run([pwsh, "-NoProfile", "-File",
                                    str(tmpd / "parse.ps1"), "-Path", str(sim)],
                                   capture_output=True, text=True, timeout=120)
                assert r.returncode == 0, (
                    f"{f.name} ב-{cp}: " + (r.stdout or r.stderr)[:300])


# ==========================================================================
def _run_all() -> int:
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    passed, failed = 0, []
    for name, fn in fns:
        before = len(skipped)
        try:
            fn()
            if len(skipped) > before:
                print(f"  \033[93m-\033[0m {name}: דולג — {skipped[-1]}")
            else:
                print(f"  \033[92m✓\033[0m {name}")
                passed += 1
        except AssertionError as exc:
            print(f"  \033[91m✗\033[0m {name}: {exc}")
            failed.append(name)
    total = len(fns) - len(skipped)
    print(f"\n{passed}/{total} בדיקות סקריפטי Windows עברו"
          + (f" · {len(skipped)} דולגו" if skipped else ""))
    if failed:
        print("נכשלו: " + ", ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
