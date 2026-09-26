"""MAA 管理：副本池(带版本) + 配置存档 + 打开/关闭 + 窗口小窗/还原 + 日志剪切。

副本是临时载体（停车位），配置是资产（data/configs/<账号>.json）。
打开 = 取空闲副本写配置 → 跑；关闭 = 配置存回存档 + 日志剪切 → 副本释放回池。
"""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException

from .config import setup_sys_path, get_config, INSTANCES_DIR, data_file
from .oplog import log as oplog
from .account_api import (
    get_account, load_account_config, save_account_config,
)

setup_sys_path()

from infrastructure.task_constants import (  # noqa: E402
    find_mumu_cli, find_adb, cli_flag,
)
from services.instance_pool import _create_instance  # noqa: E402
from .emu_api import detect_emu_port  # noqa: E402

router = APIRouter()

_POOL_LOCK = threading.Lock()
# 副本目录 → 正在同步的账号 id（运行期 30s 同步线程）
_RUNNING: dict[str, dict] = {}  # inst_name -> {"aid":..., "started":ts}
_RUN_LOCK = threading.Lock()


def _maa_source() -> Path:
    cfg = get_config()
    src = cfg.get("maa_source_path") or ""
    p = Path(src) if src else Path()
    if not p.exists() or not (p / "MAA.exe").exists():
        raise HTTPException(status_code=500,
                            detail=f"MAA source 不存在或未初始化: {p}")
    return p


def _pool_size() -> int:
    return int(get_config().get("pool_size", 4) or 4)


def _adb_bin() -> str:
    adb = find_adb()
    return adb or ""


def _inst_dir(name: str) -> Path:
    return INSTANCES_DIR / name


def _meta_of(inst: Path) -> dict:
    """读副本 .meta（工具私有格式: aid|name|emu_index）。"""
    mp = inst / ".meta"
    try:
        if mp.exists():
            aid, name, emu = mp.read_text(encoding="utf-8").split("|", 2)
            return {"aid": aid, "name": name, "emu_index": emu}
    except Exception:
        pass
    return {}


def _pid_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        import psutil
        p = psutil.Process(pid)
        return p.is_running() and p.name().lower() == "maa.exe"
    except Exception:
        return False


def _inst_free(inst: Path) -> bool:
    """空闲 = 无活 .pid 且 .meta 无运行中标记。"""
    pid = 0
    try:
        pid = int((inst / ".pid").read_text().strip())
    except Exception:
        pass
    return not _pid_alive(pid)


def _instances_snapshot() -> list[dict]:
    out = []
    for d in sorted(INSTANCES_DIR.iterdir()):
        if not d.is_dir() or not (d / "MAA.exe").exists():
            continue
        meta = _meta_of(d)
        pid = 0
        try:
            pid = int((d / ".pid").read_text().strip())
        except Exception:
            pass
        out.append({
            "name": d.name,
            "occupied": bool(meta.get("aid")) and _pid_alive(pid),
            "account_id": meta.get("aid", ""),
            "account_name": meta.get("name", ""),
            "emu_index": meta.get("emu_index", ""),
            "pid": pid,
        })
    return out


def _acquire_instance(aid: str, name: str, emu_index: str) -> Path:
    """取一个空闲副本；没有则建新的（到池上限为止）。"""
    with _POOL_LOCK:
        src = _maa_source()
        # 1) 已有空闲
        for n in range(1, _pool_size() + 1):
            inst = _inst_dir(str(n))
            if inst.exists() and (inst / "MAA.exe").exists() and _inst_free(inst):
                return inst
        # 2) 未满则新建
        for n in range(1, _pool_size() + 1):
            inst = _inst_dir(str(n))
            if not (inst / "MAA.exe").exists():
                if _create_instance(inst, src):
                    return inst
        raise HTTPException(status_code=503, detail="副本池已满，请先关闭其他 MAA")


def _write_meta(inst: Path, aid: str, name: str, emu_index: str) -> None:
    (inst / ".meta").write_text(f"{aid}|{name}|{emu_index}", encoding="utf-8")


def _clear_meta(inst: Path) -> None:
    for f in (".meta", ".pid"):
        try:
            (inst / f).unlink(missing_ok=True)
        except Exception:
            pass


def _inject_connection(cfg: dict, port: str, adb_path: str, emu_index: str) -> str:
    """把实时端口注入到配置（取 Current 指向的配置，没有则取 Default）。"""
    confs = cfg.setdefault("Configurations", {})
    current = cfg.get("Current") or "Default"
    if current not in confs:
        current = "Default"
        cfg["Current"] = current
    confs.setdefault(current, {})
    gui = confs[current].setdefault("Gui", {})
    gui["GuideStep"] = 7  # 跳过首启向导
    cs = gui.setdefault("ConnectSettings", {})
    cs.update({
        "AutoDetect": False,
        "AlwaysAutoDetect": False,
        "AdbReplaced": True,
        "AllowAdbRestart": False,
        "AllowAdbHardRestart": False,
        "EnableAdbLite": False,
        "KillAdbOnExit": False,
        "Config": "MuMuEmulator12",
        "TouchMode": "MiniTouch",
    })
    if port:
        cs["Address"] = port
    if adb_path:
        cs["AdbPath"] = adb_path
    if str(emu_index).isdigit():
        extras = cs.setdefault("Extras", {}).setdefault("MuMuEmulator12", {})
        extras["InstanceIndex"] = int(emu_index)
        extras["IsEnabled"] = False
        cli = find_mumu_cli()
        if cli:
            extras["EmulatorPath"] = str(cli)
    # StartupSettings: 不自动跑任务，等用户手动点开始
    ss = gui.setdefault("StartUpSettings", {})
    ss.update({
        "RunDirectly": False,
        "SkipStartupAutoRunAfterUpdate": False,
        "StartEmulator": False,
    })
    gui["PostActions"] = "None"
    # 禁用公告弹窗（公告窗口标题也含 MAA，会干扰小窗按钮定位主窗口）
    ann = cfg.setdefault("AnnouncementInfo", {})
    ann["DoNotShowAgain"] = True
    ann["DoNotShow"] = True
    return current


def _write_gui_new(inst: Path, cfg: dict) -> None:
    gj = inst / "config" / "gui.new.json"
    gj.parent.mkdir(parents=True, exist_ok=True)
    tmp = gj.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(gj)
    # 删 .bak 防 MAA 回退旧地址
    for b in (inst / "config").glob("gui.new.json.bak*"):
        try:
            b.unlink()
        except Exception:
            pass


def _read_gui_new(inst: Path) -> dict:
    gj = inst / "config" / "gui.new.json"
    try:
        return json.loads(gj.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ---------- 打开/关闭 MAA ----------

@router.post("/api/maa/open")
def api_maa_open(body: dict):
    aid = str(body.get("account_id", "") or "")
    mode = str(body.get("mode", "normal") or "normal")  # normal=小窗 | config=配置(不最小化)
    acc = get_account(aid)
    if not acc:
        raise HTTPException(status_code=404, detail=f"账号不存在: {aid}")
    emu_index = str(acc.get("emu_index", "") or "")
    if not emu_index:
        raise HTTPException(status_code=400, detail=f"账号 {aid} 未绑定模拟器")
    # 1. 实时端口
    port = detect_emu_port(emu_index)
    if not port:
        raise HTTPException(status_code=400,
                            detail=f"模拟器 #{emu_index} 未运行或无法检测端口，请先开启模拟器")
    # 2. 已有 MAA 在跑则拒绝
    for it in _instances_snapshot():
        if it["account_id"] == aid and it["occupied"]:
            return {"ok": True, "already": True, "instance": it["name"]}
    # 3. 取空闲副本
    inst = _acquire_instance(aid, acc.get("name", aid), emu_index)
    # 4. 配置：存档 or 模板初始化
    cfg = load_account_config(aid)
    if cfg is None:
        src = _maa_source()
        tpl = _read_gui_new(src)
        if not tpl:
            # 用副本自带的 config（_create_instance 已复制 source config）
            tpl = _read_gui_new(inst)
        cfg = tpl or {"Configurations": {"Default": {}}, "Current": "Default"}
    cur = _inject_connection(cfg, port, _adb_bin(), emu_index)
    _write_gui_new(inst, cfg)
    # 5. 启动 MAA（cwd 用 resolve 后的真实路径，junction 坑）
    real_dir = str(inst.resolve())
    env = dict(os.environ)
    inst_n = inst.name
    env["ANDROID_ADB_SERVER_PORT"] = str(5037 + int(inst_n) if inst_n.isdigit() else 5037)
    try:
        proc = subprocess.Popen([str(inst / "MAA.exe")], shell=False,
                                cwd=real_dir, env=env)
    except Exception as ex:
        _clear_meta(inst)
        raise HTTPException(status_code=500, detail=f"启动 MAA 失败: {ex}")
    _write_meta(inst, aid, acc.get("name", aid), emu_index)
    (inst / ".pid").write_text(str(proc.pid))
    # 6. normal 模式 → 默认最小化（小窗）；config 模式 → 保持窗口让用户配置
    if mode == "config":
        oplog("MAA配置", aid, "ok", f"副本{inst.name} 打开配置模式")
    else:
        threading.Thread(target=_minimize_later,
                         args=(proc.pid, aid, inst.name), daemon=True).start()
    # 7. 运行期配置同步线程（30s）
    with _RUN_LOCK:
        _RUNNING[inst.name] = {"aid": aid, "started": time.time()}
    threading.Thread(target=_sync_loop, args=(inst.name, aid), daemon=True).start()
    oplog("打开MAA", aid, "ok", f"副本{inst.name} 端口{port} 配置[{cur}] mode={mode}")
    return {"ok": True, "instance": inst.name, "pid": proc.pid, "port": port, "config": cur, "mode": mode}


def _minimize_later(pid: int, aid: str, inst_name: str) -> None:
    """启动后等主窗口出现并最小化（小窗）。"""
    for _ in range(60):
        time.sleep(1)
        if _find_main_hwnd(pid) is not None:
            _set_window_state(pid, minimize=True)
            oplog("MAA小窗", aid, "ok", f"副本{inst_name} 已最小化")
            return
    oplog("MAA小窗", aid, "warn", "未找到主窗口，跳过最小化")


def _sync_loop(inst_name: str, aid: str) -> None:
    """运行期每 30s 把副本 gui.new.json 同步到账号存档。"""
    interval = int(get_config().get("config_sync_interval", 30) or 30)
    inst = _inst_dir(inst_name)
    while True:
        time.sleep(interval)
        with _RUN_LOCK:
            if inst_name not in _RUNNING:
                return
        try:
            cfg = _read_gui_new(inst)
            if cfg:
                save_account_config(aid, cfg)
        except Exception:
            pass


@router.post("/api/maa/close")
def api_maa_close(body: dict):
    aid = str(body.get("account_id", "") or "")
    inst = _find_instance_by_account(aid)
    if inst is None:
        return {"ok": True, "already_closed": True}
    # 1. 优雅关闭 WM_CLOSE → 超时 taskkill 兜底
    _close_maa_process(inst)
    # 2. 配置最终同步回存档
    cfg = _read_gui_new(inst)
    if cfg:
        save_account_config(aid, cfg)
    # 3. 日志剪切归档
    _archive_logs(inst, aid)
    # 4. 副本释放回池
    _clear_meta(inst)
    with _RUN_LOCK:
        _RUNNING.pop(inst.name, None)
    oplog("关闭MAA", aid, "ok", f"副本{inst.name} 配置已存档 日志已归档")
    return {"ok": True, "instance": inst.name}


@router.post("/api/maa/save_config")
def api_maa_save_config(body: dict):
    """保存配置：把当前 MAA 副本的配置存回账号存档，然后关闭 MAA。

    与 close 的区别：close 是「关 MAA」的常规操作（也存配置）；
    save_config 是「配置 MAA」流程的显式保存动作，用户配置完点它，
    工具复制配置文件到存档并关闭 MAA（AUTO-MAS 同款流程）。
    """
    aid = str(body.get("account_id", "") or "")
    inst = _find_instance_by_account(aid)
    if inst is None:
        return {"ok": False, "error": "该账号没有打开的 MAA（请先点「配置 MAA」）"}
    # 配置存回存档
    cfg = _read_gui_new(inst)
    if cfg:
        save_account_config(aid, cfg)
    # 关闭 MAA（复用 close：优雅关闭 + 日志归档 + 释放副本）
    _close_maa_process(inst)
    _archive_logs(inst, aid)
    _clear_meta(inst)
    with _RUN_LOCK:
        _RUNNING.pop(inst.name, None)
    oplog("保存配置", aid, "ok", f"副本{inst.name} 配置已存档")
    return {"ok": True, "instance": inst.name}


def _find_instance_by_account(aid: str) -> Path | None:
    for it in _instances_snapshot():
        if it["account_id"] == aid:
            return _inst_dir(it["name"])
    return None


def _close_maa_process(inst: Path) -> None:
    pid = 0
    try:
        pid = int((inst / ".pid").read_text().strip())
    except Exception:
        pass
    if pid and _pid_alive(pid):
        _graceful_close(pid)
        # 等退出
        for _ in range(30):
            if not _pid_alive(pid):
                return
            time.sleep(1)
        # 兜底强杀
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, timeout=10,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception:
            pass


def _graceful_close(pid: int) -> None:
    """发 WM_CLOSE（复用 runner._graceful_close 的 HWND 枚举写法）。"""
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        found = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _cb(hwnd, _lp):
            p = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
            if p.value == pid and user32.IsWindowVisible(hwnd):
                found.append(hwnd)
                return False
            return True

        user32.EnumWindows(_cb, 0)
        if found:
            user32.PostMessageW(found[0], 0x0010, 0, 0)  # WM_CLOSE
    except Exception:
        pass


def _archive_logs(inst: Path, aid: str) -> None:
    """把副本 debug/ 下日志剪切到 data/logs/<账号>/<时间戳>/（失败则复制）。"""
    src_debug = inst / "debug"
    if not src_debug.is_dir():
        return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = data_file("logs", aid, ts)
    dst.mkdir(parents=True, exist_ok=True)
    for f in src_debug.iterdir():
        if f.is_file() and f.suffix.lower() in (".log", ".txt", ".json"):
            try:
                shutil.move(str(f), str(dst / f.name))
            except Exception:
                try:
                    shutil.copy2(str(f), str(dst / f.name))
                except Exception:
                    pass


# ---------- 窗口小窗/还原 ----------

def _find_main_hwnd(pid: int):
    """按 PID 找 MAA 主窗口（EnumWindows）。

    不能过滤 IsWindowVisible —— MAA 最小化到托盘/隐藏时主窗口不可见
    （IsVisible=False），过滤掉就永远找不到 → 小窗按钮还原不了
    （2026-08-18 实测：最小化后 IsVisible=False, IsIconic=True）。
    按标题特征（MAA / v6.）找主窗口，找不到再退化为第一个可见窗口。
    """
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        all_windows = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _cb(hwnd, _lp):
            p = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
            if p.value == pid:
                ln = user32.GetWindowTextLengthW(hwnd)
                if ln and ln < 300:
                    buf = ctypes.create_unicode_buffer(ln + 1)
                    user32.GetWindowTextW(hwnd, buf, ln + 1)
                    title = buf.value
                    all_windows.append((hwnd, title, bool(user32.IsWindowVisible(hwnd))))
            return True

        user32.EnumWindows(_cb, 0)
        if not all_windows:
            return None
        # 主窗口特征：标题含版本号（v6.）+ 模拟器连接信息
        # （MAA 主窗口形如 "MAA - v6.16.8#... - ... - MuMu 模拟器 (端口) - 官服"；
        #   公告/设置等子窗口标题无此特征，2026-08-18 用户：小窗按钮控制到了公告窗口）
        for hwnd, title, _vis in all_windows:
            t = title.lower()
            if ("v6." in t or "v5." in t) and ("模拟器" in title or "mumu" in t):
                return hwnd
        # 退化：标题含"模拟器"或版本号的窗口
        for hwnd, title, _vis in all_windows:
            t = title.lower()
            if "模拟器" in title or "mumu" in t or "v6." in t or "v5." in t:
                return hwnd
        # 再退化：标题含 MAA 且不是公告/设置类
        for hwnd, title, _vis in all_windows:
            t = title.lower()
            if ("maa" in t and "公告" not in title
                    and "announce" not in t and "设置" not in title):
                return hwnd
        # 最后：第一个可见窗口
        for hwnd, _t, vis in all_windows:
            if vis:
                return hwnd
        return all_windows[0][0]
    except Exception:
        return None


def _is_minimized(hwnd) -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.user32.IsIconic(hwnd))
    except Exception:
        return False


def _is_visible(hwnd) -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.user32.IsWindowVisible(hwnd))
    except Exception:
        return False


def _set_window_state(pid: int, minimize: bool) -> bool:
    hwnd = _find_main_hwnd(pid)
    if hwnd is None:
        return False
    try:
        import ctypes
        user32 = ctypes.windll.user32
        if minimize:
            user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
        else:
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            # 托盘最小化后窗口可能仍隐藏 → 再 Show + 置前（SW_SHOWNORMAL 兜底）
            user32.ShowWindow(hwnd, 5)  # SW_SHOW
            user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


@router.post("/api/maa/window")
def api_maa_window(body: dict):
    aid = str(body.get("account_id", "") or "")
    action = str(body.get("action", "toggle") or "toggle")
    inst = _find_instance_by_account(aid)
    if inst is None:
        raise HTTPException(status_code=404, detail="该账号 MAA 未在运行")
    pid = 0
    try:
        pid = int((inst / ".pid").read_text().strip())
    except Exception:
        pass
    if not pid or not _pid_alive(pid):
        raise HTTPException(status_code=404, detail="MAA 进程不存在")
    if action == "toggle":
        hwnd = _find_main_hwnd(pid)
        if hwnd is None:
            return {"ok": False, "error": "未找到 MAA 窗口"}
        # 不可见（托盘隐藏）或已最小化 → 还原；可见且非最小化 → 最小化
        hidden = not _is_visible(hwnd)
        minimized = _is_minimized(hwnd)
        minimize = not (hidden or minimized)
        ok = _set_window_state(pid, minimize)
        oplog("MAA窗口", aid, "ok", "最小化" if minimize else "还原")
        return {"ok": ok, "minimized": minimize}
    if action == "minimize":
        ok = _set_window_state(pid, True)
        return {"ok": ok, "minimized": True}
    if action == "restore":
        ok = _set_window_state(pid, False)
        return {"ok": ok, "minimized": False}
    raise HTTPException(status_code=400, detail="未知 action")


@router.get("/api/maa/configs")
def api_maa_configs(account_id: str = ""):
    """该账号存档里的配置名列表。"""
    if not account_id:
        raise HTTPException(status_code=400, detail="account_id 必填")
    cfg = load_account_config(account_id)
    if not cfg:
        return {"ok": True, "configs": [], "current": ""}
    confs = cfg.get("Configurations", {})
    return {"ok": True, "configs": list(confs.keys()), "current": cfg.get("Current", "")}


@router.post("/api/maa/config")
def api_maa_set_config(body: dict):
    """固定某账号存档用哪份配置（Current）。"""
    aid = str(body.get("account_id", "") or "")
    config = str(body.get("config", "") or "")
    cfg = load_account_config(aid)
    if cfg is None:
        cfg = {"Configurations": {"Default": {}}, "Current": "Default"}
    confs = cfg.setdefault("Configurations", {})
    if config not in confs:
        raise HTTPException(status_code=400, detail=f"配置不存在: {config}")
    cfg["Current"] = config
    save_account_config(aid, cfg)
    oplog("选择配置", aid, "ok", config)
    return {"ok": True, "config": config}


@router.get("/api/maa/instances")
def api_maa_instances():
    return {"ok": True, "instances": _instances_snapshot()}
