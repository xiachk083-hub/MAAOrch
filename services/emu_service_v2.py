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

    def release(self, account) -> None:
        """完成（BUSY → IDLE — 调用方负责关游戏）。幂等（§9.3.3）。"""
        idx = str(account.get("emu_instance_index", ""))
        if not idx:
            return
        self._move(idx, EV_RELEASE, account.get("name", "")[:16])
        st = self.states.get(idx)
        if st:
            st.account_id = None

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
        try:
            if not self.ctx.config.get("emu_v2_reclaim", False):
                return
            lq = getattr(getattr(self.ctx, "_mw", None), "launch_queue", None)
            pending_ids = ({e.account_id for e in lq._pending}
                           if lq is not None else set())
            active_ids = (set(lq._active_emus.values())
                          if lq is not None else set())
            for idx, st in list(self.states.items()):
                try:
                    if st.state not in (READY, IDLE):
                        continue  # BUSY/EXTERNAL/CLOSING 转移表已拒绝
                    # 关闭冷却（5 分钟 — 刚关过的进程可能还在退出）
                    _t = self._closed_at.get(idx, 0)
                    if _t and time.time() - _t < 300:
                        continue
                    # 排位决策: 账号在 pending/active → 保留（30 分钟内轮到）
                    if st.account_id:
                        if st.account_id in pending_ids or st.account_id in active_ids:
                            continue
                    # 闲置超时（READY/IDLE — 60s）
                    if time.time() - st.state_since < 60:
                        continue
                    self._move(idx, EV_RECLAIM, "idle timeout")
                    self._closed_at[idx] = time.time()
                    threading.Thread(target=self._close_bg, args=(idx,),
                                     daemon=True).start()
                except Exception:
                    pass
            # 壳识别（A 型崩溃残留 — <200MB + 启动 >5 分钟 → 直接 taskkill）
            try:
                from services.emu_service import _running_headless_idx
                import psutil as _ps
                running = set(_running_headless_idx())
                for idx, st in list(self.states.items()):
                    try:
                        if idx not in running:
                            continue
                        if st.state in (BUSY, PREWARMING):
                            continue
                        for _p in _ps.process_iter(["name", "cmdline", "create_time", "memory_info"]):
                            try:
                                import re as _re
                                if _p.info["name"] != "MuMuVMMHeadless.exe":
                                    continue
                                if not _re.search(r"MuMuPlayer-12\.0-" + str(idx) + r"",
                                                  " ".join(_p.info["cmdline"] or [])):
                                    continue
                                _age = time.time() - _p.info["create_time"]
                                _mb = (_p.info["memory_info"].rss if _p.info["memory_info"] else 0) / 1e6
                                if _age > 300 and _mb < 200:
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
            running = _running_headless_idx()
            inst_root = Path(__file__).parent / "maa" / "instances"
            for idx in running:
                st = self.ensure(idx)
                if st.state == OFF:
                    has_pid = (inst_root / str(idx) / ".pid").exists()
                    if not has_pid:
                        # 无 .pid 的进程 = 用户手动开的（EXTERNAL — 不回收）
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
