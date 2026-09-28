#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""按需发车共享账本（`runtime/mas_ondemand_ledger.json`）—— CLI 与 MCP 两端唯一账本。

kanban t_4ddb3728 决定（此前 CLI/MCP 各一本账、互不可见）：

schema（写出统一口径；读入兼容旧字段名 `taskId`/`at`）：

```json
{
  "meta": {"v": 1, "updated": "...", "legacy_merged": {"<path>": {"size":.., "mtime_ns":.., "at":"..", "records":N}}},
  "temp_queues": [{"name","id","dir","accounts","scripts","created","deleted",...}],
  "2026-09-28": {
    "q:<queue uid>": {
      "kind": "queue|script|account|wake",
      "target": "队列名 / 脚本名 / 账号名（或逗号列表）",
      "uid": "<发给 /api/dispatch/start 的目标 uid：队列 uid / 脚本 sid / 账号→临时队列 uid>",
      "task_uid": "<start 返回的 taskId>",
      "ts": "2026-09-28 06:31:28",
      "src": "cli|mcp",
      "mode": "AutoProxy|ManualReview",
      "resources": {"dirs": [...], "scripts": [...], "emus": [...], "names": [...]},
      "busy_until": "2026-09-28 09:01:28",
      "...": "run_id/temp_queue/queue_name/queue_uid/item_uid/account/note/stopped_at/released_at/released_by ..."
    }
  }
}
```

key 口径（两端一致）：`q:<队列uid>` / `s:<脚本sid>` / `a:<账号名>` / `w:<脚本sid>`；
CLI 单号多组（同目录并一条临时队列）时，第 2..n 组用 `a:<token>#2`、`#3`… 避免互相覆盖。

其它约定：
  * 同 key 同一天再发（force 重发 / 迁移撞车）：**新记录为主，旧记录进 `prev`**（不丢记录）；
  * `load()` 会按 mtime/size 指纹把两个旧账本（`mas_ondemand_state.json` = CLI 旧账、
    `mas_ondemand_mcp_state.json` = MCP 旧账）**只读合并**进来：不重命名、不删除
    —— 网关里可能还活着旧版 MCP 进程，它仍会写旧文件；重连后旧文件不再增长，可人工归档。
    合并按 (day,key) 去重、按 task_uid 幂等：同一任务重复读到只更新字段，不会产生 `prev` 杂物。
  * `save()` 只保留最近 7 天的日期段与最近 200 条临时队列登记（busy 窗口默认 150 分钟，够用）。
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME = os.environ.get("MAAORCH_ONDEMAND_RUNTIME") or os.path.join(ROOT, "runtime")
LEDGER_NAME = "mas_ondemand_ledger.json"
LEGACY_NAMES = ("mas_ondemand_state.json", "mas_ondemand_mcp_state.json")
TS_FMT = "%Y-%m-%d %H:%M:%S"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
KEEP_DAYS = 7
TEMP_KEEP = 200
PREV_KEEP = 5
DEFAULT_HOLD_MIN = 150


def ledger_path() -> str:
    return os.path.join(RUNTIME, LEDGER_NAME)


def legacy_paths() -> list:
    return [os.path.join(RUNTIME, n) for n in LEGACY_NAMES]


def now_str(now: dt.datetime | None = None) -> str:
    return (now or dt.datetime.now()).strftime(TS_FMT)


def parse_ts(s) -> dt.datetime | None:
    try:
        return dt.datetime.strptime(str(s), TS_FMT)
    except (TypeError, ValueError):
        return None


def key_of(kind: str, uid: str = "", target: str = "") -> str:
    """两端统一的 key 口径（见模块 docstring）。"""
    k = str(kind or "").strip()
    if k in ("queue", "q"):
        return "q:" + str(uid or target or "")
    if k in ("script", "s"):
        return "s:" + str(uid or target or "")
    if k in ("wake", "w"):
        return "w:" + str(uid or target or "")
    return "a:" + str(target or uid or "")


def _norm_entry(rec: dict, src_default: str = "") -> dict:
    """旧字段名 -> 新口径（taskId->task_uid、at->ts）；其余键原样保留。"""
    e = dict(rec or {})
    if "task_uid" not in e and "taskId" in e:
        e["task_uid"] = e.get("taskId")
    if "ts" not in e and "at" in e:
        e["ts"] = e.get("at")
    e.setdefault("src", src_default)
    return e


def _read(on_error=None) -> dict:
    path = ledger_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:  # noqa: BLE001  损坏 => 隔离 + 空账自愈（不阻塞发车）
        detail = "%s: %s" % (type(e).__name__, e)
        with contextlib.suppress(OSError):
            os.replace(path, "%s.corrupt-%s" % (path, dt.datetime.now().strftime("%Y%m%d-%H%M%S")))
        if on_error:
            with contextlib.suppress(Exception):
                on_error(detail)
        return {}
    if not isinstance(data, dict):
        return {}
    out = {"meta": data.get("meta") if isinstance(data.get("meta"), dict) else {}}
    tq = data.get("temp_queues")
    out["temp_queues"] = [x for x in tq if isinstance(x, dict)] if isinstance(tq, list) else []
    for k, v in data.items():
        if DATE_RE.match(str(k)) and isinstance(v, dict):
            out[str(k)] = {kk: _norm_entry(vv) for kk, vv in v.items() if isinstance(vv, dict)}
        elif str(k) not in ("meta", "temp_queues"):
            out[str(k)] = v  # 未知顶层键原样保留
    return out


def _write(st: dict) -> None:
    os.makedirs(RUNTIME, exist_ok=True)
    cutoff = (dt.date.today() - dt.timedelta(days=KEEP_DAYS)).isoformat()
    for k in [k for k in list(st) if DATE_RE.match(str(k)) and str(k) < cutoff]:
        st.pop(k, None)
    st["temp_queues"] = (st.get("temp_queues") or [])[-TEMP_KEEP:]
    st.setdefault("meta", {})["v"] = 1
    st["meta"]["updated"] = now_str()
    tmp = ledger_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, ledger_path())


def _upsert(st: dict, day: str, key: str, entry: dict) -> dict:
    """(day,key) 落账；同 task_uid 幂等更新，不同任务新旧并存（旧进 prev）。"""
    entry = dict(entry)
    sec = st.setdefault(day, {})
    cur = sec.get(key)
    if cur is None:
        sec[key] = entry
        return sec[key]
    if entry.get("task_uid") and entry.get("task_uid") == cur.get("task_uid"):
        carry = cur.get("prev")
        cur.update(entry)
        if carry and not cur.get("prev"):
            cur["prev"] = carry
        return cur
    a, b = parse_ts(cur.get("ts")), parse_ts(entry.get("ts"))
    newer, older = (entry, cur) if ((b or dt.datetime.min) >= (a or dt.datetime.min)) else (cur, entry)
    older = dict(older)
    older_prev = older.pop("prev", None)
    newer = dict(newer)
    prev = list(newer.get("prev") or [])
    prev.append(older)
    if older_prev:
        prev.extend(older_prev)
    newer["prev"] = prev[-PREV_KEEP:]
    sec[key] = newer
    return sec[key]


def iter_entries(st: dict):
    """yield (day, key, entry_in_place)——entry 是账本里的真实对象（可改后 save）。"""
    for d in sorted(st or {}):
        if not DATE_RE.match(str(d)) or not isinstance(st.get(d), dict):
            continue
        for key in sorted(st[d]):
            e = st[d][key]
            if isinstance(e, dict):
                yield str(d), str(key), e


def _until_of(rec: dict, hold_min: int) -> dt.datetime | None:
    until = parse_ts(rec.get("busy_until"))
    if until:
        return until
    ts = parse_ts(rec.get("ts"))
    if ts:
        return ts + dt.timedelta(minutes=max(1, hold_min))
    return None


def is_open(rec: dict, now: dt.datetime | None = None, hold_min: int = DEFAULT_HOLD_MIN) -> bool:
    """该记录是否仍在 busy 窗口内（busy_until 优先，缺失时退 ts+hold_min）。"""
    now = now or dt.datetime.now()
    rec = rec or {}
    if not (rec.get("task_uid") or rec.get("taskId")):
        return False
    until = _until_of(rec, hold_min)
    return bool(until and until > now)


def open_entries(st: dict, now: dt.datetime | None = None, hold_min: int = DEFAULT_HOLD_MIN) -> list:
    """仍在 busy 窗口内的记录（含被顶替但未出窗的 prev 记录）——展示用副本。"""
    now = now or dt.datetime.now()
    out = []
    for day, key, e in iter_entries(st):
        cand = [("", e)] + [("", p) for p in (e.get("prev") or []) if isinstance(p, dict)]
        for _, rec in cand:
            if not (rec.get("task_uid") or rec.get("taskId")):
                continue
            until = _until_of(rec, hold_min)
            if not until or until <= now:
                continue
            row = dict(rec)
            row["day"], row["key"] = day, key
            row["task_uid"] = rec.get("task_uid") or rec.get("taskId")
            row["ts"] = rec.get("ts") or rec.get("at") or ""
            row["busy_until"] = until.strftime(TS_FMT)
            if rec is not e:
                row["superseded_by"] = e.get("task_uid") or e.get("taskId") or ""
            out.append(row)
    out.sort(key=lambda r: (r.get("ts") or "", r.get("key") or ""))
    return out


def record(st: dict, kind: str, target: str, uid: str, task_uid: str, src: str,
           ts: str | None = None, key: str | None = None, day: str | None = None,
           resources: dict | None = None, busy_until: str | None = None,
           hold_min: int = DEFAULT_HOLD_MIN, mode: str | None = None, note: str | None = None,
           **extra) -> dict:
    """落一条在场记录（同 key 幂等/顶替见 _upsert）。返回账本里的主记录（可继续改后 save）。"""
    ts = ts or now_str()
    day = day or (ts[:10] if DATE_RE.match(ts[:10]) else dt.date.today().isoformat())
    key = key or key_of(kind, uid, target)
    entry: dict = {"kind": str(kind), "target": str(target), "uid": str(uid or ""),
                   "task_uid": str(task_uid or ""), "ts": ts, "src": str(src or "")}
    if mode:
        entry["mode"] = mode
    if note:
        entry["note"] = str(note)[:300]
    if resources is not None:
        entry["resources"] = resources
    base = parse_ts(ts) or dt.datetime.now()
    entry["busy_until"] = busy_until or now_str(base + dt.timedelta(minutes=max(1, hold_min)))
    entry.update(extra)
    return _upsert(st, day, key, entry)


def _merge_legacy(st: dict) -> bool:
    """旧账本（CLI 的 runs/temp_queues 形态 与 MCP 的 day->key 形态）只读合并；返回是否有变更。"""
    changed = False
    merged = st.setdefault("meta", {}).setdefault("legacy_merged", {})
    for path in legacy_paths():
        try:
            stat = os.stat(path)
            fp = {"size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns)}
        except OSError:
            continue
        old = merged.get(path) or {}
        if old.get("size") == fp["size"] and old.get("mtime_ns") == fp["mtime_ns"]:
            continue  # 指纹没变 => 已经并过
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:  # noqa: BLE001 坏文件不阻塞
            data = None
        n = 0
        if isinstance(data, dict):
            n = _absorb_legacy(st, data, path)
        merged[path] = dict(fp, at=now_str(), records=n)
        changed = True
    return changed


def _absorb_legacy(st: dict, data: dict, path: str) -> int:
    n = 0
    if "runs" in data or "temp_queues" in data:      # CLI 旧账
        for run in data.get("runs") or []:
            if not isinstance(run, dict):
                continue
            kind = str(run.get("target_kind") or "queue")
            res = run.get("resources") or {}
            uid = run.get("queue_id") or ((res.get("scripts") or [""])[0]) or ""
            entry = _norm_entry(run, src_default="cli")
            entry["kind"] = kind
            entry["uid"] = str(uid or entry.get("uid") or "")
            entry["task_uid"] = str(run.get("taskId") or entry.get("task_uid") or "")
            day = str(entry.get("ts") or "")[:10]
            if not DATE_RE.match(day):
                day = dt.date.today().isoformat()
            _upsert(st, day, key_of(kind, entry["uid"], str(run.get("target") or "")), entry)
            n += 1
        for tq in data.get("temp_queues") or []:
            if isinstance(tq, dict) and tq.get("id") and not any(
                    (x or {}).get("id") == tq.get("id") for x in st.get("temp_queues") or []):
                st.setdefault("temp_queues", []).append(tq)
    else:                                            # MCP 旧账（day -> key -> rec）
        for day, recs in data.items():
            if not DATE_RE.match(str(day)) or not isinstance(recs, dict):
                continue
            for key, rec in recs.items():
                if not isinstance(rec, dict):
                    continue
                _upsert(st, str(day), str(key), _norm_entry(rec, src_default="mcp"))
                n += 1
    return n


def load(on_error=None) -> dict:
    """读共享账本（含旧账本合并迁移；损坏 => 隔离 + 空账自愈）。"""
    st = _read(on_error=on_error)
    if _merge_legacy(st):
        _write(st)
    return st


def save(st: dict) -> None:
    _write(st)
