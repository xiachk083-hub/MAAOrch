# -*- coding: utf-8 -*-
"""MAAOrch L3 服务层：产线状态的统一数据源（给工作台 /api/mas/* 用）。

三个**只读**数据源 + 两个显式动作：

1. **主机实时快照** —— 复用 `tools/mas_watch.py` 的 SSH 采集（在飞 MAA / 盘位 / 队列进度 / 失败 / 未跑），
   单一来源，避免 SSH 命令两处维护；带 45 秒进程内缓存（SSH 一次要几秒）。
2. **AUTO-MAS 历史** —— `POST /api/history/search`（DAILY）按天聚合。
3. **本地留痕** —— `runtime/reports/latest.md`、`runtime/mas_ledger.jsonl`。

动作（不重写发车逻辑，直接调已有 CLI 工具）：
- `rerun_async(accounts)` → `tools/auto_mas_rerun.py --apply`（后台线程，立刻返回，进度见 rerun_state()）
- `cleanup()` → `tools/auto_mas_rerun.py --cleanup`

**逐次尝试的语义**（AUTO-MAS history）：`data[日期][账号].index[]` 是逐次尝试，
`status ∈ DONE / ERROR`；`error_info` 是「时间 → 原因」。判定：
    一次过 = 全部 DONE ｜ 补跑成功 = 先 ERROR 后 DONE ｜ **全败 = 最后一次仍是 ERROR**
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME = os.path.join(ROOT, "runtime")
REPORTS = os.path.join(RUNTIME, "reports")
LEDGER = os.path.join(RUNTIME, "mas_ledger.jsonl")

API = "http://100.79.173.69:36163"
HOST_CACHE_TTL = 45.0          # 秒；主机快照缓存，refresh=True 绕过

_LOCK = threading.Lock()
_HOST_CACHE: dict = {"ts": 0.0, "data": None}
_WATCH = None

_RERUN = {"running": False, "accounts": [], "started_at": None,
          "ok": None, "detail": "", "done_at": None}


# ── 主机快照（复用 mas_watch 的 SSH 采集）─────────────────────────────

def _watch_mod():
    """加载 tools/mas_watch.py 复用其 SSH 命令 / fetch()（该模块只有 main() 有副作用）。"""
    global _WATCH
    if _WATCH is None:
        path = os.path.join(ROOT, "tools", "mas_watch.py")
        spec = importlib.util.spec_from_file_location("_mas_watch", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _WATCH = mod
    return _WATCH


def host_snapshot(refresh: bool = False) -> dict:
    """主机实时快照。返回 {ok, age_s, error?, ...mas_watch 的字段}。"""
    with _LOCK:
        age = time.time() - float(_HOST_CACHE.get("ts") or 0)
        if not refresh and _HOST_CACHE["data"] is not None and age < HOST_CACHE_TTL:
            d = dict(_HOST_CACHE["data"])
            d["age_s"] = round(age, 1)
            d["cached"] = True
            return d
    try:
        d = _watch_mod().fetch()
    except Exception as e:                                    # noqa: BLE001
        d = None
        err = str(e)[:200]
    else:
        err = None
    if not d:
        with _LOCK:
            old = _HOST_CACHE.get("data")
        if old:                                               # 采集失败时退回旧快照，页面不至于空
            o = dict(old)
            o.update(ok=False, stale=True, age_s=round(time.time() - float(_HOST_CACHE["ts"]), 1),
                     error=err or "主机采集失败")
            return o
        return {"ok": False, "stale": True, "error": err or "主机采集失败（SSH 不可达或脚本报错）"}
    d["ok"] = True
    d["cached"] = False
    d["age_s"] = 0.0
    with _LOCK:
        _HOST_CACHE["ts"] = time.time()
        _HOST_CACHE["data"] = d
    return d


# ── AUTO-MAS 历史 ──────────────────────────────────────────────────

def _api(path: str, body: dict | None = None, timeout: int = 60) -> dict:
    req = urllib.request.Request(
        API + path, data=json.dumps(body or {}).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def history(days: int = 7) -> dict:
    """近 N 天历史：{日期: {账号: {index, error_info, ...}}}"""
    end = dt.date.today()
    start = end - dt.timedelta(days=max(1, days) - 1)
    r = _api("/api/history/search", {"mode": "DAILY",
                                     "start_date": start.isoformat(),
                                     "end_date": end.isoformat()})
    return r.get("data") or {}


def _bucket(idx: list) -> str:
    """账号-天判定：ok=一次过 / retry=补跑成功 / fail=全败 / none=无记录"""
    sts = [str((e or {}).get("status") or "") for e in (idx or [])]
    if not sts:
        return "none"
    if all(s == "DONE" for s in sts):
        return "ok"
    if sts[-1] == "DONE":
        return "retry"
    return "fail"


def aggregate(hist: dict) -> dict:
    """把 history 揉成工作台要的：今日 / 逐日 / 失败原因 / 钉子户。"""
    daily: list[dict] = []
    causes: dict[str, dict] = {}
    off: dict[str, dict] = {}

    for day in sorted(hist or {}):
        accs = hist.get(day) or {}
        row = {"date": day, "accounts": 0, "ok": 0, "retry": 0, "fail": 0,
               "runs": 0, "errors": 0, "first": "", "last": "", "failed_names": []}
        for name, rec in accs.items():
            idx = (rec or {}).get("index") or []
            row["accounts"] += 1
            row["runs"] += len(idx)
            row["errors"] += sum(1 for e in idx if str((e or {}).get("status")) != "DONE")
            b = _bucket(idx)
            row[b] = row.get(b, 0) + 1
            ts = sorted(str(e.get("date") or "") for e in idx if e.get("date"))
            if ts:
                # 全天首末要取**全局**最早/最晚，不能只留"最后一个账号"的（曾因此显示 19:45，
                # 而当晚实际发车到 20:36）。HH:MM 字符串直接比大小即可（同一天）。
                if not row["first"] or ts[0][11:16] < row["first"]:
                    row["first"] = ts[0][11:16]
                if ts[-1][11:16] > row["last"]:
                    row["last"] = ts[-1][11:16]
            if b == "fail":
                row["failed_names"].append(name)

            ei = (rec or {}).get("error_info") or {}
            if isinstance(ei, dict) and ei:
                # 账号-天 级别的计数只算一次（别放进下面的逐条原因循环里）
                o = off.setdefault(name, {"name": name, "days": set(), "fails": 0,
                                          "last_reason": "", "last_ts": ""})
                o["days"].add(day)
                if b == "fail":
                    o["fails"] += 1
                for t, reason in ei.items():
                    reason = str(reason or "未分类")
                    c = causes.setdefault(reason, {"reason": reason, "count": 0, "accounts": set()})
                    c["count"] += 1
                    c["accounts"].add(name)
                    if str(t) >= str(o["last_ts"]):
                        o["last_ts"], o["last_reason"] = str(t), reason
        row["failed_names"] = row["failed_names"][:40]
        daily.append(row)

    top_causes = sorted(({"reason": c["reason"], "count": c["count"],
                          "accounts": len(c["accounts"])} for c in causes.values()),
                        key=lambda x: -x["count"])[:12]

    offenders = []
    for o in off.values():
        if o["fails"] >= 2 or len(o["days"]) >= 3:             # 钉子户：多次全败，或反复出问题
            offenders.append({"name": o["name"], "days": len(o["days"]), "fails": o["fails"],
                              "last_reason": o["last_reason"], "last_ts": o["last_ts"]})
    offenders.sort(key=lambda x: (-x["fails"], -x["days"], x["name"]))

    today = daily[-1] if daily else {"date": dt.date.today().isoformat(), "accounts": 0,
                                     "ok": 0, "retry": 0, "fail": 0, "runs": 0,
                                     "errors": 0, "failed_names": []}
    return {"today": today, "daily": daily, "causes": top_causes, "offenders": offenders[:20]}


# ── 本地留痕 ───────────────────────────────────────────────────────

def _latest_report(max_lines: int = 40) -> dict:
    p = os.path.join(REPORTS, "latest.md")
    try:
        txt = open(p, encoding="utf-8").read()
        return {"path": os.path.relpath(p, ROOT).replace("\\", "/"),
                "text": "\n".join(txt.splitlines()[:max_lines]),
                "mtime": dt.datetime.fromtimestamp(os.path.getmtime(p)).strftime("%m-%d %H:%M")}
    except Exception:                                          # noqa: BLE001
        return {"path": "", "text": "", "mtime": ""}


def _ledger_tail(n: int = 12) -> list[dict]:
    try:
        lines = open(LEDGER, encoding="utf-8").read().splitlines()[-n:]
        return [json.loads(x) for x in lines if x.strip()]
    except Exception:                                          # noqa: BLE001
        return []


# ── 汇总 ───────────────────────────────────────────────────────────

def collect_status(days: int = 7, refresh: bool = False) -> dict:
    """工作台一次拿全：主机实时 + 今日 + 近 N 天 + 原因/钉子户 + 留痕 + 补跑状态。"""
    out: dict = {"ok": True, "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                 "days": days}
    host = host_snapshot(refresh=refresh)
    out["host"] = host

    warn = getattr(_watch_mod(), "DISK_WARN_GB", {}) or {}
    disk = {}
    for k, v in (host.get("disk") or {}).items():
        try:
            disk[k] = {"free_gb": v.get("free_gb"), "total_gb": v.get("total_gb"),
                       "warn_gb": warn.get(k, 0),
                       "low": bool(v.get("free_gb") is not None
                                   and v["free_gb"] < warn.get(k, 0))}
        except Exception:                                      # noqa: BLE001
            continue
    out["disk"] = disk

    try:
        agg = aggregate(history(days))
        out.update(agg)
        out["history_ok"] = True
    except Exception as e:                                     # noqa: BLE001
        out.update({"history_ok": False, "history_error": str(e)[:200],
                    "today": {"date": dt.date.today().isoformat(), "accounts": 0, "ok": 0,
                              "retry": 0, "fail": 0, "runs": 0, "errors": 0, "failed_names": []},
                    "daily": [], "causes": [], "offenders": []})

    out["report"] = _latest_report()
    out["ledger"] = _ledger_tail()
    out["rerun"] = rerun_state()
    out["waiting"] = sum(len(q.get("pending") or []) for q in (host.get("queues") or {}).values())
    return out


# ── 动作：补跑 / 清理 ──────────────────────────────────────────────

def rerun_state() -> dict:
    with _LOCK:
        return dict(_RERUN)


def _rerun_worker(accounts: list[str]) -> None:
    cmd = [sys.executable, os.path.join(ROOT, "tools", "auto_mas_rerun.py"),
           "--accounts", ",".join(accounts), "--apply", "--force"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=1800)
        out = (r.stdout + r.stderr).decode("utf-8", "replace")
    except Exception as e:                                     # noqa: BLE001
        out = "补跑异常：%s" % str(e)[:300]
    with _LOCK:
        _RERUN.update(running=False, ok=("发车" in out), detail=out[-1200:],
                      done_at=dt.datetime.now().strftime("%H:%M:%S"))
    _log("rerun", accounts=accounts, ok=("发车" in out), detail=out[-400:])


def rerun_async(accounts: list[str]) -> dict:
    """后台补跑（立刻返回）。accounts 为空则取主机快照里的失败号。"""
    accounts = [a for a in (accounts or []) if a][:8]
    if not accounts:
        d = host_snapshot()
        accounts = [f.get("name") for f in (d.get("failures") or []) if f.get("name")][:8]
    if not accounts:
        return {"ok": False, "error": "没有需要补跑的号"}
    with _LOCK:
        if _RERUN["running"]:
            return {"ok": False, "error": "已有补跑在进行中", "state": dict(_RERUN)}
        _RERUN.update(running=True, accounts=accounts, ok=None, detail="发车中…",
                      started_at=dt.datetime.now().strftime("%H:%M:%S"), done_at=None)
    threading.Thread(target=_rerun_worker, args=(accounts,), daemon=True).start()
    return {"ok": True, "started": accounts, "state": rerun_state()}


def cleanup() -> dict:
    """删掉所有临时补跑队列（tools/auto_mas_rerun.py --cleanup）。"""
    try:
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "auto_mas_rerun.py"),
                            "--cleanup"], capture_output=True, timeout=300)
        out = (r.stdout + r.stderr).decode("utf-8", "replace")
    except Exception as e:                                     # noqa: BLE001
        return {"ok": False, "error": str(e)[:300]}
    _log("cleanup", detail=out[-400:])
    return {"ok": True, "detail": out[-2000:]}


def _log(kind: str, **kw) -> None:
    try:
        os.makedirs(RUNTIME, exist_ok=True)
        rec = {"ts": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "kind": kind}
        rec.update(kw)
        with open(LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:                                          # noqa: BLE001
        pass
