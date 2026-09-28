# -*- coding: utf-8 -*-
r"""mas-ondemand MCP —— 按需发车 / 查在跑 / 停任务（6 个工具；**写动作默认 dry-run**）。

背景（2026-09-28，kanban t_1aa55dac）：
现有的窄面 MCP（mas-dispatch / mas-triage / mas-observe）只覆盖「班次对账补位 + 只读观察」；
缺「脚本级 / 单号级发车、停任务、在跑清单」。任何 Hermes 会话（巡检、看板 worker、临时对话）
想按需发一车，此前只能手写 HTTP —— 本 server 补上这块，且全部复用既有闸门库。

工具（白名单 6 个）：
  list_running(timeout, with_users, mask_accounts)          只读：在跑清单（三源合并，见下）
  dispatch_queue(name, apply=False, force=False, force_conflict=False, note="", lease_owner="")
                                                            按队列发（AutoProxy）
  dispatch_script(name, apply=False, force=False, force_conflict=False, note="", lease_owner="")
                                                            按脚本发（AutoProxy；该脚本**全部启用用户**）
  dispatch_account(name, apply=False, force=False, ignore_window=False, note="", lease_owner="")
                                                            按单号发（内部建「临时-按需-*」队列后 AutoProxy）
  stop_task(task_id, apply=False, force=False, reason="")   停任务（只认 start 返回的 task_uid）
  ondemand_preflight(target, kind="auto", skip_screen=True, timeout=240,
                     min_free_gb=10, warn_free_gb=30)       发车前 GO/NO-GO（复用 tools/mas_preflight.py）

## 写护栏（三条全给，缺一不发）
1. **默认 dry-run**：`apply=false`（缺省）只做只读预检并回计划，绝不触达任何写请求；
2. **单飞 + 拒绝重复**：同一目标同一天发过一次 ⇒ `refused=duplicate_today`（显式 `force=true` 才能再发）；
   并发上与 cron 调度器共锁（复用 `tools/mas_dispatch.py` 的 `runtime/mas_dispatch.lock`）⇒ 不会双发；
   **单飞账同理与 CLI 版共用一份**（`runtime/mas_ondemand_ledger.json`）⇒ CLI/MCP 互见对方的车，也不会一头一尾双发；
3. **每次调用写审计**：dry-run 与真发车都逐条落 `runtime/mas_ondemand_log.jsonl`（ts/tool/target/
   apply/verdict/taskId/detail）；真动作事件（dispatched/failed/stopped/stop_failed/temp_queue_failed）
   另写 `runtime/mas_dispatch_log.jsonl`（`source:"ondemand"`, `src:"mcp"`，与 cron 班次事件同一条流），
   并回读校验共享账本。

其它闸门（与 mas_dispatch.py 同源）：
- 队列名/脚本名/账号名必须在 AUTO-MAS 侧真实存在（解析不出来 = refused，零写请求）；
- AUTO-MAS 可达 fail-closed（`POST /api/scripts/get` 探活失败即拒）；
- `MAAORCH_DISPATCH_NO_GO=1`（或 `MAAORCH_ONDEMAND_NO_GO=1`）⇒ 拒绝一切真发车；
- 资源冲突闸门：复用 `mas_dispatch.find_conflicts`（同 MAA 目录/脚本/实例在 busy 窗口内重叠 ⇒ 拒发，
  `force_conflict=true` 显式放行并记账）；
- **G3L 实例租约（P1-2 跨族单飞）**：目标实例若已被别的任务/工具租下（`runtime/instance_leases.json`，
  机制 = `tools/mas_instance_lease.py`，消费者 = `tools/mas_lease_glue.py`）⇒ `refused=lease_busy`，
  **零上游写**（不建临时队列、不发车）；dry-run 也会判（只读 check，计划不成立就拒）。持有者口径：
  工具参数 `lease_owner` > `MAAORCH_LEASE_OWNER` > `HERMES_KANBAN_TASK` > `ondemand:mcp`；同一个持有者
  自己的租约不算被占（= 续租）。发车成功保留租约（与 busy 窗口同寿，TTL 见 `MAAORCH_ONDEMAND_LEASE_TTL_S`），
  发车失败/`stop_task` 成功即释放；`force`/`force_conflict` **放行不了租约**（要等对方放或人工 release）。
- 24 条班次队列（日常定时N / 晚班定时N）**委托给 mas_dispatch.dispatch**（保留其告警台账 / late 标记 /
  冲突判定 / 并发锁全链路），本 server 只补位、不重造；
- 真发车返回体必须 `code==200` 且带 taskId，否则按 mas_dispatch 的分类记失败（rest_unreachable /
  http_error / bad_response / no_taskid / target_running / slot_occupied / target_not_found / queue_missing）。

## list_running 的三源合并（只读）
1. `runtime/mas_dispatch_state.json`（Hermes cron 班次发车，busy 窗口内的）；
2. `runtime/mas_ondemand_ledger.json`（**共享账本**：CLI 版 `tools/mas_ondemand.py` 与本 server 的
   按需发车同写同读，busy 窗口内的；旧账 `mas_ondemand_mcp_state.json` / `mas_ondemand_state.json`
   首次读入自动合并 —— t_4ddb3728 统一，两工具各记一本、互不可见的问题已消除）；
3. 远端现场（ssh 只读巡检，复用 `mas_preflight.remote_survey`）：MAA.exe 进程 ⇒ 在跑的 MAA 槽位（目录）；
   MuMuVMMHeadless ⇒ 在跑的模拟器实例；再把在跑目录映射回 AUTO-MAS 脚本（+启用用户，默认掩码）。
另外附 `guard_window`（05:00–05:40 / 19:20–20:20，MAS_RERUN_POLICY §P0）提示：窗内 `dispatch_account`
（补跑语义）默认拒绝，`ignore_window=true` 才放行。

## 口径（引用，别重推）
- taskId 语义、三种模式、stop 只认 task_uid：`docs/audits/2026-09-27_mas_task_modes.md`；
- 单号没有原生入口 ⇒ 临时队列：`tools/auto_mas_rerun.py`；「同目录必须同队列 / 目录忙则停」三条硬约束同源；
- 班次表 / busy 窗口 / 冲突判定：`tools/mas_dispatch.py`（同一批常量：HOLD_MIN / GRACE_MIN / QUEUE_RE）。

用法：
  python tools/mas_ondemand_mcp.py            # stdio MCP server（Hermes 用 command+args 拉起）
  python tools/mas_ondemand_mcp.py --check    # 自检：依赖 + 只读探测 + 白名单，退出码 0/2
  python tools/mas_ondemand_mcp.py --selftest # 离线自测（假传输；不碰网络/runtime），退出码 0/2
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import uuid as uuidlib

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS_DIR)
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import mas_dispatch as md  # noqa: E402
import mas_ondemand_ledger as ledger  # noqa: E402  共享账本（CLI 版同读同写）
import mas_preflight as pf  # noqa: E402

SERVER_NAME = "mas-ondemand"
RUNTIME = os.environ.get("MAAORCH_ONDEMAND_RUNTIME") or os.path.join(ROOT, "runtime")
LEDGER = ledger.ledger_path()                                 # 共享账本（t_4ddb3728：CLI/MCP 唯一账本）
STATE = LEDGER  # 兼容旧引用（旧 mas_ondemand_mcp_state.json 由 ledger 首次读入时自动合并进来）
LOG = os.path.join(RUNTIME, "mas_ondemand_log.jsonl")         # 全量调用流（含 dry-run/被拒；两端统一）
LOG_EVENTS = os.path.join(RUNTIME, "mas_dispatch_log.jsonl")  # 真动作事件流（班次同源，source=ondemand）
EVENT_VERDICTS = {"dispatched": "dispatch_ok", "failed": "dispatch_failed",
                  "temp_queue_failed": "temp_queue_failed",
                  "stopped": "stop_ok", "stop_failed": "stop_failed"}
PREFLIGHT_CLI = os.path.join(TOOLS_DIR, "mas_preflight.py")
PY = sys.executable  # MCP server 由 Python312 拉起（config: command=Python312\python.exe）

TMPQ_TAG = "临时-按需"                       # 临时队列命名（同 auto_mas_rerun 的「临时-补跑」语义）
GUARD_WINDOWS = (("05:00", "05:40"), ("19:20", "20:20"))   # MAS_RERUN_POLICY §P0：发车/补跑一律回避
ONDEMAND_NO_GO = str(os.environ.get("MAAORCH_ONDEMAND_NO_GO", "")).strip().lower() in {
    "1", "true", "yes", "on", "enable", "enabled"}

# P1-2 G3L：实例租约（跨族单飞）。账本/机制与 CLI 版同源：tools/mas_instance_lease.py
# （消费者侧胶水 tools/mas_lease_glue.py）——只租不改，不新造第二本账。
DEFAULT_LEASE_OWNER = "ondemand:mcp"
try:
    LEASE_TTL_S = max(600, int(os.environ.get("MAAORCH_ONDEMAND_LEASE_TTL_S") or (md.HOLD_MIN * 60)))
except (TypeError, ValueError):
    LEASE_TTL_S = max(600, int(md.HOLD_MIN) * 60)

# 输出账号掩码口径与 mas_observe_mcp 一致：前 2 + *** + 后 2（短名只留首字符）。
try:
    import mas_observe_mcp as _obs  # noqa: E402
except Exception:  # noqa: BLE001  掩码是锦上添花，导入失败不拖垮 server
    _obs = None


def _mask(name: str) -> str:
    if _obs is not None and hasattr(_obs, "_mask"):
        return _obs._mask(name)
    s = str(name or "")
    if len(s) <= 4:
        return (s[0] + "***") if s else s
    return s[:2] + "***" + s[-2:]


def _child_env() -> dict:
    """子进程环境（ssh/adb 必需）。复用 mas_observe_mcp._env() 的实测口径。

    MCP stdio server 被 SDK 用极简 env 拉起时裸名 `ssh` 会静默失败（t_6cfe7865 实测：
    PATH/Path 双键 ⇒ WinError 2），所以启动时把修好的环境回灌 os.environ（本进程内所有
    subprocess 都继承它，含 mas_preflight 里的 ssh）。
    """
    if _obs is not None and hasattr(_obs, "_env"):
        try:
            return _obs._env()
        except Exception:  # noqa: BLE001
            pass
    return dict(os.environ)


def _fix_process_env() -> None:
    with contextlib.suppress(Exception):
        os.environ.update({k: v for k, v in _child_env().items() if isinstance(v, str)})


# --------------------------------------------------------------------------- #
# 状态 / 审计（本 server 自己的两个文件，不混 mas_dispatch 的台账）             #
# --------------------------------------------------------------------------- #

def _now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _today() -> str:
    return dt.date.today().isoformat()


def _parse_ts(s):
    try:
        return dt.datetime.strptime(str(s), "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def _state_load() -> dict:
    """读共享账本（runtime/mas_ondemand_ledger.json；旧账首次读到自动合并，损坏隔离自愈）。"""
    return ledger.load(on_error=lambda d: _audit({"kind": "state_quarantine", "detail": d}))


def _state_save(st: dict) -> None:
    ledger.save(st)


def _audit(rec: dict) -> None:
    """每次调用（含 dry-run）一行写调用流；真动作事件另写班次同源事件流（source=ondemand）。"""
    rec = dict(rec)
    rec.setdefault("ts", _now())
    rec.setdefault("pid", os.getpid())
    _append_jsonl(LOG, rec)
    ekind = EVENT_VERDICTS.get(str(rec.get("verdict") or ""))
    if ekind:
        ev = {"ts": rec.get("ts"), "kind": ekind, "source": "ondemand", "src": "mcp",
              "target_kind": rec.get("kind") or "", "target": rec.get("target") or "",
              "taskId": rec.get("taskId") or "", "apply": rec.get("apply"),
              "verdict": rec.get("verdict"), "tool": rec.get("tool") or ""}
        for k in ("queue", "queue_uid", "item_uid", "dir", "script", "reason", "detail", "note"):
            if rec.get(k) is not None:
                ev[k] = rec[k]
        _append_jsonl(LOG_EVENTS, ev)


def _append_jsonl(path: str, rec: dict) -> None:
    try:
        os.makedirs(RUNTIME, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        print("WARN mas_ondemand: 审计写入失败 %s" % e, file=sys.stderr)


def _state_entry(st: dict, day: str, key: str):
    return ((st.get(day) or {}).get(key)) or None


def _active_busy(st: dict, now: dt.datetime | None = None, hold_min: int | None = None) -> list:
    """共享账本里仍在 busy 窗口内的记录（跨日；含 CLI 端发的车与被顶替未出窗的 prev）。"""
    return ledger.open_entries(st, now=now, hold_min=hold_min or md.HOLD_MIN)


# --------------------------------------------------------------------------- #
# AUTO-MAS 只读面：脚本表 / 用户表 / 现场巡检                                   #
# --------------------------------------------------------------------------- #

def _script_map() -> dict:
    """{sid: {sid, name, dir, emu}}（只收路径合格的脚本；与 mas_preflight 同口径）。"""
    data = (md.post("/api/scripts/get", {}).get("data") or {})
    out = {}
    for sid, s in data.items():
        info = (s or {}).get("Info") or {}
        p = md.norm_dir(info.get("Path"))
        if not pf.MAA_DIR_RE.match(p):
            continue
        emu = ((s or {}).get("Emulator") or {}).get("Index")
        out[str(sid)] = {"sid": str(sid), "name": str(info.get("Name") or ""), "dir": p,
                         "emu": str(emu) if emu is not None else ""}
    return out


def _script_users(sid: str) -> list:
    """[{'uid','name','enabled'}]（AUTO-MAS 用户表；Status=启用）"""
    data = (md.post("/api/scripts/user/get", {"scriptId": sid, "userId": None}).get("data") or {})
    out = []
    for uid, u in data.items():
        if uid == "instances" or not isinstance(u, dict):
            continue
        info = u.get("Info") or {}
        # 「会跑」的口径与 AUTO-MAS 一致（manager.py:151-159）：Status=启用 且 RemainedDay≠0
        out.append({"uid": str(uid), "name": str(info.get("Name") or ""),
                    "enabled": bool(info.get("Status")) and info.get("RemainedDay") != 0,
                    "remained_day": info.get("RemainedDay")})
    return out


def _remote_busy(timeout: int = 180) -> dict:
    """只读远端巡检：{'dirs': {dir_lower: [pid]}, 'vmm': {idx: pid}, 'error': ''}"""
    try:
        sv = pf.remote_survey(timeout=timeout)
    except Exception as e:  # noqa: BLE001
        return {"dirs": {}, "vmm": {}, "error": "%s: %s" % (type(e).__name__, e)}
    dirs: dict = {}
    for m in sv.get("maa") or []:
        exe = str(m.get("exe") or "")
        d = os.path.dirname(exe).rstrip("\\") if exe else ""
        if d:
            dirs.setdefault(d.lower(), []).append(str(m.get("pid") or ""))
    return {"dirs": dirs, "vmm": dict(sv.get("vmm") or {}), "error": ""}


# --------------------------------------------------------------------------- #
# 闸门共用件                                                                   #
# --------------------------------------------------------------------------- #

def _in_guard_window(now: dt.datetime | None = None):
    now = now or dt.datetime.now()
    hm = now.strftime("%H:%M")
    for a, b in GUARD_WINDOWS:
        if a <= hm <= b:
            return "%s–%s" % (a, b)
    return ""


def _no_go() -> bool:
    return bool(md.NO_GO or ONDEMAND_NO_GO)


def _conflict_days() -> list:
    """近两日「已发车」记录（mas_dispatch + 本 server 两个来源），供 find_conflicts 用。"""
    days = []
    try:
        mst = md.load_state()
    except Exception:  # noqa: BLE001
        mst = {}
    ost = _state_load()
    for k in (0, 1):
        d = (dt.date.today() - dt.timedelta(days=k)).isoformat()
        days.append(mst.get(d) or {})
        days.append({key: rec for key, rec in (ost.get(d) or {}).items()})
    return days


def _conflicts(label: str, res: dict | None, now: dt.datetime | None = None) -> list:
    """资源冲突（同目录/脚本/实例且 busy 窗口内），复用 mas_dispatch.find_conflicts。"""
    try:
        return md.find_conflicts(label, res, _conflict_days(), now or dt.datetime.now())
    except Exception:  # noqa: BLE001
        return []


def _refused(**kw) -> dict:
    out = {"ok": False}
    out.update(kw)
    return out


def _attempt_start(uid: str, timeout: int = 60) -> dict:
    """一次真发车尝试：探活 fail-closed -> POST /api/dispatch/start {taskId, mode:AutoProxy}。"""
    ok, why = md.reachable()
    if not ok:
        return {"ok": False, "reason": "rest_unreachable", "detail": why}
    try:
        r = md.post("/api/dispatch/start", {"taskId": uid, "mode": "AutoProxy"}, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        reason, detail, _retryable = md._classify_exc(e)
        return {"ok": False, "reason": reason, "detail": detail}
    return md._classify_resp(r)


def _record_dispatch(st: dict, key: str, kind: str, target: str, uid: str, task_uid: str,
                     res: dict | None, note: str = "", lease: dict | None = None) -> dict:
    """发车成功 → 写共享账本（CLI 端同读同写；schema 由 ledger 统一）。

    `lease`（P1-2）：把 G3L 租约明细一并写进在场记录 —— 停机/收尾按记录里的 owner+idx 精确释放。
    """
    entry = ledger.record(st, kind, target, uid, task_uid, "mcp", key=key, resources=res,
                          hold_min=md.HOLD_MIN, note=note or None)
    if lease and lease.get("ok") and lease.get("idxs"):
        entry["lease_owner"] = lease.get("owner") or _lease_owner()
        entry["lease_idxs"] = [str(i) for i in lease.get("idxs") or []]
        entry["lease_expires_at"] = lease.get("expires_at") or ""
        entry["lease_status"] = lease.get("status") or "held"
    _state_save(st)
    return entry


# --------------------------------------------------------------------------- #
# P1-2 G3L 实例租约：跨族单飞（闸门顺序 单飞 → 资源冲突 → **租约** → 发车）      #
# --------------------------------------------------------------------------- #
_LEASE = None


def _lease():
    """懒加载消费者侧胶水 tools/mas_lease_glue.py；不在位 ⇒ None（G3L 整体跳过，不挡发车）。"""
    global _LEASE
    if _LEASE is None:
        try:
            import mas_lease_glue as g  # noqa: PLC0415
        except Exception:  # noqa: BLE001
            return None
        _LEASE = g
    return _LEASE


def _lease_owner(a: dict | None = None) -> str:
    """持有者：工具参数 lease_owner > MAAORCH_LEASE_OWNER > HERMES_KANBAN_TASK > ondemand:mcp。"""
    explicit = str((a or {}).get("lease_owner") or "").strip()
    g = _lease()
    if g is not None:
        with contextlib.suppress(Exception):
            return g.owner(explicit or DEFAULT_LEASE_OWNER)
    return (explicit or os.environ.get("MAAORCH_LEASE_OWNER") or os.environ.get("HERMES_KANBAN_TASK")
            or DEFAULT_LEASE_OWNER).strip()


def _lease_idxs(res: dict | None) -> list:
    g = _lease()
    if g is None:
        return []
    with contextlib.suppress(Exception):
        return g.idxs_of(res or {})
    return []


def _lease_check(res: dict | None, a: dict | None = None) -> dict:
    """只读判定：目标实例是否被**别的**任务租下（返回 {busy,owner,idxs,rows,detail,holder}）。"""
    idxs = _lease_idxs(res)
    out = {"busy": False, "owner": _lease_owner(a), "idxs": idxs, "rows": {}, "detail": "", "holder": {}}
    if not idxs:
        out["detail"] = "无目标实例（脚本没写 Emulator.Index）——跳过 G3L"
        return out
    g = _lease()
    if g is None:
        out["detail"] = "mas_lease_glue.py 不在位——跳过 G3L"
        return out
    try:
        chk = g.check_all(idxs, owner_id=out["owner"])
    except Exception as e:  # noqa: BLE001
        out.update({"busy": True, "detail": "租约检查异常（fail-closed）：%s: %s" % (type(e).__name__, e)})
        return out
    out["rows"] = chk.get("rows") or {}
    out["detail"] = str(chk.get("detail") or "")
    out["busy"] = not chk.get("ok")
    if out["busy"]:
        out["holder"] = g.holder_of(out["rows"])
    return out


def _lease_acquire(res: dict | None, a: dict | None, purpose: str) -> dict:
    """取租（全有或全无；同主=续租）。ok=False ⇒ 调用方**零上游写**拒发（不建队列/不发车）。"""
    idxs = _lease_idxs(res)
    if not idxs:
        return {"ok": True, "status": "no_target", "idxs": [], "detail": "无目标实例——跳过 G3L"}
    g = _lease()
    if g is None:
        return {"ok": True, "status": "unavailable", "idxs": idxs, "detail": "mas_lease_glue.py 不在位——跳过 G3L"}
    owner = _lease_owner(a)
    r = g.acquire_all(idxs, owner, purpose, ttl_s=LEASE_TTL_S, src="ondemand_mcp", note="ondemand:mcp")
    r = dict(r or {})
    r["owner"] = owner
    return r


def _lease_release(res: dict | None, owner: str, note: str = "") -> dict:
    idxs = _lease_idxs(res)
    g = _lease()
    if not idxs:
        return {"ok": True, "status": "no_target", "detail": "无目标实例——跳过"}
    if g is None:
        return {"ok": True, "status": "unavailable", "detail": "mas_lease_glue.py 不在位——跳过"}
    r = g.release_all(idxs, owner_id=owner or None, note=note)
    r = dict(r or {})
    r["owner"] = owner
    return r


def _lease_brief(lease: dict | None) -> dict:
    l = lease or {}
    return {k: l.get(k) for k in ("ok", "status", "idxs", "detail", "expires_at", "owner")
            if l.get(k) not in (None, "", [], {})}


def _lease_release_records(st: dict, task_id: str, note: str = "") -> dict:
    """停成功后：把共享账本里该 task_uid 名下记的租约放掉（按记录里的 owner+idx 精确放）。"""
    g = _lease()
    out = {"released": [], "detail": ""}
    if g is None:
        return out
    for _d, _k, rec in ledger.iter_entries(st or {}):
        if rec.get("task_uid") != task_id and rec.get("taskId") != task_id:
            continue
        idxs = [str(i) for i in (rec.get("lease_idxs") or [])]
        if not idxs or rec.get("lease_released_at"):
            continue
        r = g.release_all(idxs, owner_id=rec.get("lease_owner") or None,
                          note=note or ("ondemand:mcp stop %s" % task_id))
        rec["lease_released_at"] = _now()
        rec["lease_release_detail"] = str(r.get("detail") or r.get("status") or "")[:200]
        out["released"].extend(r.get("released") or [])
        out["detail"] = r.get("detail") or ""
    return out


# --------------------------------------------------------------------------- #
# 工具 1：list_running（只读）                                                  #
# --------------------------------------------------------------------------- #

def tool_list_running(a: dict) -> dict:
    try:
        timeout = max(30, min(600, int(a.get("timeout") or 180)))
    except (TypeError, ValueError):
        timeout = 180
    with_users = bool(a.get("with_users", True))
    mask = bool(a.get("mask_accounts", True))

    reach, why = md.reachable()
    out = {"ok": True, "now": _now(), "reachable": reach, "rest_detail": why,
           "hold_min": md.HOLD_MIN, "guard_window": _in_guard_window(),
           "no_go": _no_go(), "audit_log": LOG}

    # ① Hermes cron 班次发车（busy 窗口内）
    try:
        out["shift_busy"] = md.busy_entries(md.load_state())
    except Exception as e:  # noqa: BLE001
        out["shift_busy"], out["shift_busy_error"] = [], "%s: %s" % (type(e).__name__, e)
    # ② 按需发车（共享账本 busy 窗口内：CLI 版与 MCP 版同写同读）
    try:
        out["ondemand_busy"] = [{"key": b.get("key"), "src": b.get("src") or "?", "kind": b.get("kind"),
                                 "target": b.get("target"),
                                 "at": b.get("ts") or b.get("at"), "ts": b.get("ts") or b.get("at"),
                                 "taskId": b.get("task_uid") or b.get("taskId"),
                                 "busy_until": b.get("busy_until"),
                                 "superseded_by": b.get("superseded_by") or "",
                                 "dirs": (b.get("resources") or {}).get("dirs") or []}
                                for b in _active_busy(_state_load())]
        out["ledger"] = ledger.ledger_path()
    except Exception as e:  # noqa: BLE001
        out["ondemand_busy"], out["ondemand_busy_error"] = [], "%s: %s" % (type(e).__name__, e)

    # ③ 远端现场（ssh 只读）
    remote = _remote_busy(timeout=timeout)
    out["remote_error"] = remote["error"]
    out["emulators_up"] = sorted(remote["vmm"].keys(), key=lambda s: int(s) if s.isdigit() else 0)
    slots, dir_scripts = [], {}
    if not remote["error"]:
        try:
            smap = _script_map()
        except Exception as e:  # noqa: BLE001
            smap, out["script_map_error"] = {}, "%s: %s" % (type(e).__name__, e)
        by_dir = {}
        for s in smap.values():
            by_dir.setdefault(s["dir"].lower(), []).append(s)
        for d_low, pids in sorted(remote["dirs"].items()):
            row = {"dir": d_low, "pids": pids, "scripts": []}
            for s in by_dir.get(d_low, []):
                item = {"name": s["name"], "sid": s["sid"], "emu": s["emu"]}
                if with_users:
                    try:
                        users = _script_users(s["sid"])
                    except Exception:  # noqa: BLE001
                        users = []
                    item["users_enabled"] = [(_mask(u["name"]) if mask else u["name"])
                                             for u in users if u["enabled"]]
                    item["users_enabled_n"] = len(item["users_enabled"])
                row["scripts"].append(item)
            if not row["scripts"]:
                row["note"] = "该目录不在 AUTO-MAS 脚本表（残留进程？）"
            slots.append(row)
            dir_scripts[d_low] = [s["name"] for s in by_dir.get(d_low, [])]
        out["maa_slots"] = slots
        out["maa_slot_count"] = len(slots)
        # busy 窗口记录 vs 现场：对不上的两头都点出来
        live = set(remote["dirs"])
        tracked = set()
        for b in (out.get("shift_busy") or []) + (out.get("ondemand_busy") or []):
            for d in b.get("dirs") or []:
                tracked.add(str(d).rstrip("\\").lower())
        out["tracked_not_live"] = sorted(tracked - live)   # 记录说在跑、现场没进程（可能已收工）
        out["live_not_tracked"] = sorted(live - tracked)   # 现场在跑、两本账都没有（人工/UI 发的？）
    return out


# --------------------------------------------------------------------------- #
# 工具 2/3：按队列 / 按脚本发车（AutoProxy）                                    #
# --------------------------------------------------------------------------- #

def _dispatch_target(kind: str, name: str, uid: str, res: dict | None, a: dict) -> dict:
    """按需发车核心（kind ∈ queue/script/account，target_uid 已是可 POST 的目标）。"""
    apply_ = bool(a.get("apply", False))
    force = bool(a.get("force", False))
    force_conflict = bool(a.get("force_conflict", False))
    note = str(a.get("note") or "")
    label = "%s:%s" % (kind, name)
    key = {"queue": "q:", "script": "s:", "account": "a:"}[kind] + (uid if kind != "account" else name)

    ok, why = md.reachable()
    if not ok:
        r = _refused(tool="dispatch_%s" % kind, target=name, refused="upstream_unreachable",
                     reason="rest_unreachable", detail=why)
        _audit({"tool": "dispatch_%s" % kind, "target": name, "kind": kind, "apply": apply_,
                "verdict": "upstream_unreachable", "detail": why})
        return r

    if apply_ and _no_go():
        r = _refused(tool="dispatch_%s" % kind, target=name, refused="dispatch_disabled",
                     detail="MAAORCH_DISPATCH_NO_GO / MAAORCH_ONDEMAND_NO_GO 置位：拒绝一切真发车")
        _audit({"tool": "dispatch_%s" % kind, "target": name, "kind": kind, "apply": True,
                "verdict": "dispatch_disabled"})
        return r

    st = _state_load()
    dup = _state_entry(st, _today(), key)
    if dup and not force:
        r = _refused(tool="dispatch_%s" % kind, target=name, refused="duplicate_today",
                     duplicate_of={"at": dup.get("ts") or dup.get("at"),
                                   "taskId": dup.get("task_uid") or dup.get("taskId"),
                                   "src": dup.get("src") or "?", "target": dup.get("target")},
                     hint="今天已发过同一目标（共享账本，含 CLI 端发的车）；确认要再发用 force=true（会记审计）")
        _audit({"tool": "dispatch_%s" % kind, "target": name, "kind": kind, "apply": apply_,
                "verdict": "duplicate_today", "taskId": dup.get("task_uid") or dup.get("taskId")})
        return r

    conf = _conflicts(label, res)
    if conf and not force_conflict:
        detail = "资源重叠: %s（%s 发车，busy 至 %s）" % (
            ", ".join(str(c.get("queue")) for c in conf), conf[0].get("dispatched_at"),
            conf[0].get("busy_until"))
        r = _refused(tool="dispatch_%s" % kind, target=name, refused="conflict_inflight",
                     conflict=conf, detail=detail,
                     hint="确认对方任务已结束再用 force_conflict=true 放行（会记账）")
        _audit({"tool": "dispatch_%s" % kind, "target": name, "kind": kind, "apply": apply_,
                "verdict": "conflict_inflight", "conflict": conf,
                "resources": res})
        return r

    # G3L（P1-2）：目标实例被**别的**任务租下 ⇒ 拒发（dry-run 同判；零上游写）
    lease_pre = _lease_check(res, a)
    if lease_pre.get("busy"):
        detail = "实例租约：%s" % (lease_pre.get("detail") or lease_pre.get("status") or "")
        _audit({"tool": "dispatch_%s" % kind, "target": name, "kind": kind, "apply": apply_,
                "verdict": "lease_busy", "lease": _lease_brief(lease_pre), "detail": detail})
        return _refused(tool="dispatch_%s" % kind, target=name, refused="lease_busy",
                        lease=_lease_brief(lease_pre), detail=detail,
                        hint="跨族单飞：别的任务正占着实例（等它结束/过期，或持有者 release；"
                             "force / force_conflict 都放行不了租约）")

    if not apply_:
        r = {"ok": True, "tool": "dispatch_%s" % kind, "apply": False, "dry_run": True,
             "target": name, "target_uid": uid, "would_dispatch": {"taskId": uid, "mode": "AutoProxy"},
             "resources": res, "conflicts": conf, "guard_window": _in_guard_window(),
             "lease": _lease_brief(lease_pre),
             "note": "dry-run：加 apply=true 才真发车（同一目标当天单飞，重复须 force=true）"}
        _audit({"tool": "dispatch_%s" % kind, "target": name, "kind": kind, "apply": False,
                "verdict": "plan", "target_uid": uid})
        return r

    try:
        with md._dispatch_lock():
            st2 = _state_load()
            dup2 = _state_entry(st2, _today(), key)
            if dup2 and not force:
                _audit({"tool": "dispatch_%s" % kind, "target": name, "kind": kind, "apply": True,
                        "verdict": "duplicate_today", "taskId": dup2.get("task_uid") or dup2.get("taskId")})
                return _refused(tool="dispatch_%s" % kind, target=name, refused="duplicate_today",
                                duplicate_of={"at": dup2.get("ts") or dup2.get("at"),
                                              "taskId": dup2.get("task_uid") or dup2.get("taskId")})
            lease = _lease_acquire(res, a, "按需发车 %s:%s（%s）" % (kind, name, uid))
            if not lease.get("ok"):
                detail = "实例租约：%s" % (lease.get("detail") or lease.get("status") or "")
                _audit({"tool": "dispatch_%s" % kind, "target": name, "kind": kind, "apply": True,
                        "verdict": "lease_busy", "lease": _lease_brief(lease), "detail": detail})
                return _refused(tool="dispatch_%s" % kind, target=name, refused="lease_busy",
                                lease=_lease_brief(lease), detail=detail,
                                hint="零上游写拒发：拿不到实例租约（被别的任务占着，或持有者为空）")
            res_ = _attempt_start(uid)
            if res_.get("ok"):
                entry = _record_dispatch(st2, key, kind, name, uid, str(res_.get("taskId")), res, note,
                                         lease=lease)
                _audit({"tool": "dispatch_%s" % kind, "target": name, "kind": kind, "apply": True,
                        "verdict": "dispatched", "target_uid": uid, "taskId": entry["task_uid"],
                        "busy_until": entry["busy_until"], "note": note[:160],
                        "lease": _lease_brief(lease),
                        "conflict_overridden": [c.get("queue") for c in conf] if (conf and force_conflict) else None})
                return {"ok": True, "tool": "dispatch_%s" % kind, "apply": True, "dispatched": True,
                        "target": name, "target_uid": uid, "taskId": entry["task_uid"],
                        "busy_until": entry["busy_until"], "resources": res,
                        "lease": _lease_brief(lease),
                        "conflict_overridden": [c.get("queue") for c in conf] if (conf and force_conflict) else None,
                        "stop_hint": "停机用 stop_task(task_id=%s)（只认这个 task_uid，别 kill）" % entry["task_uid"]}
            rel = _lease_release(res, lease.get("owner") or _lease_owner(a), "发车失败即放（ondemand:mcp）")
            _audit({"tool": "dispatch_%s" % kind, "target": name, "kind": kind, "apply": True,
                    "verdict": "failed", "reason": res_.get("reason"), "detail": (res_.get("detail") or "")[:300],
                    "lease_release": _lease_brief(rel)})
            return {"ok": False, "tool": "dispatch_%s" % kind, "apply": True, "dispatched": False,
                    "target": name, "target_uid": uid, "failed": True,
                    "reason": res_.get("reason"), "detail": res_.get("detail"),
                    "lease_release": _lease_brief(rel)}
    except md._LockBusy as e:
        _audit({"tool": "dispatch_%s" % kind, "target": name, "kind": kind, "apply": True,
                "verdict": "concurrent_dispatch", "detail": str(e)})
        return _refused(tool="dispatch_%s" % kind, target=name, refused="concurrent_dispatch",
                        detail=str(e), hint="cron 调度器（每 3 分钟）正在发车，稍后重试")


def tool_dispatch_queue(a: dict) -> dict:
    name = str(a.get("name") or "").strip()
    if not name:
        return _refused(tool="dispatch_queue", refused="bad_args", detail="name 不能为空")
    try:
        qm = md.queue_map()
    except Exception as e:  # noqa: BLE001
        _audit({"tool": "dispatch_queue", "target": name, "kind": "queue", "apply": bool(a.get("apply")),
                "verdict": "upstream_unreachable", "detail": "%s: %s" % (type(e).__name__, e)})
        return _refused(tool="dispatch_queue", target=name, refused="upstream_unreachable",
                        detail="%s: %s" % (type(e).__name__, e))
    if name not in qm:
        # 名字打错时给出现有队列（最多 12 条），省一次盲猜
        hint = sorted(x for x in qm if x)
        _audit({"tool": "dispatch_queue", "target": name, "kind": "queue",
                "apply": bool(a.get("apply")), "verdict": "refused:unknown_queue"})
        return _refused(tool="dispatch_queue", target=name, refused="unknown_queue", queue_uid=None,
                        hint={"known_queues": hint[:12], "known_n": len(hint)})
    uid = qm[name]
    if md.QUEUE_RE.match(name):
        # 24 条班次队列：委托 mas_dispatch（保留告警台账 / late 标记 / 冲突闸门 / 单飞全链路）
        st = None
        try:
            st = md.load_state()
        except Exception:  # noqa: BLE001
            st = None
        res = md.dispatch(name, uid, apply_=bool(a.get("apply", False)),
                          force=bool(a.get("force", False)), st=st, retries=0,
                          force_conflict=bool(a.get("force_conflict", False)))
        _audit({"tool": "dispatch_queue", "target": name, "kind": "queue", "apply": bool(a.get("apply")),
                "verdict": "shift_delegated", "target_uid": uid,
                "result": {k: res.get(k) for k in ("ok", "dispatched", "dry_run", "refused", "taskId",
                                                   "reason", "alert") if k in res}})
        out = dict(res)
        out.setdefault("ok", bool(res.get("dispatched") or res.get("dry_run")))
        out.update({"tool": "dispatch_queue", "via": "mas_dispatch(shift_gate)",
                    "summary": md.dispatch_summary(res)})
        return out
    res = _resource_of_queue(name)
    return _dispatch_target("queue", name, uid, res, a)


def _resource_of_queue(name: str) -> dict | None:
    """队列资源（MAA 目录/脚本/实例）；解析失败 None（冲突退化为槽位启发式）。"""
    try:
        return md.resources_of(name)
    except Exception:  # noqa: BLE001
        return None


def tool_dispatch_script(a: dict) -> dict:
    name = str(a.get("name") or "").strip()
    if not name:
        return _refused(tool="dispatch_script", refused="bad_args", detail="name 不能为空")
    try:
        smap = _script_map()
    except Exception as e:  # noqa: BLE001
        _audit({"tool": "dispatch_script", "target": name, "kind": "script", "apply": bool(a.get("apply")),
                "verdict": "upstream_unreachable", "detail": "%s: %s" % (type(e).__name__, e)})
        return _refused(tool="dispatch_script", target=name, refused="upstream_unreachable",
                        detail="%s: %s" % (type(e).__name__, e))
    hit = [s for s in smap.values() if s["name"] == name]
    if not hit:
        _audit({"tool": "dispatch_script", "target": name, "kind": "script",
                "apply": bool(a.get("apply")), "verdict": "refused:unknown_script"})
        return _refused(tool="dispatch_script", target=name, refused="unknown_script",
                        hint={"known_scripts_n": len(smap),
                              "known_sample": sorted(s["name"] for s in smap.values())[:8]})
    if len(hit) > 1:
        _audit({"tool": "dispatch_script", "target": name, "kind": "script",
                "apply": bool(a.get("apply")), "verdict": "refused:ambiguous_script"})
        return _refused(tool="dispatch_script", target=name, refused="ambiguous_script",
                        hits=[h["sid"] for h in hit], hint="同名脚本多条：请改用 dispatch_queue（队列面更可控）")
    s = hit[0]
    res = {"dirs": [s["dir"]] if s["dir"] else [], "scripts": [s["sid"]],
           "emus": [s["emu"]] if s["emu"] else [], "names": [s["name"]]}
    out = _dispatch_target("script", name, s["sid"], res, a)
    if isinstance(out, dict):
        out.setdefault("runs", "该脚本全部启用用户（AutoProxy 语义，见 docs/audits/2026-09-27_mas_task_modes.md §1）")
        out.setdefault("dir", s["dir"])
    return out


# --------------------------------------------------------------------------- #
# 工具 4：按单号发车（内部走临时队列）                                          #
# --------------------------------------------------------------------------- #

def _resolve_account(name: str, smap: dict) -> dict:
    """账号名 -> {sid, dir, emu, script_name, enabled, users_n}；找不到/多条都报出来。"""
    hits, disabled = [], []
    for s in smap.values():
        try:
            users = _script_users(s["sid"])
        except Exception:  # noqa: BLE001
            continue
        for u in users:
            if u["name"] != name:
                continue
            row = {"sid": s["sid"], "script": s["name"], "dir": s["dir"], "emu": s["emu"],
                   "enabled": u["enabled"], "user_uid": u["uid"]}
            (hits if u["enabled"] else disabled).append(row)
    return {"hits": hits, "disabled": disabled}


def tool_dispatch_account(a: dict) -> dict:
    name = str(a.get("name") or "").strip()
    apply_ = bool(a.get("apply", False))
    force = bool(a.get("force", False))
    ignore_window = bool(a.get("ignore_window", False))
    note = str(a.get("note") or "")
    if not name:
        return _refused(tool="dispatch_account", refused="bad_args", detail="name 不能为空")
    if not apply_ and not force:
        pass  # 只读预检同样支持 force 忽略重复（dry 计划照显）

    ok, why = md.reachable()
    if not ok:
        _audit({"tool": "dispatch_account", "target": name, "apply": apply_,
                "verdict": "upstream_unreachable", "detail": why})
        return _refused(tool="dispatch_account", target=name, refused="upstream_unreachable",
                        reason="rest_unreachable", detail=why)

    try:
        smap = _script_map()
    except Exception as e:  # noqa: BLE001
        return _refused(tool="dispatch_account", target=name, refused="upstream_unreachable",
                        detail="%s: %s" % (type(e).__name__, e))
    r = _resolve_account(name, smap)
    if not r["hits"]:
        if r["disabled"]:
            _audit({"tool": "dispatch_account", "target": name, "apply": apply_,
                    "verdict": "refused:account_disabled"})
            return _refused(tool="dispatch_account", target=name, refused="account_disabled",
                            hit=[{"script": d["script"], "dir": d["dir"], "enabled": False} for d in r["disabled"]],
                            hint="该号在 AUTO-MAS 存在但未启用（Status=假 / RemainedDay=0）；要跑先启用（属改配置，需人工）")
        _audit({"tool": "dispatch_account", "target": name, "apply": apply_,
                "verdict": "refused:unknown_account"})
        return _refused(tool="dispatch_account", target=name, refused="unknown_account",
                        hint="账号名必须与 AUTO-MAS 用户昵称完全一致（脚本 × 用户表口径）")
    if len({h["sid"] for h in r["hits"]}) > 1:
        _audit({"tool": "dispatch_account", "target": name, "apply": apply_,
                "verdict": "refused:ambiguous_account"})
        return _refused(tool="dispatch_account", target=name, refused="ambiguous_account",
                        hits=[{"script": h["script"], "sid": h["sid"]} for h in r["hits"]],
                        hint="同名账号出现在多条脚本：请用 dispatch_script / dispatch_queue")
    h = r["hits"][0]
    res = {"dirs": [h["dir"]] if h["dir"] else [], "scripts": [h["sid"]],
           "emus": [h["emu"]] if h["emu"] else [], "names": [h["script"]]}
    label = "account:%s" % name
    key = "a:" + name

    # 守卫窗（MAS_RERUN_POLICY §P0：补跑一律回避）——补跑语义单独收紧
    gw = _in_guard_window()
    if gw and not ignore_window:
        _audit({"tool": "dispatch_account", "target": name, "apply": apply_,
                "verdict": "guard_window", "window": gw})
        return _refused(tool="dispatch_account", target=name, refused="guard_window",
                        window=gw, detail="班次守卫窗（MAS_RERUN_POLICY §P0）内不发补跑车",
                        hint="确认必须发（如空窗抢救）用 ignore_window=true（会记审计）")

    if apply_ and _no_go():
        return _refused(tool="dispatch_account", target=name, refused="dispatch_disabled",
                        detail="MAAORCH_DISPATCH_NO_GO / MAAORCH_ONDEMAND_NO_GO 置位")

    st = _state_load()
    dup = _state_entry(st, _today(), key)
    if dup and not force:
        return _refused(tool="dispatch_account", target=name, refused="duplicate_today",
                        duplicate_of={"at": dup.get("ts") or dup.get("at"),
                                      "taskId": dup.get("task_uid") or dup.get("taskId"),
                                      "src": dup.get("src") or "?"},
                        hint="今天已发过同一账号（共享账本，含 CLI 端发的车）；再发用 force=true")
    conf = _conflicts(label, res)
    if conf:
        return _refused(tool="dispatch_account", target=name, refused="conflict_inflight", conflict=conf,
                        detail="目标资源在 busy 窗口内：%s" % ", ".join(str(c.get("queue")) for c in conf),
                        hint="确认对方已结束：mas_dispatch.py --release <队列> --apply 后再发")

    # G3L（P1-2）：目标实例被别的任务租下 ⇒ 拒发（在建临时队列**之前**，零上游写；dry-run 同判）
    lease_pre = _lease_check(res, a)
    if lease_pre.get("busy"):
        detail = "实例租约：%s" % (lease_pre.get("detail") or lease_pre.get("status") or "")
        _audit({"tool": "dispatch_account", "target": name, "apply": apply_,
                "verdict": "lease_busy", "lease": _lease_brief(lease_pre), "detail": detail})
        return _refused(tool="dispatch_account", target=name, refused="lease_busy",
                        lease=_lease_brief(lease_pre), detail=detail,
                        hint="跨族单飞：别的任务正占着实例（等它结束/过期，或持有者 release；"
                             "force / force_conflict 都放行不了租约）")

    # 目录忙检查（三条硬约束之②：目标 MAA 目录必须空闲；现场口径 = MAA.exe 进程）
    busy = None
    try:
        busy = _remote_busy(timeout=180)
    except Exception as e:  # noqa: BLE001
        busy = {"dirs": {}, "vmm": {}, "error": "%s: %s" % (type(e).__name__, e)}
    dir_busy = bool(h["dir"]) and h["dir"].lower() in (busy.get("dirs") or {})
    emu_up = (h["emu"] in (busy.get("vmm") or {})) if h["emu"] else None

    if apply_ and dir_busy:
        _audit({"tool": "dispatch_account", "target": name, "apply": True, "verdict": "dir_busy",
                "dir": h["dir"]})
        return _refused(tool="dispatch_account", target=name, refused="dir_busy", dir=h["dir"],
                        detail="目标 MAA 目录有 MAA.exe 在跑（同目录并行=互杀）",
                        hint="等它收工（list_running 看现场）；不要 kill")

    plan = {"ok": True, "tool": "dispatch_account", "apply": apply_, "target": name,
            "script": h["script"], "script_uid": h["sid"], "dir": h["dir"], "emu": h["emu"],
            "dir_busy": dir_busy, "emulator_running": emu_up,
            "conflicts": conf, "guard_window": gw, "lease": _lease_brief(lease_pre),
            "temp_queue_name": "%s-%s" % (TMPQ_TAG, dt.datetime.now().strftime("%m%d_%H%M")),
            "note": ("该脚本全部启用用户会被跑到（AutoProxy 语义）；临时队列跑完保留，"
                     "清理用 tools/auto_mas_rerun.py --cleanup")}
    if not apply_:
        plan["dry_run"] = True
        plan["would_create"] = {"queue_name": plan["temp_queue_name"], "item_script": h["script"],
                                "dispatch": {"taskId": "<new queue uid>", "mode": "AutoProxy"}}
        _audit({"tool": "dispatch_account", "target": name, "apply": False, "verdict": "plan",
                "script": h["script"], "dir": h["dir"]})
        return plan

    try:
        with md._dispatch_lock():
            st2 = _state_load()
            dup2 = _state_entry(st2, _today(), key)
            if dup2 and not force:
                return _refused(tool="dispatch_account", target=name, refused="duplicate_today",
                                duplicate_of={"at": dup2.get("ts") or dup2.get("at"),
                                              "taskId": dup2.get("task_uid") or dup2.get("taskId")})
            # G3L：建队列前先取租（拿不到 ⇒ 零上游写拒发，连临时队列都不建）
            lease = _lease_acquire(res, a, "按需发车 account:%s（%s）" % (name, h["sid"]))
            if not lease.get("ok"):
                detail = "实例租约：%s" % (lease.get("detail") or lease.get("status") or "")
                _audit({"tool": "dispatch_account", "target": name, "apply": True,
                        "verdict": "lease_busy", "lease": _lease_brief(lease), "detail": detail})
                return _refused(tool="dispatch_account", target=name, refused="lease_busy",
                                lease=_lease_brief(lease), detail=detail,
                                hint="零上游写拒发：拿不到实例租约（临时队列未建、未发车）")
            # 建临时队列（口径同 tools/auto_mas_rerun.py：add → update 名 → item add/update → dispatch）
            try:
                ts = dt.datetime.now().strftime("%m%d_%H%M")
                qname = "%s-%s" % (TMPQ_TAG, ts)
                qid = str(md.post("/api/queue/add", {}).get("queueId") or "")
                if not qid:
                    raise RuntimeError("queue/add 未返回 queueId")
                u = md.post("/api/queue/update", {"queueId": qid, "data": {"Info": {
                    "Name": qname, "TimeEnabled": False, "StartUpEnabled": False,
                    "AfterAccomplish": "NoAction"}}})
                if u.get("code") != 200:
                    raise RuntimeError("queue/update: %s" % json.dumps(u, ensure_ascii=False)[:200])
                r1 = md.post("/api/queue/item/add", {"queueId": qid})
                iid = str(r1.get("queueItemId") or (r1.get("data") or {}).get("uid") or "")
                if not iid:
                    raise RuntimeError("queue/item/add 未返回 queueItemId")
                r2 = md.post("/api/queue/item/update", {"queueId": qid, "queueItemId": iid,
                                                        "data": {"Info": {"ScriptId": h["sid"]}}})
                if r2.get("code") != 200:
                    raise RuntimeError("queue/item/update: %s" % json.dumps(r2, ensure_ascii=False)[:200])
            except Exception as e:  # noqa: BLE001
                _audit({"tool": "dispatch_account", "target": name, "apply": True,
                        "verdict": "temp_queue_failed", "detail": "%s: %s" % (type(e).__name__, e)})
                return {"ok": False, "tool": "dispatch_account", "apply": True, "dispatched": False,
                        "refused": "temp_queue_failed", "detail": "%s: %s" % (type(e).__name__, e),
                        "hint": "临时队列可能只建了一半，检查 AUTO-MAS 队列列表里 %s* 是否需要手删" % TMPQ_TAG}
            res_ = _attempt_start(qid)
            if res_.get("ok"):
                entry = _record_dispatch(st2, key, "account", name, qid, str(res_.get("taskId")), res,
                                         note or "account=%s" % name, lease=lease)
                entry["queue_name"] = qname
                entry["queue_uid"] = qid
                entry["item_uid"] = iid
                entry["account"] = name
                _state_save(st2)
                _audit({"tool": "dispatch_account", "target": name, "apply": True,
                        "verdict": "dispatched", "queue": qname, "queue_uid": qid, "item_uid": iid,
                        "taskId": entry["task_uid"], "dir": h["dir"], "script": h["script"],
                        "lease": _lease_brief(lease),
                        "note": note[:160]})
                plan.update({"dispatched": True, "queue": qname, "queue_uid": qid, "item_uid": iid,
                             "taskId": entry["task_uid"], "busy_until": entry["busy_until"],
                             "dry_run": False, "lease": _lease_brief(lease),
                             "stop_hint": "停机用 stop_task(task_id=%s)；清理队列用 "
                                          "tools/auto_mas_rerun.py --cleanup" % entry["task_uid"]})
                return plan
            rel = _lease_release(res, lease.get("owner") or _lease_owner(a), "发车失败即放（ondemand:mcp）")
            _audit({"tool": "dispatch_account", "target": name, "apply": True, "verdict": "failed",
                    "queue_uid": qid, "reason": res_.get("reason"),
                    "lease_release": _lease_brief(rel),
                    "detail": (res_.get("detail") or "")[:300]})
            plan.update({"ok": False, "dispatched": False, "failed": True, "queue_uid": qid,
                         "reason": res_.get("reason"), "detail": res_.get("detail"),
                         "lease_release": _lease_brief(rel),
                         "hint": "临时队列 %s 已建但发车失败；确认无用后可手删（或 --cleanup）" % qname})
            return plan
    except md._LockBusy as e:
        _audit({"tool": "dispatch_account", "target": name, "apply": True,
                "verdict": "concurrent_dispatch", "detail": str(e)})
        return _refused(tool="dispatch_account", target=name, refused="concurrent_dispatch", detail=str(e))


# --------------------------------------------------------------------------- #
# 工具 5：停任务（只认 start 返回的 task_uid）                                  #
# --------------------------------------------------------------------------- #

def tool_stop_task(a: dict) -> dict:
    task_id = str(a.get("task_id") or "").strip()
    apply_ = bool(a.get("apply", False))
    force = bool(a.get("force", False))
    reason = str(a.get("reason") or "")
    if not task_id:
        return _refused(tool="stop_task", refused="bad_args", detail="task_id 不能为空")
    if task_id.upper() == "ALL":
        return _refused(tool="stop_task", task_id=task_id, refused="refuse_all",
                        detail="不接受 ALL（会停掉全部任务含电源动作）；只停具体 task_uid")
    try:
        uuidlib.UUID(task_id)
    except (ValueError, AttributeError, TypeError):
        return _refused(tool="stop_task", task_id=task_id, refused="not_task_uid",
                        detail="不是合法 UUID；stop 只认 dispatch/start 返回的 task_uid")
    # 反向防呆：把队列/脚本 uid 当 task_uid 传进来是 no-op（上游「任务已结束视为成功」），必须拦下
    try:
        qm = md.queue_map()
    except Exception:  # noqa: BLE001
        qm = {}
    if task_id in set(qm.values()):
        names = [n for n, u in qm.items() if u == task_id]
        return _refused(tool="stop_task", task_id=task_id, refused="is_queue_uid",
                        queue=names[:3], detail="这是队列 uid，不是 task_uid（stop 只认 start 返回的 task_uid）",
                        hint="停队列当前任务：先 list_running 拿 taskId，再 stop_task(task_id=<taskId>)")
    if task_id in set(_script_map().keys()):
        return _refused(tool="stop_task", task_id=task_id, refused="is_script_uid",
                        detail="这是脚本 uid，不是 task_uid")

    # 已知 task_uid 台账（共享账本 + 班次台账）；找不到时 fail-closed（需 force 显式放行）
    known = []
    ost = _state_load()
    for d, key, rec in ledger.iter_entries(ost):
        if rec.get("task_uid") == task_id or rec.get("taskId") == task_id:
            known.append({"source": "mas_ondemand:%s" % (rec.get("src") or "?"), "date": d, "key": key,
                          "target": rec.get("target"), "at": rec.get("ts") or rec.get("at")})
    try:
        for d, recs in (md.load_state() or {}).items():
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(d)):
                continue
            for qname, rec in (recs or {}).items():
                if isinstance(rec, dict) and rec.get("taskId") == task_id:
                    known.append({"source": "mas_dispatch", "date": d, "queue": qname, "at": rec.get("at")})
    except Exception:  # noqa: BLE001
        pass

    if not known and not force:
        _audit({"tool": "stop_task", "task_id": task_id, "apply": apply_, "verdict": "unknown_task_uid"})
        return _refused(tool="stop_task", task_id=task_id, refused="unknown_task_uid",
                        detail="两个发车台账里都没有这个 task_uid（可能是 UI/他人发的，或已结束过久）",
                        hint="确认无误用 force=true 放行；UI 发的任务请看 AUTO-MAS 调度台")

    if not apply_:
        _audit({"tool": "stop_task", "task_id": task_id, "apply": False, "verdict": "plan",
                "known": [k.get("source") for k in known]})
        return {"ok": True, "tool": "stop_task", "apply": False, "dry_run": True, "task_id": task_id,
                "known_as": known,
                "would_stop": {"taskId": task_id},
                "note": "dry-run：加 apply=true 真停（优雅停 + 解锁 + Temp→config 还原；不要 kill）"}

    ok, why = md.reachable()
    if not ok:
        _audit({"tool": "stop_task", "task_id": task_id, "apply": True,
                "verdict": "upstream_unreachable", "detail": why})
        return _refused(tool="stop_task", task_id=task_id, refused="upstream_unreachable", detail=why)
    try:
        r = md.post("/api/dispatch/stop", {"taskId": task_id}, timeout=90)
    except Exception as e:  # noqa: BLE001
        part = md._classify_exc(e)
        _audit({"tool": "stop_task", "task_id": task_id, "apply": True, "verdict": "failed",
                "reason": part[0], "detail": part[1][:200]})
        return {"ok": False, "tool": "stop_task", "apply": True, "task_id": task_id,
                "failed": True, "reason": part[0], "detail": part[1], "known_as": known}
    # 回读校验：共享账本里的记录标记 stopped（audit 留痕；CLI 端发的车也一并标记）
    for _d, _key, rec in ledger.iter_entries(ost):
        if rec.get("task_uid") == task_id or rec.get("taskId") == task_id:
            with contextlib.suppress(Exception):
                rec["stopped_at"] = _now()
                rec["stopped_reason"] = reason[:200]
    okstop = (r.get("code") == 200)
    rel = _lease_release_records(ost, task_id, "ondemand:mcp stop %s" % task_id) if okstop else {"released": [], "detail": ""}
    with contextlib.suppress(Exception):
        _state_save(ost)
    _audit({"tool": "stop_task", "task_id": task_id, "apply": True,
            "verdict": "stopped" if okstop else "stop_failed", "reason": reason[:160],
            "resp": {"code": r.get("code"), "message": str(r.get("message") or "")[:200]},
            "lease_release": _lease_brief(rel),
            "known_as": [k.get("source") for k in known]})
    return {"ok": okstop, "tool": "stop_task", "apply": True, "task_id": task_id,
            "stopped": okstop, "resp": {"code": r.get("code"), "status": r.get("status"),
                                        "message": r.get("message")},
            "known_as": known,
            "note": "上游对「已结束任务的 stop」也回成功；如需解锁同资源补发，用 "
                    "tools/mas_dispatch.py --release <队列> --apply（人工确认）"}


# --------------------------------------------------------------------------- #
# 工具 6：发车前 GO/NO-GO（复用 tools/mas_preflight.py）                        #
# --------------------------------------------------------------------------- #

def tool_preflight(a: dict) -> dict:
    target = str(a.get("target") or "").strip()
    if not target:
        return _refused(tool="ondemand_preflight", refused="bad_args", detail="target 不能为空")
    kind = str(a.get("kind") or "auto").strip().lower()
    if kind not in ("auto", "queue", "script"):
        return _refused(tool="ondemand_preflight", refused="bad_args", detail="kind ∈ auto|queue|script")
    skip = bool(a.get("skip_screen", True))
    try:
        tmo = max(30, min(600, int(a.get("timeout") or (120 if skip else 300))))
    except (TypeError, ValueError):
        tmo = 120 if skip else 300
    try:
        min_gb, warn_gb = float(a.get("min_free_gb", 10) or 10), float(a.get("warn_free_gb", 30) or 30)
    except (TypeError, ValueError):
        min_gb, warn_gb = 10.0, 30.0

    # 目标面解析：先按名字真实存在性判定（避免把队列名当脚本名传进 preflight）
    resolved_kind = kind
    if kind in ("auto", "queue"):
        try:
            if target in md.queue_map():
                resolved_kind = "queue"
        except Exception:  # noqa: BLE001
            pass
    if resolved_kind == "auto":
        try:
            if any(s["name"] == target for s in _script_map().values()):
                resolved_kind = "script"
        except Exception:  # noqa: BLE001
            pass
    if resolved_kind == "auto":
        return _refused(tool="ondemand_preflight", refused="unknown_target", target=target,
                        detail="target 既不是现有队列名、也不是现有脚本名（两边都查过）")

    argv = [PY, PREFLIGHT_CLI, "--json", "--min-free-gb", str(min_gb), "--warn-free-gb", str(warn_gb),
            "--timeout", str(tmo)]
    argv += (["--queue", target] if resolved_kind == "queue" else ["--script", target])
    if skip:
        argv.append("--skip-screen")
    t0 = time.time()
    try:
        r = subprocess.run(argv, capture_output=True, stdin=subprocess.DEVNULL, timeout=tmo + 60,
                           cwd=ROOT, env=_child_env())
    except subprocess.TimeoutExpired:
        _audit({"tool": "ondemand_preflight", "target": target, "kind": resolved_kind,
                "verdict": "timeout"})
        return {"ok": False, "tool": "ondemand_preflight", "preflight": "UNKNOWN",
                "error": {"kind": "timeout", "detail": "mas_preflight 超时 %ss" % (tmo + 60)},
                "target": target, "target_kind": resolved_kind, "elapsed_s": round(time.time() - t0, 1)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "tool": "ondemand_preflight", "preflight": "UNKNOWN", "target": target,
                "target_kind": resolved_kind,
                "error": {"kind": "unknown", "detail": "%s: %s" % (type(e).__name__, e)}}
    out = (r.stdout or b"").decode("utf-8", "replace")
    err = (r.stderr or b"").decode("utf-8", "replace")
    elapsed = round(time.time() - t0, 1)
    rc = r.returncode
    try:
        res = json.loads(out)
    except Exception:  # noqa: BLE001
        _audit({"tool": "ondemand_preflight", "target": target, "kind": resolved_kind,
                "verdict": "parse_error", "exit_code": rc})
        return {"ok": False, "tool": "ondemand_preflight", "preflight": "UNKNOWN", "target": target,
                "target_kind": resolved_kind, "exit_code": rc, "elapsed_s": elapsed,
                "error": {"kind": "unresolved" if rc == 2 else "parse_error",
                          "detail": (err or out)[-400:]}}
    checks = {k: {"status": (v or {}).get("status"), "detail": (v or {}).get("detail")}
              for k, v in (res.get("checks") or {}).items()}
    out_res = {"ok": rc in (0, 1), "tool": "ondemand_preflight", "preflight": res.get("preflight"),
               "target": target, "target_kind": resolved_kind,
               "target_detail": {"dirs": (res.get("target") or {}).get("dirs"),
                                 "emus": (res.get("target") or {}).get("emus")},
               "checks": checks, "failing_checks": res.get("failing_checks"), "reasons": res.get("reasons"),
               "exit_code": rc, "elapsed_s": elapsed,
               "hint": "PREFLIGHT=GO 才发车；NO-GO 只报告不硬拦（发车工具自身闸门照旧）"}
    _audit({"tool": "ondemand_preflight", "target": target, "kind": resolved_kind,
            "verdict": res.get("preflight"), "exit_code": rc, "failing": res.get("failing_checks")})
    return out_res


# --------------------------------------------------------------------------- #
# MCP 面                                                                       #
# --------------------------------------------------------------------------- #

_SCHEMAS = [
    types.Tool(
        name="list_running",
        description=("【只读】当前在跑清单（三源合并）：① Hermes 班次发车 busy 窗口内的队列（runtime/"
                     "mas_dispatch_state.json）；② 按需发车 busy 窗口内的目标（共享账本 runtime/"
                     "mas_ondemand_ledger.json —— CLI 版 mas_ondemand.py 与本 server 同写同读，行带 src=cli/mcp）；"
                     "③ 远端现场（ssh 只读）：MAA.exe 进程 ⇒ 在跑的 MAA 槽位目录、"
                     "MuMuVMMHeadless ⇒ 在跑的模拟器实例，并把槽位映射回 AUTO-MAS 脚本/启用用户（默认掩码）。"
                     "另给 emulators_up / tracked_not_live（记录说在跑、现场没进程）/ live_not_tracked"
                     "（现场在跑、账本里没有）/ guard_window（05:00–05:40、19:20–20:20）。"
                     "发车前先看 maa_slots 与 guard_window；不写任何东西。"),
        inputSchema={"type": "object",
                     "properties": {
                         "timeout": {"type": "integer", "default": 180,
                                     "description": "远端 ssh 巡检超时（秒），默认 180"},
                         "with_users": {"type": "boolean", "default": True,
                                        "description": "是否把在跑槽位的脚本启用用户也列出来（要多几次 REST）"},
                         "mask_accounts": {"type": "boolean", "default": True,
                                           "description": "账号名掩码（前2+***+后2）；false=原文（按需发车要用真名时）"},
                     },
                     "additionalProperties": False},
    ),
    types.Tool(
        name="dispatch_queue",
        description=("按队列发车（POST /api/dispatch/start {taskId:<队列uid>, mode:AutoProxy}）。"
                     "队列名必须在 AUTO-MAS 存在（24 条班次队列走 mas_dispatch 的完整闸门；其它队列走按需通道）。"
                     "apply=false（默认）只做只读预检并回计划，绝不发车；apply=true 才真发。"
                     "单飞：同一队列同一天发过一次 ⇒ refused=duplicate_today（force=true 才能再发）；"
                     "资源冲突（同 MAA 目录/脚本/实例在 busy 窗口内重叠）⇒ refused=conflict_inflight"
                     "（force_conflict=true 显式放行并记账）；并发锁与 cron 调度器共用 ⇒ 不会双发。"
                     "每次调用（含 dry-run）写 runtime/mas_ondemand_log.jsonl 审计。"
                     "成功返回 taskId（= 停机用的 task_uid，交给 stop_task）。"),
        inputSchema={"type": "object",
                     "properties": {"name": {"type": "string", "description": "队列名（如 日常定时3 / 临时-补跑-0928_0100-1）"},
                                    "apply": {"type": "boolean", "default": False,
                                              "description": "false=只预检（默认）；true=真发车"},
                                    "force": {"type": "boolean", "default": False,
                                              "description": "忽略当天已发记录（重复发车，审计留痕）"},
                                    "force_conflict": {"type": "boolean", "default": False,
                                                       "description": "放行资源冲突拒绝（确认对方任务已结束再用）"},
                                    "note": {"type": "string", "description": "给审计的理由/来源"},
                                    "lease_owner": {"type": "string",
                                                    "description": "G3L 实例租约持有者（默认 MAAORCH_LEASE_OWNER / HERMES_KANBAN_TASK / ondemand:mcp）"}},
                     "required": ["name"], "additionalProperties": False},
    ),
    types.Tool(
        name="dispatch_script",
        description=("按脚本发车（POST /api/dispatch/start {taskId:<脚本uid>, mode:AutoProxy}）。"
                     "注意 AutoProxy 语义：会跑该脚本**全部启用用户**（不是单个号；单号走 dispatch_account）。"
                     "apply=false（默认）只预检；单飞/冲突/并发锁/审计口径与 dispatch_queue 相同。"
                     "脚本名必须与 AUTO-MAS 脚本名完全一致（同名多条会拒绝，改用 dispatch_queue）。"
                     "成功返回 taskId（停机用 stop_task）。"),
        inputSchema={"type": "object",
                     "properties": {"name": {"type": "string", "description": "脚本名（如 明日方舟-官-1-1-7）"},
                                    "apply": {"type": "boolean", "default": False},
                                    "force": {"type": "boolean", "default": False},
                                    "force_conflict": {"type": "boolean", "default": False},
                                    "note": {"type": "string"},
                                    "lease_owner": {"type": "string",
                                                    "description": "G3L 实例租约持有者（默认 MAAORCH_LEASE_OWNER / HERMES_KANBAN_TASK / ondemand:mcp）"}},
                     "required": ["name"], "additionalProperties": False},
    ),
    types.Tool(
        name="dispatch_account",
        description=("按单号发车：账号名 → 所属脚本（MAA 目录/实例）→ 内部建「临时-按需-*」队列（单脚本项）"
                     "→ AutoProxy 发车（单号没有原生入口，临时队列是既有口径，见 tools/auto_mas_rerun.py）。"
                     "护栏：账号必须在 AUTO-MAS 且已启用（未启用/找不到 ⇒ 拒绝）；目标 MAA 目录有 MAA.exe 在跑"
                     "⇒ refused=dir_busy（同目录并行=互杀，等收工）；守卫窗 05:00–05:40 / 19:20–20:20 内默认拒绝"
                     "（ignore_window=true 显式放行）；当天同一账号只发一次（force=true 再发）。"
                     "apply=false（默认）只回计划（含目录忙/模拟器状态/重复/冲突判定），不建队列不发车。"
                     "注意：该脚本全部启用用户会被跑到（AutoProxy 语义）。成功返回 taskId + 临时队列名。"),
        inputSchema={"type": "object",
                     "properties": {"name": {"type": "string", "description": "AUTO-MAS 用户昵称（账号名，需完全一致）"},
                                    "apply": {"type": "boolean", "default": False},
                                    "force": {"type": "boolean", "default": False},
                                    "ignore_window": {"type": "boolean", "default": False,
                                                      "description": "放行班次守卫窗（默认窗内拒发）"},
                                    "note": {"type": "string"},
                                    "lease_owner": {"type": "string",
                                                    "description": "G3L 实例租约持有者（默认 MAAORCH_LEASE_OWNER / HERMES_KANBAN_TASK / ondemand:mcp）"}},
                     "required": ["name"], "additionalProperties": False},
    ),
    types.Tool(
        name="stop_task",
        description=("停任务（POST /api/dispatch/stop {taskId:<task_uid>}）。**只认 dispatch/start 返回的 "
                     "task_uid**：传队列/脚本 uid 上游会静默当成功（no-op），所以这里显式拦下；不是合法 UUID、"
                     "或两个发车台账都没有这个 uid（fail-closed）都会拒绝（后者确认无误用 force=true）。"
                     "apply=false（默认）只回计划；apply=true 才真停。走官方 stop 才会 解锁配置 + Temp→config "
                     "还原；**绝不 kill MAA**。上游对已结束任务的 stop 也回成功。"),
        inputSchema={"type": "object",
                     "properties": {"task_id": {"type": "string", "description": "start 返回的 task_uid（UUID）"},
                                    "apply": {"type": "boolean", "default": False},
                                    "force": {"type": "boolean", "default": False,
                                              "description": "台账里找不到这个 task_uid 时仍放行（审计留痕）"},
                                    "reason": {"type": "string", "description": "为什么要停（进审计）"}},
                     "required": ["task_id"], "additionalProperties": False},
    ),
    types.Tool(
        name="ondemand_preflight",
        description=("发车前 GO/NO-GO 自检（复用 tools/mas_preflight.py，只读）：REST 可达 / 目标槽位空闲 / "
                     "模拟器可连 / 残留模态（截图+OCR，可跳）/ 注入闸门 / 磁盘水位。target 可给队列名或脚本名"
                     "（kind=auto 自动判定）；skip_screen=true（默认）跳过截图+OCR，约 30s；false 为全量（+20~40s，"
                     "需 rapidocr）。返回 {preflight:GO|NO-GO|UNKNOWN, checks{status,detail}, failing_checks, reasons}。"
                     "PREFLIGHT=GO 才发车；UNKNOWN（目标解析失败/超时）按 NO-GO 对待。只读：不启动/不关闭实例、"
                     "不发车。"),
        inputSchema={"type": "object",
                     "properties": {"target": {"type": "string", "description": "队列名或脚本名"},
                                    "kind": {"type": "string", "enum": ["auto", "queue", "script"], "default": "auto"},
                                    "skip_screen": {"type": "boolean", "default": True},
                                    "timeout": {"type": "integer", "description": "单次远端命令超时（秒），默认 120（全量 300）"},
                                    "min_free_gb": {"type": "number", "default": 10},
                                    "warn_free_gb": {"type": "number", "default": 30}},
                     "required": ["target"], "additionalProperties": False},
    ),
]

_DISPATCH = {
    "list_running": tool_list_running,
    "dispatch_queue": tool_dispatch_queue,
    "dispatch_script": tool_dispatch_script,
    "dispatch_account": tool_dispatch_account,
    "stop_task": tool_stop_task,
    "ondemand_preflight": tool_preflight,
}

_ALLOWED = set(_DISPATCH)


def _text(payload) -> list:
    return [types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=1))]


def _call(name: str, args: dict):
    fn = _DISPATCH.get(name)
    if fn is None:
        return {"ok": False, "refused": "unknown_tool", "tool": name, "allowed": sorted(_ALLOWED)}
    try:
        return fn(args or {})
    except Exception as e:  # noqa: BLE001  任何异常都收成结构化错误（server 不崩）
        _audit({"tool": name, "verdict": "exception", "detail": "%s: %s" % (type(e).__name__, e)})
        return {"ok": False, "tool": name, "refused": "internal_error",
                "error": {"kind": type(e).__name__, "detail": str(e)[:400]}}


async def _main() -> None:
    _fix_process_env()
    server = Server(SERVER_NAME)

    @server.list_tools()
    async def _list() -> list:
        return _SCHEMAS

    @server.call_tool()
    async def _call_tool(tool_name: str, arguments: dict | None):
        return _text(_call(tool_name, arguments or {}))

    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


# --------------------------------------------------------------------------- #
# 自检 / 离线自测                                                               #
# --------------------------------------------------------------------------- #

def _check() -> int:
    _fix_process_env()
    problems, warnings = [], []
    if set(t.name for t in _SCHEMAS) != _ALLOWED:
        problems.append("schema 与 dispatch 表不一致: %s vs %s" % (sorted(t.name for t in _SCHEMAS), sorted(_ALLOWED)))
    if _no_go():
        warnings.append("警告：NO_GO 已置位（MAAORCH_DISPATCH_NO_GO / MAAORCH_ONDEMAND_NO_GO），真发车会被拒")
    gw = _in_guard_window()
    if gw:
        warnings.append("提示：当前在班次守卫窗 %s（dispatch_account 默认拒发）" % gw)
    try:
        reach, why = md.reachable()
    except Exception as e:  # noqa: BLE001
        reach, why = False, "%s: %s" % (type(e).__name__, e)
    if not reach:
        problems.append("AUTO-MAS 不可达: %s" % why)
    nq = len(md.queue_map()) if reach else 0
    ns = len(_script_map()) if reach else 0
    if reach and not nq:
        problems.append("队列表为空（异常）")
    if reach and not ns:
        problems.append("脚本表为空（异常）")
    try:
        st = _state_load()
        n_od = sum(1 for k, v in st.items() if re.match(r"^\d{4}-\d{2}-\d{2}$", str(k)) for _ in v)
    except Exception as e:  # noqa: BLE001
        problems.append("ondemand state 读取失败: %s" % e)
        n_od = -1
    probe = {"reachable": reach, "rest_detail": why, "queues": nq, "scripts": ns,
             "ondemand_records": n_od, "ledger": ledger.ledger_path(), "log": LOG, "log_events": LOG_EVENTS,
             "guard_window": gw, "no_go": _no_go()}
    a = {"name": "日常定时1", "apply": False}
    plan = tool_dispatch_queue(a) if reach else {"skipped": True}
    print("mas_ondemand_mcp --check: %s" % ("FAIL" if problems else "OK"))
    print("  tools=%s" % ",".join(t.name for t in _SCHEMAS))
    print("  %s" % json.dumps(probe, ensure_ascii=False))
    print("  dry-run dispatch_queue(日常定时1)=%s" % json.dumps(
        {k: plan.get(k) for k in ("ok", "dry_run", "refused", "summary", "would_dispatch", "target_uid")},
        ensure_ascii=False))
    for p in problems:
        print("  - " + p)
    for w in warnings:
        print("  - " + w)
    return 2 if problems else 0


def selftest() -> dict:
    """离线回归（假传输；不碰网络、不碰真 runtime）。"""
    import shutil
    import tempfile

    cases = []

    def add(name, fn):
        try:
            fn()
            cases.append({"name": name, "ok": True, "detail": ""})
        except Exception as e:  # noqa: BLE001
            cases.append({"name": name, "ok": False, "detail": "%s: %s" % (type(e).__name__, e)})

    tmp = tempfile.mkdtemp(prefix="mas_ondemand_selftest_")
    LEASE_RT = os.path.join(tmp, "lease")                     # P1-2：租约账本沙箱（绝不碰 runtime/）
    lease_env_saved = os.environ.get("MAAORCH_LEASE_RUNTIME")
    saved = {k: globals()[k] for k in ("RUNTIME", "STATE", "LOG", "LOG_EVENTS", "ONDEMAND_NO_GO",
                                       "GUARD_WINDOWS", "_remote_busy")}
    saved_ledger_runtime = ledger.RUNTIME
    saved_md = {"post": md.post, "reachable": md.reachable, "NO_GO": md.NO_GO, "queue_map": md.queue_map,
                "load_state": md.load_state, "resources_of": md.resources_of,
                "find_conflicts": md.find_conflicts, "LOCK": md.LOCK}
    calls = {"post": [], "lock": 0}

    def set_runtime():
        globals()["RUNTIME"] = tmp
        globals()["STATE"] = os.path.join(tmp, "mas_ondemand_ledger.json")
        globals()["LOG"] = os.path.join(tmp, "log.jsonl")
        globals()["LOG_EVENTS"] = os.path.join(tmp, "mas_dispatch_log.jsonl")
        ledger.RUNTIME = tmp      # 共享账本也指向自测沙箱
        os.environ["MAAORCH_LEASE_RUNTIME"] = LEASE_RT   # P1-2：租约账本同进沙箱
        if _lease() is not None:
            with contextlib.suppress(Exception):
                _lease().set_runtime(LEASE_RT)

    MAADIR = r"E:\MAA-Multi\MAA_6"
    SCRIPTS = {"sid-1": {"Info": {"Name": "明日方舟-官-1-1-7", "Path": MAADIR}, "Emulator": {"Index": 6}},
               "sid-2": {"Info": {"Name": "明日方舟-官-38", "Path": r"E:\MAA-Multi\MAA_7"}, "Emulator": {"Index": 7}}}
    USERS = {"sid-1": {"u1": {"Info": {"Name": "甲号", "Status": True, "RemainedDay": -1}},
                       "u2": {"Info": {"Name": "乙号", "Status": False}}},
             "sid-2": {"u1": {"Info": {"Name": "丙号", "Status": True, "RemainedDay": -1}}}}
    QUID = {"q1": "aaaaaaaa-1111-2222-3333-444444444444", "q2": "bbbbbbbb-1111-2222-3333-444444444444",
            "q3": "cccccccc-1111-2222-3333-444444444444"}
    QUEUES = {QUID["q1"]: {"Info": {"Name": "日常定时1"}}, QUID["q2"]: {"Info": {"Name": "临时-补跑-0928-1"}},
              QUID["q3"]: {"Info": {"Name": "临时-按需-0928"}}}

    def script(reach=True, resp=None):
        def _reach():
            return (reach, "code=200" if reach else "URLError: refused")

        def _post(path, body=None, timeout=30):
            calls["post"].append({"path": path, "body": body})
            if path == "/api/scripts/get":
                return {"code": 200, "data": SCRIPTS}
            if path == "/api/scripts/user/get":
                sid = (body or {}).get("scriptId")
                return {"code": 200, "data": dict(USERS.get(sid, {}), instances=[])}
            if path == "/api/queue/get":
                return {"code": 200, "data": QUEUES}
            if path in ("/api/queue/add",):
                return {"code": 200, "queueId": "tmp-qid"}
            if path in ("/api/queue/update", "/api/queue/item/update"):
                return {"code": 200, "status": "success", "message": "操作成功"}
            if path == "/api/queue/item/add":
                return {"code": 200, "queueItemId": "item-1"}
            if path == "/api/dispatch/start":
                return resp if resp is not None else {"code": 200, "status": "success",
                                                      "message": "操作成功", "taskId": "11111111-2222-3333-4444-555555555555"}
            if path == "/api/dispatch/stop":
                return {"code": 200, "status": "success", "message": "操作成功"}
            return {"code": 200, "data": {}}

        globals()["_attempt_start"] = _attempt_start
        md.post = _post
        md.reachable = _reach
        md.queue_map = lambda: {v["Info"]["Name"]: k for k, v in QUEUES.items()}
        md.load_state = lambda: {}
        md.resources_of = lambda name, snap=None: {"dirs": [MAADIR.lower()], "scripts": ["sid-1"], "emus": ["6"],
                                                  "names": ["明日方舟-官-1-1-7"]}
        md.find_conflicts = lambda label, res, days, now, **kw: []
        md.LOCK = os.path.join(tmp, "mas_dispatch.lock")
        md.NO_GO = False
        globals()["_remote_busy"] = lambda timeout=180: {"dirs": {}, "vmm": {"6": "1"}, "error": ""}

    def reset():
        set_runtime()
        for f in ("mas_ondemand_ledger.json", "mas_ondemand_ledger.json.tmp", "log.jsonl",
                  "mas_dispatch_log.jsonl", "state.json", "state.json.tmp",
                  "mas_ondemand_state.json", "mas_ondemand_mcp_state.json", "alerts.json",
                  "mas_dispatch.lock"):
            with contextlib.suppress(OSError):
                os.remove(os.path.join(tmp, f))
        for f in ("instance_leases.json", "instance_leases.json.tmp", "instance_leases.lock"):
            with contextlib.suppress(OSError):
                os.remove(os.path.join(LEASE_RT, f))     # P1-2：租约沙箱逐例清空
        globals()["ONDEMAND_NO_GO"] = False
        globals()["GUARD_WINDOWS"] = (("05:00", "05:40"), ("19:20", "20:20"))
        script()
        calls["post"] = []

    def check(cond, msg):
        if not cond:
            raise AssertionError(msg)

    # ---- P1-2 G3L 实例租约（跨族单飞；与 CLI 版共用同一本账）----------------
    def lease_lib():
        g = _lease()
        check(g is not None, "租约胶水应可用（tools/mas_lease_glue.py）")
        return g

    def lease_state():
        return lease_lib().mod().load()

    def lease_idxs():
        return [str(i) for i in _lease_idxs({"emus": ["6"]})]

    def wrote_upstream():
        return [c["path"] for c in calls["post"]
                if c["path"] in ("/api/dispatch/start", "/api/queue/add", "/api/queue/update",
                                 "/api/queue/item/add", "/api/queue/item/update")]

    def c_lease_busy_refused_zero_write():
        reset()
        LG = lease_lib()
        pre = LG.acquire_all(lease_idxs(), "t_mcp_holder", "别族的活（自测替身）", ttl_s=300, src="selftest")
        check(pre.get("ok"), "前置：替身应先拿到租约：%r" % pre)
        try:
            r = tool_dispatch_script({"name": "明日方舟-官-1-1-7", "apply": True, "lease_owner": "t_mcp_self"})
            check(r.get("refused") == "lease_busy", "被占 ⇒ refused=lease_busy：%r" % r)
            check(not wrote_upstream(), "拒发必须零上游写：%r" % wrote_upstream())
            check(any(x.get("verdict") == "lease_busy" for x in _audit_read()), "审计应记 lease_busy")
            r2 = tool_dispatch_script({"name": "明日方舟-官-1-1-7", "lease_owner": "t_mcp_self"})
            check(r2.get("refused") == "lease_busy", "dry-run 同判（计划不成立就拒）：%r" % r2)
        finally:
            LG.release_all(lease_idxs(), owner_id="t_mcp_holder")
        r3 = tool_dispatch_script({"name": "明日方舟-官-1-1-7", "apply": True, "lease_owner": "t_mcp_self"})
        check(r3.get("dispatched"), "空窗后应能发车：%r" % r3)

    def c_lease_kept_then_stop_releases():
        reset()
        r = tool_dispatch_script({"name": "明日方舟-官-1-1-7", "apply": True, "lease_owner": "t_mcp_self"})
        check(r.get("dispatched"), "apply 应发车成功：%r" % r)
        tl = r.get("lease") or {}
        check(tl.get("status") == "acquired", "结果应带租约：%r" % tl)
        st = lease_state()
        check(len(st.get("leases") or {}) == 1, "应在租一台：%r" % st.get("leases"))
        ent = list((st.get("leases") or {}).values())[0]
        check(ent.get("owner") == "t_mcp_self", "持有者应是本车：%r" % ent)
        tid = r.get("taskId")
        check(tid, "应回 taskId")
        r2 = tool_stop_task({"task_id": tid, "apply": True})
        check(r2.get("stopped"), "停任务应成功：%r" % r2)
        check(not (lease_state().get("leases") or {}), "停成功后租约必须空：%r" % lease_state())

    def c_tools_whitelist():
        names = [t.name for t in _SCHEMAS]
        check(names == ["list_running", "dispatch_queue", "dispatch_script", "dispatch_account",
                        "stop_task", "ondemand_preflight"], "工具面变了: %s" % names)
        check(set(names) == _ALLOWED, "dispatch 表与 schema 不一致")

    def c_dry_no_post():
        reset()
        r = tool_dispatch_script({"name": "明日方舟-官-1-1-7"})
        check(r.get("dry_run") and r.get("ok"), "dry-run 应回计划: %r" % r)
        check(not any(c["path"] == "/api/dispatch/start" for c in calls["post"]), "dry-run 不得发车")
        check(any(x.get("verdict") == "plan" for x in _audit_read()), "dry-run 也要写审计")

    def c_script_dispatch_ok():
        reset()
        r = tool_dispatch_script({"name": "明日方舟-官-1-1-7", "apply": True})
        check(r.get("dispatched") and r.get("taskId"), "应发车成功: %r" % r)
        check(any(c["path"] == "/api/dispatch/start" and c["body"]["mode"] == "AutoProxy"
                  for c in calls["post"]), "应 POST dispatch/start AutoProxy")
        st = _state_load()
        ent = (st.get(_today()) or {}).get("s:sid-1") or {}
        check((ent.get("task_uid") or ent.get("taskId")) == r["taskId"], "账本未落: %r" % st)
        check(ent.get("src") == "mcp", "账本 src 应为 mcp: %r" % ent)

    def c_duplicate_refused():
        reset()
        tool_dispatch_script({"name": "明日方舟-官-1-1-7", "apply": True})
        n1 = len([c for c in calls["post"] if c["path"] == "/api/dispatch/start"])
        r2 = tool_dispatch_script({"name": "明日方舟-官-1-1-7", "apply": True})
        check(r2.get("refused") == "duplicate_today", "重复应拒: %r" % r2)
        n2 = len([c for c in calls["post"] if c["path"] == "/api/dispatch/start"])
        check(n1 == n2, "重复拒绝不得再发车")
        r3 = tool_dispatch_script({"name": "明日方舟-官-1-1-7", "apply": True, "force": True})
        check(r3.get("dispatched"), "force=true 应放行: %r" % r3)

    def c_no_go():
        reset()
        globals()["ONDEMAND_NO_GO"] = True
        r = tool_dispatch_script({"name": "明日方舟-官-1-1-7", "apply": True})
        check(r.get("refused") == "dispatch_disabled", "NO_GO 应拒: %r" % r)
        check(not any(c["path"] == "/api/dispatch/start" for c in calls["post"]), "NO_GO 不得发车")

    def c_unknown_targets():
        reset()
        r = tool_dispatch_script({"name": "不存在的脚本"})
        check(r.get("refused") == "unknown_script", "未知脚本应拒: %r" % r)
        r2 = tool_dispatch_queue({"name": "不存在的队列"})
        check(r2.get("refused") == "unknown_queue", "未知队列应拒: %r" % r2)
        r3 = tool_dispatch_account({"name": "不存在的人"})
        check(r3.get("refused") == "unknown_account", "未知账号应拒: %r" % r3)

    def c_conflict_refused():
        reset()
        md.find_conflicts = lambda label, res, days, now, **kw: [
            {"queue": "晚班定时1", "dispatched_at": _now(), "busy_until": _now(),
             "shared": {"dirs": [MAADIR.lower()], "scripts": ["sid-1"], "emus": ["6"]}}]
        r = tool_dispatch_script({"name": "明日方舟-官-1-1-7", "apply": True})
        check(r.get("refused") == "conflict_inflight", "冲突应拒: %r" % r)
        check(not any(c["path"] == "/api/dispatch/start" for c in calls["post"]), "冲突拒绝不得发车")
        r2 = tool_dispatch_script({"name": "明日方舟-官-1-1-7", "apply": True, "force_conflict": True})
        check(r2.get("dispatched") and r2.get("conflict_overridden") == ["晚班定时1"], "放行应记账: %r" % r2)

    def c_queue_shift_delegated():
        reset()
        r = tool_dispatch_queue({"name": "日常定时1", "apply": False})
        check(r.get("via", "").startswith("mas_dispatch"), "班次队列应委托: %r" % r)
        check(r.get("dry_run") or r.get("ok"), "dry-run 应成功: %r" % r)

    def c_queue_ondemand_path():
        reset()
        r = tool_dispatch_queue({"name": "临时补跑x", "apply": False})
        check(r.get("refused") == "unknown_queue", "未知临时队列应拒: %r" % r)
        QUEUES["q-4"] = {"Info": {"Name": "临时-在建-1"}}
        try:
            r2 = tool_dispatch_queue({"name": "临时-在建-1", "apply": True})
            check(r2.get("dispatched") and r2.get("taskId"), "非班次队列应走按需通道: %r" % r2)
        finally:
            QUEUES.pop("q-4", None)

    def c_account_flow():
        reset()
        r = tool_dispatch_account({"name": "甲号", "apply": False})
        check(r.get("ok") and r.get("dry_run"), "账号 dry-run 应回计划: %r" % r)
        check(r.get("dir_busy") is False and r.get("emulator_running") is True, "计划字段不对: %r" % r)
        calls["post"] = []
        r2 = tool_dispatch_account({"name": "甲号", "apply": True})
        check(r2.get("dispatched") and r2.get("queue", "").startswith(TMPQ_TAG), "应发车并建临时队列: %r" % r2)
        sent = [c for c in calls["post"] if c["path"] == "/api/dispatch/start"]
        check(len(sent) == 1 and sent[0]["body"]["taskId"] == "tmp-qid", "应发临时队列: %r" % sent)
        check(any(c["path"] == "/api/queue/item/update" and c["body"]["data"]["Info"]["ScriptId"] == "sid-1"
                  for c in calls["post"]), "队列项应指向脚本")

    def c_account_guards():
        reset()
        r = tool_dispatch_account({"name": "乙号", "apply": False})
        check(r.get("refused") == "account_disabled", "未启用账号应拒: %r" % r)
        globals()["_remote_busy"] = lambda timeout=180: {"dirs": {MAADIR.lower(): ["1"]}, "vmm": {"6": "1"}, "error": ""}
        r2 = tool_dispatch_account({"name": "甲号", "apply": True})
        check(r2.get("refused") == "dir_busy", "目录忙应拒: %r" % r2)
        globals()["_remote_busy"] = lambda timeout=180: {"dirs": {}, "vmm": {"6": "1"}, "error": ""}
        globals()["GUARD_WINDOWS"] = (("00:00", "23:59"),)
        try:
            r3 = tool_dispatch_account({"name": "甲号", "apply": True})
            check(r3.get("refused") == "guard_window", "守卫窗应拒: %r" % r3)
            r4 = tool_dispatch_account({"name": "甲号", "apply": False, "ignore_window": True})
            check(r4.get("ok"), "ignore_window 应放行（dry）: %r" % r4)
        finally:
            globals()["GUARD_WINDOWS"] = (("05:00", "05:40"), ("19:20", "20:20"))

    def c_stop_guards():
        reset()
        r = tool_stop_task({"task_id": "ALL", "apply": True})
        check(r.get("refused") == "refuse_all", "ALL 应拒: %r" % r)
        r2 = tool_stop_task({"task_id": QUID["q2"]})
        check(r2.get("refused") == "is_queue_uid", "队列 uid 应拒: %r" % r2)
        r3 = tool_stop_task({"task_id": "not-a-uuid"})
        check(r3.get("refused") == "not_task_uid", "非 UUID 应拒: %r" % r3)
        r4 = tool_stop_task({"task_id": "11111111-2222-3333-4444-555555555555", "apply": True})
        check(r4.get("refused") == "unknown_task_uid", "台账外应 fail-closed: %r" % r4)
        r5 = tool_stop_task({"task_id": "11111111-2222-3333-4444-555555555555", "apply": True, "force": True})
        check(r5.get("stopped"), "force 应放行: %r" % r5)

    def c_stop_known_flow():
        reset()
        tool_dispatch_script({"name": "明日方舟-官-1-1-7", "apply": True})
        tid = ((_state_load().get(_today()) or {}).get("s:sid-1") or {})
        tid = tid.get("task_uid") or tid.get("taskId")
        check(tid, "账本应有 task_uid")
        r = tool_stop_task({"task_id": tid, "apply": False})
        check(r.get("dry_run") and r.get("known_as"), "dry 应回 known_as: %r" % r)
        calls["post"] = []
        r2 = tool_stop_task({"task_id": tid, "apply": True, "reason": "自测"})
        check(r2.get("stopped"), "应停成功: %r" % r2)
        check(any(c["path"] == "/api/dispatch/stop" and c["body"]["taskId"] == tid for c in calls["post"]),
              "应 POST dispatch/stop")
        rec = (_state_load().get(_today()) or {}).get("s:sid-1") or {}
        check(rec.get("stopped_at"), "state 应记 stopped_at: %r" % rec)

    def c_preflight_passthrough():
        reset()
        real_run = subprocess.run

        class _R:
            returncode = 0
            stdout = json.dumps({"preflight": "GO", "checks": {"rest": {"status": "PASS", "detail": "code=200"}},
                                 "failing_checks": [], "reasons": [],
                                 "target": {"kind": "queue", "name": "日常定时1", "dirs": [], "emus": []}},
                                ensure_ascii=False).encode("utf-8")
            stderr = b""

        def _fake_run(argv, **kw):
            calls["post"].append({"argv": argv, "kw": {k: v for k, v in kw.items() if k != "env"}})
            return _R()

        globals()["subprocess"].run = _fake_run
        try:
            r = tool_preflight({"target": "日常定时1"})
            check(r.get("preflight") == "GO" and r.get("target_kind") == "queue", "preflight 应透传: %r" % r)
            argv = calls["post"][0]["argv"]
            check("--queue" in argv and "--skip-screen" in argv, "argv 不对: %r" % argv)
            r2 = tool_preflight({"target": "不存在"})
            check(r2.get("refused") == "unknown_target", "未知目标应拒: %r" % r2)
            r3 = tool_preflight({"target": "明日方舟-官-1-1-7", "kind": "script"})
            check(r3.get("preflight") == "GO", "脚本面应透传: %r" % r3)
        finally:
            globals()["subprocess"].run = real_run

    def _audit_read():
        try:
            with open(os.path.join(tmp, "log.jsonl"), "r", encoding="utf-8") as f:
                return [json.loads(x) for x in f if x.strip()]
        except OSError:
            return []

    def _events_read():
        try:
            with open(os.path.join(tmp, "mas_dispatch_log.jsonl"), "r", encoding="utf-8") as f:
                return [json.loads(x) for x in f if x.strip()]
        except OSError:
            return []

    def c_audit_lines():
        reset()
        tool_dispatch_script({"name": "明日方舟-官-1-1-7"})
        tool_dispatch_script({"name": "明日方舟-官-1-1-7", "apply": True})
        lines = _audit_read()
        check(len(lines) >= 2, "审计应有 plan+dispatched 两行: %r" % lines)
        check(any(x.get("verdict") == "plan" for x in lines) and
              any(x.get("verdict") == "dispatched" for x in lines), "审计 verdict 不对: %r" % lines)

    # ---- 共享账本 / 跨端互见 / 共锁（t_4ddb3728） -----------------------------
    def c_ledger_sees_cli_and_cross_dedup():
        """CLI 端发的车：MCP ① list_running 可见；② 同日同目标再发被拒（跨端单飞）。"""
        reset()
        st = _state_load()
        ledger.record(st, "script", "明日方舟-官-1-1-7", "sid-1",
                      "dddd1111-2222-3333-4444-555555555555", "cli", key="s:sid-1", hold_min=30,
                      resources={"dirs": [MAADIR.lower()], "scripts": ["sid-1"], "emus": ["6"]})
        ledger.save(st)
        r = tool_list_running({})
        rows = [b for b in (r.get("ondemand_busy") or []) if b.get("target") == "明日方舟-官-1-1-7"]
        check(rows and rows[0].get("src") == "cli", "MCP 未看到 CLI 发的车: %r" % r.get("ondemand_busy"))
        check((r.get("ledger") or "").endswith("mas_ondemand_ledger.json"), "list_running 未带共享账本路径")
        r2 = tool_dispatch_script({"name": "明日方舟-官-1-1-7", "apply": True})
        check(r2.get("refused") == "duplicate_today", "跨端同目标同日再发应拒: %r" % r2)
        check(not any(c["path"] == "/api/dispatch/start" for c in calls["post"]), "跨端拒绝不得发车")
        r3 = tool_dispatch_script({"name": "明日方舟-官-1-1-7", "apply": True, "force": True})
        check(r3.get("dispatched"), "force=true 应放行: %r" % r3)

    def c_audit_event_stream():
        """真动作 → 事件流（班次同源）；dry-run/被拒只进调用流。"""
        reset()
        tool_dispatch_script({"name": "明日方舟-官-1-1-7"})                       # dry-run
        check(_events_read() == [], "dry-run 不得写事件流: %r" % _events_read())
        tool_dispatch_script({"name": "明日方舟-官-1-1-7", "apply": True})       # 真发车
        ev = _events_read()
        check(len(ev) == 1 and ev[0].get("kind") == "dispatch_ok" and ev[0].get("src") == "mcp"
              and ev[0].get("source") == "ondemand" and ev[0].get("taskId"), "事件流行不对: %r" % ev)
        check(len(_audit_read()) >= 2, "调用流应含 plan+dispatched: %r" % _audit_read())

    def c_shared_lock_with_cron():
        """与 cron/MCP 共锁（mas_dispatch.lock）：锁被占 ⇒ concurrent_dispatch、零发车 POST。"""
        reset()
        check(md.LOCK == os.path.join(tmp, "mas_dispatch.lock"), "锁路径未指向共享 mas_dispatch.lock")
        with md._dispatch_lock():
            r = tool_dispatch_script({"name": "明日方舟-官-1-1-7", "apply": True})
        check(r.get("refused") == "concurrent_dispatch", "锁被占应拒: %r" % r)
        check(not any(c["path"] == "/api/dispatch/start" for c in calls["post"]), "锁被占不得发车")

    add("tools_whitelist_6", c_tools_whitelist)
    add("dry_run_no_post_audited", c_dry_no_post)
    add("script_dispatch_ok", c_script_dispatch_ok)
    add("duplicate_refused_then_force", c_duplicate_refused)
    add("no_go_refuse", c_no_go)
    add("unknown_targets_refused", c_unknown_targets)
    add("conflict_refused_then_force_conflict", c_conflict_refused)
    add("queue_shift_delegated", c_queue_shift_delegated)
    add("queue_ondemand_path", c_queue_ondemand_path)
    add("account_temp_queue_flow", c_account_flow)
    add("account_guards_dir_busy_window", c_account_guards)
    add("stop_task_five_guards", c_stop_guards)
    add("stop_known_flow_marks_state", c_stop_known_flow)
    add("preflight_argv_passthrough", c_preflight_passthrough)
    add("audit_lines_plan_and_dispatched", c_audit_lines)
    add("ledger_sees_cli_and_cross_dedup", c_ledger_sees_cli_and_cross_dedup)
    add("audit_event_stream_split", c_audit_event_stream)
    add("shared_lock_blocks_mcp_apply", c_shared_lock_with_cron)
    add("lease_busy_refused_zero_write", c_lease_busy_refused_zero_write)
    add("lease_kept_then_stop_releases", c_lease_kept_then_stop_releases)

    globals().update(saved)
    ledger.RUNTIME = saved_ledger_runtime
    # P1-2：租约沙箱收尾（环境还原 + 账本路径回默认）——自测绝不把租约留在 runtime/
    if lease_env_saved is None:
        os.environ.pop("MAAORCH_LEASE_RUNTIME", None)
    else:
        os.environ["MAAORCH_LEASE_RUNTIME"] = lease_env_saved
    if _lease() is not None:
        with contextlib.suppress(Exception):
            _lease().set_runtime(None)
    md.post = saved_md["post"]
    md.reachable = saved_md["reachable"]
    md.NO_GO = saved_md["NO_GO"]
    md.queue_map = saved_md["queue_map"]
    md.load_state = saved_md["load_state"]
    md.resources_of = saved_md["resources_of"]
    md.find_conflicts = saved_md["find_conflicts"]
    md.LOCK = saved_md["LOCK"]
    shutil.rmtree(tmp, ignore_errors=True)

    failed = [c for c in cases if not c["ok"]]
    return {"ok": not failed, "passed": len(cases) - len(failed), "failed": len(failed), "cases": cases}


def _cmd_selftest() -> int:
    rep = selftest()
    print("mas_ondemand_mcp --selftest: %s (%d/%d)" % (
        "OK" if rep["ok"] else "FAIL", rep["passed"], rep["passed"] + rep["failed"]))
    for c in rep["cases"]:
        if not c["ok"]:
            print("  - %s: %s" % (c["name"], c["detail"]))
    return 0 if rep["ok"] else 2


if __name__ == "__main__":
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    if "--check" in sys.argv:
        sys.exit(_check())
    if "--selftest" in sys.argv:
        sys.exit(_cmd_selftest())
    asyncio.run(_main())
