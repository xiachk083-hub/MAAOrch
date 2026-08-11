"""EmulatorService 状态机核心（2026-08-12 Phase 1）—
EmuState 单一数据源 + 状态转移表（docs/EMULATOR_SERVICE.md §11）。

替代散落的判定（_active_emus/_system_started/recently_closed/_locks）：
"这个模拟器什么状态"只查 emu_state，状态转移由转移表驱动（非法转移拒绝）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

# ── 状态 ──
OFF = "OFF"                  # 无 VMM 进程
PREWARMING = "PREWARMING"    # VBox 启动中（安卓未就绪）
READY = "READY"              # 安卓就绪，无账号占用
BUSY = "BUSY"                # 账号占用（MAA 运行）
IDLE = "IDLE"                # 账号完成，游戏已关（空载，等待排位决策）
CLOSING = "CLOSING"          # 关闭中（优雅 → 兜底）
RECOVERING = "RECOVERING"    # 崩溃/失联恢复中
EXTERNAL = "EXTERNAL"        # 用户手动/外部启动（不回收、可被接管）

ALL_STATES = (OFF, PREWARMING, READY, BUSY, IDLE, CLOSING, RECOVERING, EXTERNAL)

# ── 事件 ──
EV_PREWARM = "prewarm"
EV_ACQUIRE = "acquire"
EV_RELEASE = "release"
EV_CLOSE = "close"
EV_RECLAIM = "reclaim"
EV_CRASH = "crash"
EV_READY = "ready"
EV_LOST = "lost"
EV_MANUAL = "manual"
EV_CANCEL = "cancel"

ALL_EVENTS = (EV_PREWARM, EV_ACQUIRE, EV_RELEASE, EV_CLOSE, EV_RECLAIM,
              EV_CRASH, EV_READY, EV_LOST, EV_MANUAL, EV_CANCEL)


@dataclass
class EmuState:
    """模拟器状态（单一数据源 — docs §3）。"""
    idx: str
    node_id: str = ""            # 实体电脑标识（多机预留 — docs §7）
    state: str = OFF
    account_id: str | None = None
    state_since: float = 0.0
    boot_ok: bool = False
    adb_port: int | None = None
    vaddr_errors: int = 0
    vaddr_window_ts: float = 0.0
    crash_count: int = 0
    prewarm_ts: float | None = None
    cancel_pending: bool = False
    source: str = ""             # system / manual / connect（EXTERNAL 来源）
    extra: dict = field(default_factory=dict)


# ── 状态转移表（docs §11 — 规格：8 状态 × 10 事件）──
# 值: 目标状态（str）或动作 tuple；缺省 = 拒绝/忽略（转移函数处理）
_TRANSITIONS: dict[str, dict[str, str | tuple]] = {
    OFF: {
        EV_PREWARM: PREWARMING,
        EV_ACQUIRE: PREWARMING,      # 冷启动（acquire 走启动）
        EV_MANUAL: EXTERNAL,         # 扫描发现外部启动
    },
    PREWARMING: {
        EV_READY: READY,             # 安卓就绪
        EV_LOST: ("start_failed",),  # 启动失败 → 调用方计次/回 OFF
        EV_CANCEL: ("cancel_pending",),  # 取消 = 等自然完成再回收（防壳）
    },
    READY: {
        EV_ACQUIRE: BUSY,
        EV_CLOSE: CLOSING,
        EV_RECLAIM: CLOSING,         # 闲置超时回收
    },
    BUSY: {
        EV_RELEASE: IDLE,            # 完成（调用方负责关游戏）
        EV_CRASH: RECOVERING,
        EV_LOST: RECOVERING,
    },
    IDLE: {
        EV_ACQUIRE: BUSY,            # 复用（下轮直接用）
        EV_RELEASE: ("ignore",),     # 幂等（完成事件重复触发）
        EV_CLOSE: CLOSING,
        EV_RECLAIM: CLOSING,         # 排位决策：回收
    },
    CLOSING: {
        EV_LOST: OFF,                # 进程消失 → 关闭完成
        EV_CLOSE: ("ignore",),       # 幂等
        EV_RECLAIM: ("ignore",),     # 已在关
    },
    RECOVERING: {
        EV_READY: BUSY,              # 模拟器恢复 → 账号重试
        EV_LOST: ("retry",),         # 恢复失败 → 调用方计次（N 次挂起）
    },
    EXTERNAL: {
        EV_ACQUIRE: BUSY,            # 接管（连接页/账号启动直接连）
        EV_CLOSE: ("reject",),       # 直接 close 拒绝（用户手动开的）
        EV_RECLAIM: CLOSING,         # 回收允许 — 闲置超时由 reclaim_tick 判定
                                     # （2026-08-12 用户: 模拟器全归回收管）
        EV_LOST: OFF,                # 用户自己关了
    },
}


def transition(st: EmuState, event: str) -> tuple[bool, str | None]:
    """执行状态转移（转移表驱动）。

    返回 (是否转移, 说明/拒绝原因)。转移成功更新 state/state_since。
    调用方负责审计日志（转移记录由事件循环统一写）。
    """
    rules = _TRANSITIONS.get(st.state, {})
    target = rules.get(event)
    if target is None:
        return False, f"{st.state} 不允许 {event}"
    if isinstance(target, str):
        st.state = target
        st.state_since = time.time()
        return True, None
    act = target[0]
    if act == "ignore":
        return False, "忽略（幂等）"
    if act == "reject":
        return False, f"{st.state} 拒绝 {event}"
    if act == "cancel_pending":
        st.cancel_pending = True
        return True, None
    if act in ("start_failed", "retry"):
        return False, act  # 动作由调用方处理（计次/挂起）
    return False, f"未知动作 {act}"


def transitable(st: EmuState, event: str) -> bool:
    """只读检查是否允许该事件（不转移）。"""
    rules = _TRANSITIONS.get(st.state, {})
    return event in rules
