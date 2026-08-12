"""Runtime health check — one endpoint that surfaces operational issues.

Turns the manual diagnostics from production incidents (zombie MAA processes,
stale emulators, stuck queue, ADB port drift, connect failures) into an
automatic check: GET /api/health → issues list + counts.

Each issue: {severity: error|warn, category, detail}
"""
from __future__ import annotations
import json
import subprocess
from pathlib import Path

PROJ = Path(__file__).parent.parent
INSTANCES = PROJ / "services" / "maa" / "instances"
EVENTS_LOG = PROJ / "events.log"
MM = r"E:\MuMu Player 12\nx_main\MuMuManager.exe"


def _pid_alive(pid: int) -> bool:
    try:
        import psutil
        if not psutil.pid_exists(pid):
            return False
        try:
            # 验证是 MAA 进程（防 PID 复用误杀 — 僵尸 .pid 的 PID 可能被
            # 其他进程复用，2026-08-11）
            return "MAA" in (psutil.Process(pid).name() or "").upper()
        except Exception:
            return True  # 进程在但读不到名字（权限）— 保守视为 MAA 活着
    except ImportError:
        try:
            r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                               capture_output=True, text=True, timeout=5,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return str(pid) in r.stdout
        except Exception:
            return False


def _running_emulators() -> dict[str, dict]:
    """{index: info} for emulators with process started."""
    out = {}
    try:
        r = subprocess.run([MM, "info", "-v", "all"], capture_output=True, text=True,
                           timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                           encoding="utf-8", errors="replace")
        d = json.loads(r.stdout.lstrip("\ufeff").strip())
        for idx, info in d.items():
            if info.get("is_process_started"):
                out[str(idx)] = info
    except Exception:
        pass
    return out


def check_health(mw) -> dict:
    """mw: WebContext with .accounts / .runner / .launch_queue."""
    issues: list[dict] = []
    counts: dict = {"maa": 0, "emulators": 0, "running": 0, "queued": 0,
                    "zombie_maa": 0, "stale_emulators": 0, "port_drift": 0,
                    "queue_stuck": 0, "connect_fail": 0, "crash_reporter": 0}

    accounts = getattr(mw, "accounts", []) or []
    lq = getattr(mw, "launch_queue", None)

    # ── running / queued ──
    active_ids = set(getattr(lq, "_active_emus", {}).values()) if lq else set()
    queued_ids = {e.account_id for e in getattr(lq, "_pending", [])} if lq else set()
    counts["running"] = len(active_ids)
    counts["queued"] = len(queued_ids)

    # ── 1. zombie MAA: .pid alive but account not running ──
    # 目录不存在容错（实例池尚未创建/部署后丢失 — 2026-08-11 health 500 根因）
    try:
        _inst_dirs = sorted(INSTANCES.iterdir(), key=lambda p: int(p.name) if p.name.isdigit() else 0)
    except FileNotFoundError:
        _inst_dirs = []
    for inst_dir in _inst_dirs:
        if not inst_dir.is_dir() or not inst_dir.name.isdigit():
            continue
        pid_file = inst_dir / ".pid"
        if not pid_file.exists():
            continue
        try:
            pid = int(pid_file.read_text().strip())
        except Exception:
            continue
        if not _pid_alive(pid):
            continue
        counts["maa"] += 1
        meta_file = inst_dir / ".meta"
        aid = ""
        if meta_file.exists():
            try:
                aid = meta_file.read_text(encoding="utf-8").split("|")[0]
            except Exception:
                pass
        if aid and aid not in active_ids:
            counts["zombie_maa"] += 1
            name = next((a.get("name", aid) for a in accounts if a.get("id") == aid), aid)
            # 自动清理（2026-08-11: 僵尸不再手动清 — 检测到即 taskkill + 清标记。
            # 根因已修: _cleanup 占位符路径跳过杀逻辑；此清理为历史遗留兜底）
            try:
                subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               capture_output=True, timeout=5,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                pid_file.unlink(missing_ok=True)
                meta_file.unlink(missing_ok=True)
                counts["zombie_killed"] = counts.get("zombie_killed", 0) + 1
                issues.append({"severity": "error", "category": "zombie_maa",
                               "detail": f"僵尸 MAA: 实例 {inst_dir.name} ({name}) — 账号不在运行，已自动清理"})
            except Exception:
                issues.append({"severity": "error", "category": "zombie_maa",
                               "detail": f"僵尸 MAA: 实例 {inst_dir.name} ({name}) — 账号不在运行"})

    # ── 1b. MuMu 崩溃弹窗（MuMuNxCrashReporter）— 模拟器运行异常/崩溃的
    # 信号。检测到即自动关闭弹窗（崩溃的模拟器由失联恢复链处理；弹窗只是
    # 挂着碍眼 + 挡住模拟器画面 — 2026-08-11 用户: "一直莫名其妙运行异常，
    # 这种情况不应该有关掉重试吗"）。计次供趋势统计（崩溃频率）。
    try:
        r = subprocess.run(["tasklist", "/NH", "/FI", "IMAGENAME eq MuMuNxCrashReporter.exe"],
                           capture_output=True, text=True, timeout=5,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        n_crash = r.stdout.count("MuMuNxCrashReporter.exe")
        if n_crash:
            counts["crash_reporter"] = n_crash
            # 自动关弹窗（崩溃上报无价值，用户不需要）
            subprocess.run(["taskkill", "/F", "/IM", "MuMuNxCrashReporter.exe"],
                           capture_output=True, timeout=5,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            issues.append({"severity": "warn", "category": "emulator_crash",
                           "detail": f"模拟器崩溃弹窗 ×{n_crash}（已自动清理）"})
    except Exception:
        pass

    # ── 1c. 僵尸 VMM（VMMHeadless 残留 — 关闭链没清干净）──
    # 2026-08-12: 27 个残留 VMM 占满 64GB 内存（可用 0.7GB → 过载保护
    # 卡队列半天）。VMM 进程在但对应模拟器不在运行（MuMuManager info all
    # 一次判定）= 关闭残留 → taskkill（模拟器不在运行，杀 VMM 安全）。
    # 保护: 启动 5 分钟内不判僵尸（boot 中/刚 launch 的模拟器）。
    try:
        _running = _running_emulators()
        import psutil as _ps
        import re as _re
        import time as _time
        for _p in _ps.process_iter(["name", "cmdline", "pid", "create_time"]):
            try:
                if _p.info["name"] != "MuMuVMMHeadless.exe":
                    continue
                _m = _re.search(r"MuMuPlayer-12\.0-(\d+)", " ".join(_p.info["cmdline"] or []))
                if not _m:
                    continue
                _idx = _m.group(1)
                if _idx in _running:
                    counts["vmm"] = counts.get("vmm", 0) + 1
                    continue
                # boot 保护: VMM 刚起（<5 分钟）可能是 launch 中的模拟器
                if _time.time() - (_p.info["create_time"] or 0) < 300:
                    continue
                subprocess.run(["taskkill", "/F", "/PID", str(_p.info["pid"])],
                               capture_output=True, timeout=5,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                counts["zombie_vmm_killed"] = counts.get("zombie_vmm_killed", 0) + 1
                issues.append({"severity": "warning", "category": "zombie_vmm",
                               "detail": f"僵尸 VMM: #{_idx} 模拟器不在运行，已清理"})
            except Exception:
                pass
    except Exception:
        pass

    # ── 2. stale emulators: emulator up but account not running ──
    emu2aid = {}
    for a in accounts:
        ei = a.get("emu_instance_index", "")
        if ei:
            emu2aid[str(ei)] = a["id"]
    emus = _running_emulators()
    counts["emulators"] = len(emus)
    for idx in sorted(emus, key=int):
        aid = emu2aid.get(str(idx))
        if aid and aid in active_ids:
            continue
        counts["stale_emulators"] += 1
        name = "未绑定"
        if aid:
            name = next((a.get("name", "?") for a in accounts if a.get("id") == aid), "?")
        issues.append({"severity": "warn", "category": "stale_emulator",
                       "detail": f"闲置模拟器 #{idx} ({name}) — 空闲回收应在 60s 内关闭"})

    # ── 3. port drift: cached adb_address vs emulator's real port ──
    for idx, info in emus.items():
        real_port = str(info.get("adb_port", ""))
        if not real_port:
            continue
        for a in accounts:
            if str(a.get("emu_instance_index", "")) == idx:
                addr = a.get("adb_address", "")
                cached = addr.split(":")[-1] if addr else ""
                if cached and cached != real_port:
                    counts["port_drift"] += 1
                    issues.append({"severity": "error", "category": "port_drift",
                                   "detail": f"端口漂移: {a.get('name', '?')} 缓存 {cached} → 实际 {real_port}"})
                break

    # ── 4. instance config address drift: gui.new.json ConnectSettings vs real ──
    # MAA 6.16 reads connection settings from gui.new.json
    # (Configurations.Default.Gui.ConnectSettings.Address — ConfigFactory);
    # gui.json flat Connect.Address is legacy and no longer read. If injection
    # failed, a stale/wrong address persists and MAA connects to the WRONG
    # emulator (symptom: stuck at PRTS1 / wrong account's screen).
    try:
        for inst_dir in sorted(INSTANCES.iterdir(), key=lambda p: int(p.name) if p.name.isdigit() else 0):
            if not inst_dir.is_dir() or not inst_dir.name.isdigit():
                continue
            meta_file = inst_dir / ".meta"
            if not meta_file.exists():
                continue
            try:
                aid = meta_file.read_text(encoding="utf-8").split("|")[0]
            except Exception:
                continue
            ac = next((a for a in accounts if a.get("id") == aid), None)
            if not ac:
                continue
            emu = str(ac.get("emu_instance_index", ""))
            real_port = emus.get(emu, {}).get("adb_port")
            if not real_port:
                continue
            gj = inst_dir / "config" / "gui.new.json"
            if not gj.exists():
                continue
            try:
                d = json.loads(gj.read_text(encoding="utf-8"))
                cs = d.get("Configurations", {}).get("Default", {}).get("Gui", {}).get("ConnectSettings", {})
                cfg_addr = cs.get("Address", "")
                # 校验 InstanceIndex 是否对应该账号的模拟器 — 比地址更能反映
                # 注入残留（地址可能碰巧是别的账号的端口）
                ex = cs.get("Extras", {}) or {}
                cfg_idx = (ex.get("MuMuEmulator12", {}) or {}).get("InstanceIndex", "")
            except Exception:
                continue
            if cfg_addr and not cfg_addr.endswith(":" + str(real_port)):
                counts["port_drift"] += 1
                issues.append({"severity": "error", "category": "inst_addr_drift",
                               "detail": f"实例 {inst_dir.name} 配置地址 {cfg_addr} ≠ {ac.get('name','?')} 实际端口 {real_port}（注入失败/残留 — MAA 会连错模拟器）"})
            elif str(cfg_idx) and str(cfg_idx) != emu:
                counts["port_drift"] += 1
                issues.append({"severity": "error", "category": "inst_addr_drift",
                               "detail": f"实例 {inst_dir.name} 配置模拟器索引 {cfg_idx} ≠ {ac.get('name','?')} 实际模拟器 {emu}（注入残留 — MAA 会连错模拟器）"})
    except Exception:
        pass

    # ── 5. queue stuck: accounts with high failure counts ──
    for a in accounts:
        f = a.get("failures", 0) or 0
        if f >= 3:
            counts["queue_stuck"] += 1
            issues.append({"severity": "warn", "category": "queue_stuck",
                           "detail": f"{a.get('name', '?')} 连续失败 {f} 次（可能卡死循环）"})

    # ── 5. connect failures in recent events.log ──
    try:
        if EVENTS_LOG.exists():
            tail = EVENTS_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-300:]
            recent = [ln for ln in tail if "连接失败" in ln or "ADB 连接超时" in ln]
            counts["connect_fail"] = len(recent)
            if recent:
                issues.append({"severity": "warn", "category": "connect_fail",
                               "detail": f"最近有 {len(recent)} 次连接失败（含 ADB 超时）"})
    except Exception:
        pass

    return {"ok": True, "healthy": not any(i["severity"] == "error" for i in issues),
            "counts": counts, "issues": issues}
