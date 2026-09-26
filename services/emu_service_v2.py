"""EmulatorService v2（2026-08-12 Phase 1）— 模拟器生命周期状态机服务。

设计: docs/EMULATOR_SERVICE.md
- 单线程事件循环（事件/回收/用户操作顺序处理 — §9.2.3 天然无锁）
- 状态转移表驱动（services/emu_state.py — §11 规格）
- 长操作（VBox 启停/taskkill/adb）一律后台线程 — 事件循环内不阻塞（§9.5.2）
- 状态转移审计（oplog 模式 — §9.1.4）：谁在什么时候把模拟器从 X 移到 Y

Phase 1 范围: 骨架 + emu_state 管理 + 回收决策框架（迁入入口）。
关闭动作复用 emu_service 现有关闭链（graceful/direct shutdown）。
旧逻辑（launch_queue._reclaim_idle_emus）保留开关并存 — 行为等价后切换。
"""
from __future__ import annotations

import queue
import threading
import time
from pathlib import Path

from services.emu_state import (EmuState, transition,
                                OFF, PREWARMING, READY, BUSY, IDLE, CLOSING,
                                RECOVERING, EXTERNAL,
                                EV_PREWARM, EV_ACQUIRE, EV_RELEASE, EV_CLOSE,
                                EV_RECLAIM, EV_CRASH, EV_READY, EV_LOST,
                                EV_MANUAL, EV_CANCEL)

_OPLOG_PATH = Path(__file__).parent.parent / "logs" / "emu_state_oplog.jsonl"


class EmulatorService:
    """模拟器生命周期服务（状态机驱动）。"""

    def __init__(self, ctx, node_id: str = "local") -> None:
        self.ctx = ctx
        self.node_id = node_id
        self.states: dict[str, EmuState] = {}
        self._q: "queue.Queue" = queue.Queue()
        self._thread: threading.Thread | None = None
        self._started = False
        self._subs: dict[str, list] = {}
        self._last_beat = 0.0
        # 关闭冷却（复用 emu_service 语义 — 防二次关闭）
        self._closed_at: dict[str, float] = {}

    # ── 事件循环 ──

    def start(self) -> None:
        """启动事件循环守护线程（心跳 + 崩溃自动重启 — §9.4.4）。"""
        if self._started:
            return
        self._started = True
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="emu_state_loop")
        self._thread.start()

    def _loop(self) -> None:
        while True:
            try:
                # 心跳（10s 打点 — 事件循环存活证明）
                self._last_beat = time.time()
                # 周期回收决策（合并进事件循环 — 心跳即回收活动）
                self.reclaim_tick()
                try:
                    fn = self._q.get(timeout=2.0)
                except queue.Empty:
                    continue
                try:
                    fn()
                except Exception as ex:
                    self._log(f"[状态机] 事件处理异常: {ex}")
            except Exception as ex:
                # 事件循环自身崩溃 → 日志 + 重启（§9.4.4 失败当可见）
                self._log(f"[状态机] 事件循环崩溃: {ex} — 重启循环")
                time.sleep(2)

    def post(self, fn) -> None:
        """事件入队（单线程顺序处理 — 天然无锁）。"""
        self._q.put(fn)

    def alive(self) -> bool:
        return (self._started and self._thread is not None
                and self._thread.is_alive())

    # ── 状态访问 ──

    def get(self, idx) -> EmuState | None:
        return self.states.get(str(idx))

    def ensure(self, idx) -> EmuState:
        """取或建（OFF 初始）。"""
        idx = str(idx)
        st = self.states.get(idx)
        if st is None:
            st = EmuState(idx=idx, node_id=self.node_id)
            self.states[idx] = st
        return st

    def snapshot(self) -> dict:
        """前端/健康检查只读快照（§9.3.2 — 复制引用不锁）。"""
        return {idx: {"state": s.state, "account_id": s.account_id,
                      "since": s.state_since, "source": s.source,
                      "cancel_pending": s.cancel_pending}
                for idx, s in self.states.items()}

    # ── 转移执行（审计 + 事件）──

    def _move(self, idx, event: str, note: str = "") -> bool:
        """执行转移（转移表驱动）+ 审计。返回是否转移。"""
        st = self.ensure(idx)
        ok, why = transition(st, event)
        self._audit(idx, event, ok, why or note)
        if ok and event in (EV_READY, EV_LOST, EV_CRASH):
            self._emit(event, idx)
        return ok

    def _audit(self, idx, event, ok, note: str) -> None:
        """状态转移审计（oplog 模式 — §9.1.4）。"""
        try:
            st = self.states.get(str(idx))
            rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "node": self.node_id, "emu": str(idx),
                   "event": event, "ok": ok,
                   "state": st.state if st else "?",
                   "account": (st.account_id or "")[:8] if st else "",
                   "note": note[:120]}
            with open(_OPLOG_PATH, "a", encoding="utf-8") as f:
                f.write(json_dumps(rec) + "\n")
        except Exception:
            pass

    def _emit(self, event, idx) -> None:
        for cb in self._subs.get(event, []):
            try:
                cb(idx)
            except Exception:
                pass

    def subscribe(self, event: str, cb) -> None:
        self._subs.setdefault(event, []).append(cb)

    # ── API（事件循环内执行）──

    def prewarm(self, idx) -> None:
        """预热（PREWARMING — 后台启动模拟器，就绪后 READY 待命）。"""
        st = self.ensure(idx)
        if self._move(idx, EV_PREWARM):
            st.prewarm_ts = time.time()
            threading.Thread(target=self._boot_bg, args=(idx,), daemon=True).start()

    def acquire(self, account) -> bool:
        """账号占用（READY/IDLE/EXTERNAL → BUSY；OFF 冷启动）。
        返回是否成功（BUSY 占用中拒绝 — 调用方等事件）。"""
        idx = str(account.get("emu_instance_index", ""))
        if not idx:
            return False
        st = self.ensure(idx)
        if st.state == BUSY:
            return False
        if self._move(idx, EV_ACQUIRE, account.get("name", "")[:16]):
            st.account_id = account.get("id")
            st.source = "system"
            if st.state == PREWARMING:
                # 冷启动路径：等 ready 事件后账号启动（调用方订阅）
                pass
            return True
        return False

    def acquire_ready(self, account) -> bool:
        """账号占用（模拟器已由调用方确认就绪 — 2026-08-15 接入 runner）。

        runner 启动链已有自己的模拟器启动/就绪确认（boot_wait + ADB 探测），
        此处不再触发状态机自己的 _boot_bg。语义:
          OFF/未跟踪 → 直接置 BUSY（调用方已确认就绪，跳过状态机启动）
          READY/IDLE/EXTERNAL → 走 acquire（转移表）
          BUSY → 拒绝（占用中）
        """
        idx = str(account.get("emu_instance_index", ""))
        if not idx:
            return False
        st = self.ensure(idx)
        if st.state == BUSY:
            return False
        if st.state == OFF:
            # 未跟踪/无状态 — 调用方确认就绪，直接标记占用
            st.state = BUSY
            st.state_since = time.time()
            st.account_id = account.get("id")
            st.source = "system"
            self._audit(idx, EV_ACQUIRE, True, account.get("name", "")[:16])
            return True
        return self.acquire(account)

    def release(self, account) -> None:
        """完成（BUSY → IDLE — 调用方负责关游戏）。幂等（§9.3.3）。

        2026-08-16 修复：不再清 account_id。此前置 None 导致 IDLE 态丢失
        账号绑定，reclaim_tick 的 `if st.account_id:` 保护块（active 账号 /
        队列前部保留窗口）对新入队/任务间切换的账号全部失效 → 模拟器被
        60s 闲置回收，下一任务又得重开（实测 00:44 emu#36 官-27 任务间
        被关-重开 churn）。保留绑定后：active_ids 命中 → 保留（任务间
        切换）；pending 前部到期 → 保留；远期回排/真空闲 → 照常回收。
        acquire() 会覆盖绑定（无跨账号脏绑定风险）。"""
        idx = str(account.get("emu_instance_index", ""))
        if not idx:
            return
        self._move(idx, EV_RELEASE, account.get("name", "")[:16])

    def close(self, idx, reason: str = "") -> None:
        """关闭（READY/IDLE → CLOSING → 后台关闭链 → OFF）。幂等。"""
        idx = str(idx)
        if self._move(idx, EV_CLOSE, reason):
            threading.Thread(target=self._close_bg, args=(idx,), daemon=True).start()

    def handle_crash(self, idx) -> None:
        """崩溃/失联（BUSY → RECOVERING — 恢复链由调用方/事件驱动）。"""
        if self._move(idx, EV_CRASH, "crash"):
            st = self.ensure(idx)
            st.crash_count += 1

    def on_ready(self, idx) -> None:
        """安卓就绪回调（PREWARMING → READY；若账号已占用 → BUSY）。"""
        if self._move(idx, EV_READY):
            st = self.states.get(str(idx))
            if st and st.account_id:
                self._move(idx, EV_ACQUIRE, "boot done → busy")

    def on_lost(self, idx, note: str = "") -> None:
        """进程消失（CLOSING → OFF / 其他 → 恢复语义由转移表定）。"""
        self._move(idx, EV_LOST, note)

    def mark_external(self, idx) -> None:
        """启动扫描发现外部启动（手动开的 — EXTERNAL）。"""
        self._move(idx, EV_MANUAL)

    # ── 回收决策（§5 排位决策 — Phase 1 框架）──

    def reclaim_tick(self) -> None:
        """周期回收：READY/IDLE 按排位决策 → CLOSING。
        只做决策 + 转移；实际关闭走 _close_bg（现有关闭链）。
        EXTERNAL/BUSY/CLOSING 由转移表拒绝（不回收）。
        并存期开关: emu_v2_reclaim=false 时只管理状态不回收（旧回收继续
        跑 — 9 道保护未全迁前不双重回收）。"""
        # ★★ 2026-09-23 用户拍板「焊死」：回收器总闸默认关闭（config emu_reclaim_enabled，
        #    缺省即 False）。与 launch_queue._reclaim_idle_emus 同一道闸，谁都别想关模拟器。
        try:
            if not self.ctx.config.get("emu_reclaim_enabled", False):
                return
        except Exception:
            return
        try:
            if not self.ctx.config.get("emu_v2_reclaim", False):
                return
            mw = getattr(self.ctx, "_mw", None)
            lq = getattr(mw, "launch_queue", None)
            runner = getattr(mw, "runner", None)
            _accounts = (getattr(mw, "accounts", None)
                         or getattr(self.ctx, "accounts", None) or [])
            _conn = getattr(mw, "connect_accounts", None) or []
            pending_ids = ({e.account_id for e in lq._pending}
                           if lq is not None else set())
            active_ids = (set(lq._active_emus.values())
                          if lq is not None else set())
            # 内存压力强制回收（#8 — 对齐旧回收 2026-08-14 内存耗尽事故修复）:
            # 可用内存低于 reclaim_force_mem_gb 时跳过「排队保留窗口」软保护
            try:
                import psutil as _psu
                _free_gb = _psu.virtual_memory().available / 1024 / 1024 / 1024
                _force = _free_gb < float(self.ctx.config.get("reclaim_force_mem_gb", 2.0) or 2.0)
            except Exception:
                _free_gb = 99.0
                _force = False
            # 排队保留窗口（#8）— 只保护 keep_min 内会轮到（含已到期等槽位）的
            # 队列前部账号；远期回排/长队尾一律回收（轮到启动时 runner 自动拉
            # 起 — 串行启动门保证不启动风暴）。
            try:
                from services.dynamic_params import get_pending_keep_min
                _keep_min = get_pending_keep_min(self.ctx.config, 10)
            except Exception:
                _keep_min = float(self.ctx.config.get("emu_pending_keep_min", 10) or 10)
            try:
                import datetime as _dtmod
                _front_keep = max(int(self.ctx.config.get("parallel_max", 1) or 1) + 4, 6)
                _pending_list = list(lq._pending) if lq is not None else []
                _front_ids = {e.account_id for e in
                              sorted(_pending_list, key=lambda e: e.sort_key)[:_front_keep]}
                _now = _dtmod.datetime.now()
                _due_ids = {aid for aid in pending_ids
                            if aid in _front_ids and any(
                                (e.not_before - _now).total_seconds() <= _keep_min * 60
                                for e in _pending_list if e.account_id == aid)}
            except Exception:
                _front_keep, _front_ids, _due_ids = 6, set(), set()
                _now = None
            # #3 实例池活 MAA 独立防线（2026-08-14 — 对齐旧回收 L1114-1166）:
            # 不依赖 _procs/active 映射 — 自动重启/残留清理竞态下映射可能丢失，
            # 若只查映射会把正在跑 MAA 的模拟器当闲置回收 → 失联 → 杀 MAA →
            # 启动失败循环（19:58 b-5 事故）。直接扫实例池 .pid/.meta。
            _active_maa_emus: set[str] = set()
            try:
                import psutil as _psu2
                _inst_root = Path(__file__).parent / "maa" / "instances"
                for _idir2 in _inst_root.glob("*"):
                    _pfile = _idir2 / ".pid"
                    if not _pfile.exists():
                        continue
                    try:
                        _pid2 = int(_pfile.read_text().strip())
                        if not _psu2.pid_exists(_pid2):
                            continue
                        try:
                            if "MAA" not in (_psu2.Process(_pid2).name() or "").upper():
                                continue
                        except Exception:
                            pass
                        _mfile = _idir2 / ".meta"
                        if not _mfile.exists():
                            continue
                        _maid = _mfile.read_text(encoding="utf-8").split("|")[0]
                        if not _maid:
                            continue
                        for _a2 in (_accounts + _conn):
                            if _a2.get("id") == _maid and _a2.get("emu_instance_index"):
                                _active_maa_emus.add(str(_a2["emu_instance_index"]))
                    except Exception:
                        continue
            except Exception:
                pass
            # 靠近共享关闭服务（只读引用 — 跨路径一致）
            try:
                from services.emu_service import (recently_closed as _rc_fn,
                                                  lock_busy as _lb_fn)
            except Exception:
                _rc_fn = _lb_fn = None
            for idx, st in list(self.states.items()):
                try:
                    if st.state not in (READY, IDLE, EXTERNAL):
                        continue  # BUSY/CLOSING 转移表已拒绝
                    if st.state == EXTERNAL:
                        # 手动开的闲置超时才回收（用户 2026-08-12: 全归回收管）
                        _ext_min = int(self.ctx.config.get("emu_external_reclaim_min", 30) or 30)
                        if time.time() - st.state_since < _ext_min * 60:
                            continue
                    # #5 关闭冷却（5 分钟 — 刚关过的进程可能还在退出）。读共享
                    # recently_closed（旧回收/即时回收 direct_shutdown + v2 的
                    # graceful_shutdown 都经 mark_closed 写入 → 跨回收路径一致；
                    # 本机 _closed_at 兜底，防关闭走到非 mark_closed 路径）
                    _cooling = bool(_rc_fn and _rc_fn(idx))
                    if not _cooling:
                        _t = self._closed_at.get(idx, 0)
                        _cooling = bool(_t and time.time() - _t < 300)
                    if _cooling:
                        continue
                    # #7 手动/API 启动保护期（10 分钟）— 用户手动开的/API 启动的
                    # 模拟器不立刻回收，否则手动操作被反复打断（2026-08-10 实测）。
                    _mts = getattr(mw, "manual_emu_started", None)
                    if _mts:
                        _t7 = _mts.get(str(idx))
                        if _t7 and time.time() - _t7 < 600:
                            continue
                    # #6 优雅关闭进行中 → 跳过（回收的直接 shutdown 绕过 graceful
                    # 锁，会与进行中的优雅关闭并发 → 二次关闭。2026-08-11 实测）。
                    if _lb_fn and _lb_fn(idx):
                        continue
            # #3 实例池活 MAA 保护（独立防线 — 不再查映射）
                    if str(idx) in _active_maa_emus:
                        continue
                    # #3b adb 连接保护（2026-08-15 补 — 对齐旧回收终极保险）:
                    # 扫 adb.exe 进程命令行，任何连接 127.0.0.1:<port> 的 adb
                    # 客户端（MAA 正在连该模拟器）→ 不回收。完全不依赖队列/
                    # 实例映射（映射丢失时依然有效），堵死"运行中账号的模拟器
                    # 被误关 → MAA 失联"（21:53 四账号同秒 exit=1 事故模式）。
                    try:
                        import psutil as _psu3
                        import re as _re3
                        _busy_port = False
                        for _p3 in _psu3.process_iter(["name", "cmdline"]):
                            try:
                                if (_p3.info["name"] or "").lower() != "adb.exe":
                                    continue
                                _cl3 = " ".join(_p3.info["cmdline"] or [])
                                _m3 = _re3.search(r"127\.0\.0\.1:(\d+)", _cl3)
                                if not _m3:
                                    continue
                                _port3 = _m3.group(1)
                                for _a3 in (_accounts + _conn):
                                    _ad3 = _a3.get("adb_address", "")
                                    if (_ad3.endswith(":" + _port3)
                                            and str(_a3.get("emu_instance_index", "")) == str(idx)):
                                        _busy_port = True
                                        break
                                if _busy_port:
                                    break
                            except Exception:
                                continue
                        if _busy_port:
                            continue
                    except Exception:
                        pass
                    # 排位决策: active → 无条件保留；pending → 仅在保留窗口内
                    # 保留（#8）
                    if st.account_id:
                        if st.account_id in active_ids:
                            continue
                        if st.account_id in pending_ids:
                            if st.account_id in _due_ids and not _force:
                                continue
                            # 远期回排/长队尾/内存压力 → 不保留，落到回收下方
                        # #2 MAA 进程还活着（降级优雅关闭中/账号过渡期/连接页
                        # 手动操作）→ 保留模拟器 — 否则关掉模拟器后 MAA 失联
                        # 卡住，且进程活着不触发自动重启（2026-08-11 用户:
                        # "MAA 还开着，模拟器自己关掉了，之后也没有重启" —
                        # 与旧回收 _has_real_process 保护对齐，已全迁）。
                        try:
                            if runner and runner._has_real_process(st.account_id):
                                continue
                        except Exception:
                            pass
                    # 闲置超时（READY/IDLE — 60s）
                    if time.time() - st.state_since < 60:
                        continue
                    self._move(idx, EV_RECLAIM, "idle timeout")
                    self._closed_at[idx] = time.time()
                    threading.Thread(target=self._close_bg, args=(idx,),
                                     daemon=True).start()
                except Exception:
                    pass
            # 壳识别（A 型崩溃残留 — 低内存 + 启动 >5 分钟 → 直接 taskkill）
            # 2026-08-15 修复: IDLE 状态豁免 — 任务完成+游戏已关（force-stop）
            # 后模拟器内存会掉到 150-200MB（游戏是内存大头），这是正常空闲
            # 不是崩溃壳（实测 b-4/b-5: 完成后面临"任务完成挂起"→ 壳识别
            # 169MB 误杀 → 模拟器终止 → MAA 重连失败连环）。真崩溃的模拟器
            # ADB 会失联，由失联恢复链（reclaim 的进程检查）处理。
            try:
                from services.emu_service import _running_headless_idx
                import psutil as _ps
                running = set(_running_headless_idx())
                for idx, st in list(self.states.items()):
                    try:
                        if idx not in running:
                            continue
                        if st.state in (BUSY, PREWARMING, IDLE):
                            continue
                        for _p in _ps.process_iter(["name", "cmdline", "create_time", "memory_info"]):
                            try:
                                import re as _re
                                if _p.info["name"] != "MuMuVMMHeadless.exe":
                                    continue
                                if not _re.search(r"MuMuPlayer-12\.0-" + str(idx) + r"\b",
                                                  " ".join(_p.info["cmdline"] or [])):
                                    continue
                                _age = time.time() - _p.info["create_time"]
                                _mb = (_p.info["memory_info"].rss if _p.info["memory_info"] else 0) / 1e6
                                if _age > 300 and _mb < 120:
                                    self._log(f"[状态机] 壳识别 #{idx} ({int(_mb)}MB 残留) — taskkill")
                                    import subprocess
                                    subprocess.run(["taskkill", "/F", "/PID", str(_p.pid)],
                                                   capture_output=True, timeout=5,
                                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                                    self._move(idx, EV_LOST, "shell killed")
                                break
                            except Exception:
                                pass
                    except Exception:
                        pass
            except Exception:
                pass
        except Exception:
            pass

    # ── 后台长操作（事件循环外 — §9.5.2）──

    def _boot_bg(self, idx: str) -> None:
        """后台启动模拟器（VBox launch → 等安卓就绪 → on_ready）。"""
        try:
            from infrastructure.task_constants import find_mumu_cli, cli_flag
            cli = find_mumu_cli()
            if not cli:
                self.on_lost(idx, "no cli")
                return
            import subprocess
            subprocess.run([cli, "control", cli_flag(cli), idx, "launch"],
                           capture_output=True, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            # 等安卓就绪（最 60s）
            dl = time.time() + 60
            while time.time() < dl:
                time.sleep(5)
                try:
                    r = subprocess.run([cli, "info", cli_flag(cli), idx],
                                       capture_output=True, text=True, timeout=5,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                       encoding="utf-8", errors="replace")
                    if r.returncode == 0:
                        d = json_loads(r.stdout.lstrip("\ufeff").strip())
                        if d.get("is_android_started"):
                            self.on_ready(idx)
                            return
                except Exception:
                    pass
            self.on_lost(idx, "boot timeout")
        except Exception as ex:
            self.on_lost(idx, f"boot err {ex}")

    def _close_bg(self, idx: str) -> None:
        """后台关闭（复用 emu_service 关闭链 — 优雅 → 兜底 → OFF）。"""
        try:
            from services.emu_service import graceful_shutdown
            from infrastructure.task_constants import find_mumu_cli, cli_flag
            cli = find_mumu_cli()
            if cli:
                graceful_shutdown(cli, idx, log=lambda m: self._log(f"[状态机关闭] {m}"))
            self.on_lost(idx, "closed")
        except Exception as ex:
            self._log(f"[状态机] 关闭 #{idx} 失败: {ex}")
            self.on_lost(idx, f"close err {ex}")

    # ── 启动恢复（§3 重启重建 — 吸收 _system_started 教训）──

    def scan_and_recover(self) -> None:
        """启动扫描真实进程重建 emu_state（壳识别/EXTERNAL 识别）。
        由 main_web 启动时调用（队列 _restore 之后）。
        有 .pid（系统管理的实例）→ 不标 EXTERNAL（属系统池，回收语义保留）；
        无 .pid → EXTERNAL（用户手动开的 — 不回收）。"""
        try:
            from services.emu_service import _running_headless_idx
            from infrastructure.task_constants import detect_emu_instances
            running = _running_headless_idx()
            inst_root = Path(__file__).parent / "maa" / "instances"
            # 管理范围判定（#1 — 复用 launch_queue/task_constants 既有判定）:
            # detect_emu_instances 已叠加 _strip_excluded_emus 过滤（EMU_EXCLUDE
            # "l-*" 排除、EMU_INCLUDE 白名单 l-4/l-6 放行）→ 返回的即本仓库管理
            # 范围内的模拟器（含 index/name）。范围外（他人 l- workspace、未纳管
            # l-）不在返回集 → 视为不受本回收管理，永不标 EXTERNAL（永不进回收
            # 路径）— 对齐旧回收 aid=None 永不回收（launch_queue L1153-1159，
            # 防 2026-08-10 l-系列误关事故）。
            try:
                _managed_idx = {str(e.get("index", "")) for e in detect_emu_instances()}
            except Exception:
                _managed_idx = set(running)
            for idx in running:
                st = self.ensure(idx)
                if st.state == OFF:
                    # 范围外模拟器 → 不标 EXTERNAL（不回收；保留手动模式无人动）
                    if str(idx) not in _managed_idx:
                        continue
                    has_pid = (inst_root / str(idx) / ".pid").exists()
                    if not has_pid:
                        # 管理范围内且无 .pid 的进程 = 用户手动开的（EXTERNAL —
                        # 闲置超时才回收，且活 MAA 仍被 #3 防线保护）
                        self._move(idx, EV_MANUAL, "scan found (no pid)")
        except Exception:
            pass

    def _log(self, m: str) -> None:
        try:
            self.ctx.log(f"[状态机] {m}")
        except Exception:
            pass


def json_dumps(rec: dict) -> str:
    import json
    return json.dumps(rec, ensure_ascii=False)


def json_loads(s: str):
    import json
    return json.loads(s)
