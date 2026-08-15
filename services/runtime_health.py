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
# 根日志收口（2026-08-14 Step 5）：logs/ 优先，根目录兜底 —
# logger.py 迁 logs/ 后自动跟随（写者迁移前读根目录）
def _events_path() -> Path:
    _p = PROJ / "logs" / "events.log"
    return _p if _p.exists() else PROJ / "events.log"
INSTANCES = PROJ / "services" / "maa" / "instances"
# 模拟器 CLI 走单点（单点收敛 Step 4 — 2026-08-14）：不再硬编码路径，
# 复用 task_constants.find_mumu_cli（MuMuManager 优先/索引防错的统一入口）
MM = None  # 惰性解析（health 端点可能先于模拟器环境就绪调用）


def _mu_cli() -> str:
    global MM
    if not MM:
        try:
            from infrastructure.task_constants import find_mumu_cli
            MM = find_mumu_cli() or r"E:\MuMu Player 12\nx_main\MuMuManager.exe"
        except Exception:
            MM = r"E:\MuMu Player 12\nx_main\MuMuManager.exe"
    return MM


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
    """{index: info} for emulators with process started. 查询失败返回 None。"""
    out = {}
    try:
        r = subprocess.run([_mu_cli(), "info", "-v", "all"], capture_output=True, text=True,
                           timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                           encoding="utf-8", errors="replace")
        d = json.loads(r.stdout.lstrip("\ufeff").strip())
        for idx, info in d.items():
            if info.get("is_process_started"):
                out[str(idx)] = info
    except Exception:
        # 2026-08-14 修复：失败静默空集 = 僵尸 VMM 清理把运行中模拟器
        # 全量误杀面（92a3d6d 审查复核）— 必须显式区分失败与真空集
        return None
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
        elif not aid:
            # 幽灵 MAA（2026-08-14）: .pid 存活但 .meta 缺失/为空。正常流程
            # meta 先于 pid 写入（_do_launch → _spawn_instance），meta 缺失 =
            # 实例引用已错乱（并发启动/自动重启竞态覆盖 .pid、cleanup 误删
            # meta 等）→ 该 MAA 无主（不属任何账号），dashboard 会显示成
            # "MAA-xxxx" 占位。无主进程不清理会永久占实例 → 模拟器少 MAA 多。
            counts["ghost_maa"] = counts.get("ghost_maa", 0) + 1
            try:
                subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               capture_output=True, timeout=5,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                pid_file.unlink(missing_ok=True)
                meta_file.unlink(missing_ok=True)
                counts["ghost_maa_killed"] = counts.get("ghost_maa_killed", 0) + 1
                issues.append({"severity": "error", "category": "ghost_maa",
                               "detail": f"幽灵 MAA: 实例 {inst_dir.name} PID={pid} 无账号归属，已清理"})
            except Exception:
                issues.append({"severity": "error", "category": "ghost_maa",
                               "detail": f"幽灵 MAA: 实例 {inst_dir.name} PID={pid} 无账号归属"})

    # ── 1a2. 无主 MAA 系统级扫描（2026-08-15）──
    # 上面只扫实例池 .pid 文件 — 若 cleanup 已删 .pid 但 MAA 进程仍活着
    # （08:43 批量启动卡资源更新后 .pid 被清、进程残留），health 完全看
    # 不见 → 僵尸占实例窗口 → 新 MAA 单实例保护启动即退 → 队列死锁
    # （实测: 7 个无主 MAA 卡死队列 6 分钟）。这里扫系统进程表: 所有
    # MAA.exe，凡不属于任何实例 .pid 的一律判无主 → taskkill。
    try:
        _known_pids = set()
        for _id in _inst_dirs:
            _pf = _id / ".pid"
            if _pf.exists():
                try:
                    _known_pids.add(int(_pf.read_text().strip()))
                except Exception:
                    pass
        _rp = subprocess.run(["tasklist", "/NH", "/FI", "IMAGENAME eq MAA.exe", "/FO", "CSV"],
                             capture_output=True, text=True, timeout=5,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        import re as _re2
        for _line in _rp.stdout.splitlines():
            _m2 = _re2.search(r'"MAA\.exe","(\d+)"', _line)
            if not _m2:
                continue
            _mpid = int(_m2.group(1))
            if _mpid in _known_pids:
                continue  # 有 .pid 归属 — 由 zombie/ghost 分支管
            # 无 .pid 归属 = 无主 MAA（正常流程 .pid 与进程同生共死）
            counts["ghost_maa"] = counts.get("ghost_maa", 0) + 1
            try:
                subprocess.run(["taskkill", "/F", "/PID", str(_mpid)],
                               capture_output=True, timeout=5,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                counts["ghost_maa_killed"] = counts.get("ghost_maa_killed", 0) + 1
                issues.append({"severity": "error", "category": "ghost_maa",
                               "detail": f"无主 MAA: PID={_mpid} 无实例 .pid 归属，已清理"})
            except Exception:
                issues.append({"severity": "error", "category": "ghost_maa",
                               "detail": f"无主 MAA: PID={_mpid} 无实例 .pid 归属"})
    except Exception:
        pass

    # ── 1a3. adb 僵尸命令进程（2026-08-15 用户: 模拟器运行终止）──
    # MAA 每次连接失败会留 disconnect/connect 僵尸命令进程（卡在跟 adb
    # server 通信）→ 长期堆积（实测 23→46 个）→ 阻塞 adb server 串行
    # 处理 → 新连接全部超时 → MAA 连不上模拟器 → 模拟器无 MAA 驱动可能
    # 被误判/崩溃。这里检测 disconnect/connect 命令进程，超过阈值即清理。
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process -Filter \"Name='adb.exe'\" | Where-Object { $_.CommandLine -match 'disconnect|connect' } | Measure-Object).Count"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            n_adb_stuck = int((r.stdout or "").strip() or 0)
        except Exception:
            n_adb_stuck = 0
        if n_adb_stuck >= 5:
            counts["adb_stuck"] = n_adb_stuck
            # 自动清理（卡死的 connect/disconnect 命令进程 — 非 server，杀了
            # 不影响已建立连接；server 进程（fork-server）保留）
            try:
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     "Get-CimInstance Win32_Process -Filter \"Name='adb.exe'\" | Where-Object { $_.CommandLine -match 'disconnect|connect' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"],
                    capture_output=True, text=True, timeout=15,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                counts["adb_stuck_killed"] = n_adb_stuck
                issues.append({"severity": "warn", "category": "adb_stuck",
                               "detail": f"清理 adb 僵尸命令进程 ×{n_adb_stuck}（连接超时堆积，已自动清理）"})
            except Exception:
                issues.append({"severity": "error", "category": "adb_stuck",
                               "detail": f"adb 僵尸命令进程 {n_adb_stuck} 个（清理失败）"})
        elif n_adb_stuck:
            counts["adb_stuck"] = n_adb_stuck
    except Exception:
        pass

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
        if _running is None:
            # 查询失败跳过僵尸清理（防全量误杀 — 2026-08-14）
            issues.append({"severity": "info", "category": "vmm_query",
                           "detail": "MuMuManager info all 查询失败，跳过僵尸 VMM 清理"})
        else:
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
    if emus is None:
        emus = {}  # 查询失败按空处理（只影响体检展示，不做清理动作）
    counts["emulators"] = len(emus)
    for idx in sorted(emus, key=int):
        aid = emu2aid.get(str(idx))
        if aid and aid in active_ids:
            continue
        # 排队保留窗口（与回收器 _reclaim_idle_emus 对齐 — 2026-08-14）:
        # 账号有 keep_min 内会轮到（含已到期等槽位）的排队条目时，模拟器
        # 按设计保留（防关/开颠簸 + 批量轮转防冷启动风暴），不算 stale。
        # 条目过期超 keep_min 仍未轮到 → 回收器会关，这里照常报警。
        if aid and lq is not None:
            try:
                _cfg2 = getattr(getattr(lq, "ctx", None), "config", None) or {}
                from services.dynamic_params import get_pending_keep_min
                _keep_min2 = get_pending_keep_min(_cfg2, 10)
            except Exception:
                _keep_min2 = 10
            # 与回收器位置门对齐（2026-08-14 v2）: 只认可「排前部 + keep_min
            # 内会轮到」的保留，其余照常报 stale（回收器会关它们）。
            _front_keep2 = max(int(_cfg2.get("parallel_max", 1) or 1) + 4, 6)
            _front_ids2 = {e.account_id for e in
                           sorted(lq._pending, key=lambda e: e.sort_key)[:_front_keep2]}
            from datetime import datetime as _dt2
            _due2 = aid in _front_ids2 and any(
                (e.not_before - _dt2.now()).total_seconds() <= _keep_min2 * 60
                for e in lq._pending if e.account_id == aid)
            if _due2:
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
        if _events_path().exists():
            tail = _events_path().read_text(encoding="utf-8", errors="replace").splitlines()[-300:]
            recent = [ln for ln in tail if "连接失败" in ln or "ADB 连接超时" in ln]
            counts["connect_fail"] = len(recent)
            if recent:
                issues.append({"severity": "warn", "category": "connect_fail",
                               "detail": f"最近有 {len(recent)} 次连接失败（含 ADB 超时）"})
    except Exception:
        pass

    return {"ok": True, "healthy": not any(i["severity"] == "error" for i in issues),
            "counts": counts, "issues": issues}
