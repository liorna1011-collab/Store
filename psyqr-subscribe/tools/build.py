#!/usr/bin/env python3
"""Inline the animation code, the clean banner layer and the Hebrew font into a
single self-contained player: dist/psyqr-subscribe-he.html"""
import base64
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def b64(p):
    return base64.b64encode((ROOT / p).read_bytes()).decode("ascii")


html = (ROOT / "src" / "player.html").read_text(encoding="utf-8")
js = (ROOT / "src" / "animation.js").read_text(encoding="utf-8")
html = (
    html.replace("/*__ANIMATION_JS__*/", js)
    .replace("__AVATAR_PNG__", b64("assets/banner-avatar.png"))
    .replace("__REST_PNG__", b64("assets/banner-rest.png"))
    .replace("__AVATAR_KEY_PNG__", b64("assets/banner-avatar-key.png"))
    .replace("__REST_KEY_PNG__", b64("assets/banner-rest-key.png"))
    .replace("__HEEBO_HEBREW__", b64("assets/heebo-hebrew.woff2"))
    .replace("__HEEBO_LATIN__", b64("assets/heebo-latin.woff2"))
)
out = ROOT / "dist" / "psyqr-subscribe-he.html"
out.parent.mkdir(exist_ok=True)
out.write_text(html, encoding="utf-8")
print(f"wrote {out.relative_to(ROOT)} ({out.stat().st_size // 1024} KB)")
