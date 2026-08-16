"""Single-account launch → monitor → complete cycle runner."""
from __future__ import annotations
import time, subprocess, re, json, os, threading
from pathlib import Path
from datetime import datetime
from typing import Any

from collections.abc import Callable

from infrastructure.task_constants import find_mumu_cli, CF
from app.service_context import ServiceContext
from models.stats import RunStats

_STAGE_CHECKS_PATH = Path(__file__).parent.parent / "models" / "stage_checks.json"


def _close_mumu_popups():
    """Close MuMu error popup windows (manual API use only)."""
    try:
        import ctypes, ctypes.wintypes, time as _tt
        user32 = ctypes.windll.user32
        # x64 句柄是 64 位：不声明 argtypes 会被默认 c_int 截断，
        # 子窗口句柄（按钮识别/递归遍历）全部失灵 — 统一声明
        RECT = ctypes.POINTER(ctypes.wintypes.RECT)
        user32.GetWindowRect.argtypes = [ctypes.c_void_p, RECT]
        user32.GetWindowRect.restype = ctypes.c_bool
        user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
        user32.GetClassNameW.restype = ctypes.c_int
        user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
        user32.IsWindowVisible.restype = ctypes.c_bool
        user32.IsIconic.argtypes = [ctypes.c_void_p]
        user32.IsIconic.restype = ctypes.c_bool
        user32.IsZoomed.argtypes = [ctypes.c_void_p]
        user32.IsZoomed.restype = ctypes.c_bool
        user32.GetWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        user32.GetWindow.restype = ctypes.c_void_p
        user32.FindWindowExW.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                         ctypes.c_wchar_p, ctypes.c_wchar_p]
        user32.FindWindowExW.restype = ctypes.c_void_p
        user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                        ctypes.c_void_p, ctypes.c_void_p]
        user32.PostMessageW.restype = ctypes.c_bool
        user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.GetWindowLongW.restype = ctypes.c_long
        WM_CLOSE = 0x0010
        enum_windows = user32.EnumWindows
        enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        closed = set()

        def _is_popup(hwnd):
            if user32.IsIconic(hwnd) or user32.IsZoomed(hwnd):
                return False
            rect = ctypes.wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            w = rect.right - rect.left
            h = rect.bottom - rect.top
            return w < 500 and h < 300

        def _has_button(hwnd):
            """递归找文本按钮（忽略/再次启动/确定/OK/Yes）— 错误弹窗的强特征。"""
            try:
                found = []

                def _walk(parent):
                    child = user32.GetWindow(parent, 5)  # GW_CHILD
                    while child:
                        ln = user32.GetWindowTextLengthW(child) + 1
                        buf = ctypes.create_unicode_buffer(ln)
                        user32.GetWindowTextW(child, buf, ln)
                        t = buf.value
                        if t and any(k in t for k in ("忽略", "Ignore", "再次启动", "确定", "OK", "Yes")):
                            found.append(child)
                        _walk(child)
                        child = user32.GetWindow(child, 2)  # GW_HWNDNEXT
                _walk(hwnd)
                return found
            except Exception:
                return []

        def _close_hwnd(hwnd, method="close"):
            try: user32.PostMessageW(hwnd, WM_CLOSE if method == "close" else 0x0001, 0, 0)
            except: pass
            closed.add(hwnd)

        def _find_and_click_ignore(hwnd):
            for text in ("忽略", "Ignore", "确定", "OK"):
                child = user32.FindWindowExW(hwnd, None, None, text)
                if child:
                    user32.PostMessageW(child, 0x00F5, 0, 0)
                    _tt.sleep(0.1)
                    if not user32.IsWindowVisible(hwnd):
                        return True
                child = user32.FindWindowExW(hwnd, None, "Button", text)
                if child:
                    user32.PostMessageW(child, 0x00F5, 0, 0)
                    _tt.sleep(0.1)
                    if not user32.IsWindowVisible(hwnd):
                        return True
            return False

        def callback(hwnd, lparam):
            length = user32.GetWindowTextLengthW(hwnd) + 1
            buf = ctypes.create_unicode_buffer(length)
            user32.GetWindowTextW(hwnd, buf, length)
            title = buf.value
            if not user32.IsWindowVisible(hwnd):
                return True
            is_error = False
            if title == 'MuMuNxDevice':
                # 正常模拟器画面/悬浮窗永不关（用户原则: 误杀成本>>漏关成本）。
                # 实机证据（2026-08-12 任务运行中监测）: MuMuNxDevice 标题窗口全是
                # 模拟器画面窗 —— ToolSaveBits(384×216~1022×575) 和 Qt5156QWindow(316×257)
                # 两种类名变体，均无按钮、owner=同进程主窗口；尺寸/类名都不该当判定依据。
                # 只有带按钮才可能是真弹窗（"运行异常"弹窗走 '异常' 标题分支，不在这里）。
                if _has_button(hwnd):
                    is_error = True
            elif '异常' in title:
                # "运行异常"弹窗：小窗或带按钮即关（2026-08-12 加按钮兜底 —
                # 大尺寸异常弹窗原来会因 _is_popup 尺寸限制漏关）。
                # 画面窗不会含"异常"标题（MuMuNxDevice/实例名），无误杀面。
                if _is_popup(hwnd) or _has_button(hwnd):
                    is_error = True
            elif 'CrashReporter' in title or '崩溃' in title:
                # MuMu 崩溃报告弹窗（关闭模拟器时可能出现）— 直接关闭
                if hwnd not in closed:
                    _close_hwnd(hwnd, "close")
                    closed.add(hwnd)
                return True
            elif '运行任务' in title or '确定要退出' in title:
                # MAA 确认退出弹窗（ConfirmExit: "MAA 正在运行任务 确定要退出吗?"）
                # — 我们的优雅关闭（WM_CLOSE）触发它，模态卡住 MAA 直到用户确认。
                # 自动点"是"（Yes）→ 优雅关闭完成 → MAA 正常退出（不留弹窗、不强杀）。
                if hwnd not in closed:
                    clicked = False
                    for text in ("是(Y)", "是(&Y)", "是", "Yes", "&Yes", "确定"):
                        child = user32.FindWindowExW(hwnd, None, "Button", text)
                        if child:
                            user32.PostMessageW(child, 0x00F5, 0, 0)  # BM_CLICK
                            clicked = True
                            _tt.sleep(0.2)
                            break
                    if not clicked:
                        _close_hwnd(hwnd, "close")
                    closed.add(hwnd)
                return True
            if is_error:
                if hwnd not in closed and not _find_and_click_ignore(hwnd):
                    _close_hwnd(hwnd, "close")
                    _tt.sleep(0.3)
                    if user32.IsWindowVisible(hwnd):
                        _close_hwnd(hwnd, "destroy")
            return True

        enum_windows(enum_proc(callback), 0)
    except Exception as _e:
        # 不静默：弹窗清理异常要能看到（pythonw 无控制台时写日志兜底）
        import traceback
        traceback.print_exc()
        try:
            from infrastructure.logger import Logger
            Logger("runner").error(f"_close_mumu_popups: {_e}")
        except Exception:
            pass


class AccountRunner:
    """Encapsulates single-account launch → monitor → complete lifecycle."""

    # MuMu 12 can't handle concurrent `control launch` (2nd instance fails to
    # boot, ADB never comes up). Serialize emulator launches globally; MAA
    # tasks still run in parallel afterwards.
    _EMU_LAUNCH_LOCK = threading.Lock()

    MAX_TOTAL_MEM_MB = 12288
    RESUME_MEM_MB = 6144
    MAX_PROC_MEM_MB = 4096
    MAX_RESTART_PER_MIN = 4

    def __init__(self, ctx: ServiceContext) -> None:
        # Callback lists (replaces Qt Signals for PySide6-free operation)
        self._log_msg_callbacks: list[Callable[[str], None]] = []
        self._status_msg_callbacks: list[Callable[[str], None]] = []
        self._started_callbacks: list[Callable[[str], None]] = []
        self._finished_callbacks: list[Callable[[tuple], None]] = []
        self._error_callbacks: list[Callable[[str, str], None]] = []
        self.ctx = ctx
        self._active: dict[str, dict] = {}
        self._progs: dict[str, list[dict]] = {}
        self._procs: dict[str, Any] = {}
        self._inst_reserved: dict[str, str] = {}  # aid -> inst_path（分配即预留，防并发同实例）
        self._inst_alloc_lock = threading.Lock()  # 2026-08-14: 实例分配原子化（防并发同实例竞态）
        self._start_times: dict[str, float] = {}
        self._task_start_times: dict[str, float] = {}
        self._stopping: set = set()
        self._proc_info: dict[str, dict] = {}
        self._restart_times: dict[str, list[float]] = {}
        self._overloaded = False
        self._log_buffers: dict[str, list[str]] = {}
        self._log_positions: dict[str, int] = {}
        self._gui_log_positions: dict[str, int] = {}
        self._adb_fail_count: dict[str, int] = {}
        self._adb_restart_count: dict[str, int] = {}
        self._adb_check_ts: float = 0.0
        self._done_flags: dict[str, bool] = {}  # 完成检测标记（归 0 竞态防护）
        self._stall_skip: dict[str, int] = {}  # 空转跳关计数（stall_loop）
        self._watchers: dict = {}  # aid → LogWatcher（日志事件流）
        self._err_windows: dict = {}  # aid -> [ts]（连续子任务错误窗口）
        self._emu_fail_count: dict[str, int] = {}
        self._emu_lost_suspect: dict[str, float] = {}  # aid -> ts（失联嫌疑，连续 2 轮确认才杀）
        self._adb_connected: dict[str, bool] = {}  # aid -> MAA 首次成功 adb（连接信号，启动节流用）
        self._run_sanity_first: dict[str, dict] = {}  # aid -> 本次运行首体力快照（统计真实消耗 — 2026-08-13）
        self._fight_times: dict[str, int] = {}  # aid -> FightTimesTaskPlugin times_finished（0=刷关空转假完成 — 2026-08-14）
        self._run_medicine_used: dict[str, bool] = {}  # aid -> 本次运行是否吃了理智药（过期药突击识别）
        self._run_chain_error: dict[str, bool] = {}  # aid -> 本次运行出现过 TaskChainError（假完成判定 — 2026-08-15）
        self._core_instances: dict[str, Any] = {}  # aid -> MaaCore instance (direct drive)
        self._core_tasks: dict[str, list[dict]] = {}  # aid -> [{name,status}]
        self._downgrading: dict[str, bool] = {}  # aid -> downgrade in progress
        self._recent_exit: dict[str, float] = {}  # aid -> ts（异常退出时间 — 回收保护期，2026-08-14）
        self._probe_pending: dict[str, dict] = {}  # aid -> {ac, inst_dir, ts, probe_stages}（关卡检测待跑）
        self._probe_running: dict[str, bool] = {}  # aid -> 关卡检测进行中（抑制 stall/error 误杀）
        self._probe_origin: dict[str, object] = {}  # aid -> probe 让位时的 MAA Popen（finally 身份校验，08-14 审查 #1）
        from infrastructure.logger import Logger
        self._log = Logger("runner")
        # 单点收敛 Step 3a: 体力写入者收口 — sanity_tracker 订阅 bus
        # （先于任何 LogWatcher 生效；与直调双写同值，迁移期过渡）
        try:
            from services.sanity_tracker import subscribe_bus
            subscribe_bus()
        except Exception:
            pass
        self._clean_orphan_marks()

    def _clean_orphan_marks(self) -> None:
        """Startup sweep: clear .pid/.meta whose process no longer exists.
        Orphan MAA processes (crashed / killed without cleanup) leave stale
        marks that block instance reuse and show ghost "running" state.
        Also dedup: the same live PID copied across multiple instances
        (instance pool copy pollution, 2026-08-10) must keep only one mark."""
        try:
            _pool = Path(__file__).parent / "maa" / "instances"
            if not _pool.exists():
                return
            pid_instances: dict[int, list] = {}
            for inst_dir in _pool.glob("*"):
                pf = inst_dir / ".pid"
                if not pf.exists():
                    continue
                try:
                    pid = int(pf.read_text().strip())
                except Exception:
                    pf.unlink(missing_ok=True)
                    (inst_dir / ".meta").unlink(missing_ok=True)
                    continue
                try:
                    import psutil as _pu
                    alive = _pu.pid_exists(pid)
                except ImportError:
                    r = subprocess.run(["tasklist", "/NH", "/FI", f"PID eq {pid}"],
                                       capture_output=True, text=True, timeout=3,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
                    alive = str(pid) in r.stdout
                if not alive:
                    pf.unlink(missing_ok=True)
                    (inst_dir / ".meta").unlink(missing_ok=True)
                    self._log.info(f"[孤儿清理] inst {inst_dir.name}: pid {pid} 不存在，清标记")
                    continue
                # 活 PID 去重：同一 PID 只允许一个实例持有（其余是复制污染）
                pid_instances.setdefault(pid, []).append(inst_dir)
            for pid, dirs in pid_instances.items():
                for extra in dirs[1:]:
                    (extra / ".pid").unlink(missing_ok=True)
                    (extra / ".meta").unlink(missing_ok=True)
                    self._log.warn(f"[孤儿清理] inst {extra.name}: pid {pid} 重复标记（复制污染），清理")
        except Exception:
            pass

    # ── Signal replacements (Qt-free callbacks) ──

    def emit_log(self, msg: str) -> None:
        for cb in self._log_msg_callbacks: cb(msg)
    def emit_status(self, msg: str) -> None:
        for cb in self._status_msg_callbacks: cb(msg)
    def emit_started(self, aid: str) -> None:
        for cb in self._started_callbacks: cb(aid)
    def emit_finished(self, data: tuple) -> None:
        for cb in self._finished_callbacks: cb(data)
    def emit_error(self, aid: str, err: str) -> None:
        for cb in self._error_callbacks: cb(aid, err)

    # ── Public API ──

    def is_running(self, account_id: str) -> bool:
        return account_id in self._active

    @property
    def active_count(self) -> int:
        return len(self._active)

    def active_ids(self) -> list[str]:
        return list(self._active.keys())

    def _detect_emu_address(self, emu_idx) -> str:
        """Get the current ADB address for an emulator — direct detection.

        The emulator is the source of truth for its port: MuMuManager
        single-query returns the real value. Formula ports (16384+idx*32)
        drift after emulator restarts, and mumu-cli index-mismatches on
        MuMu 12 — both are banned here.
        """
        from infrastructure.task_constants import detect_emu_adb
        return detect_emu_adb(emu_idx)

    def _auto_derive(self, ac: dict) -> None:
        """Auto-fill runtime fields — with emulator ADB port detection."""
        # ADB port via MuMuManager (--vmindex all). Single-query mumu-cli
        # index mismatch returns wrong ports (16992 vs 16708), so we always
        # use the all-instances query here.
        if ac.get("emu_instance_index") and not ac.get("adb_address"):
            addr = self._detect_emu_address(ac["emu_instance_index"])
            if addr:
                ac["adb_address"] = addr
        if not ac.get("adb_path"):
            cli = find_mumu_cli()
            if cli:
                cand = Path(cli).parent / "adb.exe"
                if cand.exists():
                    ac["adb_path"] = str(cand)
        if not ac.get("adb_path"):
            # MuMu 12 has no mumu-cli — fall back to drive-wide adb search
            # (covers nx_main/MuMuManager installs). Without this, adb_path stays
            # empty and the code falls back to bare "adb" (system PATH) → connect fails.
            from infrastructure.task_constants import find_adb
            adb = find_adb()
            if adb:
                ac["adb_path"] = adb
        ac.setdefault("connection_preset", "MuMuEmulator12")
        ac.setdefault("touch_mode", "MiniTouch")
        ac.setdefault("post_action", "ExitEmulator,ExitSelf")

    def _get_free_instance(self, aid: str) -> tuple[int, str] | None:
        """Get a free MAA instance directory."""
        # 2026-08-14: 分配原子化 — 检查-预留非原子导致两个并发启动（队列 tick
        # 与自动重启线程）同时拿到同一实例 → 后启动者杀掉先启动者的 MAA
        # （实测官-12 清理官-15 的 MAA PID=50640 → 互相杀 → 启动失败循环）。
        with self._inst_alloc_lock:
            try:
                import psutil as _psutil
                _pid_exists = _psutil.pid_exists
            except ImportError:
                def _pid_exists(pid: int) -> bool:
                    try:
                        r = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/NH'],
                                          capture_output=True, text=True, timeout=2,
                                          creationflags=subprocess.CREATE_NO_WINDOW)
                        return str(pid) in r.stdout
                    except Exception:
                        return True
            max_n = max(self.ctx.config.get("maa_instances", 9), self.ctx.config.get("parallel_max", 1))
            pool = Path(__file__).parent / "maa" / "instances"
            for i in range(1, max_n + 1):
                inst_dir = pool / str(i)
                exe = inst_dir / "MAA.exe"
                if not exe.exists():
                    continue
                inst_path = str(inst_dir)
                already_used = any(
                    p is not None and (
                        (isinstance(p, str) and p == inst_path) or
                        (getattr(p, "_inst_path", None) == inst_path)
                    )
                    for p in self._procs.values()
                )
                if already_used:
                    continue
                # 并发预留: _procs[aid] 要到 _spawn_instance（MAA 启动后）才写入，
                # 中间有 30-60s（模拟器启动）窗口 — 两个并发启动会拿到同一实例，
                # 后启动的 _launch_for_instance 会把先启动的 MAA taskkill（5s 退出，
                # 2026-08-10 反复"启动失败 MAA 5s 退出"）。分配即预留。
                if inst_path in self._inst_reserved.values():
                    continue
                pid_file = inst_dir / ".pid"
                running = False
                try:
                    pid = int(pid_file.read_text().strip())
                    running = _pid_exists(pid)
                except Exception:
                    pid_file.unlink(missing_ok=True)
                if not running:
                    # 僵尸残留防线: .pid 缺失/进程已死，但实例目录仍有活动（旧 MAA
                    # 被杀未死透 / 刚启动）→ 视为占用。否则新账号分配该实例 →
                    # MAA 启动撞 "Existing instance" → 2s 退出 → asst.log 停滞 →
                    # "日志停滞"判定 → 误关模拟器（2026-08-10 连锁根因）。
                    try:
                        _al = inst_dir / "debug" / "asst.log"
                        if _al.exists() and time.time() - _al.stat().st_mtime < 120:
                            continue
                    except Exception:
                        pass
                    self._inst_reserved[aid] = inst_path
                    return (i, str(inst_dir))
            return None

    def launch(self, row: int) -> bool:
        if row < 0 or row >= len(self.ctx.accounts):
            self.emit_log(f"无效账号索引: {row}")
            return False
        return self._launch_account(self.ctx.accounts[row])

    def _launch_account(self, ac: dict) -> bool:
        aid = ac["id"]
        # 防双启动（2026-08-14）: 实例池里该账号的 .meta 指向活 MAA → 已在
        # 跑 → 拒绝。不依赖 _active/_procs 映射 — 误杀 cleanup 会清映射但
        # MAA 进程可能没死透 → 同一账号两个 MAA 实例抢 ADB → 互扰失败循环
        # （实测官-37 占实例 2+9、官-38 占实例 4+5）。
        # v2（2026-08-14 21:0x）: 只看 pid 会漏掉"启动中"的账号 — meta 在
        # spawn 前 30-120s 就写入（boot 模拟器期间），慢 boot（>150s 启动
        # 超时）触发残留释放 → 二次启动 → 双 MAA（实测日-4 占 3+5、官-7 占
        # 6+8）。meta 写入 180s 内视为启动中，同样拒绝。
        try:
            import psutil as _psu3
            _inst_root2 = Path(__file__).parent / "maa" / "instances"
            for _idir3 in _inst_root2.glob("*"):
                _mfile3 = _idir3 / ".meta"
                if not _mfile3.exists():
                    continue
                try:
                    if _mfile3.read_text(encoding="utf-8").split("|")[0] != aid:
                        continue
                except Exception:
                    continue
                try:
                    _mfresh = time.time() - _mfile3.stat().st_mtime < 180
                except Exception:
                    _mfresh = False
                _pid_alive3 = False
                _pfile3 = _idir3 / ".pid"
                if _pfile3.exists():
                    try:
                        _pid3 = int(_pfile3.read_text().strip())
                        _pid_alive3 = _psu3.pid_exists(_pid3)
                    except Exception:
                        pass
                if _pid_alive3 or _mfresh:
                    self.emit_log(f"{ac.get('name', aid)} 实例 {_idir3.name} 已有该账号 MAA (pid活={_pid_alive3} meta新={_mfresh})，拒绝重复启动")
                    # 恢复队列一致性（2026-08-14）: active 标记丢失但 MAA 实际
                    # 活着 → 把账号补回 active_emus → 队列视为运行中 → 条目
                    # 弹出不再每 5s 空转重试（实测官-41 连续拒绝 1 分钟）。
                    try:
                        _lq2 = getattr(getattr(self.ctx, "_mw", None), "launch_queue", None)
                        if _lq2 is not None and ac.get("emu_instance_index"):
                            _ei = str(ac["emu_instance_index"])
                            if _lq2._active_emus.get(_ei) != aid:
                                _lq2._active_emus[_ei] = aid
                                _lq2._active_emus_ts[_ei] = time.time()
                    except Exception:
                        pass
                    return False
        except Exception:
            pass
        if aid in self._active:
            # Stale mark: MAA process may have died without _cleanup (crash/kill
            # raced the exit handler). If the process is really gone, clear the
            # mark and proceed — otherwise every launch returns "已在运行中"
            # and the queue loops forever (launch → fail → requeue).
            if not self._has_real_process(aid):
                self._log.warn(f"[残留清理] {ac.get('name', aid)} _active 标记但进程已死，清理后重试")
                self._cleanup(aid, -1, [])
            else:
                self.emit_log(f"{ac.get('name', aid)} 已在运行中")
                return False

        self._auto_derive(ac)

        if not ac.get("adb_address") and not ac.get("emu_instance_index"):
            self.emit_log(f"{ac.get('name', aid)} 未配置模拟器索引和 ADB，跳过")
            return False
        if not ac.get("adb_path") and not ac.get("_connect_only"):
            self.emit_log(f"{ac.get('name', aid)} 未找到 adb.exe，跳过")
            return False
        # 自定义 MAA 实例目录（l-4/l-6 等 fz-maa 业务机 — 2026-08-13 用户:
        # 像正常账号一样管理但不清日常，MAA 实例在 E:\maa-alas\fz-maa\l\l-N）。
        # 不走实例池（独立实例，不占 MAAOrch 的 instances/N）。
        if ac.get("maa_instance_dir"):
            _custom = Path(ac["maa_instance_dir"])
            if not (_custom / "MAA.exe").exists():
                self.emit_log(f"{ac.get('name', aid)} 自定义 MAA 目录无效: {ac['maa_instance_dir']}")
                return False
            inst = (-1, str(_custom))
        else:
            inst = self._get_free_instance(aid)
        if not inst:
            from services.maa_download import _is_source_ready
            _src = Path(__file__).parent / "maa" / "source"
            if not _is_source_ready(_src):
                self.emit_log(f"{ac.get('name', aid)} MAA 未就绪（首次下载中），任务留在队列等待")
                return False
            self.emit_log(f"{ac.get('name', aid)} 无空闲 MAA 实例")
            return False

        # 2026-08-15 修复: 手动启动（launch/launch_by_id）不经队列 _tick，
        # 需自行设置 _active_emus 标记 — 否则 spawn 前防孤儿检查
        # （_active_emus[emu] != aid → 取消）误判"标记已释放"→ 放弃 spawn
        # （实测日-4: 手动启动被"启动被队列取消"打断，MAA 连不上）。
        # 与队列启动一致: 标记在 spawn 前写入，cleanup/stop 释放。
        try:
            _lq0 = getattr(getattr(self.ctx, "_mw", None), "launch_queue", None)
            _ei0 = str(ac.get("emu_instance_index", "")) if ac.get("emu_instance_index") else ""
            if _lq0 is not None and _ei0:
                with _lq0._lock:
                    _lq0._active_emus[_ei0] = aid
                    _lq0._active_emus_ts[_ei0] = time.time()
        except Exception:
            pass

        self.emit_log(f"[启动] ADB({ac.get('adb_address','?')}) Emu({ac.get('emu_instance_index','?')}) 实例#{inst[0]}")
        self._active[aid] = ac
        self._progs[aid] = [w for w in self.ctx.warehouse if w.get("account_ref") == aid]
        self.emit_log(f"[启动] {ac.get('name', aid)}")
        self._track_stats(ac)
        self._do_launch(ac, inst)
        return True

    def launch_by_id(self, account_id: str) -> bool:
        for i, a in enumerate(self.ctx.accounts):
            if a["id"] == account_id:
                return self.launch(i)
        # Connect-only temp accounts live in ctx._mw.connect_accounts
        try:
            conn = getattr(getattr(self.ctx, "_mw", None), "connect_accounts", None)
            if conn:
                for a in conn:
                    if a.get("id") == account_id:
                        return self._launch_account(a)
        except Exception:
            pass
        return False

    def stop(self, account_id: str) -> None:
        """Stop a running account's MAA process — gracefully (WM_CLOSE) first,
        so MAA releases its ADB connection and touch services before exit.
        Hard-killing (TerminateProcess) mid-connection corrupts the emulator's
        ADB/touch state (MuMu shows '运行异常' afterwards)."""
        self._stopping.add(account_id)
        # Stop MaaCore direct-drive instance if present (no GUI process)
        core = self._core_instances.pop(account_id, None)
        if core:
            try:
                core.stop()
            except Exception:
                pass
            try:
                core.destroy()
            except Exception:
                pass
            self.emit_log(f"已停止 Core 直连任务")
        p = self._procs.pop(account_id, None)
        if p and hasattr(p, 'pid'):
            try:
                from models.account_state import AccountState
                AccountState(account_id).on_stopped("手动停止")
            except Exception:
                pass
            # 2026-08-14 审查 #4: 任务运行中（如肉鸽）WM_CLOSE 会弹"确认
            # 退出"→ 不点则不退 → 走 terminate 硬杀 → minitouch 残留 +
            # ADB 端口占用 → 让位后新任务启动撞"双连/运行异常"窗口。
            # 复用完整优雅关闭（WM_CLOSE + 点弹窗 + 等退出 + 兜底），
            # 确保返回时进程已死、ADB 连接已释放（同步语义）。
            self._kill_maa_graceful(p)
        self._active.pop(account_id, None)
        self.ctx.proc_status.discard(account_id)
        self._release_emu_mark(account_id)
        # Remove .pid/.meta so the instance isn't treated as occupied/stale on
        # the next launch (stop() doesn't go through _cleanup). Without this,
        # a stopped task leaves a stale .pid that `_get_free_instance` skips
        # (PID reuse false-positive) and .meta that mislabels the instance.
        try:
            if p is not None and getattr(p, "_inst_path", None):
                _ip = Path(p._inst_path)
                (_ip / ".pid").unlink(missing_ok=True)
                (_ip / ".meta").unlink(missing_ok=True)
        except Exception:
            pass

    def _release_emu_mark(self, account_id: str) -> None:
        """Release queue-side occupancy marks immediately so a relaunch isn't
        blocked by the 150s stale-cleaner (covers stop, crash, and cleanup paths)."""
        try:
            lq = getattr(getattr(self.ctx, "_mw", None), "launch_queue", None)
            if lq:
                key = lq._get_emu_key(account_id)
                with lq._lock:
                    lq._active_emus.pop(key, None)
                    lq._active_emus_ts.pop(key, None)
        except Exception:
            pass

    def _graceful_close(self, proc) -> bool:
        """Send WM_CLOSE to the process's main window → MAA's OnClose runs
        Bootstrapper.Shutdown() which releases ADB/touch resources cleanly."""
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            # 64 位 HWND 必须显式声明参数类型 — 否则 ctypes 按 32 位 int 截断
            # HWND → OverflowError → EnumWindows 回调中断 → 找不到窗口 →
            # WM_CLOSE 发不出去 → MAA 无法优雅关闭（2026-08-10 发现）。
            user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            user32.GetWindowThreadProcessId.restype = wintypes.DWORD
            user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            found = []

            @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            def _cb(hwnd, _lp):
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value == proc.pid and user32.IsWindowVisible(hwnd):
                    found.append(hwnd)
                    return False  # found main window → stop enumerating
                return True

            user32.EnumWindows(_cb, 0)
            if found:
                user32.PostMessageW(found[0], 0x0010, 0, 0)  # WM_CLOSE
                return True
        except Exception:
            pass
        return False

    def _kill_maa_graceful(self, p, name: str = "MAA") -> None:
        """优雅杀 MAA（WM_CLOSE → 等退出 → 兜底 taskkill）。
        硬杀（terminate/taskkill）残留 minitouch 触摸服务 → MuMu 弹
        "运行异常，请重启安卓设备"（2026-08-11 用户: 三个运行异常 —
        空转检测硬杀导致）。统一收尾用这个，不直接 terminate。"""
        try:
            self._graceful_close(p)
            # 优雅关闭触发 MAA "确认退出"弹窗（任务在跑）— 自动点"是"
            for _ in range(4):
                try:
                    _close_mumu_popups()
                except Exception:
                    pass
                if p.poll() is not None:
                    break
                time.sleep(1.5)
            try:
                p.wait(5)
            except Exception:
                pass
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(3)
                except Exception:
                    pass
            if p.poll() is None:
                # 最终兜底（极少 — MAA 完全无响应）
                subprocess.run(["taskkill", "/F", "/PID", str(p.pid)],
                               capture_output=True, timeout=5, creationflags=CF)
        except Exception:
            pass

    def _guard_kill(self, aid: str, action: str, reason: str,
                    evidence: str = "") -> bool:
        """守护审计（2026-08-15 用户: 「打个报告，我们事后再回来看」）。

        审计是**事后报告**不是事前拦截：破坏性动作（杀/关/回收）照常执行，
        同时写入 kill_audit 报告（Web 审计页回看 — d704eb1）。timeout_s=0
        = 报告即完成，不留 pending，不挡任何流程。

        判据以实际状态为准（用户铁律）— 调用方必须传真实证据摘录
        （asst.log 尾部 taskchain / 心跳年龄 / 进程状态），禁止只传
        "任务列表硬编码状态"这类推断值。

        返回 True（永远放行）— 正常完成的动作不需要审（用户: 什么东西
        都做完了为什么要审）；可疑判定由报告留痕，事后人工复盘。
        """
        try:
            from models.kill_audit import request as _ka_request
            from models.kill_audit import expire_all as _ka_expire
            _ka_expire()
            _ka_request(aid, self._ac_name(aid), action, reason, evidence,
                        timeout_s=0)
        except Exception:
            # 审计系统异常时不影响主流程 — 默认放行（宁可多记录不能卡死调度）
            pass
        return True

    def _ac_name(self, aid: str) -> str:
        try:
            ac = self._active.get(aid) or next(
                (a for a in (self.ctx.accounts or []) if a.get("id") == aid), None)
            return ac.get("name", aid) if ac else aid
        except Exception:
            return aid

    def _find_emu_pid(self, addr: str) -> int | None:
        if not addr:
            return None
        try:
            host, port_str = addr.rsplit(":", 1)
            port = int(port_str)
            import psutil as _ps
            for conn in _ps.net_connections():
                if conn.status == "LISTEN" and conn.laddr.port == port:
                    return conn.pid
        except Exception:
            pass
        return None

    # ── Launch pipeline ──

    def _has_real_process(self, aid: str) -> bool:
        """Check if account has a running MAA process (not a dead Popen or string)."""
        p = self._procs.get(aid)
        if p is None or isinstance(p, str):
            return False
        if not hasattr(p, 'pid'):
            return False
        try:
            return p.poll() is None  # None = still running
        except:
            return False

    def _do_launch(self, ac: dict, inst: tuple[int, str]) -> None:
        aid = ac["id"]
        inst_id, inst_dir = inst
        self._procs[aid] = inst_dir
        # Write .meta before MAA starts so log endpoint can find it
        try:
            (Path(inst_dir) / ".meta").write_text(f"{aid}|{ac.get('name', aid)}", encoding="utf-8")
        except:
            pass
        import threading as _th
        try:
            _th.Thread(target=self._launch_job, args=(ac, inst), daemon=True).start()
        except Exception as e:
            self._log.error(f"[启动] {ac.get('name', aid)} 线程创建失败: {e}")
            self._cleanup(aid, -1, [])

    def _launch_job(self, ac: dict, inst: tuple[int, str]) -> None:
        aid = ac["id"]
        _deadline = time.time() + 120

        def _check_deadline(phase: str) -> bool:
            if time.time() > _deadline:
                self.emit_log(f"[超时] {ac.get('name', aid)} {phase} 超时，放弃启动")
                self._cleanup(aid, -2, [])
                return True
            return False

        try:
            self._launch_job_body(ac, inst, _check_deadline)
        except Exception as e:
            self._log.error(f"[崩溃] {ac.get('name', aid)} launcher 异常: {e}")
            self.emit_log(f"[崩溃] launcher 异常: {e}")
            self._cleanup(aid, -999, [])

    def _launch_job_body(self, ac: dict, inst: tuple[int, str], _check_deadline) -> None:
        emu_idx = ac.get("emu_instance_index", "")
        aid = ac["id"]
        inst_id, inst_dir = inst
        self._adb_restart_count.pop(aid, None)

        # Re-detect ADB port before EVERY launch. Emulator restarts shift the
        # ADB port (MuMu 12, e.g. 16416 -> 16417), so a cached adb_address can
        # be stale and `adb connect` fails forever. Always refresh from
        # MuMuManager — the emulator is the source of truth for its port.
        if emu_idx:
            addr = self._detect_emu_address(emu_idx)
            if addr:
                ac["adb_address"] = addr

        # Launch emulator
        if emu_idx:
            cli = find_mumu_cli()
            if cli is None and ac.get("adb_path"):
                # MuMu 12: no mumu-cli — MuMuManager.exe lives next to adb.exe
                try:
                    cand = Path(ac["adb_path"]).parent / "MuMuManager.exe"
                    if cand.exists():
                        cli = str(cand)
                except Exception:
                    pass
            if cli:
                # EmulatorService v2 状态记录（Phase 1 — acquire 语义：
                # OFF→PREWARMING / READY/IDLE/EXTERNAL→BUSY）
                try:
                    _v2 = getattr(getattr(self.ctx, "_mw", None), "emu_service_v2", None)
                    if _v2:
                        _v2.acquire(ac)
                except Exception:
                    pass
                # Serialize emulator launch: MuMu 12 fails when 2+ `control
                # launch` run concurrently (2nd instance never boots).
                with self._EMU_LAUNCH_LOCK:
                    # CLI syntax differs: mumu-cli uses --vmindex, MuMuManager -v
                    use_mm = "MuMuManager" in cli
                    idx_flag = "-v" if use_mm else "--vmindex"
                    already_running = False
                    try:
                        import json as _json
                        r = subprocess.run([cli, "info", idx_flag, str(emu_idx)],
                                          capture_output=True, text=True, timeout=5, creationflags=CF,
                                          encoding="utf-8", errors="replace")
                        if r.returncode == 0:
                            data = _json.loads(r.stdout)
                            if data.get("is_android_started") or data.get("is_process_started"):
                                already_running = True
                    except: pass
                    if already_running:
                        self.emit_log(f"模拟器 #{emu_idx} 已在运行")
                    else:
                        self.emit_log(f"启动模拟器 #{emu_idx}")
                    # 记录系统启动的模拟器（回收只关系统拉的；用户手动开的
                    # （MuMu 管理器）不在记录 → 永不回收 — 2026-08-11 用户:
                    # 手动启动的模拟器也被关掉）
                    try:
                        from services.emu_service import mark_system_started
                        mark_system_started(emu_idx)
                    except Exception:
                        pass
                    try:
                        subprocess.run([cli, "control", idx_flag, str(emu_idx), "launch"], creationflags=CF, timeout=15)
                    except Exception as e:
                        self.emit_log(f"启动模拟器失败: {e}")
                        try:
                            _v2 = getattr(getattr(self.ctx, "_mw", None), "emu_service_v2", None)
                            if _v2:
                                _v2.on_lost(str(emu_idx), f"launch fail {e}")
                        except Exception:
                            pass
            # Re-detect ADB port AFTER launching — detect_emu_instances only
            # returns running emulators, so a cold start needs a second pass.
            # Retry loop: right after `launch` the Android guest isn't up yet
            # and port detection returns empty/wrong ports (e.g. 16708 vs real
            # 16768). Keep probing until a valid port appears or timeout.
            # UNCONDITIONAL: even if ac["adb_address"] is cached, the emulator
            # may have been restarted since (port +1 drift on MuMu 12) — the
            # cached value is stale and MAA would connect to the wrong port
            # (2026-08-10: b-2/b-3 injected 16416/16448 while real ports were
            # 16417/16449 → MAA connect fail).
            if emu_idx:
                from infrastructure.task_constants import detect_emu_instances
                wait = int(ac.get("emu_wait", 60))
                deadline = time.time() + wait
                _prev = ac.get("adb_address", "")
                _found = False
                while time.time() < deadline:
                    try:
                        for e in detect_emu_instances():
                            if str(e.get("index", "")) == str(emu_idx) and e.get("adb_port"):
                                _addr = f"127.0.0.1:{e['adb_port']}"
                                # 验证连通才采用 — 部署/重启期模拟器端口不稳，
                                # detect 可能返回旧值/错位值（2026-08-12 官-47
                                # 注入 16769 vs 实际 16708 → MAA 连不上卡住；
                                # 旧逻辑 detect 没找到时缓存值恒真 → 直接注入
                                # 旧缓存）。连不上继续轮询（端口在变化）。
                                _adb = ac.get("adb_path", "") or "adb"
                                try:
                                    _vr = subprocess.run([_adb, "connect", _addr],
                                                         capture_output=True, timeout=5, creationflags=CF)
                                    if b"connected" in _vr.stdout.lower():
                                        ac["adb_address"] = _addr
                                        _found = True
                                        break
                                except Exception:
                                    pass
                        if _found:
                            break
                    except Exception:
                        pass
                    time.sleep(3)
                if ac.get("adb_address"):
                    _changed = "" if ac["adb_address"] == _prev else f"（{_prev} → {ac['adb_address']}）"
                    self.emit_log(f"模拟器 #{emu_idx} ADB 端口 {ac['adb_address']}{_changed}")
                if not ac.get("adb_address"):
                    self.emit_log(f"警告: 模拟器 #{emu_idx} 端口探测超时")
            adb = ac.get("adb_path", "") or "adb"
            addr = ac.get("adb_address", "")
            if addr:
                wait = int(ac.get("emu_wait", 60))
                deadline = time.time() + wait
                self.emit_log(f"等待模拟器 ADB 连接 (最长 {wait}s)...")
                while time.time() < deadline:
                    try:
                        r = subprocess.run([adb, "connect", addr], capture_output=True, timeout=5, creationflags=CF)
                        if r.returncode == 0 and b"connected" in r.stdout.lower():
                            self.emit_log(f"模拟器 #{emu_idx} ADB 已连接")
                            try:
                                _v2 = getattr(getattr(self.ctx, "_mw", None), "emu_service_v2", None)
                                if _v2:
                                    _v2.on_ready(str(emu_idx))
                            except Exception:
                                pass
                            break
                    except: pass
                    time.sleep(2)
                else:
                    self.emit_log(f"警告: 模拟器 #{emu_idx} ADB 连接超时")
                    # Port drift fix: MuMu 12 emulator restarts can shift the
                    # ADB port (e.g. 16416 -> 16417). The cached adb_address is
                    # then stale and `adb connect` fails forever. Clear it and
                    # re-detect from MuMuManager (--vmindex all gives correct ports).
                    if emu_idx:
                        ac.pop("adb_address", None)
                        new_addr = self._detect_emu_address(emu_idx)
                        if new_addr:
                            ac["adb_address"] = new_addr
                            self.emit_log(f"端口漂移修复: 模拟器 #{emu_idx} → {new_addr}")
                        addr = ac.get("adb_address", "")
                self.emit_log(f"等待 Android 开机完成...")
                # boot_completed 独立等待（不 share 端口探测的 deadline）—
                # 模拟器冷启动在 50 台多开负载下 boot 可能 60-120s。MAA 必须
                # 在 Android 完全启动后 spawn，否则连半启动系统 → 连接失败/
                # 失联/假画面空转（2026-08-11 用户指出: MAA 应在模拟器完全
                # 启动后再启动）。
                # 2026-08-14: 默认 90s → 180s — 回收修复后模拟器全部冷启动
                # （用完即关），冷启动 boot 常超 90s（实测 21:22 b-1 开机
                # 超时 90s 放弃 → exit=-2 快速失败 → 半小时 111 次失败
                # 风暴）。180s 覆盖慢冷启动；账号级 boot_wait 可覆盖。
                _boot_dl = time.time() + int(ac.get("boot_wait", 180))
                _booted = False
                while time.time() < _boot_dl:
                    try:
                        r = subprocess.run([adb, "-s", addr, "shell", "getprop", "sys.boot_completed"],
                                          capture_output=True, timeout=5, creationflags=CF,
                                          encoding="utf-8", errors="replace")
                        if r.returncode == 0 and r.stdout.strip() == "1":
                            self.emit_log(f"Android 开机完成")
                            _booted = True
                            break
                    except Exception:
                        pass
                    time.sleep(2)
                if not _booted:
                    # boot 未完成 → 放弃本次启动（同超时路径: 清理标记 →
                    # 队列重试）。绝不带半启动系统 spawn MAA。
                    # ⚠️ 2026-08-11: 仅放弃不够 — 模拟器进程活着但 Android
                    # 卡死（崩溃重启后状态损坏）时，重试发现进程在 → 不重启
                    # 模拟器 → 永远等 boot → 死循环。放弃前重启模拟器
                    # （shutdown+launch 彻底重置），下次重试 boot 成功率高。
                    try:
                        from infrastructure.task_constants import find_mumu_cli as _find_cli, cli_flag as _cflag
                        _cli = _find_cli()
                        if _cli and emu_idx:
                            subprocess.run([_cli, "control", _cflag(_cli), str(emu_idx), "shutdown"],
                                           capture_output=True, timeout=15, creationflags=CF)
                            time.sleep(3)
                            subprocess.run([_cli, "control", _cflag(_cli), str(emu_idx), "launch"],
                                           capture_output=True, timeout=15, creationflags=CF)
                            self.emit_log(f"🔄 Android 开机超时，已重启模拟器 #{emu_idx}")
                    except Exception:
                        pass
                    self.emit_log(f"⚠️ Android 开机超时（{int(ac.get('boot_wait', 180))}s），放弃本次启动")
                    self._cleanup(aid, -2, [])
                    return

        if _check_deadline("ADB连接"):
            return

        addr = ac.get("adb_address", "")
        if addr:
            for _attempt in range(3):
                try:
                    r = subprocess.run([adb, "connect", addr], capture_output=True, creationflags=CF, timeout=5)
                    if r.returncode == 0:
                        break
                except: pass
                time.sleep(2)
            time.sleep(1)

        if _check_deadline("注入配置"):
            return

        self._launch_for_instance(ac, inst_dir)

    def _launch_for_instance(self, ac: dict, inst_dir: str) -> None:
        aid = ac["id"]
        mode = self.ctx.config.get("schedule_mode", "daily")
        exe = Path(inst_dir) / "MAA.exe"
        config_dir = Path(inst_dir) / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        # Kill any stale MAA still holding this instance dir BEFORE injecting —
        # a running MAA overwrites the injected config (StagePlan etc.) with its
        # own in-memory values on save, so the new launch reads stale data.
        try:
            pf = Path(inst_dir) / ".pid"
            if pf.exists():
                _pid = int(pf.read_text().strip())
                _r = subprocess.run(["tasklist", "/NH", "/FI", f"PID eq {_pid}"],
                                    capture_output=True, text=True, timeout=3,
                                    creationflags=CF)
                if str(_pid) in _r.stdout:
                    # 2026-08-14: 若是其他活跃账号的 MAA（并发启动/重启竞态
                    # 实例串用 — 分配预留被清理后另一账号已 spawn），不能杀 —
                    # 杀了对方失联 + 产生无主 MAA。放弃本次启动让队列重试
                    # （_clean_stale_emus 150s 兜底释放标记）。
                    _other = next((pp for pp in self._procs.values()
                                   if getattr(pp, "pid", None) == _pid), None)
                    if _other is not None:
                        self.emit_log(f"↩ {ac.get('name', aid)} 实例 {Path(inst_dir).name} 已被其他账号占用 (PID={_pid})，放弃本次启动")
                        self._inst_reserved.pop(aid, None)
                        return
                    # 2026-08-14 v2: .meta 归属检查 — _procs 映射可能已丢
                    # （误杀 cleanup 清映射但进程没死透），此时 _other 保护
                    # 失效 → 用 .meta 判定归属：.pid 活进程属于**另一个账号**
                    # → 不杀（那是对方正在跑的 MAA），放弃本次启动。
                    try:
                        _mf = Path(inst_dir) / ".meta"
                        if _mf.exists():
                            _owner = _mf.read_text(encoding="utf-8").split("|")[0]
                            if _owner and _owner != aid:
                                self.emit_log(f"↩ {ac.get('name', aid)} 实例 {Path(inst_dir).name} 的 .pid 属于 {_owner[:8]}（他账号 MAA 存活），放弃本次启动避免误杀")
                                self._inst_reserved.pop(aid, None)
                                return
                    except Exception:
                        pass
                    self._log.warn(f"[注入] {ac.get('name', aid)} 清理实例残留 MAA PID={_pid}")
                    subprocess.run(["taskkill", "/F", "/PID", str(_pid)],
                                   capture_output=True, timeout=5, creationflags=CF)
                    time.sleep(2)
        except Exception:
            pass
        try:
            # Unified dispatch path: use slot-based dispatch_id
            from services.dispatch_pool import get_template_info
            _slot = ac.get("_slot", "")
            _did_key = f"_dispatch_{_slot}" if _slot else "dispatch_id"
            did = ac.get(_did_key, "") or ac.get("dispatch_id", "")
            task_list, params = (get_template_info(did) if did else ([], {}))
            if not task_list:
                # slot 模板缺失回退固定默认（2026-08-13: 重启丢账号瞬态指针
                # 后模板找不回，按 slot 语义兜底 — maintenance/fight/annihilation）
                _slot_defaults = {
                    "maintenance": ["StartUp", "Infrast", "Recruit", "Mall", "Award"],
                    "fight": ["StartUp", "Fight"],
                    "annihilation": ["StartUp", "Fight"],
                    "roguelike": ["StartUp", "Roguelike"],
                }
                if _slot in _slot_defaults:
                    task_list = list(_slot_defaults[_slot])
            if not task_list:
                if ac.get("roguelike_only"):
                    # 账号级肉鸽（l-4/l-6 等业务机 — 2026-08-13 用户: 像正常
                    # 账号一样管理但不清日常，只跑肉鸽 24*7）。
                    task_list = ["StartUp", "Roguelike"]
                    # 主题轮换（l-4 打 5 主题 / l-6 只界园）：当前主题写进
                    # task_settings（注入时应用 theme + 满级停止），完成时
                    # _finish_completed 推进到下一主题（2026-08-13）。
                    try:
                        _themes = ac.get("roguelike_themes") or []
                        if _themes:
                            _idx = int(ac.get("roguelike_theme_idx", 0) or 0) % len(_themes)
                            _theme = _themes[_idx]
                            _ts = dict(ac.get("task_settings") or {})
                            _rl = dict(_ts.get("Roguelike") or {})
                            _rl["theme"] = _theme
                            _rl["stop_when_level_max"] = True  # 满级自动停（MAA CheckLevelMax）
                            _ts["Roguelike"] = _rl
                            ac["task_settings"] = _ts
                            self.emit_log(f"🎮 {ac.get('name', aid)} 肉鸽主题 {_idx + 1}/{len(_themes)}: {_theme}")
                    except Exception:
                        pass
                elif mode == "roguelike":
                    task_list = ["StartUp", "Roguelike"]
                elif mode == "reclamation":
                    task_list = ["StartUp", "Reclamation"]
                elif ac.get("skip_daily"):
                    # Skip farming AND daily tasks — sanity already exhausted,
                    # just claim awards (Award/Mail/Orundum/Mining/SpecialAccess).
                    # 2026-08-15 用户: 手动专用模拟器连领奖都不要 —
                    # skip_daily + skip_award = 纯登录（StartUp only），
                    # 账号归用户手动操作，MAAOrch 不碰任何日常。
                    if ac.get("skip_award"):
                        task_list = ["StartUp"]
                    else:
                        task_list = ["StartUp", "Award"]
                else:
                    task_list = ["StartUp", "Fight", "Infrast", "Recruit", "Mall", "Award"]
            # Per-account annihilation (overrides dispatch template)
            if ac.get("smart_annihilation", ""):
                if "Annihilation" not in task_list:
                    task_list.append("Annihilation")
            else:
                if "Annihilation" in task_list:
                    task_list.remove("Annihilation")
            # Probe 模式（关卡检测 — 调度台"仅检测"）：MAA 只跑 StartUp 登录，
            # 检测由 StageProbe 子进程完成（不占 MAA 任务链）。注入时过滤
            # Probe（MAA 不认此任务），登录完成后 _run_probe_for_account 接管。
            _probe_mode = "Probe" in task_list
            if _probe_mode:
                task_list = [t for t in task_list if t != "Probe"]
            # ── 断点续做（2026-08-16）: 重启/中断后跳过本次已完成的方块 ──
            # 读 AccountState.done_chains（LogWatcher 在 TaskCompleted 时记录），
            # 已完成的剔除（StartUp 永不跳 — 重登+确保模拟器/游戏就绪）。
            # 用户痛点: 一次任务 5 方块，第 1 个完成中断重启后从头重做 —
            # 跳过已完成 → 从断点继续（不重复清体力/基建已确认的操作）。
            # 安全: 只跳明确记录的；TaskChainError 不算完成；全部完成清除。
            try:
                if len(task_list) > 1:  # 纯 StartUp 不处理
                    from models.account_state import AccountState as _AS
                    _done = _AS(aid).done_chains
                    if _done:
                        _norm = {str(d).lower() for d in _done}
                        _before = list(task_list)
                        task_list = [t for t in task_list if str(t).lower() not in _norm]
                        if len(task_list) != len(_before):
                            _skip = [t for t in _before if str(t).lower() in _norm]
                            self.emit_log(f"♻️ 断点续做: 跳过已完成的 {_skip}（{','.join(_before)} → {','.join(task_list)}）")
            except Exception:
                pass
            plan_txt = ",".join(task_list)
            self.emit_log(f"🧠 智能调度: {plan_txt}" + (" (+Probe 关卡检测)" if _probe_mode else ""))
            self._log.info(f"[注入] {ac.get('name', aid)} task_list={task_list} params={params}")
            # 每次启动重置错误计数 — 2026-08-14 凌晨：_err_count 跨启动粘滞，
            # 风暴期累积 ≥3 后每次健康启动 3 秒被「连续异常」误杀（日-1/日-2
            # 挂起根因，模拟器无损伤）。计数只应衡量本次运行。
            ac["_err_count"] = 0
            self._fight_times[aid] = -1  # -1=未收到 FightTimesTaskPlugin 信号
            self._run_chain_error[aid] = False  # 任务链错误标记重置（本次运行）
            ac["_injected_tasks"] = list(task_list)
            self.ctx.cfg.inject_smart(task_list, ac, str(config_dir), params=params)
            self.emit_log(f"注入配置: {config_dir}/")
            if _probe_mode:
                self._probe_pending[aid] = {
                    "ac": ac, "inst_dir": str(inst_dir), "ts": time.time(),
                    "probe_stages": ac.get("probe_stages") or [],
                }
            try:
                from services.log_watcher import record_event
                record_event(aid, "launch", f"task_list={plan_txt}" + ("+Probe" if _probe_mode else ""))
            except Exception:
                pass
            # 防孤儿 MAA: 启动等待期间（模拟器冷启动/ADB 探测）队列可能已释放
            # 本账号标记（_clean_stale_emus/超时清理）— 此刻再 spawn 就是孤儿
            # （进程活着但无人跟踪 → 占实例不释放 → 无空闲 MAA 实例连环）。
            # 实测 2026-08-10: 9 个 MAA 活着但队列 active 只有 4。
            try:
                _lq = getattr(getattr(self.ctx, "_mw", None), "launch_queue", None)
                if _lq is not None and ac.get("emu_instance_index"):
                    _occ = _lq._active_emus.get(str(ac.get("emu_instance_index")))
                    if _occ != aid:
                        self.emit_log(f"↩ {ac.get('name', aid)} 启动被队列取消（标记已释放），放弃 spawn")
                        self._inst_reserved.pop(aid, None)  # 释放预留，防泄漏
                        # ⚠️ 2026-08-15: 队列侧标记已释放但 runner 的 _procs
                        # 占位还在 → 队列不再跟踪 → active 残留卡住该模拟器
                        # 循环「入队→注入→取消→重试」。同步清 runner 占位，
                        # 让重试走完整启动路径。
                        self._procs.pop(aid, None)
                        return
            except Exception:
                pass
            # 防僵尸 MAA: cleanup 会移除 _procs 占位（_do_launch 写入的实例
            # 目录字符串）— 若占位已不在（期间被清理过），再 spawn 就是无主
            # 进程（cleanup 对字符串占位不杀进程 → MAA 活着没人管 →
            # zombie_maa 累积占满实例 — 2026-08-10 实测 5 个僵尸）。
            if self._procs.get(aid) is not inst_dir:
                self.emit_log(f"↩ {ac.get('name', aid)} 启动已被清理（占位移除），放弃 spawn")
                self._inst_reserved.pop(aid, None)  # 释放预留，防泄漏
                return
            # 注入验证（2026-08-14）: MAA 6.16 读 gui.new.json 的
            # ConnectSettings.Address — 注入写失败/残留会让 MAA 连到错误的
            # 模拟器（inst_addr_drift 实锤: 实例 6 残留 16768 vs 官-6 实际
            # 17024 → MAA 10-40s 快速失败循环）。spawn 前读回校验，不一致
            # → 放弃本次启动（队列重试会重新注入），绝不 spawn 必失败的 MAA。
            try:
                _gj = Path(inst_dir) / "config" / "gui.new.json"
                if _gj.exists():
                    _d = json.loads(_gj.read_text(encoding="utf-8", errors="replace"))
                    _addr = (((_d.get("Configurations") or {}).get("Default") or {})
                             .get("Gui", {}).get("ConnectSettings", {})).get("Address", "")
                    _want = ac.get("adb_address", "")
                    if _want and _addr and _addr != _want:
                        self.emit_log(f"↩ {ac.get('name', aid)} 注入验证失败: gui.new.json Address={_addr} ≠ 期望 {_want}（写入失败/残留），放弃本次启动")
                        self._inst_reserved.pop(aid, None)
                        return
            except Exception:
                pass
            self._spawn_instance(exe, ac, inst_dir)
            try:
                from models.account_state import AccountState
                AccountState(aid).on_login()
            except Exception:
                pass
            self._active[aid] = ac
        except Exception as e:
            self._log.error(f"[启动] {ac.get('name', aid)} 启动失败: {e}")
            self.emit_log(f"启动失败: {e}")
            self.emit_error(aid, str(e))
            self._cleanup(aid, -1, [])

    def _launch_core_daily(self, ac: dict, inst_dir: str) -> bool:
        """Drive MaaCore directly for connect-mode daily tasks (no MAA GUI).
        Appends StartUp/Infrast/Recruit/Mall/Award, connects via ADB, starts,
        and reports progress via callbacks. Returns True if Core is usable."""
        aid = ac["id"]
        try:
            from infrastructure import maa_core
            if not maa_core.is_loaded():
                return False
            addr = ac.get("adb_address", "")
            adb_path = ac.get("adb_path", "")
            if not addr or not adb_path:
                self.emit_log(f"{ac.get('name', aid)} ADB 信息不完整，无法 Core 直连")
                return False
            client = ac.get("game_client", "") or "Official"
            connect_cfg = (ac.get("connection_preset") or "MuMuEmulator12")
            connect_cfg = {"MuMuPro": "MuMuEmulator12"}.get(connect_cfg, connect_cfg)

            from services.dispatch_pool import get_template
            _did_key = f"_dispatch_{ac.get('_slot','')}" if ac.get("_slot") else "dispatch_id"
            task_list = get_template(ac.get(_did_key, "")) or ["StartUp", "Infrast", "Recruit", "Mall", "Award"]

            # Task params per MAA Core integration spec
            tasks = []
            for t in task_list:
                if t == "StartUp":
                    tasks.append(("StartUp", {"enable": True, "client_type": client,
                                              "start_game_enabled": True}))
                elif t == "Infrast":
                    tasks.append(("Infrast", {"enable": True, "mode": 0,
                                              "facility": ["Mfg", "Trade", "Power", "Control",
                                                           "Reception", "Office", "Dorm"],
                                              "drones": "Money", "dorm_threshold": 0.3,
                                              "dorm_trust_enabled": True,
                                              "dorm_filter_not_stationed": True,
                                              "originium_shard_auto": True}))
                elif t == "Recruit":
                    tasks.append(("Recruit", {"enable": True, "refresh": True,
                                              "select": [5, 4, 3], "confirm": [5, 4, 3],
                                              "times": 4}))
                elif t == "Mall":
                    tasks.append(("Mall", {"enable": True, "shopping": True,
                                           "credit_fight": False, "visit_friends": True,
                                           "blacklist": ["碳", "家具", "加急许可"]}))
                elif t == "Award":
                    tasks.append(("Award", {"enable": True, "award": True, "mail": True,
                                            "free_gacha": True, "orundum": True}))
                elif t == "Fight":
                    stage = self._pick_daily_stage(ac)
                    tasks.append(("Fight", {"enable": True, "stage": stage, "times": 99,
                                            "medicine": 0, "stone": 0}))
                # Roguelike / Reclamation / Annihilation: skip in daily connect mode

            if not tasks:
                return False

            state = {"finished": False}
            self._core_tasks[aid] = [{"name": n, "status": "排队"} for n, _ in tasks]

            def _cb(msg: int, details: str, _arg=None):
                self._core_callback(aid, msg, details)

            inst = maa_core.create_instance(_cb)
            if not inst:
                return False
            self._core_instances[aid] = inst
            self._active[aid] = ac
            self._start_times[aid] = time.time()

            # Connect
            self.emit_log(f"🔌 Core 直连 ADB({addr}) client={client}")
            ok = inst.connect(adb_path, addr, connect_cfg)
            if not ok:
                self.emit_log(f"{ac.get('name', aid)} Core 连接失败")
                inst.destroy()
                self._core_instances.pop(aid, None)
                self._cleanup(aid, -1, [])
                return False

            # Append tasks
            for name, params in tasks:
                tid = inst.append_task(name, params)
                if tid <= 0:
                    self.emit_log(f"任务添加失败: {name}")
            self.emit_log(f"▶ Core 任务启动: {','.join(n for n, _ in tasks)}")
            inst.start()
            return True
        except Exception as e:
            self._log.error(f"[Core] {ac.get('name', aid)} 直连异常: {e}")
            self.emit_log(f"Core 直连异常: {e}")
            return False

    def _pick_daily_stage(self, ac: dict) -> str:
        """Pick a fight stage for daily connect mode (default 1-7)."""
        stage = ac.get("fight_default", "") or ac.get("smart_stage", "")
        return stage or "1-7"

    def _core_callback(self, aid: str, msg: int, details: str) -> None:
        """Handle MaaCore callbacks (runs on Core's callback thread)."""
        from infrastructure import maa_core as mc
        name = ac.get("name", aid) if (ac := self._active.get(aid)) else aid
        try:
            if msg == mc.MSG_CONNECTION_INFO:
                try:
                    d = json.loads(details)
                    if not d.get("what") == "ConnectSuccess":
                        self.emit_log(f"[Core] {name} 连接状态: {d.get('what','?')}")
                except Exception:
                    pass
            elif msg == mc.MSG_TASK_CHAIN_START:
                try:
                    d = json.loads(details)
                    t = d.get("taskchain", "")
                    self._update_core_task(aid, t, "运行中")
                    self.emit_log(f"▶ {name} 任务开始: {t}")
                except Exception:
                    pass
            elif msg == mc.MSG_TASK_CHAIN_COMPLETED:
                try:
                    d = json.loads(details)
                    t = d.get("taskchain", "")
                    self._update_core_task(aid, t, "完成")
                    self.emit_log(f"✅ {name} 任务完成: {t}")
                except Exception:
                    pass
            elif msg == mc.MSG_TASK_CHAIN_ERROR:
                try:
                    d = json.loads(details)
                    t = d.get("taskchain", "")
                    self._update_core_task(aid, t, "失败")
                    self.emit_log(f"❌ {name} 任务失败: {t}")
                except Exception:
                    pass
            elif msg == mc.MSG_ALL_TASKS_COMPLETED:
                self.emit_log(f"🏁 {name} 全部任务完成")
                inst = self._core_instances.get(aid)
                if inst:
                    try:
                        inst.destroy()
                    except Exception:
                        pass
                self._core_instances.pop(aid, None)
                tasks = list(self._core_tasks.get(aid) or [])
                self._cleanup(aid, 0, tasks)
        except Exception as e:
            self._log.error(f"[Core回调] {name} 处理异常: {e}")

    def _update_core_task(self, aid: str, taskname: str, status: str) -> None:
        tasks = self._core_tasks.get(aid)
        if not tasks:
            return
        for t in tasks:
            if t["name"].lower() == taskname.lower():
                t["status"] = status
                break

    def _spawn_instance(self, exe: Path, ac: dict, inst_dir: str) -> None:
        aid = ac["id"]
        # 2026-08-16: spawn 前清本实例残留 MAA 进程（单实例锁防崩）。
        # 崩溃/被杀后 MAA 可能留 60-170MB 僵尸（窗口无标题或不可见 →
        # _check_startup_window 枚举不到 → 不杀），占住实例目录 → 新 MAA
        # 启动检测到实例被占用 → ErrorCongratulations 对话框 → exit=1
        # 快速失败循环（实测实例 5: 66MB 僵尸卡死官-10 连续崩溃）。
        # 按 CommandLine 精确匹配本实例路径，绝不误杀他实例健康进程。
        try:
            import psutil as _psz
            _real = str(Path(inst_dir).resolve()).lower()
            for _pz in _psz.process_iter(["name", "cmdline", "pid"]):
                try:
                    if (_pz.info["name"] or "").lower() != "maa.exe":
                        continue
                    _cl = " ".join(_pz.info["cmdline"] or []).lower()
                    if _real in _cl:
                        _pz.kill()
                        self.emit_log(f"🧹 清理实例残留 MAA PID={_pz.info['pid']}（{_real}）")
                except Exception:
                    pass
        except Exception:
            pass
        pid_file = Path(inst_dir) / ".pid"
        # asst.log 清空（MAA 句柄占用可能失败 → 失败则记录当前位置，只解析
        # 本次启动后的新日志 — 否则读到上个账号的残留 → 误判降级/完成）
        _al = Path(inst_dir) / "debug" / "asst.log"
        try:
            _al.write_text("")
            self._log_positions[aid] = 0
        except Exception:
            self._log_positions[aid] = _al.stat().st_size if _al.exists() else 0
        # gui.log 不清空（累积）— 记录当前位置，增量读取只看到本次启动的
        # 新内容。"关卡无效"判定（1052-1065）读 gui.log 增量，位置为 0 会
        # 读到上个账号的旧日志（含"添加任务失败"）→ 1 秒误判降级。
        try:
            _gl = Path(inst_dir) / "debug" / "gui.log"
            self._gui_log_positions[aid] = _gl.stat().st_size if _gl.exists() else 0
        except Exception:
            self._gui_log_positions[aid] = 0
        # MAA resolves .\config\gui.json relative to the process working dir.
        # We MUST run with cwd=instance dir or MAA misses the injected config
        # (RunDirectly=False → never auto-starts). Use resolved real path —
        # junction (unresolved) cwd crashed MAA.exe with E_FAIL.
        real_dir = str(Path(inst_dir).resolve())
        # 2026-08-14: 每实例独立 adb server 端口 — 多 MAA 共享全局 adb server
        # (5037) 时并发连接互相干扰 → 约 1/4 启动连接失败（asst.log 断在
        # "retry when connect" → TimerCanceled 快速退出）。adb 客户端原生读
        # ANDROID_ADB_SERVER_PORT 环境变量：给每个实例独立的 server 端口
        # （5037+实例号），连接彻底隔离。MAA 的 adb 子进程继承本环境。
        try:
            _inst_n = int(Path(inst_dir).name)
            _env = dict(os.environ)
            _env["ANDROID_ADB_SERVER_PORT"] = str(5037 + _inst_n)
        except Exception:
            _env = None
        p = subprocess.Popen([str(exe)], shell=False, cwd=real_dir, env=_env)
        p._inst_path = str(Path(inst_dir).resolve())
        self._procs[aid] = p
        self._start_times[aid] = time.time()
        self.ctx.proc_status.add(aid)
        # .meta 与 .pid 成对写（2026-08-14）: 防"pid 活 + meta 空"的幽灵 MAA
        # （并发启动/重启竞态下 _do_launch 写的 meta 可能被清理/覆盖，而
        # spawn 又写入了 pid → 无主 MAA 占实例 → dashboard 显示 MAA-xxxx）。
        # meta 先写：任何时刻 .pid 存在则 .meta 必存在。
        try:
            (Path(inst_dir) / ".meta").write_text(f"{aid}|{ac.get('name', aid)}", encoding="utf-8")
        except Exception:
            pass
        try: pid_file.write_text(str(p.pid))
        except: pass
        self.emit_log(f"✓ 启动 MAA PID={p.pid}")
        import threading as _th
        def _wait_exit():
            try: p.wait()
            except: pass
            # Direct cleanup — QTimer.singleShot from daemon threads may not fire
            self._on_process_exit(aid, p)
        _th.Thread(target=_wait_exit, daemon=True).start()
        # 日志监控线程（事件驱动完成/失败检测 — 2026-08-11 P1 替代 5s 轮询）
        try:
            from services.log_watcher import LogWatcher
            _w = LogWatcher(inst_dir, aid, self._on_log_event, name=ac.get("name", aid))
            _w.start()
            self._watchers[aid] = _w
        except Exception:
            pass
        # 启动后窗口检查（独立线程，不阻塞）— MAA 配置崩溃时 asst.log 为空，
        # 窗口标题（{{ ErrorCongratulations }} / JsonReaderException）是唯一
        # 诊断线索（2026-08-10 配置崩溃事故：日志全空只有窗口能看出来）。
        _th.Thread(target=self._check_startup_window,
                   args=(aid, ac.get("name", aid)), daemon=True).start()
        self.emit_started(aid)

    def _check_startup_window(self, aid: str, name: str) -> None:
        """MAA 启动 5s 后枚举窗口标题，找异常窗口（错误/异常/崩溃）并处理。

        2026-08-14 凌晨：只记日志不够 — ErrorCongratulations 崩掉的 MAA 进程
        不退（僵尸弹窗），占住实例目录文件锁 → 下一个分到该实例的账号必崩
        （实例 3 连环崩 3 次：日-3→日-1→日-2）。此处找到异常窗口后直接杀
        掉对应进程：窗口属于本次启动的 MAA=它已崩，杀了让退出检测走清理
        重试；窗口属于别的进程=僵尸，照样杀（错误弹窗的 MAA 都是死重）。
        """
        try:
            import time as _tt
            _tt.sleep(5)
            import ctypes
            user32 = ctypes.windll.user32
            found: list[tuple[int, str]] = []  # (pid, title)

            def _cb(hwnd, _):
                try:
                    ln = user32.GetWindowTextLengthW(hwnd)
                    if ln and ln < 300:
                        buf = ctypes.create_unicode_buffer(ln + 1)
                        user32.GetWindowTextW(hwnd, buf, ln + 1)
                        t = buf.value
                        if t and any(k in t for k in ("Error", "Exception", "错误", "异常", "崩溃")):
                            _pid = ctypes.c_ulong()
                            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(_pid))
                            found.append((int(_pid.value), t))
                except Exception:
                    pass
                return True

            PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            user32.EnumWindows(PROC(_cb), 0)
            if not found:
                return
            _seen: set[int] = set()
            for _pid, t in found:
                if _pid in _seen:
                    continue
                _seen.add(_pid)
                self._log.warn(f"[MAA窗口] {name} 启动后异常窗口: {t[:100]} (PID={_pid})")
                try:
                    import subprocess as _sp
                    _sp.run(["taskkill", "/F", "/T", "/PID", str(_pid)],
                            capture_output=True, timeout=10)
                    self._log.warn(f"[MAA窗口] {name} 已杀掉异常窗口进程 PID={_pid}")
                    try:
                        from services.event_bus import emit as _eb_emit
                        _eb_emit("maa.zombie_killed", {"name": name, "pid": _pid})
                    except Exception:
                        pass
                except Exception:
                    pass
        except Exception:
            pass

    def _run_probe_for_account(self, aid: str) -> None:
        """Probe 模式执行（关卡检测 — 调度台"仅检测"入队）。

        MAA 只跑 StartUp 登录（注入时已过滤 Probe），登录完成后这里接管：
        逐关 StageProbe 检测（导航 + 判定）→ 结果写 stage_ability →
        触发产线映射 → 完成收尾。检测是子进程（StageProbe 设计），
        不阻塞 MAA 任务链；MAA 此时已登录，直接复用。
        """
        pend = self._probe_pending.pop(aid, None)
        if not pend:
            return
        ac = pend["ac"]
        name = ac.get("name", aid)
        inst_dir = pend["inst_dir"]
        stages = pend.get("probe_stages") or ac.get("probe_stages") or [
            "Chernobog@Annihilation", "LungmenOutskirts@Annihilation",
            "LungmenDowntown@Annihilation"]
        self._probe_running[aid] = True  # 抑制 stall/error 误杀（检测期间 MAA 挂着）
        self.emit_log(f"🔬 {name} 开始关卡检测（{len(stages)} 关）...")

        # MAA GUI 的 StartUp 已完成（游戏已登录）— 先优雅杀掉 MAA，让
        # StageProbe 子进程独占 ADB（双连接会抢端口/冲突 — 2026-08-12）。
        # 2026-08-14 审查 #1: 记录让位时的进程引用 — _on_process_exit 见
        # _probe_running 会跳过退出清理（防关模拟器/失败重入队 → 孤儿探针
        # 与新 MAA 双连 ADB 循环），probe finally 用此身份校验防误伤新运行。
        self._probe_origin[aid] = self._procs.get(aid)
        try:
            _p0 = self._procs.get(aid)
            if isinstance(_p0, subprocess.Popen) and _p0.poll() is None:
                self._kill_maa_graceful(_p0, name)
                time.sleep(1)
        except Exception:
            pass

        def _do_probe():
            try:
                from services.stage_probe import StageProbe
                client = {"Bilibili": "Bilibili", "YoStarJP": "JP",
                          "YoStarEN": "EN"}.get(ac.get("game_client", ""), "Official")
                # 实例号从 inst_dir 推导（检测必须用队列分配的实例，不是
                # 固定实例 1 — 2026-08-12: 模拟器/实例一一对应）。
                _ma = 1
                try:
                    _ma = int(Path(inst_dir).name)
                except Exception:
                    pass
                probe = StageProbe(str(ac.get("emu_instance_index", "")),
                                   client_type=client, maa_instance=_ma)
                results = {}
                for st in stages:
                    try:
                        r = probe.probe(st)
                        results[st] = r.get("verdict", "ERROR")
                        self.emit_log(f"🔬 {name} {st}: {r.get('verdict')} "
                                      f"({r.get('score', '')}) {r.get('detail', '')[:40]}")
                    except Exception as e:
                        results[st] = "ERROR"
                        self.emit_log(f"🔬 {name} {st}: ERROR {e}")
                # 持久化 stage_ability + 产线映射
                try:
                    mw = getattr(self.ctx, "_mw", None)
                    if mw is not None:
                        ability = dict(ac.get("stage_ability") or {})
                        ability.update(results)
                        ac["stage_ability"] = ability
                        from models.config_manager import save_config
                        save_config(mw.config)
                        self.emit_log(f"🔬 {name} 检测完成: " +
                                      ", ".join(f"{k}={v}" for k, v in results.items()))
                    # 桥接 farm_probe.json — farm_dispatch 分派只读这个缓存
                    # （2026-08-14 凌晨：走队列预检活动关后分派认不出结果，
                    # stage_ability 与 farm_probe 两套存储不互通）。
                    try:
                        import json as _json
                        _bp = Path(__file__).parent.parent / "models" / "accounts" / aid / "farm_probe.json"
                        _cache = {}
                        if _bp.exists():
                            _cache = _json.loads(_bp.read_text(encoding="utf-8"))
                        for _st, _v in results.items():
                            _cache[_st] = {"verdict": _v,
                                           "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
                        _bp.parent.mkdir(parents=True, exist_ok=True)
                        _bp.write_text(_json.dumps(_cache, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
                    except Exception:
                        pass
                except Exception as e:
                    self.emit_log(f"🔬 {name} 结果持久化失败: {e}")
            except Exception as e:
                self.emit_log(f"🔬 {name} 检测异常: {e}")
            finally:
                self._probe_running.pop(aid, None)  # 检测结束，恢复 stall/error 检测
                # 完成收尾（复用正常完成路径 — 关闭游戏 + cleanup + 出队）。
                # 2026-08-14 审查 #1: 身份校验 — 仅当 _procs[aid] 仍是 probe
                # 让位时那个进程（或 None=让位前无进程）才收尾；probe 期间
                # 账号被外部 stop/重入队 → 新 MAA 已接管 → 不得误伤。
                try:
                    _origin = self._probe_origin.get(aid)
                    _ac = self._active.get(aid)
                    _p = self._procs.get(aid)
                    if _ac and isinstance(_p, subprocess.Popen) and _p is _origin:
                        self._finish_completed(aid, _ac, _p)
                    elif _ac and isinstance(_p, subprocess.Popen):
                        self.emit_log(f"🔬 {name} 检测完成但进程已被接管，跳过收尾"
                                      f"（防误伤新运行）")
                except Exception:
                    pass
                self._probe_origin.pop(aid, None)

        threading.Thread(target=_do_probe, daemon=True).start()

    def _save_completion_shot(self, aid: str, ac: dict) -> None:
        """完成截图留档（2026-08-15 用户）: MAA 跑完时截 1 张模拟器画面，
        存 logs/shots/<aid>_<ts>.png（目标机本地）。低频单张，不走轮询。
        adb exec-out screencap 直出 PNG（PowerShell > 会 UTF-16 损坏，用
        subprocess 二进制）。失败静默（截图不影响主流程）。
        """
        try:
            import subprocess as _sp
            _adb = ac.get("adb_path", "") or "adb"
            _addr = ac.get("adb_address", "")
            if not _addr:
                return
            r = _sp.run([_adb, "-s", _addr, "exec-out", "screencap", "-p"],
                        capture_output=True, timeout=15,
                        creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0))
            if r.returncode != 0 or len(r.stdout) < 1000:
                return
            _d = Path(__file__).parent.parent / "logs" / "shots"
            _d.mkdir(parents=True, exist_ok=True)
            _ts = time.strftime("%Y%m%d_%H%M%S")
            (_d / f"{aid}_{_ts}.png").write_bytes(r.stdout)
            self.emit_log(f"📸 {ac.get('name', aid)} 完成截图已存 logs/shots/")
        except Exception:
            pass

    def _save_failure_shot(self, aid: str, ac: dict, exit_code: int, inst_dir) -> None:
        """失败截图留档（2026-08-15 用户: 日常失败也要收集日志和图片）—
        异常退出时截 1 张模拟器画面，存两处:
          logs/shots/<aid>_fail_<ts>.png          平铺视图（截图页可见）
          logs/maa_history/<aid>/<ts>_fail.png    账号历史目录（与文字日志同处）
        必须在杀 MAA 进程**之前**调用（ADB 还通、画面还是失败现场）。
        失败静默（截图不影响主流程）。"""
        try:
            import subprocess as _sp
            _adb = ac.get("adb_path", "") or "adb"
            _addr = ac.get("adb_address", "")
            if not _addr:
                return
            r = _sp.run([_adb, "-s", _addr, "exec-out", "screencap", "-p"],
                        capture_output=True, timeout=15,
                        creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0))
            if r.returncode != 0 or len(r.stdout) < 1000:
                return
            _ts = time.strftime("%Y%m%d_%H%M%S")
            _d = Path(__file__).parent.parent / "logs" / "shots"
            _d.mkdir(parents=True, exist_ok=True)
            (_d / f"{aid}_fail_{_ts}.png").write_bytes(r.stdout)
            # 历史目录归档（与 asst/gui 日志同处 — 用户: 失败要能连图带文一起看）
            if inst_dir:
                try:
                    _hist = Path(__file__).parent.parent / "logs" / "maa_history" / aid
                    _hist.mkdir(parents=True, exist_ok=True)
                    (_hist / f"{_ts}_fail_{exit_code}.png").write_bytes(r.stdout)
                except Exception:
                    pass
            # MAA 界面识别截图顺带归档（失败前的画面证据 — 收集器 60s 才扫，
            # 失败瞬间的最新图可能还没被拷贝）
            if inst_dir:
                try:
                    _inst = Path(inst_dir)
                    _hist = Path(__file__).parent.parent / "logs" / "maa_history" / aid
                    _hist.mkdir(parents=True, exist_ok=True)
                    for _sub, _pre in (("debug/interface", "i_"), ("debug/drops", "d_")):
                        _sdir = _inst / _sub
                        if _sdir.is_dir():
                            _pngs = sorted(_sdir.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
                            for _png in _pngs[:3]:  # 每类最新 3 张
                                try:
                                    _dest = _hist / f"{_ts}_{_pre}{_png.name}"
                                    if not _dest.exists():
                                        import shutil as _su
                                        _su.copy2(_png, _dest)
                                except Exception:
                                    pass
                except Exception:
                    pass
            self.emit_log(f"📸 {ac.get('name', aid)} 失败截图已存 logs/shots/ (exit={exit_code})")
        except Exception:
            pass

    def _finish_completed(self, aid: str, ac: dict, p) -> None:
        """完成收尾（AllTasksCompleted）— 轮询检测与 LogWatcher 事件共用。
        完成标记 _done_flags 防 _wait_exit 抢先 cleanup 的竞态（2026-08-11）。
        """
        try:
            self._done_flags[aid] = True
            self._stall_skip.pop(aid, None)  # 正常完成重置空转计数
        except Exception:
            pass
        self.emit_log(f"[完成后] {ac.get('name', aid)} 任务全部完成")
        # 完成截图留档（2026-08-15 用户: MAA 做完了给模拟器截个图存本地）—
        # 每个账号跑完截 1 张（低频，无高频截图崩溃风险），保存到
        # logs/shots/<aid>_<ts>.png，回看「跑完的画面」验证现场。
        try:
            self._save_completion_shot(aid, ac)
        except Exception:
            pass
        try:
            from services.event_bus import emit as _eb_emit
            _eb_emit("queue.completed", {"name": ac.get("name", aid)})
        except Exception:
            pass
        # 肉鸽主题推进（l-4/l-6 — 满级停止后换下一主题，2026-08-13 用户:
        # MAA 自己刷到满级就停止，停止后我们切下一个主题继续）。
        try:
            _themes = ac.get("roguelike_themes") or []
            if ac.get("roguelike_only") and _themes:
                _idx = int(ac.get("roguelike_theme_idx", 0) or 0)
                _next = (_idx + 1) % len(_themes)
                ac["roguelike_theme_idx"] = _next
                try:
                    mw = getattr(self.ctx, "_mw", None)
                    if mw is not None:
                        from models.config_manager import save_config
                        save_config(mw.config)
                except Exception:
                    pass
                self.emit_log(f"🎮 {ac.get('name', aid)} 肉鸽主题推进: "
                              f"{_themes[_idx]} → {_themes[_next]}（下一轮生效）")
        except Exception:
            pass
        tasks, sanity, drops = self._parse_log(aid)
        _cur = (sanity or {}).get("current")
        _mx = (sanity or {}).get("max") or 1
        # 假完成拦截（2026-08-15 日-4 实锤第二机制）: TaskChainError 也会
        # 触发 AllTasksCompleted — MAA 识别界面失败→报错→被当"完成"→体力
        # 不刷还续跑循环（StartUpBegin 50 次重试失败 + AwardBegin 失败）。
        # 与下方 Fight 空转复核互补（那个只管 times_finished=0）。
        if self._run_chain_error.get(aid):
            self.emit_log(f"⚠ {ac.get('name', aid)} 任务链报错后假完成，按失败重试")
            self._run_chain_error.pop(aid, None)
            self._done_flags.pop(aid, None)
            self._cleanup(aid, -13, tasks, sanity, drops)
            return
        # 刷关空转复核（2026-08-14 凌晨教训：MAA 连战失效/关卡不可用时
        # Fight 任务 0 把也报 AllTasksCompleted，任务链"假完成"骗过判定 —
        # 日服 4 号全部 times_finished=0、体力 172/172 原封不动）。条件：
        # 本次注入含 Fight 且收到 FightTimesTaskPlugin 信号且 0 把 → 判
        # 失败重试（-13 不关模拟器，走未完成退出重试链）。
        _inj = list(ac.get("_injected_tasks") or [])
        _ft = self._fight_times.get(aid, -1)
        if "Fight" in _inj and _ft == 0:
            self.emit_log(f"⚠ {ac.get('name', aid)} 刷关空转（Fight 0 把，体力未消耗），按失败重试")
            self._done_flags.pop(aid, None)
            tasks, sanity, drops = self._parse_log(aid)
            self._cleanup(aid, -13, tasks, sanity, drops)
            return
        _stop_threshold = int(ac.get("stop_sanity_pct", 30) or 30)
        _done_pct = (_cur / _mx * 100) if _cur is not None else 0
        _final = _done_pct <= _stop_threshold
        self._kill_maa_graceful(p, ac.get('name', aid))
        # 完成后关闭游戏（用户: MAA 做完任务后让他关闭游戏 — 游戏退掉模拟器
        # 空载，vGPU 渲染停止压力大降（60帧场景下 VAddress 错误减少），
        # 模拟器本身交给回收统一关闭 — 回收关闭前游戏已停 → 不弹崩溃报告）
        try:
            _adb = ac.get("adb_path", "") or "adb"
            _addr = ac.get("adb_address", "")
            if _addr:
                from services.emu_service import force_stop_games
                try:
                    subprocess.run([_adb, "connect", _addr], capture_output=True,
                                   timeout=8, creationflags=CF)
                except Exception:
                    pass
                force_stop_games(_adb, _addr)
                self.emit_log(f"🎮 {ac.get('name', aid)} 已完成，游戏已关闭（模拟器交给回收）")
        except Exception:
            pass
        self._cleanup(aid, 0, tasks, sanity, drops)
        # 肉鸽账号（l-4/l-6）24*7 自动续跑：满级停止/一局结束都重新入队，
        # 下一轮用推进后的主题（2026-08-13）。slot=roguelike 让短任务优先。
        if ac.get("roguelike_only"):
            try:
                mw = getattr(self.ctx, "_mw", None)
                lq = getattr(mw, "launch_queue", None)
                if lq is not None and ac.get("emu_instance_index"):
                    self.emit_log(f"↻ {ac.get('name', aid)} 肉鸽一轮结束，自动续跑（24*7）")
                    lq.enqueue(aid, "auto", priority=1000, slot="roguelike")
                    lq.tick()
            except Exception:
                pass
        elif not _final and not ac.get("_connect_only"):
            try:
                mw = getattr(self.ctx, "_mw", None)
                lq = getattr(mw, "launch_queue", None)
                if lq is not None and ac.get("emu_instance_index"):
                    self.emit_log(f"↻ {ac.get('name', aid)} 一轮完成（体力 {_done_pct:.0f}%），自动续跑")
                    lq.enqueue(aid, "auto", priority=0, slot=ac.get("_slot", ""))
                    lq.tick()
            except Exception:
                pass

    def _on_log_event(self, event: str, aid: str, line: str) -> None:
        """LogWatcher 事件回调（事件驱动，<0.2s 响应 — 2026-08-11 P1）。"""
        if event == "completed":
            ac = self._active.get(aid)
            p = self._procs.get(aid)
            if not ac or not isinstance(p, subprocess.Popen) or p.poll() is not None:
                return  # 已清理/已结束 — 防重复触发
            # Probe 模式（关卡检测）：MAA 只跑 StartUp 登录，登录完成后
            # 由 StageProbe 逐关检测，完成后才走完成收尾（2026-08-12）。
            if aid in self._probe_pending:
                self._run_probe_for_account(aid)
                return
            self._finish_completed(aid, ac, p)
        elif event == "adb_connected":
            # MAA 第一次成功调 adb = 已连接模拟器 — 启动节流就绪信号
            # （等上一台连上才启动下一台，2026-08-12 用户）。
            if not self._adb_connected.get(aid):
                self._adb_connected[aid] = True
                try:
                    mw = getattr(self.ctx, "_mw", None)
                    lq = getattr(mw, "launch_queue", None)
                    if lq is not None:
                        lq._mark_launch_ready(aid)
                except Exception:
                    pass
                # EmulatorService v2 占用标记（2026-08-15 接入: 模拟器就绪 +
                # MAA 已连 = 真正占用 → BUSY。此前的 acquire 在模拟器 launch
                # 前调用（OFF→PREWARMING 冷启动路径），adb_connected 是
                # 就绪确认点，acquire_ready 保证 BUSY 状态落地（回收判定
                # 依据）。幂等: 已 BUSY 返回 False 无害。
                try:
                    _v2 = getattr(getattr(self.ctx, "_mw", None), "emu_service_v2", None)
                    _ac2 = self._active.get(aid)
                    if _v2 and _ac2:
                        _v2.acquire_ready(_ac2)
                except Exception:
                    pass
        elif event == "sanity_before":
            # 本次运行首次进关卡体力（真实消耗统计的"首"值 — 2026-08-13）
            if aid not in self._run_sanity_first:
                try:
                    from services.sanity_tracker import get as _sg
                    _snap = _sg(aid)
                    if _snap and _snap.get("current") is not None:
                        self._run_sanity_first[aid] = dict(_snap)
                except Exception:
                    pass
        elif event == "fight_times":
            # 刷关次数信号（2026-08-15 收敛: 原 _update_status 自读日志解析，
            # 改 LogWatcher 单点解析发事件）— 假完成复核（Fight 0 把也报
            # AllTasksCompleted → 体力满着骗过完成判定）。
            try:
                _jm = json.loads(line[line.index("{"):line.rindex("}") + 1]) if "{" in line else None
                if _jm:
                    _det = _jm.get("details") or {}
                    if "times_finished" in _det:
                        self._fight_times[aid] = int(_det.get("times_finished") or 0)
            except Exception:
                pass
        elif event == "medicine_used":
            # 理智药使用标记（过期药突击识别 — 2026-08-13 用户: 48h 药
            # 挤一天刷完导致当天消耗虚高，预测时排除这类运行）
            self._run_medicine_used[aid] = True
        elif event == "taskchain_error":
            # 任务链错误（2026-08-15 日-4 实锤: MAA 识别界面失败 → 报错 →
            # TaskChainError 仍触发 AllTasksCompleted = 假完成 → 体力不刷还
            # 续跑循环）。标记本次运行，完成判定时拦截转 -13 重试。
            if not self._probe_running.get(aid):
                self._run_chain_error[aid] = True
        elif event == "subtask_error":
            # Probe 检测进行中（MAA 挂着等 StageProbe 子进程）— 跳过错误
            # 窗口检测，防误杀（2026-08-12 关卡检测调度）。
            if self._probe_running.get(aid):
                return
            # 2026-08-15 收敛: _err_count 原在 _update_status 自读日志递增
            # （已被移除）— 改在此事件源递增（LogWatcher 解析出 SubTaskError
            # 即发事件，同一信号更可靠）。错误阈值判定（_check_one 中
            # err_count>=3）依赖它。
            try:
                ac = self._active.get(aid)
                if ac:
                    ac["_err_count"] = ac.get("_err_count", 0) + 1
            except Exception:
                pass
            # 连续子任务错误（游戏崩溃/流程异常 → MAA 反复报错，2026-08-11
            # 用户: MAA 日志会报任务错误）— 2 分钟内 ≥3 次 + asst.log 停滞
            # （MAA 无进展）才判定异常。启动早期（游戏加载）的正常识别
            # 重试也会报 SubTaskError 但日志持续写入 → 不误杀
            # （2026-08-11 日-2: 3m45s 被误杀重启 — 用户观察"挺正常的"）。
            try:
                win = self._err_windows.get(aid, [])
                now = time.time()
                win = [t for t in win if now - t < 120]
                win.append(now)
                self._err_windows[aid] = win
                if len(win) >= 3:
                    # 日志活性：MAA 还在重试（日志写）→ 正常，重置窗口
                    p = self._procs.get(aid)
                    _stalled = False
                    try:
                        if isinstance(p, subprocess.Popen):
                            _al = Path(getattr(p, "_inst_path", "")) / "debug" / "asst.log"
                            if _al.exists():
                                _stalled = time.time() - _al.stat().st_mtime > 60
                    except Exception:
                        pass
                    if not _stalled:
                        self._err_windows[aid] = []
                        return
                    self._err_windows.pop(aid, None)
                    ac = self._active.get(aid)
                    if ac and isinstance(p, subprocess.Popen) and p.poll() is None:
                        self.emit_log(f"⚠ {ac.get('name', aid)} 连续子任务错误且日志停滞（游戏异常/崩溃），重启 MAA")
                        self._kill_maa_graceful(p, ac.get('name', aid))
                        tasks, sanity, drops = self._parse_log(aid)
                        self._cleanup(aid, -9, tasks, sanity, drops)
            except Exception:
                pass
        elif event == "maa_stopped":
            # MAA 自己停止（按钮状态=停止 — 任务结束/停止）→ 重置空转/错误
            # 计数（避免后续误判 — 2026-08-12 用户: 检测 MAA 自身运行状态）
            try:
                self._stall_skip.pop(aid, None)
                self._err_windows.pop(aid, None)
            except Exception:
                pass
        elif event == "stall_loop":
            # Probe 检测进行中（MAA 挂着等 StageProbe）— 跳过空转检测
            if self._probe_running.get(aid):
                return
            # 空转检测（DoNothing 循环 ~3-5 分钟 — 2026-08-11 官-41 PRTS1
            # 空转不触发卡死检测的盲区: 日志持续写 + 无 SubTaskError）。
            # 第 1 次: 杀 MAA 自动重启（偶发画面卡）；第 2 次: 关卡不可用
            # （PRTS 剿灭识别失败）→ 清剿灭配置防死循环。
            try:
                _n = self._stall_skip.get(aid, 0) + 1
                self._stall_skip[aid] = _n
                ac = self._active.get(aid)
                p = self._procs.get(aid)
                if ac and isinstance(p, subprocess.Popen) and p.poll() is None:
                    if _n >= 2:
                        for _key in ("smart_annihilation", "annihilation"):
                            if ac.get(_key):
                                ac[_key] = ""
                        if ac.get("stages"):
                            ac["stages"] = [s for s in ac["stages"] if not str(s).startswith("PRTS")]
                        try:
                            from models.config_manager import save_config
                            save_config(self.ctx.config)
                            self.emit_log(f"⏭ {ac.get('name', aid)} 空转 2 次，已跳过剿灭（PRTS 识别失败）")
                        except Exception:
                            pass
                    self.emit_log(f"⚠ {ac.get('name', aid)} 任务空转（DoNothing 循环 {_n} 次），重启 MAA")
                    self._kill_maa_graceful(p, ac.get('name', aid))
                    tasks, sanity, drops = self._parse_log(aid)
                    self._cleanup(aid, -11, tasks, sanity, drops)
            except Exception:
                pass
        elif event == "battle_failed":
            # 作战失败降级触发（FightMissionFailed）— 与 _check_one 的判定
            # 共用防重（_downgrading 标志）
            ac = self._active.get(aid)
            p = self._procs.get(aid)
            if not ac or not isinstance(p, subprocess.Popen):
                return
            if (ac.get("_stage_fallback") and aid not in self._stopping
                    and not self._downgrading.get(aid)):
                try:
                    self._downgrading[aid] = True
                    self._downgrade_stage(aid, ac, p)
                finally:
                    self._downgrading.pop(aid, None)
        # exceeded: 重试耗尽 — 由 _check_one 的 ExceededLimit 判定处理（暂不迁移）

    def _on_process_exit(self, aid: str, p: subprocess.Popen) -> None:
        # Re-entrancy guard: only the CURRENT process object may clean up.
        # A stale exit callback from a superseded process (downgrade restart
        # killed the old MAA and spawned a new one) must NOT clean up — it
        # would wipe the new process's state mid-run (orphan MAA + queue
        # mark released while the new process is still farming).
        if self._procs.get(aid) is not p:
            return
        # Probe 让位（2026-08-14 审查 #1）: StageProbe 检测前主动优雅杀 MAA —
        # 退出是预期行为。若走正常退出判定：rc=1 + StartUp"完成" → 按完成
        # 处理关模拟器（probe 子进程还在用！）; rc=1 + 任务"运行中" → 未完成
        # 退出 -13 重入队（新 MAA 与孤儿探针双连 ADB 循环）。两者都错 →
        # 跳过，清理统一由 probe finally 收尾。
        if self._probe_running.get(aid):
            self._log.info(f"[probe] {aid} MAA 已退出（StageProbe 让位），跳过退出清理")
            return
        rc = p.poll()
        tasks, sanity, drops = self._parse_log(aid) if aid in self._active else ([], None, None)
        # MAA exiting within 60s of launch with no completed tasks = startup
        # failure (emulator didn't come up / ADB lost), NOT a normal "done".
        # Otherwise a failed boot is recorded as a successful run and the
        # emulator gets shut down while the queue moves on.
        started = self._start_times.get(aid, 0)
        if rc == 0 and started and time.time() - started < 60 and not tasks:
            self._log.warn(f"[启动失败] {aid} MAA {int(time.time()-started)}s 退出且无任务，按失败处理")
            rc = -12
        # MAA 自行退出但任务链未完成（自动更新重启/资源更新/外部中断）:
        # rc=0 + 有任务还在"运行中" → 不是正常完成 → 按异常处理（自动重启
        # 重试任务）。否则被当作"刷完" → 关模拟器 → 任务丢失（2026-08-10
        # 用户指出: 队伍健康时 MAA 退出应该重试而不是结束）。
        if rc == 0 and any(t.get("status") == "运行中" for t in tasks):
            self._log.warn(f"[未完成退出] {aid} MAA 退出但任务未完成（更新/中断），按异常重试")
            rc = -13
        # PostActions=ExitSelf may exit with a non-zero code even on NORMAL
        # completion. If tasks were completed, treat it as success regardless.
        # _done_flags: 完成检测（AllTasksCompleted 增量）terminate 的 MAA 会被
        # _wait_exit 抢先走这里（rc=1），增量检测的 cleanup(0) 因 _procs 已清
        # 落空 → 完成变 exit=1 → 模拟器不按"完成"关闭（2026-08-11 b-2/b-9）。
        if (self._done_flags.pop(aid, False)
                or (rc not in (0, None) and any(t.get("status") == "完成" for t in tasks))):
            self._log.info(f"[完成后] {aid} MAA 退出(rc={rc}) 但任务已完成，按正常完成处理")
            rc = 0
        self._cleanup(aid, rc or 0, tasks, sanity, drops)

    # ── Monitoring ──

    def check_processes(self) -> None:
        """Monitor all running processes. Called by queue tick every 5s."""
        self._check_resources()
        # 不再做全局弹窗扫描 — 2026-08-10 用户指出：窗口只要动一下（位置/尺寸
        # 变化）就可能被识别为弹窗处理，误伤正常窗口。弹窗只应在我们自己的
        # 主动关闭流程内处理（_downgrade_stage 的 WM_CLOSE 闭环、stop_all、
        # /api/system/close_popups 手动端点），不做无人值守的全窗口扫描。
        for aid in list(self._procs.keys()):
            self._check_one(aid)

    def _check_resources(self) -> None:
        """Monitor system memory and process resource usage."""
        try:
            import psutil
        except ImportError:
            return
        sv = psutil.virtual_memory()
        free_gb = sv.available / 1024 / 1024 / 1024
        now = time.time()
        for aid in list(self._procs.keys()):
            p = self._procs.get(aid)
            info = self._proc_info.setdefault(aid, {"maa": {}, "emu": {}})
            if p and not isinstance(p, str) and hasattr(p, 'pid'):
                try:
                    pp = psutil.Process(p.pid)
                    mem_mb = pp.memory_info().rss / 1024 / 1024
                    cpu_pct = pp.cpu_percent(interval=0.05)
                    info["maa"] = {"mem_mb": mem_mb, "cpu_pct": cpu_pct, "pid": p.pid, "time": now}
                    if mem_mb > self.MAX_PROC_MEM_MB:
                        name = self._active.get(aid, {}).get("name", aid)
                        self.emit_log(f"[资源] {name} 内存超限 ({mem_mb:.0f}MB)")
                        self.stop(aid)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    info["maa"] = {}
            else:
                info["maa"] = {}
            ac = self._active.get(aid)
            if ac:
                addr = ac.get("adb_address", "")
                epid = self._find_emu_pid(addr)
                if epid:
                    try:
                        ep = psutil.Process(epid)
                        emu_mem = ep.memory_info().rss / 1024 / 1024
                        emu_cpu = ep.cpu_percent(interval=0.05)
                        info["emu"] = {"mem_mb": emu_mem, "cpu_pct": emu_cpu, "pid": epid, "time": now, "name": ep.name()}
                    except:
                        info["emu"] = {}
                else:
                    info["emu"] = {}
        was = self._overloaded
        # 过载阈值（可配 overload_mem_gb，默认 4GB）— 2026-08-14 事故修复：
        # 原 1.0GB 阈值太低 — 等内存只剩 1GB 才停新任务，此时模拟器堆积已把
        # 系统拖垮（StartUp 卡加载/MuMuManager 超时）。提前到 4GB 停新启动，
        # 配合回收器内存强制回收（reclaim_force_mem_gb，2GB）留出恢复空间：
        # 4GB 止血 → 2GB 清闲置模拟器 → 内存回升 → 自动恢复轮转。
        _thr = float(self.ctx.config.get("overload_mem_gb", 4.0) or 4.0)
        self._overloaded = free_gb < _thr
        if self._overloaded != was:
            self.emit_log(f"[资源] 内存 {'不足' if self._overloaded else '已恢复'} ({free_gb:.1f}GB，阈值 {_thr:.0f}GB)")

    def _check_one(self, aid: str) -> None:
        p = self._procs.get(aid)
        if p is None or isinstance(p, str):
            return
        if not hasattr(p, 'poll'):
            return
        if p.poll() is not None:
            # Process died — cleanup stale marks immediately (fallback if the
            # _wait_exit thread is gone). Otherwise a dead MAA blocks relaunch
            # with a stale "already running" mark indefinitely.
            self._log.warn(f"[进程] {aid} MAA 进程已退出 (poll={p.poll()})，清理残留")
            self._on_process_exit(aid, p)
            return
        self._update_status(aid)
        # Process pair health check
        # ── _check_one 段落导航（2026-08-15 整理，538 行拆分标记）──
        #   ① 进程存活检查       L1949  poll 非 None → _on_process_exit
        #   ② 模拟器失联判定     L1962  MuMuManager 两轮确认（60s spawn 宽限）
        #   ③ MAA 心跳检查       L2053  10s 断 → 杀（60s spawn 宽限 + 审计钩子）
        #   ④ 模拟器进程消失     L2110  psutil 3 次 → 重启模拟器
        #   ⑤ 任务完成检测       L2130  AllTasksCompleted 增量 + 尾部扫描
        #   ⑥ 关卡降级           L2133  Fight 无效/战败 → 降级链
        #   ⑦ 卡死检测           L2226  PRTS 停滞 / stuck_timeout / 运行超时
        #   ⑧ 错误阈值           L2300  err_count≥3 → 杀+重启模拟器
        #   ⑨ 启动/空闲挂起     L2344  无任务 / 日志静默 / StartUp 卡加载
        #   ⑩ ADB keepalive     L2441  30s 批量检查（仅日志不杀）
        info = self._proc_info.get(aid, {})
        emu = info.get("emu", {})
        emu_pid = emu.get("pid")
        ac = self._active.get(aid)
        # MuMuManager 真实验证模拟器进程在跑 — _find_emu_pid 按 ADB 端口查
        # 监听进程，模拟器死后残留的 ADB 端口会误判"模拟器还在" → MAA 在
        # 假画面空转、无人处理（2026-08-10 官-33: MAA 活着 + 模拟器4进程没了
        # + asst.log 活跃（残留端口截图）→ 卡死到永远）。
        if ac and aid not in self._stopping and not self._probe_running.get(aid):
            emu_idx = ac.get("emu_instance_index", "")
            if emu_idx:
                cli = find_mumu_cli()
                if cli is None and ac.get("adb_path"):
                    try:
                        cand = Path(ac["adb_path"]).parent / "MuMuManager.exe"
                        if cand.exists():
                            cli = str(cand)
                    except Exception:
                        pass
                if cli:
                    idx_flag = "-v" if "MuMuManager" in cli else "--vmindex"
                    try:
                        r = subprocess.run([cli, "info", idx_flag, str(emu_idx)],
                                           capture_output=True, text=True, timeout=5,
                                           creationflags=CF, encoding="utf-8", errors="replace")
                        if r.returncode == 0:
                            d = json.loads(r.stdout.lstrip("\ufeff").strip())
                            # bool 防御：MuMuManager 错误返回（errcode≠0）无
                            # is_* 字段 → None → 误判"失联"→ 杀 MAA + 关模拟器
                            # （2026-08-10 实测 3 台同时误判）。无法确认 → 跳过。
                            _pa = d.get("is_android_started"); _pp = d.get("is_process_started")
                            # 连续 2 轮确认才杀 — MuMuManager shutdown 执行窗口会
                            # 短暂对**其他** VM 返回合法的 false/false（bool 防御拦
                            # 不住）：2026-08-12 b-2 完成关模拟器时 6 台在跑账号
                            # 同时被误判"失联"→ 杀 MAA → 集体 -8 → 自动重启循环。
                            if isinstance(_pa, bool) and isinstance(_pp, bool) and not (_pa or _pp):
                                # 2026-08-14: MAA 刚 spawn 的宽限（60s）— 模拟器
                                # 可能在 MAA spawn 前被回收（启动失败→回排→
                                # 回收→自动重启时序），刚 spawn 的 MAA 尚在
                                # 尝试连接，此刻判失联=必杀（日-2 循环 exit=-8）。
                                if time.time() - self._start_times.get(aid, 0) < 60:
                                    self._emu_lost_suspect.pop(aid, None)
                                    return
                                # 2026-08-14: 有模拟器关闭进行中 → MuMuManager
                                # 忙碌，info 对其他 VM 可能误报 false/false
                                # （v3 并发回收实测 b-5/官-12 被误杀 → 自动重启
                                # 风暴）→ 本轮豁免判定并清嫌疑，关闭窗口过后
                                # 再查。
                                try:
                                    from services.emu_service import closing_in_progress as _cip
                                    if _cip():
                                        self._emu_lost_suspect.pop(aid, None)
                                        return
                                except Exception:
                                    pass
                                if aid in self._emu_lost_suspect:
                                    # 第 2 轮仍失联 → 确认，杀 MAA 恢复
                                    # 2026-08-14 修复：此前不读时间戳 — 嫌疑标记
                                    # 一旦落上永不超时（失联恢复后 else 分支会
                                    # 清，但若 else 不走到则残留），跨运行误杀
                                    # 面（cd3d400 审查复核）。读时间戳：仅相邻
                                    # 轮次（≤30s）的嫌疑才升级为确认。
                                    _sus_ts = self._emu_lost_suspect.get(aid, 0)
                                    if not _sus_ts or time.time() - _sus_ts > 30:
                                        self._emu_lost_suspect[aid] = time.time()
                                        return
                                    self._emu_lost_suspect.pop(aid, None)
                                    name = ac.get("name", aid)
                                    self._log.warn(f"[模拟器失联] {name} 模拟器#{emu_idx} 进程不在（连续 2 轮确认），杀 MAA 恢复")
                                    # 守护审计钩子: 失联是误杀高发区 —
                                    # 先留痕待确认（判据=真实进程状态快照）
                                    if self._guard_kill(aid, "kill_maa",
                                                        f"模拟器#{emu_idx} 进程不在(连续2轮)",
                                                        evidence=f"emu_idx={emu_idx} emu_pid={emu_pid} lost_confirm=2"):
                                        try:
                                            p.terminate()
                                            p.wait(3)
                                        except Exception:
                                            pass
                                        tasks, sanity, drops = self._parse_log(aid)
                                        self._cleanup(aid, -8, tasks, sanity, drops)
                                        return
                                    else:
                                        self._log.info(f"[守护审计] {name} 模拟器失联杀暂缓（待确认）")
                                # 第 1 次失联 → 只记嫌疑，下一轮确认（5s 后）
                                self._emu_lost_suspect[aid] = time.time()
                                name = ac.get("name", aid)
                                self._log.warn(f"[模拟器失联?] {name} 模拟器#{emu_idx} 疑似不在（第1次，5s 后确认）")
                                return
                            else:
                                # info 正常或无法确认 → 清嫌疑（防累积误杀）
                                self._emu_lost_suspect.pop(aid, None)
                    except Exception:
                        pass
        # MAA 心跳检查（RemoteControl — MAA 每秒轮询 = 进程活着+网络通。
        # 心跳断 >10s = MAA 停止/假死 — 比日志活性检测（10 分钟）快得多。
        # 2026-08-12 用户: 检测 MAA 自己是否在运行。排除: 未配心跳的旧
        # MAA（_last=0）/正常停止中（_stopping）/完成收尾（_done_flags）。
        try:
            _hb = getattr(getattr(self.ctx, "_mw", None), "_maa_heartbeat", {}) or {}
            _last = _hb.get(aid, 0)
            # 心跳检查在 probe 期间跳过 — StageProbe 让位时 MAA 已死，
            # 心跳断是预期（probe 期间模拟器/游戏归 StageProbe 全权管理）
            if (_last and aid not in self._stopping and not self._done_flags.get(aid)
                    and not self._probe_running.get(aid)):
                # 2026-08-14: spawn 宽限（与模拟器失联检查对齐）— MAA 刚
                # spawn 时 _hb[aid] 还是**上一次运行**的旧心跳，新 MAA 首次
                # POST 心跳前（约 1-5s）检查轮次会看到"断 >10s"→ 误杀刚
                # 启动的 MAA（官-38 型: spawn 后 8-60s 即死的候选窗口）。
                if time.time() - self._start_times.get(aid, 0) < 60:
                    pass
                elif time.time() - _last > 10:
                    name = ac.get("name", aid) if ac else aid
                    self._log.warn(f"[MAA心跳] {name} 心跳断 {int(time.time()-_last)}s（MAA 停止/假死），杀 MAA 恢复")
                    # 守护审计钩子（2026-08-14）: 心跳断属于"疑似假死"—
                    # 先留痕待确认，观察窗口内未放行则暂缓（宁可多等不可错杀）
                    if self._guard_kill(aid, "kill_maa",
                                        f"心跳断 {int(time.time()-_last)}s",
                                        evidence=f"heartbeat_age={int(time.time()-_last)}s"):
                        try:
                            self._kill_maa_graceful(p, name)
                        except Exception:
                            pass
                        tasks, sanity, drops = self._parse_log(aid)
                        self._cleanup(aid, -8, tasks, sanity, drops)
                        return
                    else:
                        self._log.info(f"[守护审计] {name} 心跳断杀暂缓（待确认）")
        except Exception:
            pass
        if ac and emu_pid and aid not in self._stopping and not self._probe_running.get(aid):
            name = ac.get("name", aid)
            try:
                import psutil as _pu
                ep = _pu.Process(emu_pid)
                if ep.status() == _pu.STATUS_ZOMBIE:
                    raise _pu.NoSuchProcess(emu_pid)
            except Exception:
                fail = self._emu_fail_count.get(aid, 0) + 1
                self._emu_fail_count[aid] = fail
                self._log.warn(f"[进程组] {name} 模拟器进程消失 (PID={emu_pid}) 第{fail}次")
                p = self._procs.get(aid)
                if fail >= 3 or (p and hasattr(p, 'poll') and p.poll() is not None):
                    self._emu_fail_count.pop(aid, None)
                    if p and not isinstance(p, str):
                        try: p.terminate(); p.wait(3)
                        except: pass
                    tasks, sanity, drops = self._parse_log(aid)
                    self._cleanup(aid, -8, tasks, sanity, drops)
                    return
                emu_idx = ac.get("emu_instance_index", "")
                cli = find_mumu_cli()
                if cli is None and ac.get("adb_path"):
                    try:
                        cand = Path(ac["adb_path"]).parent / "MuMuManager.exe"
                        if cand.exists():
                            cli = str(cand)
                    except Exception:
                        pass
                if cli and emu_idx:
                    idx_flag = "-v" if "MuMuManager" in cli else "--vmindex"
                    self.emit_log(f"[进程组] {name} 重启模拟器 #{emu_idx}")
                    # 统一优雅关闭再重启（直接 shutdown 留 VMM 残留）
                    try:
                        from services.launch_queue import graceful_emu_shutdown
                        graceful_emu_shutdown(cli, emu_idx, ac.get("adb_path", ""), ac.get("adb_address", ""))
                    except Exception:
                        subprocess.run([cli, "control", idx_flag, str(emu_idx), "shutdown"],
                                      timeout=10, capture_output=True, creationflags=CF)
                    subprocess.run([cli, "control", idx_flag, str(emu_idx), "launch"],
                                  timeout=10, capture_output=True, creationflags=CF)
                return
        # Task completion detection
        ac = self._active.get(aid)
        if ac and hasattr(p, '_inst_path'):
            # Per-account stage downgrade — Fight rejected as invalid stage
            # ("理智作战: ... 添加任务失败" in gui.log). Checked every tick,
            # independent of asst.log activity (MAA may hang with a dead asst.log).
            if (ac.get("_stage_fallback") and aid not in self._stopping
                    and not self._downgrading.get(aid)):
                glp = Path(p._inst_path) / "debug" / "gui.log"
                if glp.exists():
                    try:
                        gsize = glp.stat().st_size
                        gpos = self._gui_log_positions.get(aid, 0)
                        if gsize >= gpos:
                            with glp.open("r", encoding="utf-8", errors="replace") as gf:
                                gf.seek(gpos)
                                gnew = gf.read(gsize - gpos)
                            self._gui_log_positions[aid] = gsize
                            if ("添加任务失败" in gnew and "理智" in gnew) or \
                               ("selected null" in gnew and "FightStage" in gnew) or \
                               "配置无效" in gnew:
                                self.emit_log(f"⬇ {ac.get('name', aid)} Fight 关卡无效，触发降级")
                                self._downgrading[aid] = True
                                try:
                                    self._downgrade_stage(aid, ac, p)
                                finally:
                                    self._downgrading.pop(aid, None)
                                return
                    except Exception:
                        pass
            lp = Path(p._inst_path) / "debug" / "asst.log"
            if lp.exists():
                try:
                    current_size = lp.stat().st_size
                    last_pos = self._log_positions.get(aid, 0)
                    if current_size > last_pos:
                        with lp.open("r", encoding="utf-8", errors="replace") as _f:
                            _f.seek(last_pos)
                            new_content = _f.read(current_size - last_pos)
                        self._log_positions[aid] = current_size
                        if "AllTasksCompleted" in new_content:
                            self._finish_completed(aid, ac, p)
                            return
                        # Battle FAILURE downgrade: agent battle lost repeatedly
                        # (FightMissionFailed / PrtsErrorConfirm in asst.log) —
                        # account can't clear this stage (wrong team/lv). With a
                        # fallback chain, drop to the next stage instead of
                        # retrying the unwinnable one forever (b-2 hit 63 retries).
                        if (ac.get("_stage_fallback") and aid not in self._stopping
                                and not self._downgrading.get(aid)
                                and ("FightMissionFailed" in new_content
                                     or "PrtsErrorConfirm" in new_content)
                                and "FightBegin" in new_content):
                            # FightBegin 确认真的在 Fight 阶段 — PrtsErrorConfirm 在
                            # StartUp 阶段也识别（StartUp@PrtsErrorConfirm），游戏未
                            # 启动/加载界面误匹配会导致"关卡刷不了"误降级（连关卡都
                            # 没进）→ WM_CLOSE → "确认退出"弹窗。只在 Fight 阶段降级。
                            _fc = new_content.count("FightMissionFailed") + new_content.count("PrtsErrorConfirm")
                            if _fc >= 2:  # 2+ failure frames in one read = real loss
                                self.emit_log(f"⬇ {ac.get('name', aid)} 作战失败，触发降级")
                                self._downgrading[aid] = True
                                try:
                                    self._downgrade_stage(aid, ac, p)
                                finally:
                                    self._downgrading.pop(aid, None)
                                return
                        # Per-account stage downgrade: Fight task failed (nav/unlock)
                        # while a fallback chain exists → record + retry next stage.
                        if ac.get("_stage_fallback") and aid not in self._stopping and "TaskChainCompleted" in new_content:
                            self._maybe_downgrade(aid, p)
                            if self._downgrading.get(aid):
                                return
                except: pass
            # Tail-scan completion check: the incremental read above misses
            # AllTasksCompleted when asst.log stops growing right after it is
            # written (current_size == last_pos → never re-read). Scan the file
            # tail every tick as a reliable fallback.
            if ac and not ac.get("_connect_only"):
                try:
                    lp = Path(p._inst_path) / "debug" / "asst.log"
                    if lp.exists():
                        _sz = lp.stat().st_size
                        if _sz > 0:
                            with lp.open("r", encoding="utf-8", errors="replace") as _f:
                                _f.seek(max(0, _sz - 20000))
                                _tail = _f.read()
                            if "AllTasksCompleted" in _tail:
                                self.emit_log(f"[完成后] {ac.get('name', aid)} 任务全部完成（尾部检测）")
                                self._log.info(f"[完成后] {ac.get('name', aid)} 尾部检测到完成")
                                tasks, sanity, drops = self._parse_log(aid)
                                self._cleanup(aid, 0, tasks, sanity, drops)
                                try: p.terminate(); p.wait(3)
                                except: pass
                                return
                except Exception:
                    pass
            # Stuck detection
            ac = self._active.get(aid)
            if ac:
                # PRTS (agent battle) stall: MAA clicks "开始行动", game enters
                # agent-battle mode (Fight@PRTS1) but the battle NEVER settles —
                # sanity stays flat (no real cost) while asst.log keeps writing
                # (screencap noise) so the silence-based check can't catch it.
                # Mark first sighting, kill after `prts_stall_min` (default 10).
                _prts_min = int(ac.get("prts_stall_min", 10) or 10)
                try:
                    _p = Path(getattr(p, '_inst_path', "")) / "debug" / "asst.log"
                    if _p.exists():
                        with _p.open("r", encoding="utf-8", errors="replace") as _f:
                            _tail = _f.read()[-200000:]
                        if "Fight@PRTS1" in _tail and not ac.get("_connect_only"):
                            # 卡死 = PRTS1 持续 + asst.log 停滞（画面无进展）。
                            # 正常长刷关（代理作战 10+ 分钟）也有 PRTS1 但日志
                            # 持续写入 — 只看关键词会误杀（2026-08-11 日-2/
                            # 日-3: 13 分钟一轮被误判卡死 → 反复重启永远跑不完）。
                            try:
                                _stall = time.time() - _p.stat().st_mtime > 60
                            except Exception:
                                _stall = False
                            if _stall:
                                _st = ac.setdefault("_prts_stall_since", time.time())
                                if time.time() - _st > _prts_min * 60:
                                    self.emit_log(f"⚠ {ac.get('name', aid)} 代理作战卡死（PRTS1 超 {_prts_min} 分钟且日志停滞），终止")
                                    self._log.warn(f"[代理卡死] {ac.get('name', aid)} PRTS1 卡死")
                                    ac.pop("_prts_stall_since", None)
                                    if self._guard_kill(aid, "kill_maa",
                                                        f"PRTS1 卡死 {_prts_min}min 日志停滞",
                                                        evidence=f"asst_stall={int(time.time()-_p.stat().st_mtime)}s"):
                                        try: p.terminate(); p.wait(5)
                                        except: pass
                                        tasks, sanity, drops = self._parse_log(aid)
                                        self._cleanup(aid, -9, tasks, sanity, drops)
                                        return
                                    else:
                                        self._log.info(f"[守护审计] {ac.get('name', aid)} PRTS 卡死终止暂缓（待确认）")
                            else:
                                ac.pop("_prts_stall_since", None)  # 日志活跃 = 正常刷关，重置
                        else:
                            ac.pop("_prts_stall_since", None)
                except Exception:
                    pass
                timeout = ac.get("stuck_timeout_min", 0)
                if timeout > 0 and aid in self._task_start_times:
                    elapsed = time.time() - self._task_start_times[aid]
                    if elapsed > timeout * 60:
                        self.emit_log(f"⚠ {ac.get('name', aid)} 任务卡死 ({int(elapsed/60)}分钟)")
                        self._task_start_times.pop(aid, None)
                        if self._guard_kill(aid, "kill_maa",
                                            f"任务卡死 {int(elapsed/60)}min",
                                            evidence=f"task_elapsed={int(elapsed)}s"):
                            try: p.terminate(); p.wait(5)
                            except: pass
                            tasks, sanity, drops = self._parse_log(aid)
                            self._cleanup(aid, -9, tasks, sanity, drops)
                            return
                        else:
                            self._log.info(f"[守护审计] {ac.get('name', aid)} 任务卡死终止暂缓（待确认）")
                started = self._start_times.get(aid, 0)
                if started and time.time() - started > 3600:
                    self.emit_log(f"⏱ {ac.get('name', aid)} 运行超时")
                    if self._guard_kill(aid, "kill_maa",
                                        "运行超时 60min",
                                        evidence=f"started={int(time.time()-started)}s"):
                        try: p.terminate(); p.wait(5)
                        except: pass
                        tasks, sanity, drops = self._parse_log(aid)
                        self._cleanup(aid, -3, tasks, sanity, drops)
                        return
                    else:
                        self._log.info(f"[守护审计] {ac.get('name', aid)} 运行超时终止暂缓（待确认）")
            # Error threshold: ≥3 consecutive errors → kill MAA + restart emulator
            # 启动宽限 2 分钟（2026-08-14 凌晨：_err_count 跨启动粘滞 + 启动期
            # asst.log 正常重试 [ERR] 累积 → 健康启动 3 秒就被「连续异常」误杀
            # → 关模拟器 → 重试 → 循环挂起（日-1/日-2 模拟器无损伤，纯计数
            # bug）。运行满 2 分钟才启用阈值判定，启动期崩溃由异常窗口杀进程
            # 闭环 + 进程退出检测覆盖。
            if ac and aid not in self._stopping:
                err_count = ac.get("_err_count", 0)
                _err_ok = True
                try:
                    _err_ok = time.time() - float(self._start_times.get(aid, 0) or 0) > 120
                except Exception:
                    pass
                if err_count >= 3 and _err_ok:
                    self.emit_log(f"[错误] {ac.get('name', aid)} 连续 {err_count} 次异常，清理")
                    # 守护审计钩子: 错误计数误杀有前科（日-1/日-2 挂起根因）
                    if self._guard_kill(aid, "kill_maa",
                                        f"连续 {err_count} 次异常",
                                        evidence=f"err_count={err_count} started={int(time.time()-self._start_times.get(aid,0))}s"):
                        emu_idx = ac.get("emu_instance_index", "")
                        if emu_idx:
                            cli = find_mumu_cli()
                            if cli is None and ac.get("adb_path"):
                                try:
                                    cand = Path(ac["adb_path"]).parent / "MuMuManager.exe"
                                    if cand.exists():
                                        cli = str(cand)
                                except Exception:
                                    pass
                            if cli:
                                idx_flag = "-v" if "MuMuManager" in cli else "--vmindex"
                                # 统一优雅关闭（直接 shutdown 留 VMM 残留）
                                try:
                                    from services.launch_queue import graceful_emu_shutdown
                                    graceful_emu_shutdown(cli, emu_idx, ac.get("adb_path", ""), ac.get("adb_address", ""))
                                except Exception:
                                    subprocess.run([cli, "control", idx_flag, str(emu_idx), "shutdown"],
                                                  timeout=10, capture_output=True, creationflags=CF)
                        try: p.terminate(); p.wait(3)
                        except: pass
                        self._cleanup(aid, -8, [])
                        return
                    else:
                        self._log.info(f"[守护审计] {ac.get('name', aid)} 连续异常清理暂缓（待确认）")
            # Stuck at startup / idle hang cleanup:
            # - daily/schedule: no tasks 120s after launch → stuck, kill
            # - daily/schedule: tasks done but MAA hangs (asst.log silent 5min) → release instance
            # - connect mode (no tasks): hang is NORMAL (manual use) → never kill
            ac = self._active.get(aid)
            if ac:
                started = self._start_times.get(aid, 0)
                is_connect = bool(ac.get("_connect_only"))
                # Cold-start grace: emulator + game can take 3-5min to load
                # (especially after MuMu restart). Only kill when MAA is
                # actually idle — asst.log silent for 60s means stuck.
                _no_task_timeout = 300 if not is_connect else 99999
                if started and time.time() - started > _no_task_timeout and not is_connect:
                    tasks, _, _ = self._parse_log(aid)
                    if not tasks:
                        # MAA still actively probing (asst.log fresh) → wait more
                        _inst_path = getattr(p, '_inst_path', None)
                        _fresh = False
                        if _inst_path:
                            try:
                                _fresh = time.time() - (Path(_inst_path) / "debug" / "asst.log").stat().st_mtime < 60
                            except Exception:
                                pass
                        if not _fresh:
                            self.emit_log(f"⏱ {ac.get('name', aid)} 启动后无任务 ({int(time.time()-started)}s)，清理实例")
                            self._log.warn(f"[卡死] {ac.get('name', aid)} 启动 {int(time.time()-started)}s 无任务")
                            if self._guard_kill(aid, "kill_maa",
                                                f"启动后无任务 {int(time.time()-started)}s",
                                                evidence=f"asst_mtime_age={int(time.time()-(Path(_inst_path)/'debug'/'asst.log').stat().st_mtime) if _inst_path else '?'}s"):
                                try: p.terminate(); p.wait(3)
                                except: pass
                                self._cleanup(aid, -3, [])
                                return
                            else:
                                self._log.info(f"[守护审计] {ac.get('name', aid)} 无任务清理暂缓（待确认）")
                    # Tasks exist → MAA likely finished and is idling. asst.log keeps
                    # getting written while tasks run; 5min silence = done/hung.
                    _inst_path = getattr(p, '_inst_path', None)
                    _idle = 9999.0
                    if _inst_path:
                        try:
                            _idle = time.time() - (Path(_inst_path) / "debug" / "asst.log").stat().st_mtime
                        except Exception:
                            pass
                    if _idle > 300:
                        self.emit_log(f"⏱ {ac.get('name', aid)} 任务完成挂起 (日志静止 {int(_idle)}s)，清理实例")
                        if self._guard_kill(aid, "kill_maa",
                                            f"任务完成挂起 日志静止 {int(_idle)}s",
                                            evidence=f"asst_idle={int(_idle)}s"):
                            try: p.terminate(); p.wait(3)
                            except: pass
                            self._cleanup(aid, 0, tasks)
                        return
                    # StartUp stuck: game loading hangs (LoadingIcon loop) — asst.log
                    # keeps being written so the silence check never fires. If StartUp
                    # is still "运行中" 10min after launch → kill + release instance.
                    # 2026-08-14 修复: _parse_log 回退路径把注入任务 status 硬编码
                    # "运行中"（_core_tasks 仅 direct-drive 有真状态，GUI 模式恒空）
                    # → StartUp 完成后 status 仍是"运行中" → 任何跑超 600s 的账号
                    # 都被误杀（实测官-8: 22:57 StartUp 成功、23:05 正在跑 Infrast，
                    # 23:05:20 被"StartUp 卡加载"误杀）。以 asst.log 尾部真实
                    # taskchain 为准: 最新任务链已离开 StartUp → StartUp 已完成 →
                    # 不判卡死。
                    startup = next((t for t in tasks if t.get("TaskType", "").lower() == "startup"), None)
                    _startup_done = False
                    if startup and startup.get("status") == "运行中" and time.time() - started > 600:
                        try:
                            import re as _re
                            _sp = Path(getattr(p, '_inst_path', "")) / "debug" / "asst.log"
                            if _sp.exists():
                                _sz2 = _sp.stat().st_size
                                with _sp.open("r", encoding="utf-8", errors="replace") as _f2:
                                    _f2.seek(max(0, _sz2 - 100000))
                                    _tail2 = _f2.read()
                                _chains = _re.findall(r'"taskchain":"([^"]+)"', _tail2)
                                if _chains:
                                    _last_chain = _chains[-1]
                                    # StartUp 完成后 taskchain 会切到 Fight/Infrast/
                                    # Recruit 等 — 仅当最新链仍是 StartUp 才可能卡加载
                                    if _last_chain != "StartUp":
                                        _startup_done = True
                        except Exception:
                            pass
                        if not _startup_done:
                            self.emit_log(f"⏱ {ac.get('name', aid)} StartUp 卡加载超时 ({int(time.time()-started)}s)，清理实例")
                            self._log.warn(f"[卡死] {ac.get('name', aid)} StartUp 卡加载 {int(time.time()-started)}s")
                            # 守护审计钩子: StartUp 判定是误杀重灾区（已修复
                            # 判据 + 钩子双保险）— 先留痕待确认
                            if self._guard_kill(aid, "kill_maa",
                                                f"StartUp 卡加载 {int(time.time()-started)}s",
                                                evidence=f"asst_tail_taskchain={_last_chain if '_last_chain' in dir() else '?'} started={int(time.time()-started)}s"):
                                try: p.terminate(); p.wait(3)
                                except: pass
                                self._cleanup(aid, -3, tasks)
                                return
                            else:
                                self._log.info(f"[守护审计] {ac.get('name', aid)} StartUp 卡加载杀暂缓（待确认）")
            # ADB keepalive — log only, don't kill MAA。
            # ⚠️ 原实现每 5s 逐账号 `adb -s <addr> shell echo ping` — adb server
            # 是串行的，10 台并发 ping + MAA 自身操作 → server 排队超时（3s）
            # → 误报"ADB 失联" + connect 重连干扰 MAA 连接 → MAA 连接抖动
            # 退出（2026-08-11 B 服轮: 10 台同时失联误报 + 多个 ADB 关机）。
            # 改: 每 30s 一次 `adb devices` 批量检查（1 次调用查全部设备），
            # 不在设备列表才计数/重连。
            ac = self._active.get(aid)
            if ac and time.time() - self._adb_check_ts > 300:
                self._adb_check_ts = time.time()
                try:
                    from infrastructure.task_constants import find_adb
                    adb_path = find_adb() or "adb"
                except Exception:
                    adb_path = "adb"
                try:
                    r = subprocess.run([adb_path, "devices"], capture_output=True,
                                       timeout=5, creationflags=CF, encoding="utf-8", errors="replace")
                    known = {l.split()[0] for l in r.stdout.splitlines()[1:]
                             if l.strip() and "device" in l and "offline" not in l}
                except Exception:
                    known = set()
                for _aid in list(self._active.keys()):
                    _ac = self._active.get(_aid)
                    _addr = (_ac or {}).get("adb_address", "")
                    if not _addr:
                        continue
                    if _addr in known:
                        self._adb_fail_count.pop(_aid, None)
                    else:
                        fail = self._adb_fail_count.get(_aid, 0) + 1
                        self._adb_fail_count[_aid] = fail
                        # 不再 connect 重连 — find_adb 的 server 与 MAA 用的
                        # adb 不是同一个 → 设备永远"不在列表"→ 误报循环 +
                        # connect 干扰 MAA 连接（2026-08-12 官-47/日-2 等
                        # 失联第 3/6 次循环，但模拟器实测 ADB 全通）。
                        # 连接健康已由 MAA 心跳（10s 断 → 恢复）接管。
                        if fail >= 3 and fail % 3 == 0:
                            self.emit_log(f"[ADB] {(_ac or {}).get('name', _aid)} 不在 adb devices（仅观察，心跳管恢复）")

    def _update_status(self, aid: str) -> None:
        """当前任务名 — 单点收敛（2026-08-15）: 原自读 asst.log 尾部解析，
        改查 LogWatcher.cur_task（LogWatcher 是 asst.log 唯一读者）。"""
        ac = self._active.get(aid)
        if not ac:
            return
        name = ac.get("name", aid)
        try:
            _w = self._watchers.get(aid)
            _tc = _w.cur_task() if _w else ""
        except Exception:
            _tc = ""
        if _tc:
            st_map = {"StartUp": "唤醒", "Fight": "刷关", "Recruit": "公招",
                      "Infrast": "基建", "Mall": "信用", "Award": "奖励",
                      "Roguelike": "肉鸽", "Reclamation": "生息"}
            task_name = st_map.get(_tc, _tc)
            prev = ac.get("_last_task", "")
            if task_name != prev:
                ac["_last_task"] = task_name
                self.emit_log(f"[MAA] {name} 当前任务: {task_name}")
            self._task_start_times[aid] = time.time()

    def is_task_running(self, aid: str) -> bool:
        """MAA 当前是否有任务在运行 — asst.log 生命周期推断（2026-08-11 用户:
        进程活着 ≠ 任务在跑；MAA 完成/点停止后停留界面，按钮状态才是真值。
        日志推断：最后一个 TaskStart 之后有无闭合的 TaskChainCompleted/
        AllTasksCompleted — 有未闭合 = 任务运行中。零依赖，比 UI Automation 可靠）。"""
        p = self._procs.get(aid)
        if not isinstance(p, subprocess.Popen) or p.poll() is not None:
            return False
        inst = getattr(p, "_inst_path", None)
        if not inst:
            return False
        return self._infer_task_running(inst)

    @staticmethod
    def _infer_task_running(inst_path: str) -> bool:
        """从实例目录推断任务是否运行中（is_task_running 与手动 MAA 扫描共用）。"""
        try:
            al = Path(inst_path) / "debug" / "asst.log"
            if not al.exists():
                return False
            tail = al.read_text(encoding="utf-8", errors="replace").splitlines()[-300:]
            last_start = -1
            last_end = -1
            for i, ln in enumerate(tail):
                if "TaskStart" in ln:
                    last_start = i
                elif "TaskChainCompleted" in ln or "AllTasksCompleted" in ln:
                    last_end = i
            return last_start > last_end
        except Exception:
            return False

    def scan_maa_instances(self) -> list[dict]:
        """扫描所有运行中的 MAA.exe（含手动启动的）— 检测每个是否正在执行
        任务（2026-08-11 用户: 手动启动的 MAA 也要能检测运行状态）。
        系统管理的走 _procs；手动开的通过进程路径找实例目录 → asst.log 推断。"""
        results = []
        managed_pids = set()
        # 1) 系统管理的
        for aid, p in list(self._procs.items()):
            if isinstance(p, subprocess.Popen) and p.poll() is None:
                managed_pids.add(str(p.pid))
                inst = getattr(p, "_inst_path", None) or ""
                results.append({
                    "pid": p.pid, "aid": aid[:8], "managed": True,
                    "running": self._infer_task_running(inst),
                    "task": (self._active.get(aid) or {}).get("_last_task", ""),
                })
        # 2) 手动开的（tasklist 找 MAA.exe，不在系统管理的）
        try:
            r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq MAA.exe", "/FO", "CSV"],
                               capture_output=True, text=True, timeout=10,
                               creationflags=CF, errors="replace")
            pids = []
            for ln in r.stdout.splitlines()[1:]:
                parts = ln.split('","')
                if len(parts) > 1:
                    pid = parts[1].strip('"')
                    if pid.isdigit():
                        pids.append(pid)
            for pid in pids:
                if pid in managed_pids:
                    continue
                inst = self._find_inst_by_pid(pid)
                results.append({
                    "pid": pid, "aid": f"手动:{pid}", "managed": False,
                    "running": self._infer_task_running(inst) if inst else False,
                    "task": "",
                })
        except Exception:
            pass
        return results

    def _find_inst_by_pid(self, pid: str) -> str:
        """按进程 PID 找 MAA 实例目录（进程路径 → instances/N）。"""
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-Command",
                                f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue).Path"],
                               capture_output=True, text=True, timeout=10,
                               creationflags=CF, errors="replace")
            path = (r.stdout or "").strip()
            if path and "instances" in path:
                return str(Path(path).parent)
        except Exception:
            pass
        return ""

    def _parse_log(self, aid: str) -> tuple[list[dict], dict | None, dict | None]:
        """真实数据源查询（2026-08-14 Step 3b — 替代影子功能）。

        原实现读 asst.log（ctx.logs 全仓库无赋值 + parse_log 无定义 → 恒
        返回空值，见收敛附录 C）：-13 未完成退出重试、-11 失败检测、
        续跑判定（_done_pct）、前端任务列表全部依赖它 → 全部失效。
        现改为查询真实数据源：
          tasks  本次注入任务列表（_core_tasks 优先 — direct drive 模式
                 有真状态；GUI 模式从 asst.log 尾部解析真实 taskchain
                 状态 — 2026-08-15 整理，替代硬编码"运行中"假状态）
          sanity sanity_tracker 快照（sanity_live.json，bus 订阅写 — Step 3a）
          drops  无独立源，暂 None（StageDrops 事件未结构化，后续收口）
        """
        ac = self._active.get(aid)
        if not ac:
            return [], None, None
        tasks = list(self._core_tasks.get(aid) or [])
        if not tasks:
            _inj = ac.get("_injected_tasks") or []
            # TaskType 字段兼容 _maybe_downgrade 的 startup/fight 判定
            tasks = [{"name": n, "TaskType": n, "status": "运行中"} for n in _inj]
            # 2026-08-15 整理: 从 asst.log 尾部解析真实 taskchain 状态 —
            # 硬编码"运行中"让 StartUp 卡加载判定误杀正常账号（官-8 实证）、
            # 让 _maybe_downgrade 永远不触发（GUI 模式降级失效）。读真实
            # TaskChainStart/TaskCompleted/TaskChainError 事件更新状态。
            # 2026-08-15 收敛: 解析逻辑在 LogWatcher.tail_tasks 单点，此处
            # 只消费（消除 runner 自读 asst.log）。
            try:
                _w = self._watchers.get(aid)
                _chain_events = _w.tail_tasks() if _w else []
                for _ev, _chain in _chain_events:
                    for _t in tasks:
                        if _t.get("name", "").lower() == _chain.lower():
                            if _ev == "TaskChainStart":
                                _t["status"] = "运行中"
                            elif _ev == "TaskCompleted":
                                _t["status"] = "完成"
                            elif _ev == "TaskChainError":
                                _t["status"] = "失败"
                            break
            except Exception:
                pass
        sanity = None
        try:
            from services.sanity_tracker import get as _sg
            sanity = _sg(aid)
        except Exception:
            pass
        return tasks, sanity, None

    # ── Completion ──

    def _record_stage_check(self, aid: str, stage: str, reason: str) -> None:
        """Persist a per-account stage availability check to models/stage_checks.json."""
        try:
            import json as _json
            from infrastructure.utils import atomic_write
            p = _STAGE_CHECKS_PATH
            d = {}
            if p.exists():
                try:
                    d = _json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    d = {}
            entries = d.get(aid, [])
            entries.insert(0, {"stage": stage, "reason": reason, "ts": time.time()})
            d[aid] = entries[:20]
            atomic_write(p, _json.dumps(d, ensure_ascii=False))
        except Exception:
            pass

    def _downgrade_stage(self, aid: str, ac: dict, p) -> bool:
        """Fight failed (unlocked/navigation) → record check, retry next stage.
        Returns True if a downgrade/restart was issued."""
        fallback = ac.get("_stage_fallback") or []
        cur_stage = ac.get("_stage_current", "")
        if not fallback:
            return False
        next_stage = fallback[0]
        ac["_stage_fallback"] = fallback[1:]
        if cur_stage:
            self._record_stage_check(aid, cur_stage, "nav_fail")
        name = ac.get("name", aid)
        self.emit_log(f"⬇ {name} 关卡「{cur_stage or '?'}」不可刷，降级到「{next_stage}」")
        self._log.info(f"[降级] {name}: {cur_stage} → {next_stage}")
        # 记录到 AccountState（降级原因可见）
        try:
            from models.account_state import AccountState
            AccountState(aid).on_use(f"降级: {cur_stage or '?'} → {next_stage}")
        except Exception:
            pass
        # 治本: 把刷不了的关卡从 stages 移除并持久化 —
        # 否则每次重启注入又重新包含它，每次都触发降级（每次降级
        # 优雅关闭 MAA → WM_CLOSE → "确认退出"弹窗卡死循环）。
        try:
            _stages = ac.get("stages") or []
            if cur_stage and cur_stage in _stages:
                _stages.remove(cur_stage)
                ac["stages"] = _stages
                try:
                    from models.config_manager import save_config
                    save_config(self.ctx.config)
                except Exception:
                    pass
                self._log.info(f"[降级] {name} 已从 stages 移除 {cur_stage}（防循环）")
        except Exception:
            pass
        inst_dir = getattr(p, "_inst_path", None)
        if not inst_dir:
            return False
        try:
            # Supersede the old process FIRST: while we close it, its _wait_exit
            # thread wakes up and calls _on_process_exit. If _procs[aid] still
            # points at the old Popen, that callback cleans up (pops _active and
            # the queue mark) while the NEW process is about to start — leaving
            # an orphan MAA and a lost queue slot. Replacing the entry with a
            # string placeholder makes `_procs.get(aid) is not p` true, so the
            # stale callback returns early.
            self._procs[aid] = inst_dir
            # Graceful close (WM_CLOSE → MAA releases ADB/minitouch) — hard
            # terminate leaves minitouch residue that crashes the emulator
            # (MuMu "运行异常" popup). Fall back to terminate only if it hangs.
            self._graceful_close(p)
            # 优雅关闭触发 MAA "确认退出"弹窗（任务在跑）— 自动点"是"让它退出
            for _ in range(4):
                try:
                    _close_mumu_popups()
                except Exception:
                    pass
                if p.poll() is not None:
                    break
                time.sleep(1.5)
            p.wait(5)
            if p.poll() is None:
                p.terminate()
                p.wait(3)
            if p.poll() is None:
                # Still alive — hard kill. A leftover MAA becomes an orphan
                # holding the instance slot (9 instances for 8 parallel).
                subprocess.run(["taskkill", "/F", "/PID", str(p.pid)],
                               capture_output=True, timeout=5, creationflags=CF)
        except Exception:
            pass
        # Re-inject with the next stage — but NOT in-place spawn. The old MAA's
        # Shutdown sequence takes 5-7s (confirm dialog → release ADB/minitouch);
        # spawning the new MAA on the SAME instance while the old one is still
        # exiting triggers MAA's single-instance guard ("Existing instance
        # window activated by a secondary launch") → new MAA exits rc=0 in 5s
        # (→ -12 → previously ADB-shutdown the emulator, mass emulator closes,
        # 2026-08-10). Release the slot and re-enqueue — the queue's full
        # startup path (instance check + stale cleanup + 20s interval) avoids
        # the race entirely.
        ac["_stage_override"] = next_stage
        try:
            self._procs.pop(aid, None)
            self._active.pop(aid, None)
            self._release_emu_mark(aid)
            mw = getattr(self.ctx, "_mw", None)
            lq = getattr(mw, "launch_queue", None)
            if lq is not None:
                lq.enqueue(aid, "auto", priority=0, slot=ac.get("_slot", ""))
                lq.tick()
            self.emit_log(f"↩ {name} 降级「{next_stage}」完成，回队列重排")
            return True
        except Exception as e:
            self._log.error(f"[降级] {name} 重排失败: {e}")
            return False

    def _maybe_downgrade(self, aid: str, p) -> None:
        """Check if the Fight chain failed and a fallback stage exists.
        Low sanity stops the downgrade chain (all stages unplayable)."""
        if self._downgrading.get(aid):
            return
        ac = self._active.get(aid)
        if not ac or ac.get("_connect_only"):
            return
        tasks, sanity, _ = self._parse_log(aid)
        # 登录守卫: StartUp 未完成（游戏都没进）→ 任务"失败"是环境问题
        # （登录卡住/断连/被杀），不是关卡问题 — 连关卡都没进怎么可能知道
        # 它刷不了？只有登录成功（StartUp 完成）才能判定关卡能否刷。
        # （2026-08-10 用户指出: 没登录就降级是误判 — 与昨晚 PrtsErrorConfirm
        # 在 StartUp 阶段误匹配同一类问题）
        startup = next((t for t in tasks if t.get("TaskType", "").lower() == "startup"), None)
        if not startup or startup.get("status") != "完成":
            return
        fight = next((t for t in tasks if t.get("TaskType", "").lower() == "fight"), None)
        if fight:
            if fight.get("status") != "失败":
                return
            if fight.get("fight_finished"):
                return  # finished=true = 次数刷完(正常), 非失败
        else:
            # Fight never started (invalid stage → AsstAppendTask rejected) but the
            # queue moved on to later chains → treat as stage failure → downgrade.
            later = [t for t in tasks if t.get("TaskType", "").lower() in ("infrast", "recruit", "mall", "award")]
            if not later:
                return
            cur_stage = ac.get("_stage_current", "")
            if not cur_stage:
                return
        # Sanity gate: if sanity is very low, all stages are unplayable → stop
        cur = (sanity or {}).get("current")
        if cur is not None and cur < 20:
            ac["_stage_fallback"] = []
            self.emit_log(f"⏱ {ac.get('name', aid)} 理智不足({cur})，停止关卡降级")
            return
        self._downgrading[aid] = True
        try:
            self._downgrade_stage(aid, ac, p)
        finally:
            self._downgrading.pop(aid, None)

    def _archive_maa_logs(self, aid: str, inst_path: str | None) -> None:
        """Archive MAA asst.log + gui.log after a run ends (any exit path).
        Kept under logs/maa_history/{aid}/ — asst.log is wiped on every launch,
        so without archiving past runs are unrecoverable."""
        if not inst_path:
            return
        try:
            from datetime import datetime as _dt
            hist = Path(__file__).parent.parent / "logs" / "maa_history" / str(aid)
            hist.mkdir(parents=True, exist_ok=True)
            ts = _dt.now().strftime("%Y%m%d_%H%M%S")
            dbg = Path(inst_path) / "debug"
            saved = 0
            for src_name, dst_name in (("asst.log", "asst"), ("gui.log", "gui")):
                src = dbg / src_name
                if src.exists() and src.stat().st_size > 0:
                    dst = hist / f"{ts}_{dst_name}.log"
                    try:
                        import shutil as _su
                        _su.copy2(str(src), str(dst))
                        saved += 1
                    except Exception:
                        pass
            if saved:
                self._log.info(f"[归档] {aid} MAA 日志 → logs/maa_history/{aid}/{ts}_*.log")
            # 运行级样本（2026-08-11 用户）: 归档同时生成结构化摘要 —
            # 该次运行的关键事件/结果，供训练与"日志模式→动作"规则提炼。
            try:
                import json as _j2
                _sdir = Path(__file__).parent.parent / "logs" / "log_samples"
                _sdir.mkdir(parents=True, exist_ok=True)
                _summary = {"ts": ts, "aid": aid[:8], "events": []}
                _al = dbg / "asst.log"
                if _al.exists():
                    for _ln in _al.read_text(encoding="utf-8", errors="replace").splitlines():
                        _t = _ln.strip()
                        if any(k in _t for k in ("AllTasksCompleted", "TaskChainCompleted",
                                                 "FightMissionFailed", "PrtsErrorConfirm",
                                                 "ExceededLimit", "FightBegin", "SanityBeforeStage")):
                            _summary["events"].append(_t[:300])
                with open(_sdir / f"{aid[:8]}.jsonl", "a", encoding="utf-8") as _f:
                    _f.write(_j2.dumps(_summary, ensure_ascii=False) + "\n")
            except Exception:
                pass
            # Retention: keep newest 100 runs per account, drop the oldest
            # （2026-08-11 用户: 收集优先于识别 — 现场原始日志是分析底，
            # 30 次太短会丢现场。磁盘 2TB 足够，延到 100 次）
            runs = sorted(hist.glob("*.log"))
            while len(runs) > 200:  # 100 runs × 2 files
                try:
                    runs[0].unlink()
                except Exception:
                    pass
                runs = runs[1:]
        except Exception as e:
            self._log.warn(f"[归档] {aid} 日志归档失败: {e}")

    def _cleanup(self, aid: str, exit_code: int, tasks: list[dict], sanity: dict | None = None, drops: dict | None = None) -> None:
        self._inst_reserved.pop(aid, None)
        self._probe_pending.pop(aid, None)  # 清理未跑/跑一半的关卡检测（防泄漏）
        self._probe_running.pop(aid, None)
        # 停止日志监控线程
        try:
            _w = self._watchers.pop(aid, None)
            if _w:
                _w.stop()
                _w.join(2.0)  # 等线程退出释放 asst.log/gui.log 句柄（08-11 审查）
        except Exception:
            pass
        ac = self._active.pop(aid, None)
        old_procs = self._procs.pop(aid, None)
        # EmulatorService v2 释放（2026-08-15 接入: 账号结束 → BUSY→IDLE，
        # 状态机才能对该模拟器做排位回收判定。幂等: IDLE 再 release 忽略）。
        try:
            if ac:
                _v2 = getattr(getattr(self.ctx, "_mw", None), "emu_service_v2", None)
                if _v2:
                    _v2.release(ac)
        except Exception:
            pass
        # inst_dir: Popen 的 _inst_path，或占位符路径本身（_do_launch/降级
        # 把 _procs[aid] 设为 inst_dir 字符串防竞态 — 2026-08-11 僵尸根因：
        # 字符串路径下杀逻辑被 isinstance 检查跳过 → MAA 残留占实例）。
        inst_dir = old_procs if isinstance(old_procs, str) else getattr(old_procs, '_inst_path', None)
        if old_procs:
            self._archive_maa_logs(aid, inst_dir)
            # 失败截图（2026-08-15 用户: 日常失败也要收集日志和图片）—
            # 必须在杀 MAA 进程前截（ADB 还通、画面还是失败现场）。
            # 失败 = exit_code 非 0（排除用户手动停止 -999 和正常完成）。
            try:
                if exit_code not in (0, -999) and ac:
                    self._save_failure_shot(aid, ac, exit_code, inst_dir)
            except Exception:
                pass
            # 确保 MAA 进程真的死了才删 .pid/.meta — 否则僵尸 MAA 占着实例
            # （无 .pid 但进程活着）→ 后续账号分配到该实例 → MAA 启动撞
            # "Existing instance window activated by a secondary launch" →
            # 2 秒退出 → 被"日志停滞"判定关模拟器 → "一出来就被关掉"
            # （2026-08-10 实例 6 连续 12 次 secondary 根因）。
            if isinstance(old_procs, subprocess.Popen):
                try:
                    if old_procs.poll() is None:
                        old_procs.terminate()
                        try: old_procs.wait(3)
                        except: pass
                    if old_procs.poll() is None:
                        subprocess.run(["taskkill", "/F", "/PID", str(old_procs.pid)],
                                       capture_output=True, timeout=5, creationflags=CF)
                        time.sleep(1)
                except Exception:
                    pass
            elif inst_dir:
                # 占位符路径：实例 .pid 里可能有真 MAA 进程（启动后异常 /
                # 降级中断，_procs 未更新为 Popen）— 按 .pid 强杀。
                try:
                    _pf = Path(inst_dir) / ".pid"
                    if _pf.exists():
                        _zpid = int(_pf.read_text(encoding="utf-8").strip().split()[0])
                        _zalive = False
                        try:
                            import psutil as _ps
                            _zpr = _ps.Process(_zpid)
                            _zalive = _zpr.is_running() and ("MAA" in (_zpr.name() or "").upper())
                        except Exception:
                            pass
                        if _zalive:
                            subprocess.run(["taskkill", "/F", "/PID", str(_zpid)],
                                           capture_output=True, timeout=5, creationflags=CF)
                            time.sleep(1)
                except Exception:
                    pass
            # Process is gone — remove .pid/.meta so the instance is cleanly
            # reusable (stale pid risks PID-reuse false positive in
            # _get_free_instance / _launch_for_instance, stale .meta mislabels
            # the instance in dashboard fallbacks).
            if inst_dir:
                try:
                    _ip = Path(inst_dir)
                    (_ip / ".pid").unlink(missing_ok=True)
                    (_ip / ".meta").unlink(missing_ok=True)
                except Exception:
                    pass
        old_progs = self._progs.pop(aid, None)
        started = self._start_times.pop(aid, None)
        duration = int(time.time() - started) if started else 0
        self._stopping.discard(aid)
        self.ctx.proc_status.discard(aid)
        # 清心跳残留（MAA 死 → RemoteControl 心跳停 — 防假活记录残留，
        # 2026-08-12: 官-17/13/37 死后心跳表留着 age=743s 的记录）
        try:
            _hb = getattr(getattr(self.ctx, "_mw", None), "_maa_heartbeat", None)
            if _hb:
                _hb.pop(aid, None)
        except Exception:
            pass
        # Release queue mark on ANY exit (crash/kill included) — stale marks
        # blocked relaunches for up to 150s before this fix.
        self._release_emu_mark(aid)
        self._emu_lost_suspect.pop(aid, None)  # 清失联嫌疑（防重入队后残留误杀）
        self._adb_connected.pop(aid, None)  # 清连接信号（重入队后重新触发）

        try:
            from services.log_watcher import record_event
            record_event(aid, "exit", f"exit_code={exit_code} elapsed={duration}s")
        except Exception:
            pass
        name = ac.get("name", aid) if ac else aid
        if ac and ac.get("_connect_only"):
            # Connect-only temp account: release dispatch + remove from connect list
            try:
                from services.dispatch_pool import remove_dispatch
                remove_dispatch(ac.get("_dispatch_connect", ""))
                ac["_dispatch_connect"] = ""
                conn = getattr(getattr(self.ctx, "_mw", None), "connect_accounts", None)
                if conn:
                    try: conn.remove(ac)
                    except ValueError: pass
            except Exception:
                pass
        # 模拟器生命周期不归 runner 管（职责分区，2026-08-12）：正常完成后
        # 模拟器保持运行，交给回收（launch_queue._reclaim_idle_emus / v2 状态机）
        # 统一关闭。曾在 exit==0 时立即 graceful_emu_shutdown — MuMuManager
        # shutdown 执行窗口会让其他 VM 的 info 短暂返回 false/false → 在跑
        # 账号集体误判"失联"→ 杀 MAA → -8 自动重启循环（"账号旅行多次"根因）。
        if exit_code == 0 and ac:
            self._log.debug(f"[完成] {name} MAA 退出 (exit=0)")
        if ac:
            plan = ac.get("smart_plan", "")
            plan_log = f" 🧠 {plan}" if plan else ""
            self.emit_log(f"[完成] {name} 退出码={exit_code} 耗时={duration//60}m{duration%60}s{plan_log}")
            self._log.info(f"[清理] {name} exit={exit_code}")
            from services.dispatch_pool import remove_dispatch
            remove_dispatch(ac.get("dispatch_id", ""))
            ac["dispatch_id"] = ""
            mode = self.ctx.config.get("schedule_mode", "daily")
            if exit_code == 0:
                if not ac.get("_persist_plan") or mode != "daily":
                    ac["smart_plan"] = ""
                ac.pop("_persist_plan", None)

        if exit_code in (0, -9) and tasks:
            for t in tasks:
                if t.get("status") == "运行中":
                    t["status"] = "完成"

        if exit_code == 0 and tasks:
            failed = [t for t in tasks if t.get("status") == "失败"]
            completed = [t for t in tasks if t.get("status") == "完成"]
            if failed and not completed:
                names = [t["name"] for t in failed]
                exit_code = -11
                self._log.warn(f"[任务失败] {name} 失败: {names}")
                self.emit_log(f"[❌] {name} 任务失败: {','.join(names)}")
        # -12 = startup failure (MAA exited <60s with no tasks — e.g. a
        # SECONDARY launch of the same instance dir: MAA's single-instance
        # guard makes the 2nd process exit rc=0). The emulator may belong to
        # the OTHER account's still-running MAA — shutting it down here would
        # kill the wrong account (2026-08-10: mass emulator closes).
        # -13 = unfinished exit (MAA self-update/interrupt with tasks running) —
        # emulator is fine, retry reuses it.
        is_real_error = exit_code != 0 and exit_code not in (-9, -8, -12, -13) and aid not in self._stopping
        if tasks and any(t.get("status") == "完成" for t in tasks):
            is_real_error = False
        # Connect-only mode: MAA exits on its own (no tasks, GUI idle) — that's
        # normal, never treat as an error or shut down the emulator.
        if ac and ac.get("_connect_only"):
            is_real_error = False

        # Kill residual process — old_procs (was `old_proc`, a NameError that
        # left orphan MAA.exe processes behind).
        if old_procs and not isinstance(old_procs, str) and hasattr(old_procs, 'pid'):
            try:
                if old_procs.poll() is None:
                    old_procs.terminate()
                    try: old_procs.wait(3)
                    except: pass
                if old_procs.poll() is None:
                    subprocess.run(["taskkill","/F","/PID",str(old_procs.pid)], capture_output=True, timeout=3,
                                  creationflags=subprocess.CREATE_NO_WINDOW)
            except: pass

        # 错误路径不再用 ADB reboot 关模拟器（2026-08-14 清除残留）：
        # 用户铁律「关闭链绝不碰 ADB」——reboot -p 抢 MAA 端口 → 失联弹窗；
        # 且 reboot 后端口漂移而 adb_address 缓存未清（审查记录）。模拟器
        # 关闭统一交给回收/优雅关闭链，MAA 完成关游戏（PostActions）。

        try:
            from services.event_bus import emit as _eb_emit
            _eb_emit("queue.completed" if exit_code == 0 else "queue.failed",
                     {"name": name, "exit": exit_code})
        except Exception:
            pass
        self.emit_finished((aid, exit_code, tasks))
        if ac:
            ac.pop("_persist_plan", None)

        # Auto-recovery: MAA exited abnormally (externally killed / crash) →
        # re-enqueue to continue the run. Not for user stops (stop() pops _active
        # first, so ac is None here), connect-only, or normal completes.
        if ac and exit_code != 0 and not ac.get("_connect_only") and not self._done_flags.get(aid):
            # 2026-08-14: 记录异常退出时间戳 — 回收逻辑据此给该账号模拟器
            # 保留窗口（异常退出 → 自动重启马上要再拉起模拟器，若回收在
            # 间隙把模拟器当闲置关了 → 重启 MAA 失联 → 连环失败挂起。
            # 实测官-8: 23:05:20 被 StartUp 误杀 → 23:05:22 自动重启 →
            # 模拟器#18 已被回收关闭 → 23:06:30 失联确认杀 → 挂起）。
            self._recent_exit[aid] = time.time()
            # _done_flags: 完成收尾（AllTasksCompleted）terminate 的 MAA 会以
            # exit=-3 退出 — 若无此检查会被判"异常退出"→ 自动重启 → 已刷完
            # 的账号重刷一遍（2026-08-11 官-41 刷完 14m49s 又被重入队）
            retries = ac.get("_auto_restart_count", 0) + 1
            if retries <= 3:
                ac["_auto_restart_count"] = retries
                self.emit_log(f"🔄 {name} MAA 异常退出(exit={exit_code})，自动重启 (第{retries}/3 次)")
                self._log.warn(f"[自动重启] {name} exit={exit_code} 第{retries}次")
                try:
                    mw = getattr(self.ctx, "_mw", None)
                    lq = getattr(mw, "launch_queue", None)
                    if lq is not None and ac.get("emu_instance_index"):
                        lq.enqueue(aid, "auto", priority=0, slot=ac.get("_slot", ""))
                        lq.tick()
                except Exception as e:
                    self._log.warn(f"[自动重启] {name} 入队失败: {e}")
            else:
                self.emit_log(f"⛔ {name} MAA 异常退出 {retries} 次，停止自动重启")
                # 反复启动失败（模拟器/游戏起不来）→ 挂起账号，停止队列空转
                # 循环（force 条目失败 → requeue → 又启动 → 又失败，无限循环，
                # 2026-08-10 官-2/emu14 模拟器起不来）。挂起可逆（手动处理
                # 模拟器后解除）。
                try:
                    ac["suspended"] = True
                    from models.config_manager import save_config
                    save_config(self.ctx.config)
                    self.emit_log(f"🚫 {name} 反复启动失败 {retries} 次，已挂起（需人工检查模拟器）")
                    try:
                        from services.event_bus import emit as _eb_emit
                        _eb_emit("queue.suspended", {"name": name, "reason": f"启动失败{retries}次"})
                    except Exception:
                        pass
                except Exception:
                    pass
        elif ac and exit_code == 0:
            ac.pop("_auto_restart_count", None)

        # Save run stats — record EVERY run (success, failure, timeout, startup
        # failure) so daily/weekly/monthly/yearly stats are complete. tasks may
        # be [] on early exits; status comes from exit_code.
        # sanity_after: 原 _parse_log 恒 None（影子功能，见收敛附录 C）→
        # 从 sanity_tracker 快照补「清完剩余」（MAA 最后一次 SanityBeforeStage
        # 即最接近的完成时体力）— 2026-08-14 修复「余 N」恒显示 ?。
        _sa = sanity or None
        if _sa is None:
            try:
                from services.sanity_tracker import get as _sg
                _sa = _sg(aid)
            except Exception:
                _sa = None
        try:
            st = RunStats(aid)
            st.save_run(tasks, sanity, drops,
                        exit_code=exit_code,
                        elapsed=duration,
                        sanity_before=self._run_sanity_first.pop(aid, None),
                        sanity_after=_sa,
                        medicine_used=self._run_medicine_used.pop(aid, False),
                        chain_error=self._run_chain_error.pop(aid, False),
                        status=("完成" if exit_code == 0 else "失败"))
        except Exception as e:
            self._log.warn(f"[统计] {name} save_run 失败: {e}")
        # Account state — reliable usage/login/completion/sanity record
        try:
            from models.account_state import AccountState
            _st = AccountState(aid)
            _st.on_complete(exit_code, ("完成" if exit_code == 0 else "失败"),
                            sanity, drops)
        except Exception as e:
            self._log.warn(f"[状态] {name} AccountState 失败: {e}")

    def _track_stats(self, ac: dict) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        sd = ac.setdefault("stats", {})
        sd.setdefault(today, {"launches": 0, "total_sec": 0})
        sd[today]["launches"] += 1
        self.ctx.save()
