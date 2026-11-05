"""Runs inside the browser (Pyodide): the Tribemap web app, answering requests handed over by boot.js.

The data lives in Pyodide's file system under /data, kept in the browser's IndexedDB by boot.js (nothing is sent anywhere).
"""
import os
import re
import time
from urllib.parse import unquote

os.environ.setdefault("TRIBEMAP_GAME_DIR", "/data/game")
os.environ.setdefault("TRIBEMAP_DATA_DIR", "/data/tribemap")
os.environ.setdefault("TRIBEMAP_DB", "/data/tribemap.db")
os.makedirs("/data/tribemap", exist_ok=True)
os.makedirs("/data/game", exist_ok=True)

# There are no threads in the browser: FastAPI runs plain (non-async) endpoints in a thread pool. Run them inline.
import fastapi.dependencies.utils as _deps  # noqa: E402
import fastapi.routing as _routing  # noqa: E402


async def _inline(func, *args, **kwargs):
    return func(*args, **kwargs)


_routing.run_in_threadpool = _inline
_deps.run_in_threadpool = _inline

# web.py imports one small helper from render.py, which imports matplotlib (big, and only for `tn render`).
# Stand-ins so the import works; nothing here draws with it. (Later: move that helper out of render.py.)
import sys  # noqa: E402
import types  # noqa: E402

for name in ("matplotlib", "matplotlib.pyplot", "matplotlib.patches"):
    sys.modules.setdefault(name, types.ModuleType(name))
sys.modules["matplotlib"].use = lambda *a, **k: None
sys.modules["matplotlib.patches"].Polygon = object

from tribemap import config  # noqa: E402
from tribemap.web import create_app  # noqa: E402

app = None


SETTINGS = "/data/tribemap/config.toml"


def settings(clan=None):
    """Write this browser's config.toml. With no clan, the first report imported sets it (config.learn_clan)."""
    if clan is not None and not re.fullmatch(r"\d{4}", str(clan)):
        raise ValueError(f"a clan number is four digits, not {clan!r}")
    with open(SETTINGS, "w", encoding="utf-8") as f:
        if clan:
            f.write(f'clan = "{clan}"\n')
        f.write("keep_backups = 3\n")   # few backups: each is a whole copy of the DB, kept in the browser's storage


def opened(info):
    """A project file was opened: take its clan, and rebuild the app on its data (from the next request)."""
    global app
    settings(info.get("clan") or config.load().clan)
    app = create_app(config.load(), project_open=opened)


def start(clan=None):
    """Build the app. A clan given (the page's ?clan=) is saved; otherwise the one saved before (or none yet)."""
    global app
    if clan or not os.path.exists(SETTINGS):
        settings(clan)
    app = create_app(config.load(), project_open=opened)


async def handle(method, path, query, headers, body):
    """One HTTP request through the ASGI app. Returns (status, [[name, value]...], body bytes, ms)."""
    t = time.perf_counter()
    # in the page there's no network to guard: say we're the local server web.py's checks expect
    headers = [(k, v) for k, v in headers if k.lower() not in ("host", "origin")] + [("host", "localhost")]
    # ASGI wants the path decoded ('/api/hex/ZZ 1107') and the raw bytes as sent ('/api/hex/ZZ%201107')
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
             "scheme": "http", "path": unquote(path), "raw_path": path.encode(), "query_string": query.encode(),
             "root_path": "", "headers": [(k.lower().encode(), v.encode()) for k, v in headers],
             "client": ("browser", 0), "server": ("browser", 80)}
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": bytes(body), "more_body": False}

    out = {"status": 500, "headers": [], "body": bytearray()}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
            out["headers"] = [[k.decode(), v.decode()] for k, v in msg.get("headers", [])]
        elif msg["type"] == "http.response.body":
            out["body"] += msg.get("body", b"")

    await app(scope, receive, send)
    return out["status"], out["headers"], bytes(out["body"]), (time.perf_counter() - t) * 1000
