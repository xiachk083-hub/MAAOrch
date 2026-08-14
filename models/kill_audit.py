"""守护审计（Kill Audit）— 2026-08-14

杀/关/回收动作的确认钩子 + 全程留痕。

背景（用户 2026-08-14 明确）:
  一切判定数据以 MAA/模拟器/游戏的实际状态为准，MAAOrch 配合 MAA 而非
  反过来。误杀代价=重启风暴连锁，漏杀代价=多等几分钟，宁可多等不可错杀。
  因此所有破坏性动作（杀 MAA / 关模拟器 / 回收）在动手前先记录判据
  快照进待确认队列；观察窗口内由守护方（agent 监控循环/人工）交叉验证
  后放行或拦截；全程留痕供 Web「守护审计」页回看。

条目字段:
  id        唯一 id（ts + 序号）
  ts        申请时间
  aid       账号 id
  name      账号名
  action    动作类型: kill_maa / close_emu / reclaim_emu / restart_emu
  reason    申请原因（哪个判定触发的）
  evidence  判据快照（真实日志摘录/进程状态/心跳年龄 — 供确认方验证）
  status    pending(待确认) / approved(已放行) / rejected(已拦截) /
            expired(超时未确认-默认放行) / executed(已执行)
  decided_by 确认方（agent/manual/auto）
  decided_ts 决策时间
  note      决策备注（为什么放行/拦截）
  fix       解决方式（误杀复盘时填：根因+修复）
  timeout_s 观察窗口秒数（默认 300）
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

_PATH = Path(__file__).parent / "kill_audit.json"
_LOCK = threading.Lock()
_MAX_ENTRIES = 500  # 只留最近 500 条，防无限膨胀

# 动作 → 默认观察窗口（秒）。误杀风险越高的判定，窗口越长。
DEFAULT_TIMEOUT = {
    "kill_maa": 300,     # 杀 MAA：5 分钟观察
    "close_emu": 300,    # 关模拟器：5 分钟
    "reclaim_emu": 60,   # 回收模拟器：60s（2026-08-15 从 300 缩短 — 回收
                          # 已有 9 道保护（adb/活MAA/异常退出/排队保留），
                          # 5 分钟空窗会让空转模拟器堆积，用户画面观感差）
    "restart_emu": 180,  # 重启模拟器：3 分钟
}


def _load() -> list[dict]:
    if not _PATH.exists():
        return []
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(entries: list[dict]) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(_PATH)


def _next_id(entries: list[dict]) -> str:
    return f"{int(time.time())}-{len(entries) + 1}"


def request(aid: str, name: str, action: str, reason: str,
            evidence: str = "", timeout_s: int | None = None) -> dict:
    """申请一个破坏性动作（杀/关/回收）。返回条目（status=pending）。"""
    with _LOCK:
        entries = _load()
        timeout = timeout_s or DEFAULT_TIMEOUT.get(action, 300)
        entry = {
            "id": _next_id(entries),
            "ts": time.time(),
            "aid": aid,
            "name": name,
            "action": action,
            "reason": reason,
            "evidence": evidence[:2000],
            "status": "pending",
            "decided_by": "",
            "decided_ts": 0,
            "note": "",
            "fix": "",
            "timeout_s": timeout,
            "expire_ts": time.time() + timeout,
        }
        entries.append(entry)
        entries = entries[-_MAX_ENTRIES:]
        _save(entries)
        return dict(entry)


def pending() -> list[dict]:
    """待确认条目（按时间正序 — 先申请的先确认）。"""
    with _LOCK:
        return [dict(e) for e in _load() if e.get("status") == "pending"]


def _update(eid: str, **fields) -> dict | None:
    with _LOCK:
        entries = _load()
        for e in entries:
            if e.get("id") == eid:
                e.update(fields)
                _save(entries)
                return dict(e)
        return None


def decide(eid: str, approve: bool, by: str = "manual",
           note: str = "", fix: str = "") -> dict | None:
    """放行(approve=True)或拦截(approve=False)。返回更新后的条目。"""
    return _update(eid,
                   status="approved" if approve else "rejected",
                   decided_by=by,
                   decided_ts=time.time(),
                   note=note[:1000],
                   fix=fix[:1000])


def expire_all(now: float | None = None) -> list[dict]:
    """把超时未确认的 pending 置为 expired（默认放行语义，由调用方决定）。"""
    now = now or time.time()
    out = []
    with _LOCK:
        entries = _load()
        changed = False
        for e in entries:
            if e.get("status") == "pending" and now >= e.get("expire_ts", now + 1):
                e["status"] = "expired"
                e["decided_by"] = "auto"
                e["decided_ts"] = now
                e["note"] = e.get("note") or "观察窗口超时未确认，默认放行"
                changed = True
                out.append(dict(e))
        if changed:
            _save(entries)
    return out


def mark_executed(eid: str) -> dict | None:
    return _update(eid, status="executed")


def history(limit: int = 100) -> list[dict]:
    """历史记录（倒序）。"""
    with _LOCK:
        entries = _load()
    entries.sort(key=lambda e: e.get("ts", 0), reverse=True)
    return [dict(e) for e in entries[:limit]]


def summary() -> dict:
    """页头概览：各状态计数 + 最近未解决项。"""
    entries = _load()
    from collections import Counter
    cnt = Counter(e.get("status", "?") for e in entries)
    pending_list = [e for e in entries if e.get("status") == "pending"]
    # 未解决的误杀复盘（rejected 且无 fix，或 executed 且标记需复盘）
    needs_review = [e for e in entries
                    if e.get("status") in ("rejected", "expired") and not e.get("fix")]
    return {
        "total": len(entries),
        "by_status": dict(cnt),
        "pending": len(pending_list),
        "pending_oldest": min((e.get("ts", 0) for e in pending_list), default=0),
        "needs_review": len(needs_review),
    }
