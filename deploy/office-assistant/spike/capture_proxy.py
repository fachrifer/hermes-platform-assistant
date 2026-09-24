import json
import os
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = os.environ["UPSTREAM_BASE"].rstrip("/")
LOG = os.environ.get("CAPTURE_LOG", "/capture/requests.jsonl")
PORT = int(os.environ.get("CAPTURE_PORT", "8000"))
_DROP = {"host", "content-length", "connection", "accept-encoding"}


def summarize(method: str, path: str, has_auth: bool, body: bytes) -> dict:
    entry = {"ts": time.time(), "method": method, "path": path, "has_auth": has_auth}
    try:
        data = json.loads(body) if body else None
    except ValueError:
        data = None
    if isinstance(data, dict):
        entry["keys"] = sorted(data)
        entry["model"] = data.get("model")
        entry["stream"] = data.get("stream")
        entry["messages"] = len(data.get("messages") or [])
        entry["tools"] = len(data.get("tools") or [])
        entry["chat_template_kwargs"] = data.get("chat_template_kwargs")
        entry["reasoning_effort"] = data.get("reasoning_effort")
    return entry


def summarize_response(path: str, status: int, body: bytes) -> dict:
    entry = {"ts": time.time(), "response_to": path, "status": status,
             "reasoning_chunks": 0, "usage": None}
    text = body.decode("utf-8", "replace")
    payloads = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")] or [text]
    for payload in payloads:
        try:
            data = json.loads(payload)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        if data.get("usage"):
            entry["usage"] = data["usage"]
        for choice in data.get("choices") or []:
            part = choice.get("delta") or choice.get("message") or {}
            if part.get("reasoning_content") or part.get("reasoning"):
                entry["reasoning_chunks"] += 1
    return entry


class Handler(BaseHTTPRequestHandler):
    def _forward(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(summarize(
                self.command, self.path, bool(self.headers.get("Authorization")), body)) + "\n")
        suffix = self.path[3:] if self.path.startswith("/v1") else self.path
        req = urllib.request.Request(UPSTREAM + suffix, data=body or None, method=self.command)
        for key, value in self.headers.items():
            if key.lower() not in _DROP:
                req.add_header(key, value)
        try:
            resp = urllib.request.urlopen(req, timeout=600)
            status = resp.status
        except urllib.error.HTTPError as exc:
            resp, status = exc, exc.code
        except urllib.error.URLError as exc:
            self.send_error(502, f"upstream unreachable: {exc.reason}")
            return
        self.send_response(status)
        self.send_header("Content-Type", resp.headers.get("Content-Type", "application/json"))
        self.end_headers()
        seen = bytearray()
        while True:
            chunk = resp.read(4096)
            if not chunk:
                break
            seen.extend(chunk)
            self.wfile.write(chunk)
            self.wfile.flush()
        if self.path.endswith("/chat/completions"):
            with open(LOG, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(summarize_response(self.path, status, bytes(seen))) + "\n")

    do_GET = _forward
    do_POST = _forward

    def log_message(self, fmt, *args):
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
