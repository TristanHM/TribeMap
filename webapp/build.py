"""Build the static, browser-only Tribemap into webapp/dist (proof of concept).

The page is the same as `tn serve`'s; the difference is where /api/... is answered. Here the tribemap package
(FastAPI app included) runs inside the browser under Pyodide, and boot.js routes the page's fetch("/api/...")
calls to it. Nothing leaves the browser.

    python webapp/build.py [out_dir]   then serve out_dir (default webapp/dist) with any static file server
"""
import hashlib
import shutil
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
PKG = REPO / "tribemap"
DIST = HERE / "dist"


def stamp(data):
    """A short hash of a file's contents, for its ?v= in the page."""
    return hashlib.sha256(data).hexdigest()[:10]


def build(DIST=DIST):
    # written over in place (Windows often won't remove a folder just emptied, e.g. in a synced folder)
    DIST.mkdir(parents=True, exist_ok=True)
    shutil.copytree(PKG / "static", DIST / "static", dirs_exist_ok=True)
    shutil.copy(HERE / "webboot.py", DIST / "webboot.py")
    # the user guide, beside the app (index.html and its pictures; not the script that makes the demo reports)
    for p in (HERE / "guide").rglob("*"):
        if p.is_file() and p.suffix in (".html", ".png", ".css"):
            (DIST / "guide" / p.relative_to(HERE / "guide")).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(p, DIST / "guide" / p.relative_to(HERE / "guide"))
    # the Python package, without caches: unpacked into Pyodide's file system at start
    with zipfile.ZipFile(DIST / "tribemap.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(PKG.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                z.write(p, p.relative_to(REPO).as_posix())

    # Every file the page loads is stamped with a hash of its contents (?v=...), so after a rebuild the browser
    # fetches the new copies instead of mixing cached old files with new ones (that broke the page once).
    boot = (HERE / "boot.js").read_text(encoding="utf-8")
    for name in ("tribemap.zip", "webboot.py"):
        old = f'"{name}"'
        assert boot.count(old) == 1, name
        boot = boot.replace(old, f'"{name}?v={stamp((DIST / name).read_bytes())}"')
    (DIST / "boot.js").write_text(boot, encoding="utf-8")

    # the page: relative paths (so it works from any folder, e.g. GitHub Pages), and boot.js before app.js
    html = (PKG / "static" / "index.html").read_text(encoding="utf-8")
    for name in ("style.css", "app.js"):
        old = f'"/static/{name}"'
        assert html.count(old) == 1, name
        html = html.replace(old, f'"static/{name}?v={stamp((DIST / "static" / name).read_bytes())}"')
    html = html.replace('href="/static/', 'href="static/')
    app = '<script src="static/app.js'
    assert html.count(app) == 1
    html = html.replace(app, f'<script src="boot.js?v={stamp(boot.encode())}"></script>\n{app}')
    (DIST / "index.html").write_text(html, encoding="utf-8")
    return DIST


if __name__ == "__main__":
    print(f"Built {build(Path(sys.argv[1]) if len(sys.argv) > 1 else DIST)}")
