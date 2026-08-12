"""运行期自适应参数（2026-08-12 用户: 校准完还是死的 → 参数要活着）。

滚动窗口数据 → 每次决策取最新（缓存 10 分钟防频繁 IO）；
全部 clamp 上下限 + 样本不足回退默认 — 异常数据不会把参数推到极端。
config 显式配置优先（用户手改 > 自适应）。

数据源:
  logs/stats/launch_chain.jsonl       — 启动链路耗时（_mark_launch_ready 记录）
  models/accounts/*/stats.json        — RunStats 完成运行时长（保险丝数据）

校准依据（tools/param_calibrate_asst.py，964 样本 2026-08-12）:
  启动→ADB就绪 med=13s p90=16s max=45s  → launch_ready_timeout
  完成运行 med=15m p90=27m max=59m      → max_run_minutes（误杀检查 0/964）
  短重试(<60m) med=13.9m p90=44.5m      → emu_pending_keep_min
"""
from __future__ import annotations
import json
import time
from pathlib import Path

BASE = Path(__file__).parent.parent
STATS_DIR = BASE / "logs" / "stats"
LAUNCH_CHAIN_FILE = STATS_DIR / "launch_chain.jsonl"
# RunStats 数据目录（models/stats.py: _dir = models/accounts/<aid>/stats.json）
ACCOUNTS_DIRS = (BASE / "models" / "accounts",)

_CACHE: dict[str, tuple[float, float]] = {}  # name → (过期时间, 值)
_CACHE_TTL = 600  # 10 分钟


def _cached(name: str, fn) -> float:
    now = time.time()
    hit = _CACHE.get(name)
    if hit and hit[0] > now:
        return hit[1]
    v = fn()
    _CACHE[name] = (now + _CACHE_TTL, v)
    return v


def _clamp(v: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, v))


def _p90(v: list) -> float:
    v = sorted(v)
    return v[int(len(v) * 0.9)]


# ── 启动链路耗时 ──────────────────────────────────────────────

def record_launch_chain(aid: str, sec: float) -> None:
    """记录一次启动链路耗时（MAA 连上 ADB 时调用，_mark_launch_ready）。"""
    if not aid or sec <= 0:
        return
    try:
        STATS_DIR.mkdir(parents=True, exist_ok=True)
        with open(LAUNCH_CHAIN_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "aid": aid[:8], "sec": round(sec, 1)}) + "\n")
    except Exception:
        pass  # 记录失败不影响主流程（下次决策回退默认）


def _launch_chain_recent(n: int = 40) -> list[float]:
    out = []
    try:
        for ln in open(LAUNCH_CHAIN_FILE, encoding="utf-8", errors="replace"):
            try:
                d = json.loads(ln)
                if d.get("sec") and d["sec"] > 0:
                    out.append(float(d["sec"]))
            except Exception:
                continue
    except FileNotFoundError:
        return []
    return out[-n:]


# ── 任务时长（完成运行）───────────────────────────────────────

def _task_dur_recent(n: int = 60) -> list[float]:
    """最近 n 次完整运行时长（分钟）— 扫 RunStats 每账号 stats.json。"""
    out = []
    try:
        for base in ACCOUNTS_DIRS:
            for fp in sorted(base.glob("*/stats.json")):
                try:
                    data = json.loads(fp.read_text(encoding="utf-8", errors="replace"))
                except Exception:
                    continue
                for r in data.get("runs", []):
                    el = r.get("elapsed") or 0
                    if el > 0:
                        out.append(float(el) / 60.0)
    except Exception:
        return []
    return out[-n:]


# ── 重试间隔（同账号相邻启动）─────────────────────────────────

def _retry_gap_recent(n: int = 40) -> list[float]:
    """同账号相邻启动间隔（分钟）— 启动链路记录天然带 aid+时间。
    短间隔=失败重试；长间隔=正常调度/队列长等（回收器该回收就回收）。"""
    by_aid: dict[str, list[float]] = {}
    try:
        for ln in open(LAUNCH_CHAIN_FILE, encoding="utf-8", errors="replace"):
            try:
                d = json.loads(ln)
                ts = d.get("ts", "")
                aid = d.get("aid", "")
                if ts and aid:
                    by_aid.setdefault(aid, []).append(
                        time.mktime(time.strptime(ts, "%Y-%m-%d %H:%M:%S")))
            except Exception:
                continue
    except FileNotFoundError:
        return []
    gaps = []
    for ts_list in by_aid.values():
        ts_list.sort()
        for i in range(1, len(ts_list)):
            g = (ts_list[i] - ts_list[i - 1]) / 60
            if 0 < g < 360:  # <6h 才算重试（隔天调度不算）
                gaps.append(g)
    return gaps[-n:]


# ── 动态取值（config 显式配置优先）────────────────────────────

def get_launch_ready_timeout(config, fallback: float) -> float:
    """就绪等待超时（秒）: 启动链路 p90×2.5 clamp[60,180]；样本<10 回退。"""
    v = config.get("launch_ready_timeout")
    if v:
        return float(v)

    def calc():
        vals = _launch_chain_recent()
        if len(vals) < 10:
            return fallback
        return _clamp(_p90(vals) * 2.5, 60, 180)
    return _cached("launch_ready_timeout", calc)


def get_max_run_sec(config, fallback: float) -> float:
    """任务卡死保险丝（秒）: 完成时长 p90×1.5 clamp[60,180] 分钟；样本<10 回退。
    误杀检查（964 样本）: 无任何运行存活 >60m → 收紧安全。"""
    v = config.get("max_run_minutes")
    if v:
        return float(v) * 60

    def calc():
        vals = _task_dur_recent()
        if len(vals) < 10:
            return fallback
        return _clamp(_p90(vals) * 1.5, 60, 180) * 60
    return _cached("max_run_sec", calc)


def get_pending_keep_min(config, fallback: float) -> float:
    """排队中账号的模拟器保留（分钟）: 短重试 p90+5 clamp[15,60]；样本<10 回退。
    语义: 覆盖一轮失败重试（防回收→冷启动风暴），长队列等待该回收省内存。"""
    v = config.get("emu_pending_keep_min")
    if v:
        return float(v)

    def calc():
        gaps = _retry_gap_recent()
        short = [g for g in gaps if g < 60]
        if len(short) < 10:
            return fallback
        return _clamp(_p90(short) + 5, 15, 60)
    return _cached("pending_keep_min", calc)


def debug_state() -> dict:
    """当前自适应值（排障/界面展示用）。"""
    return {
        "launch_ready_timeout": get_launch_ready_timeout({}, 150),
        "max_run_sec": get_max_run_sec({}, 180 * 60),
        "pending_keep_min": get_pending_keep_min({}, 10),
        "launch_chain_samples": len(_launch_chain_recent()),
        "task_dur_samples": len(_task_dur_recent()),
        "retry_gap_samples": len(_retry_gap_recent()),
    }
