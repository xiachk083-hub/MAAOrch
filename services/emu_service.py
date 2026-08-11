"""模拟器操作单点 — 2026-08-11 架构计划 P1。

收敛所有 MuMuManager/adb 操作（优雅关闭/回收关闭/实时端口/bool 防御/
锁/冷却），消除散落多处实现的行为不一致（二次关闭/端口漂移/误判崩溃
— 2026-08-10~11 连环事故根因）。纯标准库，无 Qt 依赖。

模块级状态（跨调用共享）：
- _locks / _recently_closed：关闭互斥与冷却（防并发二次关闭）
- _system_started：MAAOrch 拉起的模拟器（回收只关这些；用户手动开的保留）
"""
from __future__ import annotations
import json
import os
import subprocess
import threading
import time

_glock = threading.Lock()
_locks: dict = {}
_recently_closed: dict = {}  # emu_idx -> ts（关闭冷却 5 分钟）
_system_started: dict = {}   # emu_idx -> ts（系统拉起标记）
_CF = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def is_alive(d: dict) -> bool | None:
    """MuMuManager info 状态三态判定 — bool 防御（2026-08-10 误判崩溃根因）：
    key 缺失/非 bool（errcode≠0 错误返回）→ None（无法确认，保守跳过）。"""
    a = d.get("is_android_started")
    p = d.get("is_process_started")
    if isinstance(a, bool) and isinstance(p, bool):
        return a or p
    return None


def get_adb_port(cli: str, emu_idx, flag: str = "-v") -> str:
    """实时 ADB 端口（MuMuManager info 真值，缓存 adb_address 重启后会漂移）。"""
    try:
        r = subprocess.run([cli, "info", flag, str(emu_idx)],
                           capture_output=True, text=True, timeout=5,
                           creationflags=_CF, encoding="utf-8", errors="replace")
        if r.returncode == 0:
            d = json.loads(r.stdout.lstrip("\ufeff").strip())
            port = d.get("adb_port")
            if port:
                return f"127.0.0.1:{port}"
    except Exception:
        pass
    return ""


def mark_closed(emu_idx) -> None:
    with _glock:
        _recently_closed[str(emu_idx)] = time.time()


def recently_closed(emu_idx, within: float = 300.0) -> bool:
    with _glock:
        _rc = _recently_closed.get(str(emu_idx), 0)
    return bool(_rc and time.time() - _rc < within)


def lock_busy(emu_idx) -> bool:
    """该模拟器是否有关闭进行中（优雅/直接关闭锁被持有）。"""
    with _glock:
        _lk = _locks.get(str(emu_idx))
    return _lk is not None and _lk.locked()


def mark_system_started(emu_idx) -> None:
    """记录 MAAOrch 拉起的模拟器（回收只关这些；用户在 MuMu 管理器手动
    开的没有记录 → 永不回收 — 2026-08-11 用户: 手动启动的模拟器也被关掉）。"""
    try:
        _system_started[str(emu_idx)] = time.time()
    except Exception:
        pass


def is_system_started(emu_idx) -> bool:
    return str(emu_idx) in _system_started


def direct_shutdown(cli: str, emu_idx, flag: str = "-v", log=None) -> None:
    """直接关闭（闲置无游戏场景）— MuMuManager shutdown 是正常关闭，
    无游戏在跑不会弹崩溃报告。与优雅关闭共用锁（防并发二次关闭）。"""
    with _glock:
        _lk = _locks.setdefault(str(emu_idx), threading.Lock())
    if not _lk.acquire(blocking=False):
        if log:
            log(f"[关闭] 模拟器#{emu_idx} 已有关闭进行中，跳过")
        return
    try:
        try:
            subprocess.run([cli, "control", flag, str(emu_idx), "shutdown"],
                           capture_output=True, timeout=15, creationflags=_CF)
            if log:
                log(f"[关闭] 模拟器#{emu_idx} shutdown 完成（闲置无游戏，直接关）")
        except Exception as ex:
            if log:
                log(f"[关闭] 模拟器#{emu_idx} shutdown 失败: {ex}")
    finally:
        mark_closed(emu_idx)
        _lk.release()


def graceful_shutdown(cli: str, emu_idx, adb_path: str = "", addr: str = "",
                      wait: int = 90, log=None) -> bool:
    """统一优雅关闭（唯一正常退出方式）— 2026-08-10 用户指出:
    直接 MuMuManager shutdown 是"错误退出"→ VMM 残留。正常方式:
      1. adb connect + reboot -p（Android 内优雅关机）
      2. 轮询等待完全退出（最多 wait 秒；多开负载 boot 60s+，30s 必触发兜底）
      3. 等待中重发 reboot（信号可能丢失）
      4. shutdown 兜底（仅当 Android 仍在=关机失败；兜底后等进程退出）
    防并发重入：按模拟器非阻塞锁，已有关闭进行中则跳过。
    """
    flag = "-v" if "MuMuManager" in cli else "--vmindex"
    with _glock:
        _lk = _locks.setdefault(str(emu_idx), threading.Lock())
    if not _lk.acquire(blocking=False):
        if log:
            log(f"[优雅关闭] 模拟器#{emu_idx} 已有关闭进行中，跳过")
        return True
    try:
        _body(cli, emu_idx, adb_path, addr, wait, flag, log)
    finally:
        mark_closed(emu_idx)
        _lk.release()
    return True


def _body(cli: str, emu_idx, adb_path: str, addr: str, wait: int, flag: str, log) -> None:
    _t0 = time.time()
    if log:
        log(f"[优雅关闭] 模拟器#{emu_idx} 开始 (adb={'有' if addr and adb_path else '无'}, wait={wait}s)")
    # 实时端口优先（缓存 adb_address 重启后漂移 → reboot 未送达 → 兜底强关弹窗）
    if adb_path:
        live = get_adb_port(cli, emu_idx, flag)
        if live:
            addr = live
            if log:
                log(f"[优雅关闭] 模拟器#{emu_idx} 实时 ADB 端口 {addr}")
    if addr and adb_path:
        try:
            # 先 connect（闲置模拟器可能不在 adb server 设备列表）
            subprocess.run([adb_path, "connect", addr],
                           capture_output=True, timeout=10, creationflags=_CF)
            time.sleep(1)
            r = subprocess.run([adb_path, "-s", addr, "shell", "reboot", "-p"],
                               capture_output=True, timeout=10, creationflags=_CF)
            if r.returncode != 0 and log:
                log(f"[优雅关闭] 模拟器#{emu_idx} reboot 返回 {r.returncode}: {r.stderr[:80]}")
            if log:
                log(f"[优雅关闭] 模拟器#{emu_idx} adb reboot -p 已发送")
        except Exception as ex:
            if log:
                log(f"[优雅关闭] 模拟器#{emu_idx} adb reboot 失败: {ex}")
    dl = time.time() + wait
    _last_reboot = _t0
    _reboot_count = 1
    _prev_st = None        # 上一次状态（检测"动静"=状态变化）
    _last_state_change = _t0  # 状态最后一次变化时间
    while time.time() < dl:
        try:
            r = subprocess.run([cli, "info", flag, str(emu_idx)],
                               capture_output=True, text=True, timeout=5,
                               creationflags=_CF, encoding="utf-8", errors="replace")
            if r.returncode == 0:
                d = json.loads(r.stdout.lstrip("\ufeff").strip())
                st = is_alive(d)
                if st is not None:
                    if st != _prev_st:
                        # 状态变化 = 有动静（Android 开始关机/进程开始退出）
                        _prev_st = st
                        _last_state_change = time.time()
                        if log:
                            log(f"[优雅关闭] 模拟器#{emu_idx} 状态变化 → {st}")
                        if st is False:
                            if log:
                                log(f"[优雅关闭] 模拟器#{emu_idx} 已完全退出 ({int(time.time()-_t0)}s)")
                            return True
                    elif st is True:
                        # 重发：20s 无动静（状态一直没变）→ 再发一次 reboot
                        if time.time() - _last_state_change > 20 and _reboot_count < 2:
                            _reboot_count += 1
                            _last_reboot = time.time()
                            if addr and adb_path:
                                try:
                                    subprocess.run([adb_path, "-s", addr, "shell", "reboot", "-p"],
                                                   capture_output=True, timeout=10, creationflags=_CF)
                                    if log:
                                        log(f"[优雅关闭] 模拟器#{emu_idx} 20s 无动静，重发 reboot (2/2)")
                                except Exception:
                                    pass
                        # 降级：40s 无动静（重发后仍无响应）→ MuMuManager shutdown
                        if time.time() - _last_state_change > 40:
                            if log:
                                log(f"[优雅关闭] 模拟器#{emu_idx} 40s 状态无变化（Android 无响应），降级 shutdown")
                            break
        except Exception:
            pass
        time.sleep(2)
    # 超时后确认：Android 已关仅进程收尾 → 再等 30s（不强制）；仅 Android 仍开走兜底
    try:
        r = subprocess.run([cli, "info", flag, str(emu_idx)],
                           capture_output=True, text=True, timeout=5,
                           creationflags=_CF, encoding="utf-8", errors="replace")
        if r.returncode == 0:
            d = json.loads(r.stdout.lstrip("\ufeff").strip())
            _pa = d.get("is_android_started")
            _pp = d.get("is_process_started")
            if isinstance(_pa, bool) and isinstance(_pp, bool) and not _pa and _pp:
                if log:
                    log(f"[优雅关闭] 模拟器#{emu_idx} Android 已关机，等进程收尾 (最多 30s)")
                _dl2 = time.time() + 30
                while time.time() < _dl2:
                    time.sleep(2)
                    try:
                        r2 = subprocess.run([cli, "info", flag, str(emu_idx)],
                                            capture_output=True, text=True, timeout=5,
                                            creationflags=_CF, encoding="utf-8", errors="replace")
                        if r2.returncode == 0:
                            d2 = json.loads(r2.stdout.lstrip("\ufeff").strip())
                            if isinstance(d2.get("is_process_started"), bool) and not d2.get("is_process_started"):
                                if log:
                                    log(f"[优雅关闭] 模拟器#{emu_idx} 进程已退出 ({int(time.time()-_t0)}s)")
                                return True
                    except Exception:
                        pass
                if log:
                    log(f"[优雅关闭] 模拟器#{emu_idx} 进程 30s 未收尾，shutdown 兜底")
            else:
                if log:
                    log(f"[优雅关闭] 模拟器#{emu_idx} 等待超时({wait}s) 且 Android 仍在，shutdown 兜底")
    except Exception:
        pass
    try:
        subprocess.run([cli, "control", flag, str(emu_idx), "shutdown"],
                       capture_output=True, timeout=15, creationflags=_CF)
        if log:
            log(f"[优雅关闭] 模拟器#{emu_idx} shutdown 兜底完成")
        # 兜底后等进程真正退出（最多 60s）— 否则回收看到 is_process_started=True 二次关闭
        _dl3 = time.time() + 60
        while time.time() < _dl3:
            try:
                r3 = subprocess.run([cli, "info", flag, str(emu_idx)],
                                    capture_output=True, text=True, timeout=5,
                                    creationflags=_CF, encoding="utf-8", errors="replace")
                if r3.returncode == 0:
                    d3 = json.loads(r3.stdout.lstrip("\ufeff").strip())
                    if isinstance(d3.get("is_process_started"), bool) and not d3.get("is_process_started"):
                        if log:
                            log(f"[优雅关闭] 模拟器#{emu_idx} 兜底后进程已退出 ({int(time.time()-_t0)}s)")
                        break
            except Exception:
                pass
            time.sleep(3)
    except Exception as ex:
        if log:
            log(f"[优雅关闭] 模拟器#{emu_idx} shutdown 兜底失败: {ex}")


def _info(cli: str, emu_idx, flag: str = "-v") -> dict:
    """MuMuManager info 查询（统一入口，失败返回空 dict）。"""
    try:
        r = subprocess.run([cli, "info", flag, str(emu_idx)],
                           capture_output=True, text=True, timeout=5,
                           creationflags=_CF, encoding="utf-8", errors="replace")
        if r.returncode == 0:
            return json.loads(r.stdout.lstrip("\ufeff").strip())
    except Exception:
        pass
    return {}


def diagnose_emulator(cli: str, emu_idx, vms_root: str = r"E:\MuMu Player 12\vms") -> dict:
    """模拟器体检（2026-08-11 用户: 状态收集=常用检测功能）—
    配置 + 运行状态 + 资源占用 + 健康评估（优越程度）。
    返回: {idx, name, running, android, adb_port, config{...}, resources{...}, health}
    """
    out = {"idx": str(emu_idx)}
    info = _info(cli, emu_idx)
    out["name"] = info.get("name", "")
    out["running"] = bool(info.get("is_process_started"))
    out["android"] = bool(info.get("is_android_started"))
    out["adb_port"] = info.get("adb_port")
    # 1) 配置（vm_config: cpu/memory; customer_config: 帧率/分辨率/渲染）
    cfg = {"cpu": "?", "memory": "?", "fps": "?", "resolution": "?", "render": "?"}
    try:
        import json as _j
        base = os.path.join(vms_root, f"MuMuPlayer-12.0-{emu_idx}", "configs")
        vp = os.path.join(base, "vm_config.json")
        cp = os.path.join(base, "customer_config.json")
        if os.path.exists(vp):
            vd = _j.load(open(vp, encoding="utf-8"))
            cfg["cpu"] = vd.get("vm", {}).get("cpu", "?")
            cfg["memory"] = vd.get("vm", {}).get("memory", "?") + "GB"
        if os.path.exists(cp):
            cd = _j.load(open(cp, encoding="utf-8"))
            st = cd.get("setting", {})
            fs = st.get("frame_setting", {})
            cfg["fps"] = fs.get("desired_framerate", "?")
            rs = st.get("resolution", {})
            cfg["resolution"] = (rs.get("current") or "").split(":")[0] + "x" + (rs.get("current") or "").split(":")[1] if ":" in (rs.get("current") or "") else "?"
            rd = st.get("render", {}).get("mode", {})
            cfg["render"] = rd.get("choose", "?")
    except Exception:
        pass
    out["config"] = cfg
    # 2) 资源占用（VMMHeadless 进程 CPU/内存 — 采样差算 CPU%）
    res = {"cpu_pct": None, "mem_mb": None, "pid": None}
    try:
        import glob as _g
        pid = None
        for p in _g.glob(vms_root + f"/MuMuPlayer-12.0-{emu_idx}/*.pid"):
            pass
        # 按 --comment 找进程
        ps1 = ("Get-CimInstance Win32_Process -Filter \"Name='MuMuVMMHeadless.exe'\" | "
               "Where-Object { $_.CommandLine -like '*MuMuPlayer-12.0-" + str(emu_idx) + "*' } | "
               "Select-Object ProcessId,WorkingSetSize,@{N='CT';E={$_.KernelModeTime+$_.UserModeTime}} | ConvertTo-Json -Compress")
        out2 = subprocess.run(["powershell", "-NoProfile", "-Command", ps1],
            capture_output=True, text=True, timeout=20, creationflags=_CF, errors="replace")
        import json as _j2
        data = _j2.loads(out2.stdout.strip() or "null")
        if isinstance(data, dict):
            data = [data]
        if data:
            d0 = data[0]
            pid = d0.get("ProcessId")
            res["pid"] = pid
            res["mem_mb"] = int(d0.get("WorkingSetSize", 0)) // 1048576
            ct0 = d0.get("CT", 0)
            time.sleep(3)
            ps2 = ("Get-CimInstance Win32_Process -Filter \"ProcessId=" + str(pid) + "\" | "
                   "Select-Object @{N='CT';E={$_.KernelModeTime+$_.UserModeTime}} | ConvertTo-Json -Compress")
            out3 = subprocess.run(["powershell", "-NoProfile", "-Command", ps2],
                capture_output=True, text=True, timeout=15, creationflags=_CF, errors="replace")
            d1 = _j2.loads(out3.stdout.strip() or "null")
            if d1:
                ct1 = d1.get("CT", ct0)
                res["cpu_pct"] = int((ct1 - ct0) / 100000 / 3.0) if ct1 > ct0 else 0
    except Exception:
        pass
    out["resources"] = res
    # 3) 健康评估（优越程度）: CPU% vs 核数（100%/核）; 内存 vs 2GB
    health = "ok"
    reasons = []
    try:
        cpu = int(cfg["cpu"])
        if res["cpu_pct"] is not None and res["cpu_pct"] >= cpu * 150:
            health = "busy"
            reasons.append(f"CPU {res['cpu_pct']}% >= {cpu}核×150%")
        if res["mem_mb"] and res["mem_mb"] >= 1800:
            if health == "ok":
                health = "busy"
            reasons.append(f"内存 {res['mem_mb']}MB 接近 2GB")
        if res["cpu_pct"] is not None and res["cpu_pct"] >= cpu * 190:
            health = "bottleneck"
    except Exception:
        pass
    out["health"] = health
    out["health_reason"] = "; ".join(reasons)
    return out


# ── 实时体检（2026-08-11 用户）──
_health_cache: dict = {}          # idx -> {ts, cpu_pct, mem_mb, running}
_health_baseline: dict = {}       # pid -> (cpu_time, ts) 采样基线
_health_lock = threading.Lock()


class HealthMonitor(threading.Thread):
    """后台实时体检线程：每 5s 批量采样运行中模拟器的 CPU/内存。
    采样差算 CPU%（快照 5s 内数据），API 即时读取。"""

    def __init__(self, cli_finder):
        super().__init__(daemon=True, name="emu_health")
        self._cli_finder = cli_finder

    def run(self) -> None:
        import re as _re
        try:
            import psutil as _ps
        except ImportError:
            _ps = None
        while True:
            try:
                cli = self._cli_finder()
                if cli and _ps:
                    # 1) 运行中模拟器列表（MuMuManager）
                    info = _info(cli, "all")
                    running = set()
                    if info:
                        running = {k for k, v in info.items() if v.get("is_process_started")}
                    # 2) psutil 批量采样（1s 窗口 — 游戏帧率检测粒度，2026-08-11 用户）
                    for p in _ps.process_iter(["name", "cmdline", "memory_info"]):
                        try:
                            if p.info["name"] != "MuMuVMMHeadless.exe":
                                continue
                            m = _re.search(r"MuMuPlayer-12\.0-(\d+)", " ".join(p.info["cmdline"] or []))
                            if not m:
                                continue
                            idx = m.group(1)
                            cpu = int(p.cpu_percent())  # 跨调用差（1s 窗口）
                            mem = int(p.info["memory_info"].rss) // 1048576
                            with _health_lock:
                                _health_cache[idx] = {
                                    "ts": time.strftime("%H:%M:%S"),
                                    "cpu_pct": max(0, cpu),
                                    "mem_mb": mem,
                                    "running": idx in running,
                                }
                        except Exception:
                            pass
            except Exception:
                pass
            time.sleep(1)


def get_health_snapshot() -> dict:
    """实时体检缓存快照（5s 内数据，无采样等待）。"""
    with _health_lock:
        return dict(_health_cache)


def start_health_monitor(cli_finder) -> HealthMonitor:
    """启动实时体检线程（main_web 启动时调用一次）。"""
    m = HealthMonitor(cli_finder)
    m.start()
    return m
