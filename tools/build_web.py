#!/usr/bin/env python3
"""Build one offline HTML file; no frontend toolchain or CDN dependencies."""
from pathlib import Path
import json
import re

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


def main():
    kinds = ("general", "chat", "web", "finance", "document", "calendar", "automation", "research")
    icons = {kind: (ROOT / "assets" / "icons" / f"{kind}.svg").read_text(encoding="utf-8") for kind in kinds}
    (WEB / "icons.js").write_text("window.BlueprintIcons = " + json.dumps(icons, ensure_ascii=False) + ";\n", encoding="utf-8")
    html = (WEB / "index.html").read_text(encoding="utf-8")
    for name in ("icons.js", "packager.js", "app.js"):
        script = (WEB / name).read_text(encoding="utf-8").replace("</script", "<\\/script")
        pattern = r'<script\b[^>]*\bsrc=[\"\']' + re.escape(name) + r'[\"\'][^>]*>\s*</script>'
        html, count = re.subn(pattern, lambda match: "<script>\n" + script + "\n</script>", html)
        if count != 1:
            raise SystemExit(f"Expected one script tag for {name}, found {count}")
    css = (WEB / "style.css").read_text(encoding="utf-8")
    html, count = re.subn(r'<link\b[^>]*href=[\"\']style\.css[\"\'][^>]*>', lambda match: "<style>\n" + css + "\n</style>", html)
    if count != 1:
        raise SystemExit("Expected one style.css link")
    out = ROOT / "dist"
    out.mkdir(exist_ok=True)
    (out / "index.html").write_text(html, encoding="utf-8")
    print(f"Built {out / 'index.html'} ({len(html.encode('utf-8')):,} bytes)")


if __name__ == "__main__":
    main()
