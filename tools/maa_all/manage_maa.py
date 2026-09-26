#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MAA-ALL 总管理器 — 目标机 D:\\MAA-ALL

独立 MAA 团队（与 E:\\MAA-Multi 的 AUTO-MAS 体系无关）：
  复制原样本 -> 自动填入实测 ADB -> 验证 -> 绑定模拟器 -> 一键启动/关闭
  更新原样本 -> 批量同步子 MA（程序替换、配置保留）

用法:
  python manage_maa.py serve [--host 0.0.0.0] [--port 18999] [--token xxx]
  python manage_maa.py update
  python manage_maa.py new <备注名> --emu <索引|实例名>
  python manage_maa.py list
  python manage_maa.py open <ID|备注名>... [--no-emu] [--kill-emu]
  python manage_maa.py close <ID|备注名>... [--kill-emu]
  python manage_maa.py bind <ID|备注名> --emu <索引|实例名>
  python manage_maa.py sync-copies [ID...]
  python manage_maa.py adb <ID|备注名>
  python manage_maa.py emu
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

BASE = Path(r"E:\MAA-ALL")
SAMPLE_DIR = BASE / "MAA"
MAAS_DIR = BASE / "MAAS"
LOGS_DIR = BASE / "logs"
DL_DIR = BASE / "_download"
BACKUP_DIR = BASE / "_backup"

MUMU_CLI_CANDIDATES = [
    r"E:\MuMu Player 12\nx_main\MuMuManager.exe",
    r"E:\Program Files\Netease\MuMuPlayer-12.0\nx_main\MuMuManager.exe",
]
RELEASE_API = "https://api.github.com/repos/MaaAssistantArknights/MaaRelease/releases/latest"
RELEASE_PAGE = "https://github.com/MaaAssistantArknights/MaaRelease/releases/latest"
MIRRORS = ["", "https://ghfast.top/", "https://ghproxy.net/", "https://gh-proxy.com/"]
UA = "MAA-ALL-manager/1.0"

LOG_BUF: deque[str] = deque(maxlen=500)
_OP_LOCK = threading.Lock()
_OP_RUNNING: dict = {"op": None, "start": None, "msg": ""}
_EMU_CACHE: dict = {"t": 0, "data": []}
handler_args: dict = {"token": ""}
FAVS_FILE = BASE / "favorites.json"


def load_favs() -> list[str]:
    try:
        return list(json.loads(FAVS_FILE.read_text(encoding="utf-8")).get("emus", []))
    except Exception:
        return []


def save_favs(favs: list[str]) -> None:
    favs = list(dict.fromkeys(favs))  # 去重保序
    FAVS_FILE.write_text(json.dumps({"emus": favs}, ensure_ascii=False, indent=2), encoding="utf-8")


# ------------------------- 基础工具 -------------------------
def log(msg: str) -> None:
    line = f"{datetime.now():%H:%M:%S} {msg}"
    LOG_BUF.append(line)
    print(line, flush=True)
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOGS_DIR / "manager.log", "a", encoding="utf-8", errors="replace") as f:
            f.write(line + "\n")
    except Exception:
        pass


def run(cmd: list[str], timeout: int = 300) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, "timeout(0)"
    except Exception as e:
        return -1, str(e)


def ps(cmd: str, timeout: int = 60) -> tuple[int, str]:
    return run(["powershell", "-NoProfile", "-Command", cmd], timeout=timeout)


def find_mumu_cli() -> str | None:
    for c in MUMU_CLI_CANDIDATES:
        if Path(c).exists():
            return c
    for root in ("E:\\MuMu Player 12", r"C:\Program Files\Netease\MuMuPlayer-12.0"):
        p = Path(root)
        if p.exists():
            try:
                for hit in p.rglob("MuMuManager.exe"):
                    return str(hit)
            except Exception:
                pass
    return None


def probe_port(port, timeout: float = 1.5) -> bool:
    try:
        s = socket.create_connection(("127.0.0.1", int(port)), timeout=timeout)
        s.close()
        return True
    except Exception:
        return False


# ------------------------- MuMu 模拟器 -------------------------
def mumu_info_all() -> list[dict]:
    cli = find_mumu_cli()
    if not cli:
        return []
    rc, out = run([cli, "info", "-v", "all"], timeout=30)
    if rc != 0:
        return []
    try:
        d = json.loads(out.strip().lstrip("\ufeff"))
    except Exception:
        return []
    items: list[dict] = []
    if isinstance(d, dict):
        if "index" in d and "name" in d:
            d = {str(d.get("index")): d}
        for k, v in d.items():
            if isinstance(v, dict) and "name" in v:
                items.append(v | {"index": v.get("index", k)})
    elif isinstance(d, list):
        items = d
    result = []
    for it in items:
        try:
            result.append({
                "index": str(it.get("index", "")),
                "name": str(it.get("name", "")),
                "adb_port": str(it.get("adb_port", "")),
                "proc": bool(it.get("is_process_started")),
                "android": bool(it.get("is_android_started")),
            })
        except Exception:
            pass
    return result


def _probe_all(items: list[dict]) -> list[dict]:
    with ThreadPoolExecutor(max_workers=16) as ex:
        futs = {ex.submit(probe_port, e.get("adb_port", ""), 1.5): e for e in items if e.get("adb_port")}
        for fut, e in futs.items():
            e["adb_up"] = fut.result()
    for e in items:
        if not e.get("adb_port"):
            e["adb_up"] = False
    return items


def emu_list() -> list[dict]:
    now = time.time()
    if now - _EMU_CACHE["t"] < 8 and _EMU_CACHE["data"]:
        return _EMU_CACHE["data"]
    data = mumu_info_all()
    if data:
        data = _probe_all(data)
        _EMU_CACHE.update({"t": time.time(), "data": data})
        return data
    return _EMU_CACHE["data"]


def find_emu(idx: int | str) -> dict | None:
    data = emu_list()
    for e in data:
        if str(e.get("index", "")) == str(idx):
            return e
    name = str(idx).strip()
    for e in data:
        n = e.get("name") or ""
        if n == name or n.startswith(name) or name.startswith(n):
            return e
    return None


def emu_launch(idx: str) -> tuple[bool, str]:
    cli = find_mumu_cli()
    if not cli:
        return False, "找不到 MuMuManager.exe"
    rc, out = run([cli, "control", "-v", str(idx), "launch"], timeout=60)
    if rc != 0:
        return False, out[:200]
    if '"errcode": 0' not in out and 'errcode' in out:
        return False, out[:200]
    return True, out[:200]


def emu_shutdown(idx: str) -> None:
    cli = find_mumu_cli()
    if cli:
        run([cli, "control", "-v", str(idx), "shutdown"], timeout=60)


def wait_android(idx: str, timeout: int = 120) -> str:
    t0 = time.time()
    while time.time() - t0 < timeout:
        e = find_emu(idx)
        if e and (e.get("android") or e.get("adb_up")):
            return "ready"
        time.sleep(3)
    return "timeout"


# ------------------------- MAA 版本 / 配置 -------------------------
def maa_version(inst_dir: Path) -> str:
    exe = inst_dir / "MAA.exe"
    if not exe.exists():
        return ""
    try:
        rc, out = ps(f"(Get-Item '{exe}').VersionInfo.ProductVersion", timeout=30)
        v = [l.strip() for l in out.splitlines() if l.strip()]
        return v[-1] if v else ""
    except Exception:
        return ""


def read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8", errors="replace") or "{}")
    except Exception:
        return {}


def write_json_atomic(p: Path, data: dict) -> None:
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)
    for b in p.parent.glob(p.name + ".bak*"):
        try:
            b.unlink()
        except Exception:
            pass


def backup_config(inst_dir: Path) -> None:
    config_dir = inst_dir / "config"
    if not config_dir.exists():
        return
    dst = inst_dir / "backup" / datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        shutil.copytree(config_dir, dst)
        log(f"  配置备份 -> {dst.name}")
    except Exception as e:
        log(f"  配置备份失败: {e}")


def fill_connect(inst_dir: Path, emu: dict) -> tuple[bool, str]:
    cli = find_mumu_cli()
    adb_exe = ""
    if cli:
        cand = Path(cli).parent / "adb.exe"
        if cand.exists():
            adb_exe = str(cand)
    port = str(emu.get("adb_port", ""))
    idx = str(emu.get("index", ""))
    addr = f"127.0.0.1:{port}" if port else ""
    if not addr:
        return False, f"模拟器 {idx} 无 ADB 端口（模拟器未启动？先启动再填）"

    gj = read_json(inst_dir / "config" / "gui.json")
    gj.update({
        "Connect.Address": addr,
        "Connect.AdbPath": adb_exe,
        "Connect.ConnectConfig": "MuMuEmulator12",
        "Connect.TouchMode": "minitouch",
        "Connect.AdbReplaced": "True",
        "Connect.AutoDetect": "False",
        "Connect.AlwaysAutoDetect": "False",
        "Connect.RetryOnDisconnected": "True",
        "Connect.AllowADBRestart": "False",
        "Connect.AllowADBHardRestart": "False",
        "Start.RunDirectly": "False",
        "Start.StartGame": "False",
    })
    if cli:
        gj["Start.EmulatorPath"] = cli
        gj["Start.EmulatorAddCommand"] = f"control --vmindex {idx} launch"
    write_json_atomic(inst_dir / "config" / "gui.json", gj)

    gn = read_json(inst_dir / "config" / "gui.new.json")
    gui = gn.setdefault("Configurations", {}).setdefault("Default", {}).setdefault("Gui", {})
    gui["GuideStep"] = 7
    cs = gui.setdefault("ConnectSettings", {})
    cs.update({
        "AutoDetect": False, "AlwaysAutoDetect": False, "AdbReplaced": True,
        "AllowAdbRestart": False, "AllowAdbHardRestart": False,
        "EnableAdbLite": False, "KillAdbOnExit": False,
        "Config": "MuMuEmulator12", "TouchMode": "MiniTouch",
    })
    cs["Address"] = addr
    if adb_exe:
        cs["AdbPath"] = adb_exe
    if idx.isdigit():
        extras = cs.setdefault("Extras", {}).setdefault("MuMuEmulator12", {})
        extras.update({
            "InstanceIndex": int(idx), "IsEnabled": False,
            "EnableBridgeConnection": False, "EnableTouch": False,
        })
        if cli:
            extras["EmulatorPath"] = cli
    ss = gui.setdefault("StartUpSettings", {})
    ss.update({
        "RunDirectly": False, "SkipStartupAutoRunAfterUpdate": False,
        "StartEmulator": False, "RestartEmulatorWhenAdbFailed": False,
        "EmulatorWaitSeconds": 60,
    })
    if cli:
        ss["EmulatorPath"] = cli
    if cli and idx.isdigit():
        ss["EmulatorAddCommand"] = f"control --vmindex {idx} launch"
    gui.setdefault("RuntimeSettings", {})["AutoRestartOnDrop"] = True
    gui["PostActions"] = "None"
    write_json_atomic(inst_dir / "config" / "gui.new.json", gn)
    return True, f"已写入 {addr} (InstanceIndex={idx})"


def verify_connect(inst_dir: Path, emu: dict) -> tuple[bool, str]:
    gn = read_json(inst_dir / "config" / "gui.new.json")
    got = (gn.get("Configurations", {}).get("Default", {})
             .get("Gui", {}).get("ConnectSettings", {}).get("Address", ""))
    want = f"127.0.0.1:{emu.get('adb_port', '')}"
    addr_ok = (got == want) and want != "127.0.0.1:"
    port_ok = probe_port(emu.get("adb_port", ""), 2.0)
    if addr_ok and port_ok:
        return True, f"验证通过: {want}"
    if not port_ok and not (emu.get("android") or emu.get("adb_up")):
        return False, "模拟器未开机，待启动后验证（打开时会自动校正）"
    reasons = []
    if not addr_ok:
        reasons.append(f"读回 {got!r} ≠ {want!r}")
    if not port_ok:
        reasons.append(f"端口 {emu.get('adb_port')} 不通")
    return False, "验证失败: " + "; ".join(reasons)


# ------------------------- 副本管理 -------------------------
def list_copies() -> list[dict]:
    rows = []
    if not MAAS_DIR.exists():
        return rows
    for d in sorted(MAAS_DIR.iterdir()):
        if not d.is_dir():
            continue
        bind = read_json(d / ".bind.json")
        if not bind:
            bind = {"id": d.name, "name": d.name}
        running = False
        pidf = d / ".pid"
        if pidf.exists():
            rc, out = run(["tasklist", "/FI", f"PID eq {pidf.read_text().strip()}", "/FO", "CSV"], timeout=20)
            running = "MAA.exe" in out and "正在运行" not in out and "INFO: 没有运行的任务" not in out
        rows.append({
            "dir": d.name, "id": bind.get("id", ""), "name": bind.get("name", ""),
            "emu_index": str(bind.get("emu_index", "")), "emu_name": bind.get("emu_name", ""),
            "adb": bind.get("adb", ""), "version": maa_version(d), "running": running,
            "verified": bool(bind.get("verified", False)), "created": bind.get("created", ""),
        })
    return rows


def find_copy(key: str) -> Path | None:
    if not MAAS_DIR.exists():
        return None
    for d in sorted(MAAS_DIR.iterdir()):
        if not d.is_dir():
            continue
        bind = read_json(d / ".bind.json")
        if bind.get("id") == key or bind.get("name") == key or d.name == key:
            return d
    return None


def new_copy(name: str, emu: dict) -> Path:
    if not SAMPLE_DIR.exists() or not (SAMPLE_DIR / "MAA.exe").exists():
        raise RuntimeError("原样本不存在 / 未初始化, 先执行 update")
    for d in list_copies():
        if str(d["emu_index"]) == str(emu.get("index", "")) and d["emu_index"] != "":
            raise RuntimeError(f"模拟器 {emu.get('index')}({emu.get('name')}) 已被 『{d['name']}』 绑定")
    copy_id = uuid.uuid4().hex[:8]
    tar = MAAS_DIR / f"{copy_id}-{name}"
    MAAS_DIR.mkdir(parents=True, exist_ok=True)
    log(f"复制原样本 -> {tar.name}")
    rc, out = run(["robocopy", str(SAMPLE_DIR), str(tar), "/E", "/R:1", "/W:1", "/NFL", "/NDL", "/NJS"], timeout=600)
    if rc > 7:
        raise RuntimeError(f"复制失败 rc={rc}: {out[:200]}")
    for f in (".pid", ".meta"):
        try:
            (tar / f).unlink()
        except Exception:
            pass
    if not (tar / "MAA.exe").exists() or not (tar / "resource").exists():
        raise RuntimeError("复制后完整性校验失败: MAA.exe/resource 缺失")
    # 模拟器未开机：不启动，填占位端口（5555+2*idx），打开时会自动校正
    if not emu.get("adb_port") or not (emu.get("android") or emu.get("adb_up")):
        idx_s = str(emu.get("index", ""))
        if idx_s.isdigit():
            placeholder = str(5555 + int(idx_s) * 2)
            log(f"  模拟器 {idx_s} 未开机：ADB 填占位端口 {placeholder}（点击启动时自动校正）")
            emu = dict(emu, adb_port=placeholder)
        else:
            raise RuntimeError(f"模拟器 {idx_s} 无端口且无法占位")
    ok, msg = fill_connect(tar, emu)
    log(f"  填 ADB: {msg}")
    vok, vmsg = verify_connect(tar, emu)
    bind = {
        "id": copy_id, "name": name,
        "emu_index": str(emu.get("index", "")), "emu_name": emu.get("name", ""),
        "adb": str(emu.get("adb_port", "")), "maa_version": maa_version(tar),
        "verified": vok, "verify_msg": vmsg, "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    write_json_atomic(tar / ".bind.json", bind)
    log(f"  {'✓ ' + vmsg if vok else '✗ ' + vmsg}")
    return tar


def open_copy(d: Path, no_emu: bool = False, kill_emu: bool = False) -> None:
    bind = read_json(d / ".bind.json")
    idx = str(bind.get("emu_index", ""))
    if not idx:
        raise RuntimeError(f"{bind.get('name', d.name)} 未绑定模拟器（先 new/bind）")
    emu = find_emu(idx)
    if emu and emu.get("adb_port") and bind.get("adb") != str(emu.get("adb_port")):
        log(f"  端口漂移检测: {bind.get('adb')} -> {emu.get('adb_port')}，自动重填")
        fill_connect(d, emu)
    if not no_emu:
        if emu is None:
            raise RuntimeError(f"模拟器 {idx} 在 MuMu 中不存在（检查 .bind.json / 重建绑定）")
        if emu.get("android") or emu.get("adb_up"):
            log(f"  模拟器 {idx} 已就绪")
        else:
            log(f"  启动模拟器 {idx} ({emu.get('name', '?')})")
            ok, msg = emu_launch(idx)
            if not ok:
                raise RuntimeError(f"模拟器启动失败: {msg}")
            st = wait_android(idx)
            if st != "ready":
                raise RuntimeError(f"模拟器 {idx} 未就绪 ({st})")
    exe = d / "MAA.exe"
    if not exe.exists():
        raise RuntimeError(f"MAA.exe 不存在: {d}")
    env = dict(os.environ)
    if idx.isdigit():
        env["ANDROID_ADB_SERVER_PORT"] = str(5037 + int(idx) % 100)
    log(f"  启动 MAA.exe ({d.name})")
    try:
        p = subprocess.Popen([str(exe)], cwd=str(d), env=env)
        (d / ".pid").write_text(str(p.pid))
    except Exception as e:
        raise RuntimeError(f"MAA 启动失败: {e}")
    log("  ✓ 已启动")


def close_copy(d: Path, kill_emu: bool = False) -> None:
    pidf = d / ".pid"
    if pidf.exists():
        pid = pidf.read_text().strip()
        log(f"  结束 MAA PID {pid} ({d.name})")
        run(["taskkill", "/PID", pid, "/T", "/F"], timeout=30)
        try:
            pidf.unlink()
        except Exception:
            pass
    if kill_emu:
        bind = read_json(d / ".bind.json")
        idx = str(bind.get("emu_index", ""))
        if idx:
            log(f"  shutdown 模拟器 {idx}")
            emu_shutdown(idx)


def sync_copy(d: Path) -> None:
    if not SAMPLE_DIR.exists() or not (SAMPLE_DIR / "MAA.exe").exists():
        raise RuntimeError("原样本不存在, 先执行 update")
    backup_config(d)
    log(f"  同步 {d.name}: 程序文件替换（config/data/绑定保留）")
    rc, out = run(["robocopy", str(SAMPLE_DIR), str(d), "/E", "/R:1", "/W:1", "/NFL", "/NDL", "/NJS",
                   "/XD", "config", "data", "debug", "cache", "backup",
                   "/XF", ".pid", ".meta", ".bind.json"], timeout=900)
    if rc > 7:
        raise RuntimeError(f"同步失败 rc={rc}: {out[:200]}")
    bind = read_json(d / ".bind.json")
    bind["maa_version"] = maa_version(d)
    write_json_atomic(d / ".bind.json", bind)
    log(f"  ✓ {d.name} 版本 -> {bind['maa_version']}")


def init_sample() -> None:
    exe = SAMPLE_DIR / "MAA.exe"
    if not exe.exists():
        return
    gn = SAMPLE_DIR / "config" / "gui.new.json"
    if gn.exists():
        try:
            if "$type" in gn.read_text(encoding="utf-8", errors="replace"):
                return
        except Exception:
            pass
    log("  初始化原样本（生成 MAA 模板配置）...")
    try:
        p = subprocess.Popen([str(exe)], cwd=str(SAMPLE_DIR))
        t0 = time.time()
        while time.time() - t0 < 60:
            if gn.exists():
                try:
                    if "$type" in gn.read_text(encoding="utf-8", errors="replace"):
                        break
                except Exception:
                    pass
            time.sleep(2)
        try:
            p.kill()
        except Exception:
            pass
    except Exception as e:
        log(f"  初始化异常(可忽略): {e}")


# ------------------------- 更新原样本 -------------------------
def update_sample() -> None:
    log("== 更新原样本 ==")
    api_data = None
    try:
        req = urllib.request.Request(RELEASE_API, headers={"User-Agent": UA})
        api_data = json.loads(urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace"))
    except Exception as e:
        raise RuntimeError(f"无法获取 MaaRelease 信息（网络受限: {e}; 可手动下载 win-x64 zip 放入 D:\\MAA-ALL\\_download 后重试）")
    tag = api_data.get("tag_name", "")
    url = ""
    if api_data:
        for a in api_data.get("assets", []):
            n = a.get("name", "")
            if "win-x64" in n and n.endswith(".zip"):
                url = a.get("browser_download_url", "")
                break
    if not url:
        raise RuntimeError("未找到 win-x64 zip 资产")
    log(f"  最新版本: {tag}")
    DL_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = DL_DIR / f"MAA-{tag or 'unknown'}-win-x64.zip"
    ok = False
    for m in MIRRORS:
        if ok:
            break
        u = m + url
        try:
            log(f"  下载: {u[:80]}...")
            req = urllib.request.Request(u, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r, open(zip_path, "wb") as f:
                while True:
                    chunk = r.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
            ok = True
        except Exception as e:
            log(f"  下载失败({m or '官方'}): {e}")
    if not ok:
        raise RuntimeError("下载失败（官方与镜像均不可达）")
    log(f"  下载完成: {zip_path.stat().st_size // 1048576} MB, 解压中...")
    import zipfile
    tmp = DL_DIR / f"extract-{tag or 'unknown'}"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/") and not n.startswith("__MACOSX")]
        zf.extractall(tmp)
    # zip 可能带一层包裹目录（MAA-v6.16.8-win-x64/MAA.exe），也可能根目录直放（externals/..., MAA.exe）
    exe_names = [n for n in names if n.endswith("/MAA.exe") or n == "MAA.exe"]
    if not exe_names:
        raise RuntimeError("解压结果缺少 MAA.exe")
    exe_path = exe_names[0]
    root = exe_path.split("/")[0] if "/" in exe_path else ""
    src = (tmp / root) if root else tmp
    if not (src / "MAA.exe").exists():
        raise RuntimeError("解压结果缺少 MAA.exe")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if SAMPLE_DIR.exists():
        old = BACKUP_DIR / f"MAA-{datetime.now():%Y%m%d-%H%M%S}"
        os.rename(SAMPLE_DIR, old)
        log(f"  原样本备份 -> {old}")
    else:
        SAMPLE_DIR.mkdir(parents=True)
    shutil.copytree(src, SAMPLE_DIR, dirs_exist_ok=True)
    init_sample()
    log(f"  原样本已更新: 版本={maa_version(SAMPLE_DIR)} ({tag})")


# ------------------------- Web GUI -------------------------
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402

PAGE = r"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>MAA-ALL 总管理器</title>
<style>
body{font-family:Segoe UI,Microsoft YaHei,sans-serif;margin:0;background:#12181f;color:#e8eef5}
.wrap{max-width:1180px;margin:18px auto;padding:0 14px}
h1{font-size:18px;margin:0 0 10px;display:flex;align-items:center;gap:12px}
h1 .sub{color:#8fa3b9;font-weight:400;font-size:13px}
.card{background:#1b2531;border:1px solid #2c3d52;border-radius:8px;padding:10px 14px;margin-bottom:12px}
.sec{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:8px}
.sec b{font-size:14px}
.cnt{color:#8fa3b9;font-size:12px}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:6px 0}
button{background:#2e86de;color:#fff;border:0;border-radius:5px;padding:7px 12px;cursor:pointer;font-size:12px}
button:hover{filter:brightness(1.15)}button.orange{background:#e67e22}button.red{background:#c0392b}
button.gray{background:#57606a}button.small{padding:3px 8px;font-size:11px}
input,select{background:#0f1822;border:1px solid #3a4d63;color:#e8eef5;border-radius:5px;padding:6px 8px;font-size:12px}
table{border-collapse:collapse;width:100%;font-size:12px}
th,td{border-bottom:1px solid #27384b;padding:5px 6px;text-align:left;white-space:nowrap}
th{color:#8fa3b9;font-weight:600;cursor:default}
.ok{color:#2ecc71}.err{color:#e74c3b}.dim{color:#6d7f93}
#opmsg{color:#f39c12;font-size:13px;font-weight:400}
details{margin-top:4px}summary{cursor:pointer;color:#8fa3b9;font-size:12px;margin-bottom:4px}
#logs{font-family:Consolas,monospace;font-size:11px;color:#8fa3b9;max-height:180px;overflow:auto;white-space:pre-wrap}
</style></head><body><div class="wrap">
<h1>MAA-ALL <span class="sub">原样本: <b id="sample"></b></span> <span id="opmsg"></span></h1>
<div class="sec">
 <button onclick="op('update')">更新原样本</button>
 <button class="gray" onclick="op('refresh')">刷新</button>
 <label style="font-size:12px"><input type="checkbox" id="onlyfav" onchange="showfav=this.checked;refresh()"> 只看收藏</label>
</div>

<div class="card">
 <div class="sec"><b>子 MA 副本</b><span class="cnt" id="copycnt"></span>
  <button class="small gray" onclick="selAllCopies()">全选</button>
  <button class="small" onclick="op('open','selected')">启动勾选</button>
  <button class="small gray" onclick="op('close','selected')">关闭勾选</button>
  <button class="small gray" onclick="op('close','selected',1)">关闭+模拟器</button>
 </div>
 <div id="nocopies" class="dim" style="font-size:12px">还没有子 MA —— 在下方选模拟器新建，或勾选多台批量新建。</div>
 <table id="copytable" style="display:none">
  <thead><tr><th></th><th>ID</th><th>账号</th><th>模拟器</th><th>ADB</th><th>版本</th><th>状态</th><th>操作</th></tr></thead>
  <tbody id="copies"></tbody></table>
</div>

<div class="card">
 <div class="sec"><b>新建</b><span class="cnt">单台：</span>
  <input id="nname" placeholder="账号名（如 欧哈呦学妹）" style="width:170px">
  <select id="nemo"></select>
  <button onclick="op('new')">复制+填ADB+绑定</button>
 </div>
</div>

<div class="card">
 <div class="sec"><b>模拟器 (MuMu)</b><span class="cnt" id="emucnt"></span>
  <label style="font-size:12px">勾选 <b id="emusel">0</b> 台</label>
  <input id="bname" placeholder="账号前缀（批量）" style="width:130px">
  <button class="orange" onclick="op('newbatch')">批量新建</button>
 </div>
 <table><thead><tr><th style="width:24px"><input type="checkbox" id="ckall" onchange="ckAll(this)"></th>
  <th style="width:24px"></th><th style="cursor:pointer" onclick="sortBy('idx')">索引<span id="sidx"></span></th>
  <th style="cursor:pointer" onclick="sortBy('name')">名称<span id="sname"></span></th>
  <th>ADB</th><th>状态</th><th>操作</th></tr></thead>
  <tbody id="emus"></tbody></table>
</div>

<details><summary>操作日志</summary><div id="logs"></div></details>
</div>
<script>
let copies=[], emus=[], favs=[], showfav=false, emuSel=new Set(), sortKey='idx', sortDir=1;
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
fetchAPI = async (p, body) => {
  const r = await fetch(p, {method: body?'POST':'GET', headers:{'Content-Type':'application/json'},
    body: body?JSON.stringify(body):undefined});
  return r.json();
};
function sortBy(k){ if(sortKey===k){ sortDir=-sortDir; } else { sortKey=k; sortDir=1; } refresh(); }
function sortShow(arr){
  const a=[...arr];
  a.sort((x,y)=>{
    const f1=favs.includes(String(x.index))?0:1, f2=favs.includes(String(y.index))?0:1;
    if(f1!==f2) return f1-f2;
    let v=0;
    if(sortKey==='idx') v=(parseInt(x.index)||0)-(parseInt(y.index)||0);
    else v=String(x.name).localeCompare(String(y.name),'zh');
    return v*sortDir;
  });
  return a;
}
async function refresh(){
  const s = await fetchAPI('/api/state'); if(!s) return;
  document.getElementById('sample').textContent = s.sample_version||'(未初始化)';
  copies=s.copies; emus=s.emus; favs=s.favs||[];
  document.getElementById('onlyfav').checked=showfav;
  document.getElementById('opmsg').textContent = s.running_op||'';
  renderCopies(); renderEmus();
  const lg=document.getElementById('logs'); lg.textContent=(s.logs||[]).join('\n'); lg.scrollTop=lg.scrollHeight;
}
function renderCopies(){
  const table=document.getElementById('copytable'), tb=document.getElementById('copies'),
        no=document.getElementById('nocopies');
  document.getElementById('copycnt').textContent = copies.length?('共 '+copies.length+' 个'):'';
  table.style.display=copies.length?'':'none'; no.style.display=copies.length?'none':'';
  tb.innerHTML='';
  copies.forEach(c=>{
    const tr=document.createElement('tr');
    tr.innerHTML=`<td><input type="checkbox" class="ck" data-id="${esc(c.id)}"></td>
      <td class="dim">${esc(c.id)}</td><td>${esc(c.name)}</td>
      <td>${esc(c.emu_index)} ${esc(c.emu_name)}</td>
      <td><span class="${c.verified?'ok':'err'}" title="${esc((c.verify_msg||'')||'')}">${esc(c.adb)} ${c.verified?'✓':'✗'}</span></td>
      <td>${esc(c.version)}</td>
      <td class="${c.running?'ok':''}">${c.running?'运行中':'停止'}</td>
      <td><button class="small" onclick="op('open','${esc(c.id)}')">开</button>
      <button class="small gray" onclick="op('close','${esc(c.id)}')">关</button>
      <button class="small gray" onclick="op('bind','${esc(c.id)}')">换绑</button>
      <button class="small orange" onclick="op('sync','${esc(c.id)}')">更新</button></td>`;
    tb.appendChild(tr);
  });
}
async function op(kind, key, extra){
  const body={op:kind};
  if(kind==='open'||kind==='close'||kind==='sync'){ if(key==='selected') body.keys=selKeys(); else if(key&&key!=='all') body.keys=[key]; }
  if(kind==='new'){ body.name=document.getElementById('nname').value; body.emu=document.getElementById('nemo').value; }
  if(kind==='newbatch'){ body.keys=[...emuSel]; body.name=document.getElementById('bname').value; }
  if(kind==='bind'){ const t=prompt('输入模拟器索引或名称:'); if(!t)return; body.key=key; body.emu=t; }
  if(kind==='close'&&extra) body.kill_emu=true;
  await fetchAPI('/api/op', body);
  const iv=setInterval(async()=>{ const s=await fetchAPI('/api/state'); if(!s) return;
    document.getElementById('opmsg').textContent=s.running_op||'';
    if(!s.running_op){ clearInterval(iv); refresh(); } }, 1500);
}
function renderEmus(){
  const et=document.getElementById('emus'), sel=document.getElementById('nemo');
  const shown = sortShow(showfav ? emus.filter(e=>favs.includes(String(e.index))) : emus);
  document.getElementById('sidx').textContent = (sortKey==='idx')?(sortDir>0?'△':'▽'):'';
  document.getElementById('sname').textContent = (sortKey==='name')?(sortDir>0?'△':'▽'):'';
  document.getElementById('emucnt').textContent =
    shown.length+' 台'+(showfav?('（收藏 '+favs.length+' / 全部 '+emus.length+'）'):'');
  et.innerHTML=''; sel.innerHTML='';
  shown.forEach(e=>{
    const faved = favs.includes(String(e.index));
    const opt=document.createElement('option'); opt.value=e.index;
    opt.textContent=e.index+' '+e.name+(e.android?' (运行中)':''); sel.appendChild(opt);
    const st = e.android?'运行中':(e.proc?'启动中':'已关闭');
    const tr=document.createElement('tr');
    tr.innerHTML=`<td><input type="checkbox" class="emuCk" data-idx="${esc(e.index)}"${emuSel.has(String(e.index))?' checked':''} onchange="emuCkChanged()"></td>
      <td style="cursor:pointer" title="收藏" onclick="toggleFav('${esc(e.index)}')" class="${faved?'ok':'dim'}">${faved?'★':'☆'}</td>
      <td>${esc(e.index)}</td><td>${esc(e.name)}</td>
      <td><span class="${e.adb_up?'ok':'dim'}" title="端口可连=✓">${esc(e.adb_port)} ${e.adb_up?'✓':'✗'}</span></td>
      <td class="${e.android?'ok':''}">${st}</td>
      <td><button class="small" onclick="emuOp('${esc(e.index)}','launch')">启动</button>
      <button class="small gray" onclick="emuOp('${esc(e.index)}','shutdown')">停止</button></td>`;
    et.appendChild(tr);
  });
}
async function emuOp(i,a){ await fetchAPI('/api/op',{op:'emuop',index:i,action:a}); refresh(); }
async function toggleFav(i){ const faved=favs.includes(i);
  await fetchAPI('/api/op',{op:'fav',index:i,fav:!faved}); refresh(); }
function emuCkChanged(){ let n=0;
  document.querySelectorAll('.emuCk').forEach(c=>{ if(c.checked){emuSel.add(c.dataset.idx);n++;} else emuSel.delete(c.dataset.idx); });
  document.getElementById('emusel').textContent=n; }
function ckAll(box){ const t=[...document.querySelectorAll('.emuCk')];
  t.forEach(c=>{ c.checked=box.checked; if(box.checked) emuSel.add(c.dataset.idx); else emuSel.delete(c.dataset.idx); });
  document.getElementById('emusel').textContent=box.checked?t.length:0; }
function selAllCopies(){ const t=[...document.querySelectorAll('.ck')]; const all=t.every(c=>c.checked);
  t.forEach(c=>c.checked=!all); }
function selKeys(){ return [...document.querySelectorAll('.ck:checked')].map(x=>x.dataset.id); }
setInterval(refresh, 5000); refresh();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _auth_ok(self) -> bool:
        # 局域网使用场景：不做 token 鉴权（用户拍板 2026-08-28）
        return True

    def _send(self, code: int, obj: dict) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except Exception:
            pass

    def do_GET(self):
        if not self._auth_ok():
            self._send(401, {"error": "unauthorized"})
            return
        path = self.path.split("?", 1)[0]  # 忽略 query string（防缓存绕过参数等）
        if path in ("/", "/index.html"):
            data = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif self.path == "/api/state":
            self._send(200, {
                "sample_version": maa_version(SAMPLE_DIR) if SAMPLE_DIR.exists() else "",
                "copies": list_copies(),
                "emus": emu_list(),
                "favs": load_favs(),
                "logs": list(LOG_BUF)[-120:],
                "running_op": _OP_RUNNING["op"],
            })
        elif self.path == "/api/logs":
            self._send(200, {"logs": list(LOG_BUF)[-120:]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._auth_ok():
            self._send(401, {"error": "unauthorized"})
            return
        if self.path != "/api/op":
            self._send(404, {"error": "not found"})
            return
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0")) or 0) or b"{}")
        except Exception:
            body = {}
        self._send(200, {"ok": True})
        threading.Thread(target=_run_op, args=(body,), daemon=True).start()

    def log_message(self, *a):
        pass


def _run_op(body: dict) -> None:
    op = body.get("op", "")
    if not _OP_LOCK.acquire(blocking=False):
        _OP_RUNNING["msg"] = "已有操作执行中"
        return
    try:
        _OP_RUNNING.update({"op": op, "start": datetime.now().strftime("%H:%M:%S"), "msg": ""})
        if op == "refresh":
            _EMU_CACHE.update({"t": 0, "data": []})
            emu_list()
        elif op == "emuop":
            if body.get("action") == "launch":
                ok, msg = emu_launch(str(body.get("index")))
                log(f"模拟器 {body.get('index')} launch: {'✓' if ok else '✗ ' + msg}")
                if ok:
                    wait_android(str(body.get("index")), 60)
            else:
                emu_shutdown(str(body.get("index")))
                log(f"模拟器 {body.get('index')} shutdown")
        elif op == "fav":
            idx = str(body.get("index", ""))
            favs = load_favs()
            if body.get("fav"):
                if idx not in favs:
                    favs.append(idx)
            else:
                favs = [f for f in favs if f != idx]
            save_favs(favs)
            log(f"收藏: {idx} {'★' if body.get('fav') else '☆'} ({len(favs)} 台)")
        elif op == "update":
            update_sample()
        elif op == "new":
            emu = find_emu(body.get("emu", ""))
            if not emu:
                log(f"!! 找不到模拟器: {body.get('emu')}")
            else:
                new_copy(body.get("name", "未命名"), emu)
        elif op == "newbatch":
            keys = body.get("keys") or []
            base = body.get("name", "").strip() or ""
            if not keys:
                log("!! 未勾选模拟器")
                return
            done = 0
            for i, k in enumerate(keys):
                emu = find_emu(k)
                if not emu:
                    log(f"!! 找不到模拟器 {k}")
                    continue
                note = f"{base}-{i+1}" if base and len(keys) > 1 else (base or emu.get("name", ""))
                try:
                    new_copy(note, emu)
                    done += 1
                except Exception as e:
                    log(f"!! {k}({emu.get('name')}) 新建失败: {e}")
            log(f"批量新建完成: {done}/{len(keys)}")
        elif op == "open":
            keys = body.get("keys") or []
            for k in keys:
                d = find_copy(k)
                if not d:
                    log(f"!! 找不到副本: {k}")
                    continue
                try:
                    open_copy(d, no_emu=False)
                except Exception as e:
                    log(f"!! {k} 启动失败: {e}")
        elif op == "close":
            keys = body.get("keys") or []
            kill_emu = bool(body.get("kill_emu"))
            for k in keys:
                d = find_copy(k)
                if not d:
                    log(f"!! 找不到副本: {k}")
                    continue
                try:
                    close_copy(d, kill_emu)
                except Exception as e:
                    log(f"!! {k} 关闭失败: {e}")
        elif op == "sync":
            keys = body.get("keys") or []
            targets = [find_copy(k) for k in keys if k and k != "all"]
            if keys and keys == ["all"]:
                targets = [Path(p) for p in sorted(MAAS_DIR.iterdir()) if p.is_dir()]
            elif not keys:
                targets = [Path(p) for p in sorted(MAAS_DIR.iterdir()) if p.is_dir()]
            for d in targets:
                if not d:
                    continue
                try:
                    sync_copy(d)
                except Exception as e:
                    log(f"!! {d.name} 同步失败: {e}")
        elif op == "bind":
            d = find_copy(body.get("key", ""))
            emu = find_emu(body.get("emu", ""))
            if not d or not emu:
                log(f"!! bind 失败: 副本/模拟器不存在")
            else:
                ok, msg = fill_connect(d, emu)
                vok, vmsg = verify_connect(d, emu)
                bind = read_json(d / ".bind.json")
                bind.update({"emu_index": str(emu.get("index")), "emu_name": emu.get("name", ""),
                             "adb": str(emu.get("adb_port", "")), "verified": vok, "verify_msg": vmsg})
                write_json_atomic(d / ".bind.json", bind)
                log(f"bind {body.get('key')} -> {msg} ; {vmsg}")
        else:
            log(f"!! 未知操作: {op}")
    except Exception as e:
        log(f"!! {op} 异常: {e}")
    finally:
        _OP_RUNNING["op"] = None
        _OP_LOCK.release()


def serve(args) -> None:
    host = getattr(args, "host", "0.0.0.0")
    port = getattr(args, "port", 18999)
    global handler_args
    handler_args = {"token": ""}
    srv = ThreadingHTTPServer((host, port), Handler)
    log(f"GUI 已启动: http://{host}:{port}/  (局域网可用, 无鉴权)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


# ------------------------- CLI -------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="MAA-ALL 总管理器")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("update", help="更新原样本")
    sp = sub.add_parser("serve", help="启动 Web GUI")
    sp.add_argument("--host", default="0.0.0.0")
    sp.add_argument("--port", type=int, default=18999)
    sp.add_argument("--token", default="", help=argparse.SUPPRESS)  # 兼容旧命令, 已不生效
    sub.add_parser("list", help="列出副本/模拟器")
    sub.add_parser("emu", help="列出模拟器")
    sp = sub.add_parser("new", help="新建子 MA")
    sp.add_argument("name")
    sp.add_argument("--emu", required=True)
    sp = sub.add_parser("open", help="启动子 MA（+模拟器）")
    sp.add_argument("keys", nargs="+")
    sp.add_argument("--no-emu", action="store_true")
    sp = sub.add_parser("close", help="关闭子 MA")
    sp.add_argument("keys", nargs="+")
    sp.add_argument("--kill-emu", action="store_true")
    sp = sub.add_parser("bind", help="换绑模拟器")
    sp.add_argument("key")
    sp.add_argument("--emu", required=True)
    sp = sub.add_parser("sync-copies", help="批量同步子 MA 程序")
    sp.add_argument("keys", nargs="*")
    sp = sub.add_parser("adb", help="显示 adb connect 串")
    sp.add_argument("key")
    args = ap.parse_args()

    BASE.mkdir(parents=True, exist_ok=True)
    MAAS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    if args.cmd == "update":
        update_sample()
    elif args.cmd == "list":
        print(f"原样本: 版本={maa_version(SAMPLE_DIR) if SAMPLE_DIR.exists() else '(未初始化)'}")
        for r in list_copies():
            print(f"{r['id']} {r['name']:<14} v{r['version']} emu={r['emu_index']}({r['emu_name']}) "
                  f"adb={r['adb']} {'✓' if r['verified'] else '✗'} {'运行中' if r['running'] else '停止'}")
        print("\n模拟器:")
        for e in emu_list():
            print(f"  idx={e['index']:<4} {e['name']:<24} adb={e.get('adb_port', '-'):<9} "
                  f"安卓={'✓' if e.get('android') else '✗'} 进程={'✓' if e.get('proc') else '✗'}")
    elif args.cmd == "emu":
        if not emu_list():
            print("(找不到 MuMuManager 或无实例)")
        for e in emu_list():
            print(f"idx={e['index']:<4} {e['name']:<24} adb={e.get('adb_port', '-'):<9} "
                  f"安卓={'✓' if e.get('android') else '✗'} 进程={'✓' if e.get('proc') else '✗'}")
    elif args.cmd == "adb":
        d = find_copy(args.key)
        if not d:
            raise SystemExit(f"找不到副本: {args.key}")
        bind = read_json(d / ".bind.json")
        idx = bind.get("emu_index")
        e = find_emu(idx) if idx else None
        if not e:
            raise SystemExit(f"模拟器 {idx} 未找到（请先启动模拟器）")
        print(f"adb connect 127.0.0.1:{e.get('adb_port')}   连通={'✓' if e.get('adb_up') else '✗'}")
    elif args.cmd == "new":
        emu = find_emu(args.emu)
        if not emu:
            raise SystemExit(f"找不到模拟器: {args.emu}（--emu 给索引或实例名）")
        new_copy(args.name, emu)
    elif args.cmd == "open":
        for k in args.keys:
            d = find_copy(k)
            if not d:
                raise SystemExit(f"找不到副本: {k}")
            open_copy(d, args.no_emu)
    elif args.cmd == "close":
        for k in args.keys:
            d = find_copy(k)
            if not d:
                raise SystemExit(f"找不到副本: {k}")
            close_copy(d, args.kill_emu)
    elif args.cmd == "bind":
        d = find_copy(args.key)
        if not d:
            raise SystemExit(f"找不到副本: {args.key}")
        emu = find_emu(args.emu)
        if not emu:
            raise SystemExit(f"找不到模拟器: {args.emu}")
        ok, msg = fill_connect(d, emu)
        vok, vmsg = verify_connect(d, emu)
        bind = read_json(d / ".bind.json")
        bind.update({"emu_index": str(emu.get("index")), "emu_name": emu.get("name", ""),
                     "adb": str(emu.get("adb_port", "")), "verified": vok, "verify_msg": vmsg})
        write_json_atomic(d / ".bind.json", bind)
        print(msg, "|", vmsg)
    elif args.cmd == "sync-copies":
        targets = [find_copy(k) for k in args.keys] if args.keys else \
                  [Path(p) for p in sorted(MAAS_DIR.iterdir()) if p.is_dir()]
        for d in targets:
            if not d:
                print(f"!! 找不到: {k}")
                continue
            sync_copy(d)
    elif args.cmd == "serve":
        log("启动 GUI 服务...")
        serve(args)
    else:
        ap.print_help()


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")
    except Exception:
        pass
    try:
        main()
    except SystemExit as e:
        sys.exit(e.code if e.code else 0)
    except Exception as e:
        log(f"!! {e}")
        sys.exit(1)
