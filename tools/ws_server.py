"""工作区可写服务：托管 `data/workspaces/` 并接收「保存为 cleared / 保存备注」。

为什么需要它（而不是只用 `python -m http.server`）：浏览器写不进仓库目录，静态服务
对 POST 一律回 501 ⇒ 「保存为 cleared」只能变成"下载 + 命令行收编"。这个服务补上
那一个写接口，保存就是点一下的事。

    python tools/ws_server.py                 # 127.0.0.1:8898（默认，只本机）
    python tools/ws_server.py --host 0.0.0.0  # 手机同 WiFi 打开（无鉴权，自己看着办）
    python tools/ws_server.py --open          # 起来后自动开浏览器

接口（与页面里的 `D.ws.save_url` 对齐）：
    GET  /                      工作区根主页（账号列表）
    GET  /<账号>/<活动>/<关卡>/   那一关的编辑器页
    POST /api/ws/save           {account, activity, stage, code, job, notes} → cleared.json + notes.md
    POST /api/ws/notes          {account, activity, stage, code, notes}      → notes.md
    GET  /api/ws/list           账号 → 活动 → 关卡（+是否已通关）的 JSON

**写范围严格限定在 `data/workspaces/` 内**：账户/活动/关卡三个名字都过 `_safe_name`
（只允许字母数字._-，且拒 `..`），路径最后再 resolve 校验一次落在根内 —— 一个监听
局域网的写接口，宁可拒得难看，也不能让它写到别处去。返回的仍是纯静态文件，
所以这个服务停了页面照样看不影响（只是保存要退回导出+收编）。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import webbrowser
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

_HERE = pathlib.Path(__file__).resolve().parent
PROJ = _HERE.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(PROJ))

import workspace as WSP        # noqa: E402


class Server(ThreadingHTTPServer):
    """`allow_reuse_address = False`：**端口被占就报错，绝不共用**。

    Python 的 socketserver 在 Windows 上也默认 allow_reuse_address=1，于是两个进程能
    "同时"绑 8898（一个 0.0.0.0、一个 127.0.0.1）—— 页面看着能开，但手机走局域网
    命中 0.0.0.0 那个、本机命中另一个，保存行为在两台机器上不一致。宁可起不来。
    """
    allow_reuse_address = False
    daemon_threads = True


class Handler(SimpleHTTPRequestHandler):
    root = WSP.WS

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(self.root), **kw)

    # ── 读 ──

    def _json(self, obj, code=200):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/api/ws/list":
            return self._json(self._list())
        if p == "/api/ws/tree":                       # 某关的版本链（编辑器刷新用）
            from tools.workspace import find_stage_dir     # noqa: F401  (保持入口一致)
            return self._json(self._tree())
        return super().do_GET()

    def _tree(self) -> dict:
        import urllib.parse as up
        q = up.parse_qs(up.urlparse(self.path).query)
        ac, act, key = (q.get("account") or [""])[0], (q.get("activity") or [""])[0], \
                       (q.get("stage") or [""])[0]
        import versions as VSN
        sd = WSP.find_stage_dir(ac, act, key) if (ac and act and key) else None
        if not sd:
            return {"ok": False, "error": "工作区里没有这一关：%s / %s / %s" % (ac, act, key)}
        return {"ok": True, "stage_id": json.loads((sd / "stage.json").read_text(encoding="utf-8")
                                                  ).get("stage_id"),
                "versions": VSN.tree_summary(sd)}

    def _list(self) -> dict:
        out = {}
        for ac in WSP.list_accounts():
            acts = {}
            for a in WSP.list_activities(ac):
                stages = []
                for d in sorted((WSP.WS / ac / a).glob("*/")):
                    sj = d / "stage.json"
                    if not sj.is_file():
                        continue
                    st = json.loads(sj.read_text(encoding="utf-8"))
                    stages.append({"code": st.get("code") or d.name, "stage_id": st.get("stage_id"),
                                   "name": st.get("name"),
                                   "n_jobs": len(list((d / "jobs").glob("*.json"))),
                                   "cleared": (d / "cleared.json").is_file(),
                                   "level_status": st.get("level_status")})
                acts[a] = {"name": WSP.activity_name(a), "stages": stages}
            out[ac] = acts
        return {"ok": True, "root": str(self.root), "accounts": out}

    # ── 写 ──

    def do_POST(self):
        path = self.path.split("?")[0].rstrip("/")
        if path not in ("/api/ws/save", "/api/ws/notes", "/api/ws/version"):
            return self._json({"ok": False, "error": "unknown endpoint"}, 404)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception as e:
            return self._json({"ok": False, "error": "bad json: %s" % e}, 400)
        ac, act = body.get("account"), body.get("activity")
        key = body.get("code") or body.get("stage")       # 代号优先（目录名），stageId 也认
        if not (ac and act and key):
            return self._json({"ok": False, "error": "account/activity/stage 三者必填"}, 400)
        try:
            if path == "/api/ws/save":
                r = WSP.save_cleared(ac, act, key, body.get("job"),
                                     body.get("notes"), body.get("from_job") or "",
                                     note=body.get("note") or "", parent=body.get("parent"))
                self.log_message("save %s/%s/%s → %s (版本 %s，改 %d 处)", ac, act, key,
                                 r["cleared"], r["version"], r["n_changes"])
                return self._json(r)
            if path == "/api/ws/version":
                r = WSP.save_version(ac, act, key, body.get("job"), note=body.get("note") or "",
                                     from_job=body.get("from_job") or "",
                                     parent=body.get("parent"), notes=body.get("notes"))
                self.log_message("version %s/%s/%s → %s (父 %s，改 %d 处)", ac, act, key,
                                 r["version"], r["parent"], r["n_changes"])
                return self._json(r)
            r = WSP.save_notes(ac, act, key, body.get("notes") or "")
            self.log_message("notes %s/%s/%s", ac, act, key)
            return self._json(r)
        except SystemExit as e:                          # workspace 里用 SystemExit 表达"拒绝"
            return self._json({"ok": False, "error": str(e)}, 400)
        except ValueError as e:
            return self._json({"ok": False, "error": str(e)}, 400)
        except Exception as e:
            return self._json({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}, 500)

    # ── 兜底与日志 ──

    def translate_path(self, path):                       # 防目录穿越（兜底，名字本身已校验）
        p = (self.root / pathlib.Path(path.lstrip("/"))).resolve()
        try:
            p.relative_to(self.root.resolve())
        except ValueError:
            return str(self.root / "__forbidden__")
        return str(p)

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")     # 页面是重新生成的，缓存只会看旧图
        super().end_headers()

    def log_message(self, fmt, *a):
        sys.stderr.write("[ws] %s %s\n" % (self.log_date_time_string(), fmt % a))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="排轴工作区可写服务")
    ap.add_argument("--port", type=int, default=8897,
                    help="默认 8897。**别用 8898**：那是文档里 ad-hoc 静态服务"
                         "（`cd Downloads && python -m http.server 8898`）占的位置")
    ap.add_argument("--host", default="127.0.0.1",
                    help="0.0.0.0 = 手机同 WiFi 也能开（**无鉴权**，只写 data/workspaces）")
    ap.add_argument("--root", default=None,
                    help="托管哪个工作区根（默认 data/workspaces）。"
                         "**沙盒验证用**：指到一份副本上试跑，真工作区不动")
    ap.add_argument("--open", action="store_true", help="起来后自动开浏览器")
    a = ap.parse_args(argv)
    if a.root:
        # **读写必须同一个根**：只把托管目录指到副本、写却仍走 workspace.WS 的话，
        # 就变成"读沙盒、写真库"——页面上看不到的目录被悄悄改了，是最难查的一类事故。
        # 所以这里连 workspace 的全局根一起指过去（workspace 的每个函数都从 WS 派生路径）。
        Handler.root = pathlib.Path(a.root).resolve()
        WSP.WS = Handler.root
        if not Handler.root.is_dir():
            print("! --root %s 不是目录" % Handler.root, flush=True)
            return 1
    if not Handler.root.is_dir() or not WSP.list_accounts():
        print("! 工作区还是空的：先跑\n    python tools/viz_plan.py --workspace <账号> --activity <活动>",
              flush=True)
    try:
        srv = Server((a.host, a.port), Handler)
    except OSError as e:
        print("! 端口 %d 起不来（%s）。换一个：--port 8896" % (a.port, e), flush=True)
        return 1
    url = "http://%s:%d/" % ("127.0.0.1" if a.host in ("0.0.0.0", "") else a.host, a.port)
    print("工作区服务 → %s（根 %s）\nCtrl+C 停止" % (url, WSP.WS), flush=True)
    if a.host == "0.0.0.0":
        print("! 监听 0.0.0.0：同网段任何人都能读写工作区（里面的 box 是账号数据），确认再开",
              flush=True)
    try:
        if a.open:
            webbrowser.open(url)
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
