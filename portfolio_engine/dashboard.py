# -*- coding: utf-8 -*-
"""
Serve the live position dashboard.

  python -m portfolio_engine.dashboard
  http://<ubuntu-ip>:8787
"""
from __future__ import annotations

import json
import os
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import DASHBOARD_HOST, DASHBOARD_PORT, ROOT
from .env import load_dotenv
from .positions import snapshot

WEB = Path(__file__).resolve().parent / "web" / "index.html"


def _token() -> str:
    load_dotenv()
    return (os.getenv("DASHBOARD_TOKEN") or "").strip()


def _authorized(handler: BaseHTTPRequestHandler) -> bool:
    need = _token()
    if not need:
        return True
    auth = handler.headers.get("Authorization") or ""
    if auth.lower().startswith("bearer ") and auth.split(" ", 1)[1].strip() == need:
        return True
    q = parse_qs(urlparse(handler.path).query)
    return (q.get("token") or [""])[0] == need


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            html = WEB.read_bytes() if WEB.exists() else b"<p>missing index.html</p>"
            self._send(200, html, "text/html; charset=utf-8")
            return
        if path == "/api/positions":
            if not _authorized(self):
                self._send(401, json.dumps({"error": "unauthorized"}).encode(), "application/json")
                return
            try:
                data = snapshot()
                self._send(200, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
            except Exception as exc:
                traceback.print_exc()
                self._send(500, json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8"), "application/json")
            return
        self._send(404, b"not found", "text/plain")


def main() -> int:
    load_dotenv()
    host = (os.getenv("DASHBOARD_HOST") or DASHBOARD_HOST).strip() or "127.0.0.1"
    port = int(os.getenv("DASHBOARD_PORT") or DASHBOARD_PORT)
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"dashboard http://{host}:{port}  root={ROOT}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
