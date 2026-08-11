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

# manager 独立运行于 E:\MAAOrch-Manager\（自包含，不依赖项目目录的
# infrastructure 模块 — 2026-08-10: import infrastructure 曾导致 manager
# 启动即崩 ModuleNotFoundError）。此处内联请求函数：仅允许 http/https，
# 请求地址来自用户显式配置（镜像/部署目标），非不可信输入。
def _validate_url(url: str) -> str:
    """仅接受 http/https 且 host 非空 — 配置来源校验。"""
    host = url.split("/")[2] if isinstance(url, str) and url.startswith(("http://", "https://")) else ""
    if not host:
        raise ValueError(f"URL 须为 http(s)://host:port 格式: {url!r}")
    return url


def safe_urlopen(req, timeout: float = 30):
    """manager 自包含版请求 — 配置来源 URL（直连/镜像），无重定向跟随限制。"""
    return urllib.request.urlopen(req, timeout=timeout)


def validate_http_url(url: str) -> str:
    return _validate_url(url)


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
# ── 看门狗状态（进程级自愈）──
_watchdog_expect = False  # start 置 True / stop 置 False（仅期望运行时拉起）
_watchdog_hang = 0        # 端口不通连续次数（进程在但卡死判定）


def _probe_project() -> bool:
    """TCP 探测项目端口 19999（不依赖 API 鉴权 — 健康检查需要 token）。"""
    import socket as _sock
    try:
        s = _sock.create_connection(("127.0.0.1", 19999), timeout=5)
        s.close()
        return True
    except Exception:
        return False


def _watchdog_loop() -> None:
    """每 30s 探测项目：进程不在 → 拉起；进程在但端口持续不通 → 杀+重启。
    2026-08-11: 项目进程崩溃/卡死（OOM/段错误）manager 无感知 → 系统静默
    死亡。看门狗与开机自启闭环：期望运行时自动恢复。"""
    global _watchdog_hang
    while True:
        time.sleep(30)
        try:
            if not _watchdog_expect:
                _watchdog_hang = 0
                continue
            if _probe_project():
                _watchdog_hang = 0
                continue
            running, pid = is_project_running()
            _watchdog_hang += 1
            if not running:
                log(f"[看门狗] 项目未运行，自动拉起")
                ok, msg = start_project()
                log(f"[看门狗] 拉起结果: {msg}")
            elif _watchdog_hang >= 3:
                log(f"[看门狗] 项目进程 {pid} 存在但端口连续 {_watchdog_hang} 次不通（卡死），杀进程重启")
                try:
                    subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                                   capture_output=True, timeout=5,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
                except Exception:
                    pass
                time.sleep(3)
                ok, msg = start_project()
                log(f"[看门狗] 重启结果: {msg}")
                _watchdog_hang = 0
        except Exception as e:
            log(f"[看门狗] 异常: {e}")


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
        global _watchdog_expect
        _watchdog_expect = True
        return True, f"started (PID {proc.pid})"
    except Exception as e:
        log(f"启动失败: {e}")
        return False, str(e)


def stop_project(close_emulators: bool = False) -> tuple[bool, str]:
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
    # 2026-08-10: 默认不再关闭模拟器 — 模拟器是独立资源（账号↔模拟器绑定
    # 在 config 里不变），stop/start 项目是常规操作（部署/重启），关掉全部
    # 模拟器 → start 后队列恢复立即启动账号 → MAA 连正在关机的模拟器 →
    # ADB 失联风暴 + MuMu 崩溃报告弹窗（"运行异常"/"异常关闭"）。
    # 确需全关时显式传 close_emulators=True（如整机维护）。
    if not close_emulators:
        log("保留模拟器运行（stop 默认不关模拟器）")
        try:
            if PID_FILE.exists():
                PID_FILE.unlink()
        except Exception:
            pass
        global _watchdog_expect
        _watchdog_expect = False  # 手动停止 → 看门狗不拉起
        return True, "stopped (emulators kept)"
    # Close all emulators — the project is stopping; leaving emulators up
    # leaks RAM/CPU and the account↔emulator binding is lost anyway.
    # They get relaunched on demand by runner when the queue resumes.
    # Graceful close: adb reboot -p first (in-emulator shutdown — games exit
    # cleanly, no crash-report popup), then MuMuManager shutdown as fallback.
    # A bare MuMuManager shutdown while the game is mid-run triggers
    # MuMuNxCrashReporter popups on the desktop.
    try:
        adb = Path(r"E:\MuMu Player 12\nx_main\adb.exe")
        mm = Path(r"E:\MuMu Player 12\nx_main\MuMuManager.exe")
        if adb.exists() and mm.exists():
            # 1) 枚举运行中模拟器 → adb 优雅关机
            try:
                r = subprocess.run([str(mm), "info", "-v", "all"], capture_output=True, text=True,
                                   timeout=10, encoding="utf-8", errors="replace",
                                   creationflags=subprocess.CREATE_NO_WINDOW)
                data = json.loads(r.stdout.lstrip("\ufeff").strip())
                for idx, info in data.items():
                    if info.get("is_android_started") and info.get("adb_port"):
                        addr = "127.0.0.1:" + str(info["adb_port"])
                        try:
                            subprocess.run([str(adb), "-s", addr, "shell", "reboot", "-p"],
                                           capture_output=True, timeout=8,
                                           creationflags=subprocess.CREATE_NO_WINDOW)
                        except Exception:
                            pass
            except Exception as e:
                log(f"模拟器 ADB 关机失败: {e}")
            time.sleep(3)
            # 2) MuMuManager shutdown 兜底
            subprocess.run([str(mm), "control", "-v", "all", "shutdown"],
                           capture_output=True, timeout=30,
                           creationflags=subprocess.CREATE_NO_WINDOW)
            log("已关闭所有模拟器（先 ADB 优雅关机）")
    except Exception as e:
        log(f"关闭模拟器失败: {e}")
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
                req = urllib.request.Request(validate_http_url(url), headers={"User-Agent": "MAAOrch-Manager"})
                resp = safe_urlopen(req, timeout=30)
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
        # Extra safety: always snapshot config.json to manager backups before
        # any deploy — the in-project preservation once silently produced an
        # empty accounts list and accounts were only recoverable from here.
        try:
            src_cfg = root / "models" / "config.json"
            if src_cfg.exists():
                BACKUP_DIR.mkdir(parents=True, exist_ok=True)
                bk = BACKUP_DIR / f"config_predeploy_{time.strftime('%Y%m%d_%H%M%S')}.json"
                shutil.copy2(str(src_cfg), str(bk))
                log(f"部署前备份 config.json → {bk.name}")
        except Exception as e:
            log(f"部署前备份失败: {e}")
        for rel in ("models/config.json", "logs/maa_history", "services/queue.json"):
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
        # Replace — services/maa (MAA binaries, ~500MB) is moved aside, NOT
        # copied: it's managed by MAAOrch itself (ensure_maa_available /
        # download_update) and must not be touched by deploys. Move = instant.
        old_dir = None
        maa_moved = None
        if root.exists():
            # Move services/maa out of the way (fast rename, no copy)
            maa_src = root / "services" / "maa"
            if maa_src.exists():
                # MUST be same volume as root — %TEMP% (C:) cross-volume rename
                # fails and the 500MB copytree fallback silently loses MAA
                # (2026-08-11: deploy wiped services/maa/source).
                maa_moved = root.parent / ".maa_moved_tmp"
                try:
                    if maa_moved.exists():
                        shutil.rmtree(str(maa_moved), ignore_errors=True)
                    os.rename(str(maa_src), str(maa_moved))
                    log("MAA 目录已移出（不参与部署替换）")
                except Exception:
                    maa_moved = None
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
                    shutil.copytree(item, dest, dirs_exist_ok=True,
                                    ignore=shutil.ignore_patterns("maa") if item.name == "services" else None)
                else:
                    shutil.copytree(item, dest,
                                    ignore=shutil.ignore_patterns("maa") if item.name == "services" else None)
            else:
                shutil.copy2(item, dest)

        # Put MAA back (fast move)
        if maa_moved and maa_moved.exists():
            maa_dst = root / "services" / "maa"
            maa_dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.rename(str(maa_moved), str(maa_dst))
                log("MAA 目录已放回")
            except Exception:
                shutil.copytree(maa_moved, maa_dst, dirs_exist_ok=True)

        # Restore preserved data
        for rel, bak in preserve.items():
            if rel == "services/maa":
                continue  # handled by move-aside above
            dest = root / rel
            if bak.is_dir():
                if dest.exists():
                    shutil.rmtree(str(dest), ignore_errors=True)
                shutil.copytree(bak, dest)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(bak, dest)
            log(f"恢复用户数据: {rel}")

        # Post-deploy integrity check: config.json must exist and carry accounts —
        # a silently replaced default config loses all accounts (happened once).
        try:
            cfg_path = root / "models" / "config.json"
            if cfg_path.exists():
                raw = cfg_path.read_text(encoding="utf-8", errors="replace")
                if len(raw) < 500 or '"accounts"' not in raw:
                    log("警告: config.json 疑似被默认配置覆盖，尝试从备份恢复...")
                    bk = BACKUP_DIR / "config.json"
                    if bk.exists() and len(bk.read_text(encoding="utf-8", errors="replace")) > len(raw):
                        shutil.copy2(str(bk), str(cfg_path))
                        log("已从管理器备份恢复 config.json")
            else:
                log("警告: config.json 缺失")
        except Exception as e:
            log(f"config.json 校验失败: {e}")

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
                req = urllib.request.Request(validate_http_url(url), headers={"User-Agent": "MAAOrch-Manager"})
                with safe_urlopen(req, timeout=60) as r:
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
            # close_emulators: true 才关模拟器（默认保留 — 2026-08-10 修复
            # 重启关模拟器导致 ADB 失联风暴 + MuMu 崩溃弹窗）
            ok, msg = stop_project(bool(body.get("close_emulators", False)))
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
    # 看门狗：进程级自愈（期望运行时自动拉起/重启卡死进程）
    threading.Thread(target=_watchdog_loop, daemon=True, name="manager_watchdog").start()
    try:
        server = ThreadingHTTPServer((host, port), Handler)
        log(f"监听 {host}:{port}")
        server.serve_forever()
    except Exception as e:
        log(f"服务启动失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
