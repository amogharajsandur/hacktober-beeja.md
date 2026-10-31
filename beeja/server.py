"""Local viewer server: serves the built viewer, live viewer data and a tiny import API.

    beeja serve            # http://127.0.0.1:8765  (what `make viewer` runs)

Endpoints (localhost only):
    GET  /api/status        model provider/name in use (never keys), whether GitHub auth is set
    POST /api/import        {"url": "...", "reimport": false} -> 202 {"job": id} | 409 already imported
    GET  /api/jobs/<id>     {"state": running|done|error, "progress": [...], "result"|"error"}

Each import runs in its own thread, so several can run at once while the viewer stays usable.
Writes to the store and viewer data are serialised by ``importer.WRITE_LOCK``.
"""

from __future__ import annotations

import json
import mimetypes
import threading
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from . import importer
from .github import GitHubError, parse_url, token_from_env
from .model_adapter import ModelError, load_dotenv, provider_from_env

MAX_BODY = 16_384


class Jobs:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.jobs: dict[str, dict[str, Any]] = {}
        self.active_cases: set[str] = set()

    def snapshot(self, jid: str) -> dict[str, Any] | None:
        with self.lock:
            j = self.jobs.get(jid)
            return json.loads(json.dumps(j)) if j else None


def make_handler(root: Path, static: Path, jobs: Jobs, import_fn=importer.import_github):
    data_dir = root / "web" / "public" / "data"

    class Handler(BaseHTTPRequestHandler):
        server_version = "beeja"

        def log_message(self, fmt, *args):  # keep the terminal readable; jobs report their own progress
            pass

        # ---------------------------------------------------------------- helpers
        def _json(self, code: int, payload: Any) -> None:
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _file(self, path: Path) -> None:
            if not path.is_file():
                self._json(404, {"error": "not found"})
                return
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            # hashed assets can be cached; HTML and data must always be fresh after a rebuild or import
            cache = "max-age=3600" if "/assets/" in path.as_posix() else "no-cache"
            self.send_header("Cache-Control", cache)
            self.end_headers()
            self.wfile.write(body)

        @staticmethod
        def _inside(base: Path, rel: str) -> Path | None:
            p = (base / rel).resolve()
            return p if p == base.resolve() or base.resolve() in p.parents else None

        # ---------------------------------------------------------------- routes
        def do_GET(self):  # noqa: N802
            path = unquote(urlsplit(self.path).path)
            if path == "/api/status":
                self._json(200, status(root))
            elif path.startswith("/api/jobs/"):
                j = jobs.snapshot(path.rsplit("/", 1)[-1])
                self._json(200, j) if j else self._json(404, {"error": "unknown job"})
            elif path.startswith("/data/"):
                p = self._inside(data_dir, path[len("/data/"):])
                self._file(p) if p else self._json(404, {"error": "not found"})
            else:
                rel = path.lstrip("/") or "index.html"
                p = self._inside(static, rel)
                self._file(p if p and p.is_file() else static / "index.html")

        def do_POST(self):  # noqa: N802
            if urlsplit(self.path).path != "/api/import":
                self._json(404, {"error": "not found"})
                return
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                self._json(413, {"error": "request too large"})
                return
            try:
                req = json.loads(self.rfile.read(n) or b"{}")
                url, reimport = str(req.get("url", "")), bool(req.get("reimport", False))
                ref = parse_url(url)
            except (ValueError, AttributeError):
                self._json(400, {"error": "Body must be JSON: {\"url\": \"...\"}", "kind": "invalid_request"})
                return
            except GitHubError as exc:
                self._json(400, {"error": str(exc), "kind": exc.kind})
                return
            prev = importer.existing(root, ref.case)
            if prev and not reimport:
                self._json(409, {"error": f"Already imported as {prev}.", "kind": "exists", "decision_id": prev})
                return
            with jobs.lock:
                if ref.case in jobs.active_cases:
                    self._json(409, {"error": "This thread is being imported right now.", "kind": "running"})
                    return
                jid = uuid.uuid4().hex[:12]
                jobs.jobs[jid] = {"id": jid, "url": ref.html_url, "state": "running", "progress": []}
                jobs.active_cases.add(ref.case)
            threading.Thread(target=run_job, args=(jobs, jid, ref.case, url, reimport, root, import_fn),
                             daemon=True).start()
            self._json(202, {"job": jid})

    return Handler


def run_job(jobs: Jobs, jid: str, case: str, url: str, reimport: bool, root: Path, import_fn) -> None:
    def progress(msg: str) -> None:
        with jobs.lock:
            jobs.jobs[jid]["progress"].append(msg)

    try:
        result = import_fn(url, root=root, reimport=reimport, progress=progress)
        update = {"state": "done", "result": result}
    except importer.ImportError_ as exc:
        update = {"state": "error", "error": str(exc), "kind": exc.kind, **exc.extra}
    except Exception as exc:  # never leak a traceback with paths/keys to the browser
        traceback.print_exc()
        update = {"state": "error", "error": f"Unexpected error: {exc.__class__.__name__}", "kind": "internal"}
    with jobs.lock:
        jobs.jobs[jid].update(update)
        jobs.active_cases.discard(case)


def status(root: Path) -> dict[str, Any]:
    load_dotenv(root / ".env")
    try:
        p = provider_from_env()
        model, model_error = p.name, None
    except ModelError as exc:
        model, model_error = None, str(exc)
    return {"imports": True, "model": model, "model_error": model_error, "github_auth": bool(token_from_env())}


def serve(root: Path, static: Path, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    importer.refresh_viewer(root)  # always include examples/ AND imports/ before serving
    return ThreadingHTTPServer((host, port), make_handler(root, static, Jobs()))
