"""模拟器：列表/单个与批量启停。复用 task_constants / emu_service 纯函数。"""
from __future__ import annotations
import time
from fastapi import APIRouter, HTTPException

from .config import setup_sys_path, get_config
from .oplog import log as oplog

setup_sys_path()  # 把 MAAOrch 项目根加入 sys.path

from infrastructure.task_constants import (  # noqa: E402
    detect_emu_instances, detect_emu_adb, find_mumu_cli, find_adb, cli_flag,
)
from services.emu_service import graceful_shutdown, _headless_alive  # noqa: E402

router = APIRouter()


def _cli() -> str:
    cli = find_mumu_cli()
    if not cli:
        raise HTTPException(status_code=500, detail="未找到 MuMuManager/mumu-cli")
    return cli


def emulators_snapshot() -> list[dict]:
    """模拟器列表快照（含实时端口）。"""
    out = []
    for e in detect_emu_instances():
        out.append({
            "index": str(e.get("index", "")),
            "name": e.get("name", ""),
            "running": bool(e.get("running")),
            "adb_port": str(e.get("adb_port", "")),
        })
    return out


@router.get("/api/emulators")
def api_emulators():
    return {"ok": True, "emulators": emulators_snapshot()}


def start_one(index: str) -> dict:
    cli = _cli()
    flag = cli_flag(cli)
    import subprocess
    r = subprocess.run([cli, "control", flag, str(index), "launch"],
                       capture_output=True, timeout=15,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    if r.returncode != 0:
        return {"ok": False, "error": f"launch 失败: {r.stderr[:200]}"}
    # 等就绪（进程启动 + Android 启动）
    dl = time.time() + 120
    ready = False
    while time.time() < dl:
        info = _emu_info(cli, flag, index)
        if info and (info.get("is_process_started") and info.get("is_android_started")):
            ready = True
            break
        time.sleep(2)
    return {"ok": True, "ready": ready}


def _emu_info(cli: str, flag: str, index: str) -> dict:
    import json, subprocess
    try:
        r = subprocess.run([cli, "info", flag, str(index)],
                           capture_output=True, text=True, timeout=8,
                           creationflags=subprocess.CREATE_NO_WINDOW,
                           encoding="utf-8", errors="replace")
        if r.returncode == 0 and r.stdout.strip():
            return json.loads(r.stdout.lstrip("\ufeff").strip())
    except Exception:
        pass
    return {}


@router.post("/api/emulators/start")
def api_emulators_start(body: dict):
    indexes = [str(x) for x in (body.get("indexes") or [])]
    if not indexes:
        raise HTTPException(status_code=400, detail="indexes 不能为空")
    results = {}
    for idx in indexes:
        r = start_one(idx)
        results[idx] = r
        oplog("开启模拟器", f"#{idx}", "ok" if r["ok"] else "fail", r.get("error", ""))
    return {"ok": True, "results": results}


@router.post("/api/emulators/stop")
def api_emulators_stop(body: dict):
    indexes = [str(x) for x in (body.get("indexes") or [])]
    if not indexes:
        raise HTTPException(status_code=400, detail="indexes 不能为空")
    cli = _cli()
    results = {}
    for idx in indexes:
        # 优雅关闭（复用踩坑后的关闭链，绝不碰 ADB）
        try:
            graceful_shutdown(cli, idx, wait=60,
                              log=lambda m: oplog("关闭模拟器", f"#{idx}", "log", m))
            results[idx] = {"ok": True}
            oplog("关闭模拟器", f"#{idx}", "ok")
        except Exception as ex:
            results[idx] = {"ok": False, "error": str(ex)}
            oplog("关闭模拟器", f"#{idx}", "fail", str(ex))
    return {"ok": True, "results": results}


def emu_running(index: str) -> bool:
    """psutil 真实进程检测（比 MuMuManager info 快且准）。"""
    return _headless_alive(index)


def detect_emu_port(index: str) -> str:
    """实时检测模拟器 ADB 端口（工具自研，规避 detect_emu_adb 的标准路径盲区）。

    find_mumu_cli 有盘符扫描能找到任意位置安装的 MuMuManager；此处用
    bytes 模式读取输出再解码，规避 text 模式 reader 线程的编码崩溃。
    返回 "127.0.0.1:<port>" 或 ""。
    """
    import json as _json
    import subprocess as _sp
    cli = find_mumu_cli()
    if not cli:
        return ""
    try:
        r = _sp.run([cli, "info", cli_flag(cli), str(index)],
                    capture_output=True, timeout=8,
                    creationflags=_sp.CREATE_NO_WINDOW)
        if r.returncode != 0:
            return ""
        d = _json.loads(r.stdout.lstrip(b"\xef\xbb\xbf").decode("utf-8", errors="replace"))
        port = d.get("adb_port")
        if port:
            return f"127.0.0.1:{port}"
    except Exception:
        pass
    return ""
