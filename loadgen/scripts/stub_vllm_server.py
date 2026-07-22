#!/usr/bin/env python3
"""
Minimal stand-in for a vLLM OpenAI-compatible /v1/chat/completions endpoint,
used ONLY to validate that the loadgen/ genai-perf config and CLI invocation
are mechanically correct (installed tool, parseable config, well-formed
request/response cycle). This is NOT a substitute for a real benchmark
against Qwen3.6-27B -- no TTFT/ITL/TPS numbers produced against this stub
are meaningful performance data. See loadgen/README.md for the distinction.
"""
import argparse
import json
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL_ID = "Qwen/Qwen3.6-27B-FP8"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # keep test output quiet

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"status": "ok"})
        elif self.path == "/v1/models":
            self._json(200, {"object": "list", "data": [{"id": MODEL_ID, "object": "model"}]})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self._json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        max_tokens = int(body.get("max_tokens") or 64)
        stream = bool(body.get("stream", False))
        prompt_tokens = sum(len(m.get("content", "").split()) for m in body.get("messages", []))
        if stream:
            self._stream_chat(max_tokens, prompt_tokens)
        else:
            self._chat(max_tokens, prompt_tokens)

    def _chat(self, max_tokens, prompt_tokens):
        content = " ".join(["token"] * max_tokens)
        resp = {
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": MODEL_ID,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": max_tokens,
                "total_tokens": prompt_tokens + max_tokens,
            },
        }
        self._json(200, resp)

    def _stream_chat(self, max_tokens, prompt_tokens):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        # first chunk carries the role
        first = {
            "id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
            "model": MODEL_ID,
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}],
        }
        self.wfile.write(f"data: {json.dumps(first)}\n\n".encode())
        for _ in range(max_tokens):
            chunk = {
                "id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                "model": MODEL_ID,
                "choices": [{"index": 0, "delta": {"content": "token "}, "finish_reason": None}],
            }
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        last = {
            "id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
            "model": MODEL_ID,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": max_tokens,
                      "total_tokens": prompt_tokens + max_tokens},
        }
        self.wfile.write(f"data: {json.dumps(last)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    srv = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print(f"stub vLLM-compatible server on :{args.port}")
    srv.serve_forever()
