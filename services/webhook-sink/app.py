"""Dev/e2e stand-in for Slack, Telegram and generic webhooks: stores what it receives.

POST <any path>   store the JSON body (Telegram bot API paths included)
GET  /messages    the last 200 messages, newest last
"""

import json
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MESSAGES: deque = deque(maxlen=200)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            body = json.loads(raw)
        except ValueError:
            body = raw.decode(errors="replace")
        MESSAGES.append(
            {"path": self.path, "signature": self.headers.get("X-ZTNA-Signature"), "body": body}
        )
        self._reply(200, {"ok": True})

    def do_GET(self):  # noqa: N802
        if self.path == "/messages":
            self._reply(200, list(MESSAGES))
        elif self.path == "/healthz":
            self._reply(200, {"ok": True})
        else:
            self._reply(404, {"ok": False})

    def _reply(self, code, data):
        raw = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt, *args):
        print(f"[webhook-sink] {self.command} {self.path}", flush=True)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
