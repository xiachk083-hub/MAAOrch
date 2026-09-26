# -*- coding: utf-8 -*-
"""MAS（AUTO-MAS）门面：Harness ⇄ MAAOrch ⇄ AUTO-MAS 三个子系统粘合剂里 **MAS 的那一半**。

唯一入口。MCP 工具 / 其他进程内调用方只认本模块的函数，**不直接 import services.mas_l3**
（把"远端调度器怎么采"关在门后面，将来物理搬迁 tools/mas_watch.py 时只动这一层）。

设计约束（粘合剂不能因一端坏掉而整体崩）：
- **惰性加载**：services.mas_l3 在首次调用时才 import；模块顶层不做业务 import，
  避免启动期副作用（mas_l3 内部会按需 exec tools/mas_watch.py）。
- **不抛异常**：mas_l3 加载失败或函数抛错时，统一返回
  {ok: False, error: ..., detail: ..., degraded: True}，调用方按 dict 处理即可。
- **降级集中判定**：MAS 主机（SSH 采集 / HTTP 历史）不可达一类问题统一由 _degrade()
  打上 degraded=True / host_unreachable=True / degrade_reason，
  前端与 Agent 只看这几个标记，不必各自做错误字符串匹配。

稳定契约（返回值一律 dict）：
    snapshot(refresh=False)          主机实时快照（SSH，只读）
    status(days=7, refresh=False)    产线总览（快照+历史聚合+留痕，只读）
    history(days=7)                  近 N 天原始历史（HTTP，只读）
    ledger(n=12)                     本地留痕末尾 n 条（只读）
    rerun_state()                    后台补跑进度（只读）
    rerun(accounts, dry_run=True)    补跑发车【写操作，默认只预演】
    cleanup(dry_run=True)            清理远端临时补跑队列【写操作，默认只预演】
"""
from __future__ import annotations

import importlib
import os
import sys
import threading
from typing import Any

_MODULE = "services.mas_l3"

_LOCK = threading.Lock()
_MOD: Any = None
_LOAD_ERR = ""

# MAS 主机不可达 / 网络类错误的统一指纹（小写子串匹配）
_UNREACHABLE_HINTS = (
    "ssh", "timed out", "timeout", "unreachable", "refused", "no route",
    "connection", "getaddrinfo", "host is down", "socket", "network",
    "packet loss", "reset by peer", "unable to connect", "urlopen error",
)


# ── 惰性加载 ────────────────────────────────────────────────────────

def _l3() -> Any:
    """惰性加载 services.mas_l3；失败返回 None（错误记在 _LOAD_ERR，下次调用会重试）。"""
    global _MOD, _LOAD_ERR
    if _MOD is not None:
        return _MOD
    with _LOCK:
        if _MOD is not None:
            return _MOD
        try:
            try:
                _MOD = importlib.import_module(_MODULE)
            except ModuleNotFoundError:
                # 少见：进程 sys.path 没带项目根（如脚本从别处启动）→ 补上再试一次
                root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                if root not in sys.path:
                    sys.path.insert(0, root)
                _MOD = importlib.import_module(_MODULE)
            _LOAD_ERR = ""
        except Exception as e:                                    # noqa: BLE001
            _MOD = None
            _LOAD_ERR = f"{type(e).__name__}: {e}"[:200]
        return _MOD


# ── 结构化错误 / 降级判定 ───────────────────────────────────────────

def _err(msg: str, detail: str = "", **extra: Any) -> dict:
    out: dict = {"ok": False, "error": msg, "source": "mas_l3"}
    if detail:
        out["detail"] = detail
    out.update(extra)
    return out


def _degrade(res: Any) -> Any:
    """集中判定「MAS 主机不可达」这类降级：给失败结果补 degraded/host_unreachable 标记。"""
    if not isinstance(res, dict):
        return res
    out = dict(res)
    if out.get("ok") is False or out.get("error"):
        text = f"{out.get('error') or ''} {out.get('detail') or ''}".lower()
        if any(h in text for h in _UNREACHABLE_HINTS):
            out.setdefault("host_unreachable", True)
        if out.get("stale") or out.get("host_unreachable"):
            out.setdefault("degraded", True)
            out.setdefault("degrade_reason",
                           "mas_host_unreachable" if out.get("host_unreachable") else "stale_cache")
    return out


def _call(fn_name: str, *args: Any, **kwargs: Any) -> Any:
    """调用 mas_l3 的函数；加载失败/抛异常 → 结构化错误，绝不向上抛。"""
    mod = _l3()
    if mod is None:
        return _err("MAS 服务层不可用（services.mas_l3 加载失败）", _LOAD_ERR,
                    degraded=True, host_unreachable=False)
    fn = getattr(mod, fn_name, None)
    if not callable(fn):
        return _err(f"MAS 服务层缺少接口 {fn_name}()", "", degraded=True)
    try:
        r = fn(*args, **kwargs)
    except Exception as e:                                        # noqa: BLE001
        return _degrade(_err(f"MAS {fn_name} 调用失败", f"{type(e).__name__}: {e}"[:300]))
    return _degrade(r)


def _days(days: Any) -> int:
    try:
        return max(1, min(int(days or 7), 30))                    # 与 /api/mas/status 的钳制一致
    except (TypeError, ValueError):
        return 7


def _accounts(accounts: Any) -> list[str]:
    if isinstance(accounts, str):
        accounts = accounts.split(",")
    return [str(a).strip() for a in (accounts or []) if str(a).strip()][:8]


# ── 只读 ───────────────────────────────────────────────────────────

def snapshot(refresh: bool = False) -> dict:
    """主机实时快照（SSH；45 秒缓存，refresh=True 绕过）。主机不可达 → ok=False + degraded。"""
    return _call("host_snapshot", refresh=bool(refresh)) or _err("MAS 主机快照无返回")


def status(days: int = 7, refresh: bool = False) -> dict:
    """产线总览：主机实时 + 今日/逐日 + 失败原因/钉子户 + 留痕 + 补跑状态。"""
    return _call("collect_status", days=_days(days), refresh=bool(refresh)) or _err("MAS 总览无返回")


def history(days: int = 7) -> dict:
    """近 N 天原始历史（AUTO-MAS /api/history/search）。成功包成 {ok, days, history}。"""
    d = _days(days)
    r = _call("history", days=d)
    if isinstance(r, dict) and r.get("ok") is False:
        return r
    return {"ok": True, "days": d, "history": r if isinstance(r, dict) else {}}


def ledger(n: int = 12) -> dict:
    """本地留痕（runtime/mas_ledger.jsonl）末尾 n 条。成功包成 {ok, count, items}。"""
    try:
        k = max(1, min(int(n or 12), 200))
    except (TypeError, ValueError):
        k = 12
    r = _call("_ledger_tail", k)
    if isinstance(r, dict) and r.get("ok") is False:
        return r
    items = r if isinstance(r, list) else []
    return {"ok": True, "count": len(items), "items": items}


def rerun_state() -> dict:
    """后台补跑进度。注意 mas_l3 的 state 自身也带 ok（补跑结果）→ 统一包成 {ok, state}。"""
    r = _call("rerun_state")
    if isinstance(r, dict) and r.get("ok") is False and "running" not in r:
        return r
    return {"ok": True, "state": r if isinstance(r, dict) else {}}


# ── 写操作（默认 dry_run，预演不碰远端）─────────────────────────────

def rerun(accounts: Any = None, dry_run: bool = True) -> dict:
    """补跑发车【写操作】：给远端 AUTO-MAS 建临时队列补跑失败账号（最多 8 个）。

    dry_run=True（默认）只预演：不调用 mas_l3.rerun_async，返回将要发的号；
    accounts 为空时按主机快照的失败号给出候选（快照不可达则在结果里带降级标记）。
    """
    accs = _accounts(accounts)
    if dry_run:
        out: dict = {"ok": True, "dry_run": True, "action": "rerun", "would_start": accs,
                     "note": "预演：未触达远端 MAS 队列；显式 dry_run=False 才真正发补跑"}
        if not accs:
            snap = snapshot()
            if isinstance(snap, dict) and snap.get("ok"):
                out["resolved_from_snapshot"] = [
                    f.get("name") for f in (snap.get("failures") or []) if f.get("name")][:8]
            else:
                out["resolved_from_snapshot"] = []
                out["warning"] = "主机快照不可用，无法解析待补跑账号"
                for k in ("error", "detail", "degraded", "host_unreachable", "degrade_reason"):
                    if isinstance(snap, dict) and k in snap:
                        out[k] = snap[k]
        return out
    return _call("rerun_async", accs)


def cleanup(dry_run: bool = True) -> dict:
    """清理远端临时补跑队列【写操作】。

    dry_run=True（默认）只预演；只有显式 dry_run=False 才真正执行
    （tools/auto_mas_rerun.py --cleanup → 删除远端 MAS 上的临时补跑队列）。
    """
    if dry_run:
        return {"ok": True, "dry_run": True, "action": "cleanup",
                "target": "tools/auto_mas_rerun.py --cleanup",
                "note": "预演：未删除任何远端队列；显式 dry_run=False 才真正清理"}
    return _call("cleanup")
