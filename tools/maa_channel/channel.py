# -*- coding: utf-8 -*-
"""maa-channel: 目标机远程管理通道（仅标准库，独立于 MAAOrch）。

启动:  pythonw channel.py        (监听 0.0.0.0:18899)
鉴权:  每个请求带 X-Token 头，token 首次运行自动生成于同目录 token.txt
端点:
  GET  /api/status
  POST /api/exec     {"cmd_b64": "<base64 的 PowerShell 命令>"}
  POST /api/fs/list  {"path": "..."}
  POST /api/fs/copy  {"src": "...", "dst": "...", "type": "file|dir"}
  POST /api/fs/mkdir {"path": "..."}
  POST /api/fs/read  {"path": "...", "max_len": 200000}
  POST /api/fs/write {"path": "...", "content_b64": "<base64 内容>"}

安全: 0.0.0.0 + token。仅限可信局域网/Tailscale，勿暴露公网。
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(BASE, "token.txt")
PORT = 18899


def load_token():
    if os.path.exists(TOKEN_FILE):
        t = open(TOKEN_FILE, "r", encoding="utf-8").read().strip()
        if t:
            return t
    t = uuid.uuid4().hex[:24]
    open(TOKEN_FILE, "w", encoding="utf-8").write(t)
    return t


TOKEN = load_token()


def q(s):
    return "'" + s.replace("'", "''") + "'"


def run_powershell(cmd):
    try:
        p = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
            capture_output=True, timeout=900,
        )
        return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return -1, "", "timeout(900s)"
    except Exception as e:
        return -1, "", "%s: %s" % (type(e).__name__, str(e))


def fs_copy(src, dst, kind):
    if kind == "dir":
        rc, out, err = run_powershell("robocopy %s %s /E /R:1 /W:1 /NFL /NDL /NP" % (q(src), q(dst)))
        if rc >= 8:
            return False, "robocopy rc=%d %s" % (rc, (err or out).strip()[-500:])
        return True, "robocopy rc=%d" % rc
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    return True, "ok"


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _auth(self):
        return self.headers.get("x-token", "") == TOKEN

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            return json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            return {}

    def do_GET(self):
        if not self._auth():
            return self._send(401, {"ok": False, "error": "unauthorized"})
        if self.path == "/api/status":
            return self._send(200, {"ok": True, "name": "maa-channel", "port": PORT, "pid": os.getpid()})
        self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if not self._auth():
            return self._send(401, {"ok": False, "error": "unauthorized"})
        b = self._body()
        p = self.path
        try:
            if p == "/api/exec":
                cmd = base64.b64decode(b.get("cmd_b64", "")).decode("utf-8", "replace")
                rc, out, err = run_powershell(cmd)
                return self._send(200, {"ok": True, "rc": rc, "stdout": out[-60000:], "stderr": err[-20000:]})
            if p == "/api/fs/list":
                items = sorted(os.listdir(b["path"]))
                return self._send(200, {"ok": True, "items": items})
            if p == "/api/fs/copy":
                ok, msg = fs_copy(b["src"], b["dst"], b.get("type", "file"))
                return self._send(200, {"ok": ok, "message": msg})
            if p == "/api/fs/mkdir":
                os.makedirs(b["path"], exist_ok=True)
                return self._send(200, {"ok": True})
            if p == "/api/fs/read":
                with open(b["path"], "rb") as f:
                    data = f.read(int(b.get("max_len", 200000)))
                return self._send(200, {"ok": True, "content_b64": base64.b64encode(data).decode("ascii")})
            if p == "/api/fs/write":
                os.makedirs(os.path.dirname(b["path"]), exist_ok=True)
                with open(b["path"], "wb") as f:
                    f.write(base64.b64decode(b["content_b64"]))
                return self._send(200, {"ok": True})
        except Exception as e:
            return self._send(500, {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e))})
        self._send(404, {"ok": False, "error": "not found"})


if __name__ == "__main__":
    print("maa-channel v1 启动 | port=%d | token=%s" % (PORT, TOKEN), flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
