"""Chaykas Evil Parlays — local server (Python stdlib only).

    python3 server.py          -> http://localhost:8000   (PORT env to change)

Routes:
    /                 the page (public/)
    /api/slate?date=  chaos facts for every skater playing that day
"""
import json
import os
import re
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import nhl
import slate

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def log_message(self, fmt, *args):
        if "/api/" in (self.path or ""):
            super().log_message(fmt, *args)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/api/slate":
            q = parse_qs(url.query)
            date = (q.get("date") or [""])[0] or nhl.today_et().isoformat()
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                return self._json({"status": "error", "error": "date must be YYYY-MM-DD"}, 400)
            return self._json(slate.get_slate(date))
        return super().do_GET()

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "0.0.0.0")
    print(f"Chayka's Evil Parlays -> http://localhost:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()
