"""MAAOrch-Manager — standalone resident service.

Manages the MAAOrch project on this machine via HTTP API:
  download / start / stop / delete / status / log / update_manager

Self-contained (stdlib only) — works even if the project is deleted or broken.
Deployment: E:\\MAAOrch-Manager\\manager.py + config.json
Config: {"project_dir": "E:\\\\MAAOrch", "port": 19998, "token": "<uuid>"}
"""
from __future__ import annotations

import ctypes
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
LOG_FILE = BASE_DIR / "manager.log"
BACKUP_DIR = BASE_DIR / "backups"
REPO_URL = "https://github.com/xiachk083-hub/MAAOrch/archive/refs/heads/main.zip"
RAW_BASE = "https://raw.githubusercontent.com/xiachk083-hub/MAAOrch/main/"
RAW_MIRRORS = [
    "https://raw.githubusercontent.com/xiachk083-hub/MAAOrch/main/",
    "https://ghfast.top/https://raw.githubusercontent.com/xiachk083-hub/MAAOrch/main/",
    "https://ghproxy.net/https://raw.githubusercontent.com/xiachk083-hub/MAAOrch/main/",
]
# Mirror fallbacks for slow GitHub connections (tried in order)
MIRRORS = [
    "https://github.com/xiachk083-hub/MAAOrch/archive/refs/heads/main.zip",
    "https://ghproxy.net/https://github.com/xiachk083-hub/MAAOrch/archive/refs/heads/main.zip",
    "https://gh-proxy.com/https://github.com/xiachk083-hub/MAAOrch/archive/refs/heads/main.zip",
]
DEFAULT_CONFIG = {
    "project_dir": "E:\\MAAOrch",
    "port": 19998,
    "token": "",
}
_lock = threading.Lock()
_progress = {"state": "idle", "pct": 0, "message": ""}


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    if not cfg["token"]:
        cfg["token"] = uuid.uuid4().hex[:16]
        save_config(cfg)
        log(f"生成了新的 token: {cfg['token']}")
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def set_progress(**kw) -> None:
    with _lock:
        _progress.update(kw)


def project_dir() -> Path:
    return Path(load_config()["project_dir"])


# ── 进程管理 ──────────────────────────────────────────────

PID_FILE = BASE_DIR / "project.pid"


def is_project_running() -> tuple[bool, str]:
    """Check if the launched project python process is alive."""
    # Prefer our recorded PID
    if PID_FILE.exists():
        try:
            pid = PID_FILE.read_text().strip()
            if pid:
                r = subprocess.run(["tasklist", "/NH", "/FI", f"PID eq {pid}"],
                                   capture_output=True, text=True, timeout=5,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
                if str(pid) in r.stdout:
                    return True, pid
        except Exception:
            pass
    # Fallback: scan python processes for our project path
    root = project_dir()
    try:
        r = subprocess.run(
            ["wmic", "process", "where", "name like 'python%'", "get", "ProcessId,CommandLine", "/format:csv"],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW)
        for line in r.stdout.splitlines():
            if "main_web.pyw" in line and str(root) in line:
                parts = line.strip().split(",")
                if len(parts) >= 2 and parts[-1].strip().isdigit():
                    return True, parts[-1].strip()
    except Exception:
        pass
    return False, ""


def graceful_close(pid: str) -> None:
    """Send WM_CLOSE to the process's main window (graceful exit)."""
    try:
        user32 = ctypes.windll.user32
        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def _cb(hwnd, _lp):
            wpid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
            if wpid.value == int(pid) and user32.IsWindowVisible(hwnd):
                found.append(hwnd)
                return False
            return True

        user32.EnumWindows(_cb, 0)
        if found:
            user32.PostMessageW(found[0], 0x0010, 0, 0)  # WM_CLOSE
            return True
    except Exception:
        pass
    return False


def start_project() -> tuple[bool, str]:
    cfg = load_config()
    root = Path(cfg["project_dir"])
    main_py = root / "main_web.pyw"
    if not main_py.exists():
        return False, f"main_web.pyw 不存在: {main_py}"
    try:
        if sys.executable.lower().endswith("pythonw.exe"):
            exe = sys.executable
        else:
            exe = shutil.which("pythonw") or sys.executable
        # Capture stderr so startup crashes are diagnosable
        err_f = open(BASE_DIR / "project_stderr.log", "w", encoding="utf-8", errors="replace")
        proc = subprocess.Popen([exe, str(main_py)], cwd=str(root),
                                creationflags=subprocess.CREATE_NO_WINDOW,
                                stdout=subprocess.DEVNULL, stderr=err_f)
        err_f.close()
        try:
            PID_FILE.write_text(str(proc.pid), encoding="utf-8")
        except Exception:
            pass
        log(f"已启动 MAAOrch: {main_py} (PID {proc.pid})")
        return True, f"started (PID {proc.pid})"
    except Exception as e:
        log(f"启动失败: {e}")
        return False, str(e)


def stop_project() -> tuple[bool, str]:
    running, pid = is_project_running()
    if running and pid:
        graceful_close(pid)
        time.sleep(4)
        # Still alive? Hard kill the exact PID
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           capture_output=True, timeout=5,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception:
            pass
    # Cleanup child MAA processes too
    for img in ("MAA.exe", "MAA.Updater.exe"):
        subprocess.run(["taskkill", "/F", "/IM", img],
                       capture_output=True, timeout=5,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        if PID_FILE.exists():
            PID_FILE.unlink()
    except Exception:
        pass
    log("已关闭 MAAOrch")
    return True, "stopped"


# ── 下载与替换 ────────────────────────────────────────────

def download_zip(url: str, dest: Path) -> bool:
    """Download with per-chunk read timeout + retries (self-contained)."""
    old_to = socket.getdefaulttimeout()
    socket.setdefaulttimeout(30)
    try:
        for attempt in range(1, 4):
            try:
                log(f"下载开始 (第{attempt}次): {url}")
                req = urllib.request.Request(url, headers={"User-Agent": "MAAOrch-Manager"})
                resp = urllib.request.urlopen(req, timeout=30)
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                last_log = 0
                with open(dest, "wb") as f:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            set_progress(pct=int(downloaded * 100 / total))
                        # Log every ~2MB so stalls are visible
                        if downloaded - last_log >= 2 * 1048576:
                            log(f"下载中: {downloaded // 1048576}MB"
                                + (f"/{total // 1048576}MB" if total else ""))
                            last_log = downloaded
                log(f"下载完成: {dest.stat().st_size // 1024}KB")
                return True
            except Exception as e:
                log(f"第 {attempt} 次下载失败: {e}")
                if attempt < 3:
                    time.sleep(3)
                else:
                    set_progress(state="error", message=f"下载失败: {e}")
                    return False
    finally:
        socket.setdefaulttimeout(old_to)
    return False


def is_safe_zip_path(member: str, extract_dir: Path) -> bool:
    p = Path(member)
    if member.startswith("/") or ".." in p.parts:
        return False
    try:
        resolved = (extract_dir / member).resolve(strict=False)
        base = extract_dir.resolve(strict=False)
        return resolved == base or base in resolved.parents
    except Exception:
        return False


def extract_zip(zip_path: Path, out_dir: Path) -> bool:
    """Extract, stripping top-level folder, zip-slip safe."""
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(str(zip_path)) as zf:
            names = zf.namelist()
            top_dirs = set()
            for m in names:
                parts = m.split("/")
                if len(parts) > 1 and parts[0]:
                    top_dirs.add(parts[0])
            has_top = len(top_dirs) == 1 and all(
                m.startswith(list(top_dirs)[0] + "/") or m == list(top_dirs)[0] or m == list(top_dirs)[0] + "/"
                for m in names if m)
            top = list(top_dirs)[0] if has_top and list(top_dirs)[0] else ""
            for m in names:
                if not is_safe_zip_path(m, out_dir):
                    log(f"跳过(路径检查失败): {m}")
                    continue
                rel = m[len(top) + 1:] if top and m.startswith(top + "/") else (m if not top else m)
                if not rel:
                    continue
                target = out_dir / rel
                if m.endswith("/"):
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(m) as src, open(str(target), "wb") as dst:
                        dst.write(src.read())
        return True
    except Exception as e:
        log(f"解压失败: {e}")
        return False


def download_project() -> tuple[bool, str]:
    """Download main.zip (with mirror fallbacks), replace project dir, restart."""
    cfg = load_config()
    root = Path(cfg["project_dir"])
    try:
        set_progress(state="downloading", pct=0, message="下载 main.zip")
        tmp_dir = Path(tempfile.mkdtemp(prefix="maorch_mgr_"))
        tmp_zip = tmp_dir / "main.zip"
        # Try mirrors in order until one succeeds
        ok = False
        for url in MIRRORS:
            log(f"尝试镜像: {url[:60]}...")
            set_progress(message=f"下载中: {url[:50]}")
            if download_zip(url, tmp_zip):
                ok = True
                break
            tmp_zip.unlink(missing_ok=True)
        if not ok:
            set_progress(state="error", message="所有镜像下载失败")
            shutil.rmtree(str(tmp_dir), ignore_errors=True)
            return False, "所有镜像下载失败"

        set_progress(state="extracting", message="解压")
        extract_dir = tmp_dir / "extract"
        if not extract_zip(tmp_zip, extract_dir):
            set_progress(state="error", message="解压失败")
            return False, "解压失败"

        # Preserve user data
        preserve = {}
        for rel in ("models/config.json", "services/maa"):
            src = root / rel
            if src.exists():
                bak = tmp_dir / "preserve" / rel
                bak.parent.mkdir(parents=True, exist_ok=True)
                if src.is_dir():
                    shutil.copytree(src, bak)
                else:
                    shutil.copy2(src, bak)
                preserve[rel] = bak
                log(f"保留用户数据: {rel}")

        set_progress(state="replacing", message="替换项目目录")
        # Stop project FIRST — running MAAOrch/MAA locks files (services\maa\),
        # rename/rmtree fails with WinError 183 otherwise.
        log("替换前停止 MAAOrch...")
        stop_project()
        time.sleep(3)
        for _ in range(10):
            running, _ = is_project_running()
            if not running:
                break
            time.sleep(1)
        # Kill orphaned MAA.exe children — MAAOrch spawns MAA.exe subprocesses
        # that survive the MAAOrch process exit and lock services\maa\ files,
        # which made rmtree fail and left a half-broken project behind.
        try:
            subprocess.run(["taskkill", "/F", "/IM", "MAA.exe"],
                           capture_output=True, timeout=10,
                           creationflags=subprocess.CREATE_NO_WINDOW)
            log("已清理残留 MAA.exe 进程")
        except Exception:
            pass
        # Replace
        old_dir = None
        if root.exists():
            # Try rename old first (faster, safer), fallback to rmtree
            old_dir = tmp_dir / "old_project"
            try:
                os.rename(str(root), str(old_dir))
            except Exception:
                shutil.rmtree(str(root), ignore_errors=True)
                old_dir = None
        root.mkdir(parents=True, exist_ok=True)

        # Copy new files
        for item in extract_dir.iterdir():
            dest = root / item.name
            if item.is_dir():
                if dest.exists():
                    # Leftover from a partial previous replace (e.g. rmtree failed
                    # on a locked file) — merge instead of failing with WinError 183.
                    shutil.copytree(item, dest, dirs_exist_ok=True)
                else:
                    shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)

        # Restore preserved data
        for rel, bak in preserve.items():
            dest = root / rel
            if bak.is_dir():
                if dest.exists():
                    shutil.rmtree(str(dest), ignore_errors=True)
                shutil.copytree(bak, dest)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(bak, dest)
            log(f"恢复用户数据: {rel}")

        # Cleanup old + temp
        if old_dir and old_dir.exists():
            shutil.rmtree(str(old_dir), ignore_errors=True)
        shutil.rmtree(str(tmp_dir), ignore_errors=True)

        set_progress(state="restarting", message="自动重启")
        stop_project()
        time.sleep(2)
        ok, msg = start_project()
        set_progress(state="done", message=f"完成: {msg}")
        log(f"项目已更新并重启: {msg}")
        return True, f"updated and restarted ({msg})"
    except Exception as e:
        # Rollback: if the replace failed mid-way and we renamed the old project
        # aside, put it back so the project is never left half-broken.
        try:
            if old_dir and old_dir.exists() and not (root / "ui" / "web" / "index.html").exists():
                log("替换失败，回滚旧项目...")
                if root.exists():
                    shutil.rmtree(str(root), ignore_errors=True)
                os.rename(str(old_dir), str(root))
                old_dir = None
                log("回滚完成")
        except Exception as rb:
            log(f"回滚失败: {rb}")
        set_progress(state="error", message=str(e))
        log(f"更新失败: {e}")
        return False, str(e)


def delete_project(confirm: bool) -> tuple[bool, str]:
    if not confirm:
        return False, "需要 confirm=true"
    cfg = load_config()
    root = Path(cfg["project_dir"])
    if not root.exists():
        return True, "project not exists"
    stop_project()
    # Wait for project process to fully exit (so files aren't locked)
    for _ in range(15):
        running, _ = is_project_running()
        if not running:
            break
        time.sleep(1)
    # Backup config.json
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    src = root / "models" / "config.json"
    if src.exists():
        dst = BACKUP_DIR / f"config_{time.strftime('%Y%m%d_%H%M%S')}.json"
        shutil.copy2(src, dst)
        log(f"已备份配置到 {dst}")
    try:
        shutil.rmtree(str(root))
        log(f"已删除项目: {root}")
        return True, "deleted"
    except Exception as e:
        log(f"删除失败: {e}")
        # One more retry after forcing file handle release
        time.sleep(2)
        try:
            shutil.rmtree(str(root))
            log(f"重试删除成功: {root}")
            return True, "deleted"
        except Exception as e2:
            return False, f"删除失败: {e2}"


# ── 管理器自更新 ──────────────────────────────────────────

def update_manager() -> tuple[bool, str]:
    try:
        for base in RAW_MIRRORS:
            url = base + "manager/manager.py"
            log(f"管理器更新尝试: {url[:60]}...")
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "MAAOrch-Manager"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    content = r.read().decode("utf-8", errors="replace")
                if "MAAOrch-Manager" not in content or len(content) < 5000:
                    log("内容校验失败，试下一个镜像")
                    continue
                target = Path(__file__).resolve()
                tmp = target.with_suffix(".py.new")
                tmp.write_text(content, encoding="utf-8")
                compile(tmp.read_text(encoding="utf-8"), str(tmp), "exec")
                log("管理器新版本已下载，准备替换重启")
                tmp.replace(target)
                threading.Thread(target=_restart_manager, daemon=True).start()
                return True, "updated, restarting"
            except Exception as e:
                log(f"镜像失败: {e}")
        return False, "所有镜像更新失败"
    except Exception as e:
        log(f"管理器更新失败: {e}")
        return False, str(e)


def _restart_manager() -> None:
    time.sleep(1)
    try:
        subprocess.Popen([sys.executable, "-u", str(Path(__file__).resolve())],
                         creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception:
        pass
    os._exit(0)


# ── HTTP 服务 ─────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _auth(self) -> bool:
        cfg = load_config()
        token = self.headers.get("x-manager-token", "")
        return bool(token and cfg["token"] and token == cfg["token"])

    def _json(self, code: int, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._auth():
            self._json(401, {"ok": False, "error": "unauthorized"})
            return
        path = self.path.split("?")[0]
        if path == "/api/status":
            root = project_dir()
            running, pid = is_project_running()
            main_py = root / "main_web.pyw"
            self._json(200, {
                "ok": True,
                "project_dir": str(root),
                "project_exists": root.exists(),
                "has_main": main_py.exists(),
                "running": running,
                "pid": pid,
                "manager_version": "1.0.0",
                "progress": _progress,
            })
        elif path == "/api/progress":
            self._json(200, {"ok": True, **_progress})
        elif path == "/api/log":
            if LOG_FILE.exists():
                lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-100:]
                self._json(200, {"ok": True, "lines": lines})
            else:
                self._json(200, {"ok": True, "lines": []})
        elif path == "/api/project_log":
            # Read MAAOrch logs: debug.log (default) or ?file=stderr / ?file=crash
            # MAA logs: ?file=maa_gui / maa_asst (instance 1) — add &inst=N for other instances
            root = project_dir()
            query = self.path.split("?", 1)[1] if "?" in self.path else ""
            fname = query.split("file=")[1].split("&")[0] if "file=" in query else "debug.log"
            inst = query.split("inst=")[1].split("&")[0] if "inst=" in query else "1"
            allowed = {"debug.log": root / "debug.log",
                       "stderr": BASE_DIR / "project_stderr.log",
                       "crash": root / "crash.log",
                       "maa_gui": root / "services" / "maa" / "instances" / inst / "debug" / "gui.log",
                       "maa_asst": root / "services" / "maa" / "instances" / inst / "debug" / "asst.log"}
            dp = allowed.get(fname, root / "debug.log")
            if dp.exists():
                lines = dp.read_text(encoding="utf-8", errors="replace").splitlines()[-100:]
                self._json(200, {"ok": True, "file": fname, "inst": inst, "lines": lines})
            else:
                self._json(200, {"ok": True, "file": fname, "inst": inst, "lines": [f"{dp} 不存在"]})
        elif path == "/api/config_backup":
            # Read MAAOrch config backups: ?file=<name> returns JSON content,
            # no file param lists available backups (config.json itself included).
            root = project_dir()
            cfg_file = root / "models" / "config.json"
            bk_dir = root / "models" / "backups"
            files = [cfg_file] if cfg_file.exists() else []
            if bk_dir.exists():
                files += sorted(bk_dir.glob("config_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            fname = self.path.split("?", 1)[1].split("file=")[1].split("&")[0] if "file=" in self.path else ""
            if fname:
                target = next((f for f in files if f.name == fname), None)
                if target is None:
                    self._json(404, {"ok": False, "error": f"{fname} 不存在"})
                    return
                try:
                    content = json.loads(target.read_text(encoding="utf-8"))
                except Exception as e:
                    self._json(500, {"ok": False, "error": f"读取失败: {e}"})
                    return
                self._json(200, {"ok": True, "name": target.name,
                                 "mtime": target.stat().st_mtime,
                                 "accounts": content.get("accounts", [])})
            else:
                self._json(200, {"ok": True, "files": [
                    {"name": f.name, "mtime": f.stat().st_mtime, "size": f.stat().st_size}
                    for f in files[:12]]})
        else:
            self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if not self._auth():
            self._json(401, {"ok": False, "error": "unauthorized"})
            return
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = {}
        if length:
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                body = {}
        if path == "/api/download":
            threading.Thread(target=_download_bg, daemon=True).start()
            self._json(200, {"ok": True, "message": "download started"})
        elif path == "/api/start":
            ok, msg = start_project()
            self._json(200 if ok else 500, {"ok": ok, "message": msg})
        elif path == "/api/stop":
            ok, msg = stop_project()
            self._json(200 if ok else 500, {"ok": ok, "message": msg})
        elif path == "/api/delete":
            ok, msg = delete_project(body.get("confirm", False))
            self._json(200 if ok else 400, {"ok": ok, "message": msg})
        elif path == "/api/update_manager":
            ok, msg = update_manager()
            self._json(200 if ok else 500, {"ok": ok, "message": msg})
        elif path == "/api/exec":
            # Remote PowerShell execution (token-protected) — used for
            # diagnostics/recovery on the target machine (copy files, kill
            # processes, inspect dirs) without a local console.
            cmd = body.get("command", "")
            if not cmd:
                self._json(400, {"ok": False, "error": "missing command"})
                return
            timeout = int(body.get("timeout", 120))
            try:
                r = subprocess.run(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                    capture_output=True, text=True, timeout=timeout,
                    encoding="utf-8", errors="replace",
                    creationflags=subprocess.CREATE_NO_WINDOW)
                self._json(200, {"ok": True, "code": r.returncode,
                                 "stdout": (r.stdout or "")[-8000:],
                                 "stderr": (r.stderr or "")[-4000:]})
            except subprocess.TimeoutExpired:
                self._json(408, {"ok": False, "error": f"执行超时 ({timeout}s)"})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
        else:
            self._json(404, {"ok": False, "error": "not found"})


def _download_bg() -> None:
    try:
        download_project()
    except Exception as e:
        set_progress(state="error", message=str(e))


def main() -> None:
    cfg = load_config()
    port = int(cfg["port"])
    host = "0.0.0.0"
    log(f"MAAOrch-Manager v1.0.0 启动 | 项目: {cfg['project_dir']} | 端口: {port}")
    log(f"Token: {cfg['token']}")
    # Auto-start the project if it isn't running — one-click recovery after reboot:
    # user only needs to start the manager (double-click manager.bat), the project
    # comes up automatically.
    try:
        running, pid = is_project_running()
        if running:
            log(f"项目已在运行 (PID {pid})")
        else:
            ok, msg = start_project()
            log(f"自动启动项目: {msg}")
    except Exception as e:
        log(f"自动启动项目失败: {e}")
    try:
        server = ThreadingHTTPServer((host, port), Handler)
        log(f"监听 {host}:{port}")
        server.serve_forever()
    except Exception as e:
        log(f"服务启动失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
