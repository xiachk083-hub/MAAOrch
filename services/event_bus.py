"""统一事件总线（EventBus）— 单点收敛第一步（2026-08-14 立项）。

「皇帝架构」：所有模块的运行时事件统一经 emit() 出口 —
  - 订阅回调（项目内模块即时响应，同步）
  - 结构化落盘 logs/event_bus.jsonl（排障一查到底）
  - 内存环形缓冲（状态快照端点用）
  - SSE 桥接由 api 层轮询 drain_since()（渐进接入）

事件类型约定（前缀=域）：
  queue: launched / completed / failed / requeued / suspended / resumed
  emu:   boot_ok / boot_timeout / closed / reclaimed / crashed / lost
  maa:   stuck / stalled / downgraded / zombie_killed / error_window
  fight: sanity_updated / medicine_used / fight_stalled
  ops:   deployed / config_saved / probe_done

迁移期双写：新代码发 bus，旧代码路径保留；行为等价后逐步切换
（STRUCTURE.md 原则）。本模块零依赖（仅 stdlib），可被任何层引用。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

_ROOT = Path(__file__).parent.parent
_BUS_FILE = _ROOT / "logs" / "event_bus.jsonl"
_MAX_RING = 500          # 内存环形缓冲条数
_MAX_FILE_BYTES = 8 << 20  # 落盘文件超过 8MB 轮换为 .1

_lock = threading.RLock()
_subs: dict[str, list[Callable[[dict], None]]] = {"*": []}
_ring: list[dict] = []
_seq = 0


def _rotate_file() -> None:
    try:
        if _BUS_FILE.exists() and _BUS_FILE.stat().st_size > _MAX_FILE_BYTES:
            _BUS_FILE.replace(_BUS_FILE.with_suffix(".jsonl.1"))
    except Exception:
        pass


def emit(event_type: str, payload: dict | None = None, aid: str = "") -> dict:
    """统一事件出口。返回事件 dict（含 seq/time）。"""
    global _seq
    evt = {
        "seq": 0,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "type": event_type,
        "aid": aid or (payload or {}).get("aid", "") if payload else "",
        "data": payload or {},
    }
    with _lock:
        _seq += 1
        evt["seq"] = _seq
        _ring.append(evt)
        if len(_ring) > _MAX_RING:
            del _ring[: len(_ring) - _MAX_RING]
        # 落盘（失败不阻塞业务）
        try:
            _BUS_FILE.parent.mkdir(parents=True, exist_ok=True)
            _rotate_file()
            with _BUS_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(evt, ensure_ascii=False) + "\n")
        except Exception:
            pass
        # 订阅回调（同步；订阅者自己保证快，重活放线程）
        for fn in list(_subs.get("*", [])):
            _safe_call(fn, evt)
        for fn in list(_subs.get(event_type, [])):
            _safe_call(fn, evt)
    return evt


def _safe_call(fn: Callable[[dict], None], evt: dict) -> None:
    try:
        fn(evt)
    except Exception:
        pass  # 订阅者异常不阻塞总线


def subscribe(event_type: str | list[str], fn: Callable[[dict], None]) -> Callable[[], None]:
    """订阅事件（"*" = 全部）。返回取消订阅函数。"""
    types = [event_type] if isinstance(event_type, str) else list(event_type)
    with _lock:
        for t in types:
            _subs.setdefault(t, []).append(fn)

    def _unsub() -> None:
        with _lock:
            for t in types:
                try:
                    _subs.get(t, []).remove(fn)
                except ValueError:
                    pass

    return _unsub


def drain_since(seq: int, limit: int = 200) -> list[dict]:
    """取 seq 之后的事件（SSE 轮询桥接用）。"""
    with _lock:
        return [e for e in _ring if e["seq"] > seq][-limit:]


def recent(limit: int = 100) -> list[dict]:
    """最近事件（状态快照端点用）。"""
    with _lock:
        return list(_ring[-limit:])


def last_seq() -> int:
    with _lock:
        return _seq
