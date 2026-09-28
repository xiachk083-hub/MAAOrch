#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""按需发车（AUTO-MAS）—— 一句话发车：队列 / 脚本 / 单号 / 唤醒 / 停任务 / 看在跑。

定位：把「临时补跑某号、给某脚本加跑一趟、把某实例让给活动/肉鸽、唤醒了看一眼」
做成窄面命令，任何 Hermes 会话/巡检/看板 worker 都能直接调，不用手搓 REST 或临时队列。

设计依据（源码 D:\MAS，2026-09-28 核对）：
  * `app/core/task_manager.py:287-316`：`/api/dispatch/start` 的 `taskId` 只吃
    **队列 uid**（QueueConfig）或 **脚本 uid**（ScriptConfig）；**不吃用户 uid**
    ⇒ 「按单号发」必须包一层临时队列（脚本项 = 该号所在脚本）。
  * ScriptConfig 模式同样吃 脚本uid/用户uid，但**只装配配置不跑链**（本工具不用它）。
  * `ManualReview`（人工排查）跑的是 StartUp 唤醒链，且**唤醒后会等客户端回答**
    「请检查用户代理情况…」（`app/task/MAA/ManualReview.py:237-261`）——没有客户端
    在线会一直挂着、脚本锁不释放 ⇒ 本工具对 `--wake` 的 apply 额外要 `--force`。

用法（**默认 dry-run，只读规划；真发车/真删除要 --apply**）：
  python tools/mas_ondemand.py --queue 日常定时3                     # 按队列发（AutoProxy）
  python tools/mas_ondemand.py --script 明日方舟-官-1-1-7            # 按脚本发（该脚本全部启用号）
  python tools/mas_ondemand.py --account 舒7ucky                     # 按单号发（自动建临时队列）
  python tools/mas_ondemand.py --account "号A,号B" --apply           # 真发（同目录号自动并成一条临时队列）
  python tools/mas_ondemand.py --wake 明日方舟-官-1-1-7              # ManualReview 只唤醒（不改配置、不跑链）
  python tools/mas_ondemand.py --stop <task_uid>                     # 停任务（必须是 start 返回的 task_uid）
  python tools/mas_ondemand.py --list-running                        # 只读：在跑的 MAA 槽位/任务/实例
  python tools/mas_ondemand.py --cleanup                             # 删掉本工具建的「临时-按需-*」队列
  python tools/mas_ondemand.py --release <目标|run_id> --apply       # 人工确认跑完后放行同目标重发
  python tools/mas_ondemand.py --selftest                            # 离线自测（假传输，不碰网络/runtime）
  任何命令可加 `--json` 输出单行机器可读结果；`--queue/--script/--account/--wake/--stop` 可加 `--apply`。

闸门（真发车前按顺序全过，任一不过 ⇒ 拒绝且**零上游写请求**）：
  G0 硬闸      `MAAORCH_DISPATCH_NO_GO=1` ⇒ 拒绝一切真发车/真删除（演练用）。
  G1 可达      REST 探活（POST /api/scripts/get code=200），不可达 fail-closed。
  G2 目标      队列名/uid、脚本名/uid、账号名必须唯一命中 AUTO-MAS；坏 uid ⇒ 拒绝并说明。
              账号侧附加：必须 Status=true 且 RemainedDay≠0（否则 AutoProxy 会跳过它）；
              一个账号名命中多个脚本 ⇒ 拒绝（不猜）。
  G3 单飞      (a) 同目标（队列/脚本/账号）在 DUP_WINDOW（默认 150min）内发过 ⇒ 拒绝；
              (b) 目标 MAA 目录/实例与「在跑的 MAA 进程」重叠 ⇒ 拒绝 slot_busy；
              (c) 与「刚发车还没起 MAA 进程」的在场记录（**共享账本** + 班次 mas_dispatch
                  state）在 CONFLICT_GRACE（默认 20min）内资源重叠 ⇒ 拒绝（含 MCP 端发的车）。
              平静期只信现场进程（MAA.exe + VMM），不信长窗口账本 —— 否则班次跑完半天
              都发不了车。`--force` 显式放行（记账 force=true）。
  G3L 实例租约 目标实例（脚本 Emulator.Index）已被**别的任务/工具**租下（`runtime/instance_leases.json`；
              机制见 `tools/mas_instance_lease.py` + `docs/MAS_INSTANCE_LEASE.md`）⇒ 拒绝（rc=2、零上游写）。
              与 G3 分工：G3 看「本工具自己的在场记录/现场进程」，G3L 看「跨族（班次/实测/补跑/肉鸽）的
              实例占位」——**跨族撞同一实例只有租约看得见**。持有者口径：`--lease-owner` >
              `MAAORCH_LEASE_OWNER` > `HERMES_KANBAN_TASK`（看板 worker 天然是卡 id）> `ondemand:cli`；
              自己的租约不算被占（同主 = 续租）。`--force` **放行不了租约**（要抢先人工 release）。
              判据在 dry-run 也会跑（计划不成立就 rc=2，与 G3/G4 同口径）；拒发路径零上游写。
  G4 preflight `tools/mas_preflight.py`（REST/槽位空闲/实例可 adb/残留模态/注入合规/磁盘）
              必须 GO（exit 0）；NO-GO/无法判定一律拒绝。`--skip-preflight` 显式跳过（记账）。
  G5 回执      上游必须 code==200 且带 taskId，否则记 reason（沿 mas_dispatch 的分类口径）。

租约生命周期（异步发车，与在场窗口同寿）：G3L 过 ⇒ 真发车前 `acquire(ttl=HOLD_MIN+余量)`；
  任务在跑期间租约一直占着（**不发车即失败 ⇒ 立刻 release，不留空占**）；跑完用 `--release`（或
  `--stop` 成功）释放——TTL 只是被 kill 时的兜底。租约明细（owner/idxs/到期）落在在场记录里，
  也落审计：`lease_acquire` / `lease_release`（按需调用流）。

并发与审计（t_4ddb3728 起与 MCP 版同一份账、同一把锁）：
  * 真发车临界区持 `runtime/mas_dispatch.lock` —— **与 cron 调度器、MCP 版同源**（复用
    `tools/mas_dispatch.py` 的锁协议与陈旧阈值 `MAAORCH_DISPATCH_LOCK_STALE_S`），
    `--stop/--cleanup` 共用同一把 ⇒ 任何一路在发车时另一路会得到 concurrent_write。
  * 审计分两流（CLI/MCP 两端统一口径）：
      - 全量调用行（含干跑/被拒）→ `runtime/mas_ondemand_log.jsonl`（本工具行带 `src:"cli"`）；
      - 真动作事件行（dispatch_ok/failed、temp_queue_created、stop_*、cleanup_*、release）→
        `runtime/mas_dispatch_log.jsonl`（带 `source:"ondemand"`），与班次事件同一条流。
    干跑/被拒**不再**写事件流 —— 既有读者拿行数当发车计数不会被污染。
  * 在场记录 + 临时队列账：`runtime/mas_ondemand_ledger.json`（**共享账本**，MCP 版同读同写；
    首次读入会把旧账 `mas_ondemand_state.json` / `mas_ondemand_mcp_state.json` 合并进来，只读不删）。

环境变量：
  MAAORCH_MAS_API                    默认 http://100.79.173.69:36163
  MAAORCH_SSH_KEY / MAAORCH_SSH_HOST 默认 ~\.ssh\maaorch_target / Administrator@100.79.173.69
  MAAORCH_ONDEMAND_RUNTIME           覆盖 runtime 目录（测试用）
  MAAORCH_ONDEMAND_HOLD_MIN          在场窗口分钟（默认 150）
  MAAORCH_ONDEMAND_DUP_WINDOW_MIN    同目标重复发车窗口分钟（默认 150）
  MAAORCH_ONDEMAND_CONFLICT_GRACE_MIN 资源冲突补窗分钟（默认 20）
  MAAORCH_ONDEMAND_PYTHON            跑 preflight 用的解释器（默认本机 Python312，OCR 依赖）
  MAAORCH_ONDEMAND_LEASE_TTL_S       实例租约 TTL 秒（默认跟随 HOLD_MIN：在场窗口多长就占多久）
  MAAORCH_LEASE_OWNER / HERMES_KANBAN_TASK  实例租约持有者（见 G3L）
  MAAORCH_LEASE_RUNTIME              覆盖租约账本目录（测试/隔离用）
  MAAORCH_DISPATCH_NO_GO             1/true/yes/on = 拒绝一切真写

退出码：0=完成（含 dry-run 计划成立）；2=拒绝/失败/用法错误。
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
if TOOLS not in sys.path:                  # 共享模块：发车锁/班次台账 + 按需共享账本
    sys.path.insert(0, TOOLS)

import mas_dispatch as md  # noqa: E402
import mas_ondemand_ledger as ledger  # noqa: E402

RUNTIME = os.environ.get("MAAORCH_ONDEMAND_RUNTIME") or os.path.join(ROOT, "runtime")
LOG = os.path.join(RUNTIME, "mas_dispatch_log.jsonl")        # 真动作事件流（与班次同源）
LOG_CALLS = os.path.join(RUNTIME, "mas_ondemand_log.jsonl")  # 按需面全量调用审计（两端统一）
LOCK = md.LOCK                                               # 与 cron 调度器 / MCP 版同一把（mas_dispatch.lock）
PREFLIGHT = os.path.join(TOOLS, "mas_preflight.py")
API = os.environ.get("MAAORCH_MAS_API", "http://100.79.173.69:36163").rstrip("/")
SSH_KEY = os.path.expanduser(os.environ.get("MAAORCH_SSH_KEY", r"~\.ssh\maaorch_target"))
SSH_HOST = os.environ.get("MAAORCH_SSH_HOST", "Administrator@100.79.173.69")
PY312 = os.environ.get("MAAORCH_ONDEMAND_PYTHON",
                       r"C:\Users\xiach\AppData\Local\Programs\Python\Python312\python.exe")
APP_LOG = r"E:\AUTO-MAS\debug\app.log"
TEMP_PREFIX = "临时-按需"
MAA_DIR_RE = re.compile(r"^[A-Za-z]:[\\/]MAA-Multi[\\/]MAA_\d+$", re.I)
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
NO_GO = str(os.environ.get("MAAORCH_DISPATCH_NO_GO", "")).strip().lower() in {"1", "true", "yes", "on"}
TS_FMT = "%Y-%m-%d %H:%M:%S"
# 真动作事件（写班次同源的 runtime/mas_dispatch_log.jsonl）；其余 kind（dispatch_plan/被拒）只写按需调用流
EVENT_KINDS = frozenset({"dispatch_ok", "dispatch_failed", "temp_queue_created", "stop_ok", "stop_failed",
                         "cleanup_ok", "cleanup_failed", "release"})


def _env_int(name: str, default: int) -> int:
    try:
        raw = str(os.environ.get(name, "")).strip()
        return int(raw) if raw else default
    except (TypeError, ValueError):
        return default


HOLD_MIN = max(1, _env_int("MAAORCH_ONDEMAND_HOLD_MIN", 150))
DUP_WINDOW_MIN = max(1, _env_int("MAAORCH_ONDEMAND_DUP_WINDOW_MIN", HOLD_MIN))
CONFLICT_GRACE_MIN = max(1, _env_int("MAAORCH_ONDEMAND_CONFLICT_GRACE_MIN", 20))
LEASE_TTL_S = max(600, _env_int("MAAORCH_ONDEMAND_LEASE_TTL_S", HOLD_MIN * 60))  # G3L：与在场窗口同寿（被 kill 的兜底）
DEFAULT_LEASE_OWNER = "ondemand:cli"
WARM_QUIET_MIN = max(1, _env_int("MAAORCH_ONDEMAND_WARM_QUIET_MIN", 8))


def _now() -> str:
    return dt.datetime.now().strftime(TS_FMT)


def parse_ts(s) -> dt.datetime | None:
    try:
        return dt.datetime.strptime(str(s), TS_FMT)
    except (TypeError, ValueError):
        return None


def norm_dir(p) -> str:
    """目录归一：/ -> \\、折叠重复分隔符、去尾部（比对用；不做大小写转换）。"""
    s = str(p or "").strip().replace("/", "\\")
    s = re.sub(r"\\+", "\\\\", s)
    return s.rstrip("\\")


def dkey(p) -> str:
    return norm_dir(p).lower()


# ---------------------------------------------------------------------------
# 上游 REST（只读探活 + 写端点白名单）
# ---------------------------------------------------------------------------

WRITE_ENDPOINTS = (
    "/api/dispatch/start",
    "/api/dispatch/stop",
    "/api/queue/add",
    "/api/queue/update",
    "/api/queue/delete",
    "/api/queue/item/add",
    "/api/queue/item/update",
)


def post(path: str, body: dict | None = None, timeout: int = 60) -> dict:
    req = urllib.request.Request(API + path, data=json.dumps(body if body is not None else {}).encode("utf-8"),
                                 method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def reachable() -> tuple:
    try:
        r = post("/api/scripts/get", {}, timeout=15)
        return (r.get("code") == 200), "code=%s" % r.get("code")
    except Exception as e:  # noqa: BLE001
        return False, "%s: %s" % (type(e).__name__, e)


# ---------------------------------------------------------------------------
# 目标解析（只读）
# ---------------------------------------------------------------------------

def fetch_scripts() -> dict:
    """{sid: {sid,name,dir,emu}}（只收 MAA 目录形态的脚本，与 preflight 同口径）。"""
    data = post("/api/scripts/get", {}).get("data") or {}
    out = {}
    for sid, s in data.items():
        info = (s or {}).get("Info") or {}
        p = norm_dir(info.get("Path"))
        if not MAA_DIR_RE.match(p):
            continue
        emu = ((s or {}).get("Emulator") or {}).get("Index")
        out[str(sid)] = {"sid": str(sid), "name": str(info.get("Name") or ""), "dir": p,
                         "emu": str(emu) if emu is not None else ""}
    return out


def fetch_users(sid: str) -> list:
    """[{uid,name,status,mode,server,id,remained_day}] —— 账号名 = Info.Name；id = Info.Id（官服为 11 位手机号）。

    `id` 供掩码比对（切号注入校验）用；mas_switch_gate.rest_slots 同口径（Info.Id）。
    """
    data = post("/api/scripts/user/get", {"scriptId": sid, "userId": None}).get("data") or {}
    out = []
    for uid, uv in data.items():
        if uid == "instances" or not isinstance(uv, dict):
            continue
        ui = (uv or {}).get("Info") or {}
        out.append({"uid": str(uid), "name": str(ui.get("Name") or ""),
                    "status": bool(ui.get("Status")), "mode": ui.get("Mode"),
                    "server": ui.get("Server"), "id": str(ui.get("Id") or ""),
                    "remained_day": ui.get("RemainedDay")})
    return out


def fetch_queues() -> dict:
    """{uid: name}"""
    data = post("/api/queue/get", {"queueId": None}).get("data") or {}
    out = {}
    for uid, v in data.items():
        name = ((v or {}).get("Info") or {}).get("Name")
        out[str(uid)] = str(name or "")
    return out


def fetch_queue_items(quid: str) -> list:
    data = post("/api/queue/item/get", {"queueId": quid}).get("data") or {}
    return [str(((v or {}).get("Info") or {}).get("ScriptId") or "") for v in data.values()]


def resolve_queue(token: str) -> dict:
    """队列名或 uid -> {name, uid}；找不到抛 TargetError。"""
    queues = fetch_queues()
    if token in queues:                      # uid 直通
        return {"uid": token, "name": queues[token]}
    hits = [u for u, n in queues.items() if n == token]
    if not hits:
        names = sorted(n for n in queues.values() if n)
        raise TargetError("找不到队列 %r；现有：%s" % (token, ", ".join(names[:12]) + ("…" if len(names) > 12 else "")))
    if len(hits) > 1:
        raise TargetError("队列名 %r 不唯一（%d 条），请用 uid" % (token, len(hits)))
    return {"uid": hits[0], "name": token}


def resolve_script(token: str) -> dict:
    scripts = fetch_scripts()
    if token in scripts:
        return dict(scripts[token])
    hits = [s for s in scripts.values() if s["name"] == token]
    if not hits:
        names = sorted(s["name"] for s in scripts.values())
        raise TargetError("找不到脚本 %r（共 %d 个）；样例：%s" % (token, len(names), ", ".join(names[:8])))
    if len(hits) > 1:
        raise TargetError("脚本名 %r 不唯一（%d 个），请用 uid" % (token, len(hits)))
    return dict(hits[0])


def resolve_accounts(names: list) -> dict:
    """账号名列表 -> {'hit': [{name,sid,script,uid,mode,server,dir,emu}], 'errors': [(name, why)]}。"""
    scripts = fetch_scripts()
    hit, errors, users_cache = [], [], {}
    for name in names:
        matches = []
        for sid, s in scripts.items():
            if sid not in users_cache:
                users_cache[sid] = fetch_users(sid)
            for u in users_cache[sid]:
                if u["name"] == name:
                    matches.append({"name": name, "sid": sid, "script": s["name"], "uid": u["uid"],
                                    "status": u["status"], "mode": u["mode"], "server": u["server"],
                                    "remained_day": u["remained_day"], "dir": s["dir"], "emu": s["emu"]})
        if not matches:
            errors.append((name, "找不到账号（脚本启用/停用的号都查过）"))
            continue
        if len(matches) > 1:
            errors.append((name, "账号名命中 %d 个脚本（%s），不猜——请改用 --script"
                           % (len(matches), "、".join(sorted({m["script"] for m in matches})))))
            continue
        m = matches[0]
        if not m["status"]:
            errors.append((name, "账号未启用（Status=false）——AutoProxy 会跳过它；本工具不改配置"))
            continue
        if m["remained_day"] == 0:
            errors.append((name, "RemainedDay=0——AutoProxy 会跳过它"))
            continue
        hit.append(m)
    return {"hit": hit, "errors": errors}


# ---------------------------------------------------------------------------
# 资源与单飞
# ---------------------------------------------------------------------------

def resources_of_scripts(sids: set, scripts: dict) -> dict:
    sids = {str(s) for s in sids if s}
    meta = [scripts[s] for s in sids if s in scripts]
    return {"scripts": sorted(sids),
            "dirs": sorted({dkey(s["dir"]) for s in meta if s["dir"]}),
            "emus": sorted({s["emu"] for s in meta if s["emu"]}), "names": [s["name"] for s in meta]}


def load_state() -> dict:
    """读共享账本（runtime/mas_ondemand_ledger.json；旧账本首次读到自动合并，损坏隔离自愈）。"""
    return ledger.load(on_error=lambda d: audit({"kind": "state_quarantine", "detail": d}))


# ---------------------------------------------------------------- 实例租约（G3L：跨族单飞，P1-2）
_LG = None


def lease_mod():
    """`mas_lease_glue`（消费者侧胶水；首次调用 import）。不在位 ⇒ None（调用方按「跳过 + 说明」处理）。"""
    global _LG
    if _LG is None:
        try:
            import mas_lease_glue as m  # noqa: PLC0415
        except Exception:  # noqa: BLE001 —— 胶水/机制没带上来也不该让发车挂掉（跳过 + 记账）
            return None
        _LG = m
    return _LG


def lease_owner_of(args) -> str:
    """持有者：--lease-owner > MAAORCH_LEASE_OWNER > HERMES_KANBAN_TASK > ondemand:cli。"""
    LG = lease_mod()
    if LG is not None:
        return LG.owner(getattr(args, "lease_owner", None) or DEFAULT_LEASE_OWNER)
    return str(getattr(args, "lease_owner", None) or os.environ.get("MAAORCH_LEASE_OWNER")
               or os.environ.get("HERMES_KANBAN_TASK") or DEFAULT_LEASE_OWNER).strip()


def lease_idxs_of(plan: dict) -> list:
    """目标实例号（脚本 Emulator.Index；来自 resources_of_scripts，不另查）。"""
    return [str(i) for i in ((plan.get("resources") or {}).get("emus") or []) if str(i).strip()]


def lease_check_plan(plan: dict, args) -> dict:
    """只读探针（dry-run 显示 + 拒绝判据）；胶水/机制不在位 ⇒ ok=True + unavailable（跳过）。"""
    idxs, owner = lease_idxs_of(plan), lease_owner_of(args)
    LG = lease_mod()
    if LG is None:
        return {"ok": True, "status": "unavailable", "idxs": idxs, "owner": owner,
                "detail": "mas_lease_glue 不在位——跳过实例租约"}
    r = LG.check_all(idxs, owner_id=owner)
    r["idxs"], r["owner"] = idxs, owner
    return r


def lease_brief(r) -> dict:
    """审计/在场记录用的短视图（含到期时间；别把整条租约塞进日志）。"""
    r = r or {}
    leases = r.get("leases") or []
    exp = r.get("expires_at") or ((leases[0] or {}).get("expires_at") if leases else "") or ""
    return {"ok": r.get("ok"), "status": r.get("status") or "", "owner": r.get("owner") or "",
            "idxs": r.get("idxs") or [], "expires_at": exp,
            "detail": str(r.get("detail") or "")[:200]}


def gate_lease(plan: dict, args) -> None:
    """G3L（P1-2 跨族单飞）：只读判据——别人的租约 ⇒ 拒绝。

    `--force` 放行不了租约（真要抢得先人工 release，见 `docs/MAS_INSTANCE_LEASE.md §5.2`）。
    """
    r = lease_check_plan(plan, args)
    plan["lease"] = r
    if r.get("status") in ("no_target", "unavailable"):
        plan["warnings"].append("实例租约：%s" % (r.get("detail") or r.get("status")))
        return
    if not r.get("ok"):
        plan["refusals"].append({"gate": "G3L", "reason": "lease_busy",
                                 "detail": "实例租约：%s（自己的租约不算被占；要抢先得人工 release）"
                                           % (r.get("detail") or r.get("status"))})
        return
    if r.get("status") == "self":
        plan["warnings"].append("实例租约：本持有者已在租（续租，不重复占）——%s" % (r.get("detail") or ""))


def lease_acquire_plan(plan: dict, args) -> dict:
    """真发车前取租（拿不到 ⇒ 调用方 raise LeaseBusy ⇒ 零上游写）。"""
    idxs, owner = lease_idxs_of(plan), lease_owner_of(args)
    LG = lease_mod()
    if LG is None:
        r = {"ok": True, "status": "unavailable", "idxs": idxs, "owner": owner,
             "detail": "mas_lease_glue 不在位——跳过实例租约"}
    else:
        r = LG.acquire_all(idxs, owner, "按需发车 %s（%s）" % (plan["target"]["token"], plan["target"]["kind"]),
                           ttl_s=LEASE_TTL_S, src="ondemand", note="ondemand")
        r["idxs"], r["owner"] = idxs, owner
    plan["lease"] = r
    audit({"kind": "lease_acquire", "target_kind": plan["target"]["kind"], "target": plan["target"]["token"],
           "mode": "-", "ok": bool(r.get("ok")), "code": None, "taskId": None, "reason": "lease_acquire",
           "detail": str(r.get("detail") or r.get("status"))[:300], "apply": True,
           "force": bool(getattr(args, "force", False)), "lease": lease_brief(r)})
    return r


def lease_settle(plan: dict, args) -> dict:
    """发车收尾：一个 taskId 都没拿到 ⇒ 立刻放租约（别白占实例）；发出去了 ⇒ 留给 release/任务结束/TTL。"""
    lz = plan.get("lease") or {}
    if not lz.get("ok") or lz.get("status") in ("no_target", "unavailable"):
        return {}
    r = plan.get("result") or {}
    if r.get("taskId") or r.get("taskIds"):
        plan["lease_note"] = "任务在跑：租约占着 —— 跑完用 --release（或任务结束/被 kill 由 TTL 兜底）"
        return {"kept": True, "detail": plan["lease_note"], "expires_at": lease_brief(lz).get("expires_at")}
    rel = lease_release(plan, args, note="ondemand 发车未成，收回租约")
    plan["lease_release"] = rel
    return rel


def lease_release(plan: dict, args, note: str = "ondemand") -> dict:
    """放掉本方持有者的租约（只放自己的；不是持有者 ⇒ 不误抢）。"""
    LG = lease_mod()
    idxs, owner = lease_idxs_of(plan), lease_owner_of(args)
    if LG is None or not idxs:
        return {"ok": True, "status": "skipped", "detail": "无租约可放"}
    r = LG.release_all(idxs, owner_id=owner, note=note)
    audit({"kind": "lease_release", "target_kind": plan["target"]["kind"], "target": plan["target"]["token"],
           "mode": "-", "ok": bool(r.get("ok")), "code": None, "taskId": None, "reason": "lease_release",
           "detail": str(r.get("detail") or r.get("status"))[:300], "apply": True, "lease": lease_brief(r)})
    return r


def lease_release_records(recs: list, apply_: bool, ctx: str) -> list:
    """按在场记录释放租约（只放记录里那位 owner 的；旧记录没带租约信息 ⇒ 跳过并说明）。"""
    LG, out = lease_mod(), []
    for r in recs or []:
        owner = str(r.get("lease_owner") or "").strip()
        idxs = [str(i) for i in (r.get("lease_idxs") or []) if str(i).strip()]
        if not owner or not idxs:
            out.append({"run_id": r.get("run_id"), "status": "no_lease_record",
                        "detail": "该在场记录没带租约信息（P1-2 之前的旧车）——不做任何租约操作"})
            continue
        if r.get("lease_released_at"):
            out.append({"run_id": r.get("run_id"), "status": "already_released",
                        "detail": "已于 %s 释放" % r["lease_released_at"]})
            continue
        if not apply_:
            out.append({"run_id": r.get("run_id"), "status": "dry_run", "owner": owner, "idxs": idxs,
                        "detail": "将释放 idx=%s（owner=%s）" % (",".join(idxs), owner)})
            continue
        if LG is None:
            out.append({"run_id": r.get("run_id"), "status": "unavailable", "detail": "mas_lease_glue 不在位——跳过"})
            continue
        rel = LG.release_all(idxs, owner_id=owner, note="ondemand %s" % ctx)
        r["lease_released_at"] = _now()
        r["lease_release_detail"] = str(rel.get("detail") or rel.get("status"))[:200]
        audit({"kind": "lease_release", "target_kind": r.get("kind") or "-",
               "target": r.get("target") or "-", "mode": "-", "ok": bool(rel.get("ok")), "code": None,
               "taskId": r.get("task_uid") or None, "reason": "lease_release",
               "detail": ("%s：%s" % (ctx, r["lease_release_detail"]))[:300], "apply": True,
               "owner": owner, "idxs": idxs})
        out.append({"run_id": r.get("run_id"), "status": "released" if rel.get("ok") else "partial",
                    "ok": bool(rel.get("ok")), "owner": owner, "idxs": idxs,
                    "detail": r["lease_release_detail"]})
    return out


def lease_renew_note(plan: dict, args) -> None:
    """异步发车不发 renew（租约与在场窗口同寿，见模块头注）；这里只做口径提示，便于后人查。"""
    lz = plan.get("lease") or {}
    if lz.get("ok") and lz.get("status") == "self":
        plan["warnings"].append("实例租约：同主续租（TTL 已刷新到 %ss）" % LEASE_TTL_S)


def task_records(st: dict, tid: str) -> list:
    """在场记录里匹配 task uid / run_id 的行（含被顶替未出窗的 prev）——**同一份 st 里的对象**。"""
    out = []
    for _d, _k, e in ledger.iter_entries(st):
        if str(e.get("task_uid") or "") == tid or str(e.get("run_id") or "") == tid:
            out.append(e)
        out.extend([p for p in e.get("prev") or [] if isinstance(p, dict)
                    and (str(p.get("task_uid") or "") == tid or str(p.get("run_id") or "") == tid)])
    return out


def save_state(st: dict) -> None:
    ledger.save(st)


def _append_jsonl(path: str, rec: dict) -> None:
    os.makedirs(RUNTIME, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- 统一审计流（双写期）
# 接线卡 t_a190ca4b：旧流（mas_ondemand_log 调用行 / mas_dispatch_log 事件行）照写；
# 旁路再补一条 tools/mas_audit.py 统一事件（schema maaorch.audit.v1）。映射同
# tools/mas_audit.py::_ad_dispatch ②③（一次 audit() 调用 = 一条统一事件，不按文件重复计）。
def _unified_from_audit(rec: dict) -> None:
    kind = str(rec.get("kind") or "").strip()
    tool = str(rec.get("tool") or "").strip()
    ok = rec.get("ok")
    if tool and kind in ("", "queue", "script"):
        # ② 调用行（本文件产出为事件行；此形态留给外部/MCP 写入者）
        verb = re.sub(r"^dispatch_", "", re.sub(r"[^a-z0-9_]+", "_", tool.lower()))
        verb = {"stop_task": "stop", "ondemand_preflight": "preflight"}.get(verb, verb)
        action = "dispatch." + verb
        sub = rec.get("result")
        if not isinstance(sub, dict):
            sub = {}
        blob = (str(rec.get("verdict") or "") + " " + tool).lower()
        if any(w in blob for w in ("refus", "unknown", "denied", "reject")):
            res = "refused"
        elif rec.get("apply") is False or sub.get("dry_run"):
            res = "info"
        elif sub.get("ok") is True or ok is True:
            res = "ok"
        elif sub.get("ok") is False or ok is False:
            res = "fail"
        else:
            res = "info"
    elif kind in ("dispatch_plan",):
        action, res = "dispatch.plan", "info"
    elif "refused" in kind:
        action, res = "dispatch.refuse", "refused"
    elif kind.startswith("stop"):
        action, res = "dispatch.stop", ("ok" if ok else "fail")
    elif kind.startswith("dispatch"):
        action, res = "dispatch.start", ("ok" if ok else "fail")
    elif kind:
        action, res = "dispatch." + re.sub(r"[^a-z0-9_]+", "_", kind.lower()), (
            "ok" if ok is True else ("fail" if ok is False else "info"))
    else:
        return
    tk = str(rec.get("target_kind") or "").strip()
    tv = str(rec.get("target") or "").strip()
    if tv and tk and tk != "-":
        target = "%s:%s" % (tk, tv)
    elif tv:
        target = "task:%s" % tv
    else:
        target = "-"
    run_id = str(rec.get("taskId") or "")
    params = {k: rec.get(k) for k in ("mode", "apply", "force", "preflight", "resources", "detail",
                                      "verdict", "verdict_kind", "queue_id", "temp_queue", "dir",
                                      "accounts", "code", "reason", "source", "src", "dry_run")
              if rec.get(k) not in (None, "")}
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import mas_audit  # noqa: PLC0415
    mas_audit.safe_append(
        actor="tool:mas_ondemand", action=action, target=target, result=res,
        reason=str(rec.get("reason") or rec.get("verdict") or "")[:300], params=params,
        evidence=[LOG, LOG_CALLS], card=os.environ.get("HERMES_KANBAN_TASK", ""), run_id=run_id,
        path=mas_audit.stream_for_runtime(RUNTIME))


def audit(rec: dict) -> None:
    """审计：全量调用行 → 按需流（mas_ondemand_log.jsonl）；真动作另写事件流（与班次同源）。"""
    rec = dict(rec)
    rec.setdefault("ts", _now())
    rec["source"] = "ondemand"
    rec["src"] = "cli"
    _append_jsonl(LOG_CALLS, rec)
    if rec.get("kind") in EVENT_KINDS:
        _append_jsonl(LOG, rec)
    try:
        _unified_from_audit(rec)
    except Exception:  # noqa: BLE001
        pass


def _shared(a: dict, b: dict) -> dict:
    a, b = a or {}, b or {}
    return {"dirs": sorted(set(a.get("dirs") or []) & set(b.get("dirs") or [])),
            "scripts": sorted(set(a.get("scripts") or []) & set(b.get("scripts") or [])),
            "emus": sorted(set(a.get("emus") or []) & set(b.get("emus") or []))}


def _mas_dispatch_windows(now: dt.datetime) -> list:
    """班次调度（mas_dispatch.py）近期发车记录：{queue, at, busy_until, resources}。"""
    out = []
    with contextlib.suppress(Exception):
        st = md.load_state()
        for d, recs in (st or {}).items():
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(d)) or not isinstance(recs, dict):
                continue
            for q, rec in recs.items():
                if not isinstance(rec, dict) or not rec.get("taskId"):
                    continue
                at = parse_ts(rec.get("at"))
                if at and (now - at).total_seconds() <= CONFLICT_GRACE_MIN * 60:
                    out.append({"src": "shift", "queue": q, "at": rec.get("at"),
                                "busy_until": rec.get("busy_until"), "resources": rec.get("resources") or {}})
    return out


def open_runs(st: dict, now: dt.datetime | None = None) -> list:
    """共享账本里仍在 busy_until 窗口内的记录（**含 MCP 端发的车**；含被顶替未出窗的 prev）。"""
    return ledger.open_entries(st, now=now, hold_min=HOLD_MIN)


def check_single_flight(target: dict, res: dict, st: dict, now: dt.datetime) -> list:
    """返回冲突列表 [{kind, detail, with}]；空 = 通过。target={kind,token,uid}。"""
    out = []
    for r in open_runs(st, now):
        same = (r.get("kind") == target["kind"] and
                (r.get("target") == target["token"] or
                 bool(target.get("uid")) and r.get("uid") == target.get("uid")))
        if same:
            out.append({"kind": "duplicate_inflight", "with": r.get("run_id") or r.get("key"),
                        "detail": "同目标 %s 已在 %s 发过（src=%s taskId=%s，窗口至 %s）"
                                  % (target["token"], r.get("ts"), r.get("src") or "?",
                                     str(r.get("task_uid") or "")[:8], r.get("busy_until"))})
            continue
        sh = _shared(r.get("resources") or {}, res)
        if any(sh.values()):
            out.append({"kind": "resource_inflight", "with": r.get("run_id") or r.get("key"),
                        "detail": "与 %s 端在跑的 %s（taskId=%s）资源重叠 %s"
                                  % (r.get("src") or "?", r.get("target"),
                                     str(r.get("task_uid") or "")[:8], json.dumps(sh, ensure_ascii=False))})
    for w in _mas_dispatch_windows(now):
        sh = _shared(w.get("resources") or {}, res)
        if any(sh.values()):
            out.append({"kind": "shift_inflight", "with": w.get("queue"),
                        "detail": "与刚发车 %d 分钟内的班次 %s 资源重叠 %s（班次发车的安全补窗，非整窗）"
                                  % (CONFLICT_GRACE_MIN, w.get("queue"), json.dumps(sh, ensure_ascii=False))})
    return out


# ---------------------------------------------------------------------------
# 现场（只读 ssh）：MAA 进程 / 实例 / app.log 任务事件
# ---------------------------------------------------------------------------

def _ssh_base() -> list:
    return ["ssh", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=20", SSH_HOST]


def ssh_ps(script: str, timeout: int = 240) -> str:
    """远端执行 PowerShell（**-EncodedCommand** UTF-16LE/base64）。

    教训（2026-09-28 实测）：stdin 喂 `powershell -Command -` 时，脚本里任何一处运行期
    报错（如把 `$false` 写成裸 `false`）都会让整段脚本**静默零输出**（rc=0、stdout/stderr
    全空），极难排查；-EncodedCommand 走命令行参数、编码确定，出错的 stderr 以 CLIXML
    形式回来，至少可诊断。base64 是 ASCII ⇒ 不受 ssh 多层引号/代码页影响。
    """
    b64 = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    cmd = _ssh_base() + ["powershell -NoProfile -NonInteractive -EncodedCommand " + b64]
    r = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout)
    return r.stdout.decode("utf-8", "replace")


_SNAPSHOT_PS = r"""
$ErrorActionPreference='Continue'; $ProgressPreference='SilentlyContinue'
$OutputEncoding=[System.Text.Encoding]::UTF8; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8
$maa = @(Get-CimInstance Win32_Process -Filter "Name='MAA.exe'" -ErrorAction SilentlyContinue)
foreach ($p in $maa) {
  Write-Output ("MAAPROC|" + $p.ProcessId + "|" + $p.CreationDate.ToString('yyyy-MM-dd HH:mm:ss') + "|" + [string]$p.ExecutablePath)
}
foreach ($p in (Get-CimInstance Win32_Process -Filter "Name='MuMuVMMHeadless.exe'" -ErrorAction SilentlyContinue)) {
  $m = [regex]::Match([string]$p.CommandLine, 'MuMuPlayer-12\.0-(\d+)')
  if ($m.Success) { Write-Output ("VMMPROC|" + $m.Groups[1].Value + "|" + $p.ProcessId + "|" + $p.CreationDate.ToString('yyyy-MM-dd HH:mm:ss')) }
}
if (__EVENTS__ -eq $true) {
  $lines = Get-Content -LiteralPath '__APPLOG__' -Tail 6000 -Encoding UTF8 -ErrorAction SilentlyContinue |
    Select-String -Pattern '创建任务:|开始运行任务:|任务开始:|任务结束:|中止任务:|已解锁脚本配置|跳过任务:|启动MAA进程:|开始启动模拟器' |
    Select-Object -Last __EVENTN__
  foreach ($l in $lines) { Write-Output ("APPLOG|" + $l.Line) }
}
Write-Output "SNAPSHOT-END"
"""


def host_snapshot(events: bool = False, event_n: int = 80, timeout: int = 120) -> dict:
    """只读主机快照：MAA.exe（目录+pid+启动时刻）、VMM 实例、可选 app.log 任务事件。"""
    ps = (_SNAPSHOT_PS.replace("__EVENTS__", "$true" if events else "$false")
          .replace("__EVENTN__", str(int(event_n))).replace("__APPLOG__", APP_LOG))
    out = ssh_ps(ps, timeout=timeout)
    if "SNAPSHOT-END" not in out:
        raise RuntimeError("远端快照无输出（ssh 不通 / PowerShell 异常）：%s" % out[-200:])
    snap = {"maa": [], "vmm": {}, "applog": []}
    for ln in out.splitlines():
        ln = ln.rstrip("\r")
        if ln.startswith("MAAPROC|"):
            parts = ln.split("|", 3)
            pid, ts_, exe = (parts[1:4] + ["", "", ""])[:3]
            d = norm_dir(os.path.dirname(exe)) if exe else ""
            snap["maa"].append({"pid": pid.strip(), "start": ts_.strip(), "exe": norm_dir(exe), "dir": d})
        elif ln.startswith("VMMPROC|"):
            parts = ln.split("|", 3)
            idx, pid, ts_ = (parts[1:4] + ["", "", ""])[:3]
            snap["vmm"][idx.strip()] = {"pid": pid.strip(), "start": ts_.strip()}
        elif ln.startswith("APPLOG|"):
            snap["applog"].append(ln[len("APPLOG|"):])
    return snap


def busy_dirs(snap: dict) -> dict:
    """{dir_lower: [pid...]} —— 正在跑的 MAA 目录。"""
    out = {}
    for m in snap.get("maa") or []:
        if m.get("dir"):
            out.setdefault(dkey(m["dir"]), []).append(m.get("pid") or "?")
    return out


# ---------------------------------------------------------------------------
# preflight 调用（子进程，解释器优先 Python312 —— OCR 依赖）
# ---------------------------------------------------------------------------

def pick_python() -> str:
    """preflight 要 OCR（rapidocr_onnxruntime）⇒ 优先 Python312；探测失败退 sys.executable。"""
    cands = [PY312, sys.executable]
    for c in cands:
        if not c or not os.path.exists(c):
            continue
        if c == sys.executable:
            return c
        try:
            r = subprocess.run([c, "-c", "import rapidocr_onnxruntime"], capture_output=True, timeout=60)
            if r.returncode == 0:
                return c
        except Exception:  # noqa: BLE001
            continue
    return sys.executable


def run_preflight(kind: str, name: str, timeout: int = 900) -> dict:
    """跑 tools/mas_preflight.py；返回 {verdict, exit, failing, reasons, raw_head}。fail-closed。"""
    if not os.path.exists(PREFLIGHT):
        return {"verdict": "MISSING", "exit": 2, "failing": [], "reasons": ["mas_preflight.py 不在位"], "raw": ""}
    flag = "--queue" if kind == "queue" else "--script"
    py = pick_python()
    try:
        p = subprocess.run([py, PREFLIGHT, flag, name, "--json"], capture_output=True,
                           stdin=subprocess.DEVNULL, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"verdict": "TIMEOUT", "exit": 2, "failing": [], "reasons": ["preflight 超时"], "raw": ""}
    blob = (p.stdout or b"").decode("utf-8", "replace") + (p.stderr or b"").decode("utf-8", "replace")
    data, verdict, failing, reasons = None, "", [], []
    try:
        data = json.loads(blob)
    except ValueError:
        m = re.search(r'"preflight"\s*:\s*"(GO|NO-GO)"', blob) or re.search(r"PREFLIGHT\s*=\s*(GO|NO-GO)", blob)
        if m:
            verdict = m.group(1)
    if isinstance(data, dict):
        verdict = str(data.get("preflight") or verdict or "")
        failing = data.get("failing_checks") or []
        reasons = data.get("reasons") or []
        for k, v in (data.get("checks") or {}).items():
            if isinstance(v, dict) and v.get("status") == "FAIL" and k not in failing:
                failing.append(k)
    if not verdict:
        verdict = "UNKNOWN"
    return {"verdict": "GO" if verdict == "GO" else verdict, "exit": p.returncode,
            "failing": failing, "reasons": reasons[:6], "raw": blob[:1500],
            "interpreter": py}


# ---------------------------------------------------------------------------
# 锁（真写临界区串行；与 --cleanup 共用）
# ---------------------------------------------------------------------------

LockBusy = md._LockBusy  # 与 mas_dispatch/MCP 同源：同一把 runtime/mas_dispatch.lock、同一套陈旧阈值


class LeaseBusy(Exception):
    """G3L（P1-2）：实例租约被别人占着 ⇒ 拒发（零上游写）。"""


@contextlib.contextmanager
def _lock(enabled: bool = True):
    """真写临界区：复用 mas_dispatch 的锁协议（同文件互斥；陈旧锁可抢）。

    `MAAORCH_DISPATCH_LOCK_STALE_S` 控制陈旧阈值（默认 1500s），与 cron 调度器同源 ——
    CLI 在发车时 cron/MCP 发车会被拒 concurrent_write，反之亦然。
    """
    with md._dispatch_lock(enabled=enabled, path=LOCK):
        yield


# ---------------------------------------------------------------------------
# 计划（只读）与执行（写）
# ---------------------------------------------------------------------------

class TargetError(Exception):
    """目标名不存在/不唯一/不合格（用法问题）。"""


def make_target(kind: str, token: str, qid: str = "", token_kind: str = "name", uid: str = "") -> dict:
    return {"kind": kind, "token": token, "queue_id": qid, "token_kind": token_kind, "uid": uid or qid}


def _kind_of_args(args) -> str:
    return "queue" if args.queue else ("script" if args.script else ("wake" if args.wake else "account"))


def _token_of_args(args) -> str:
    return str(args.queue or args.script or args.wake or args.account or "")


def _resolve_plan_target(args, plan: dict) -> tuple:
    """解析目标（可能抛 TargetError）——返回 (target, sids)；顺带把计划步骤写进 plan。"""
    sids, target = set(), None
    if args.queue:
        q = resolve_queue(args.queue)
        items = [s for s in fetch_queue_items(q["uid"]) if s]
        scripts = fetch_scripts()
        unknown = [s for s in items if s not in scripts]
        target = make_target("queue", q["name"], qid=q["uid"])
        target["token_kind"] = "uid" if args.queue in (fetch_queues() or {}) else "name"
        plan["steps"] = [{"op": "dispatch_start", "taskId": q["uid"], "mode": "AutoProxy"}]
        if unknown:
            plan["refusals"].append({"gate": "G2", "reason": "queue_items_unknown_script",
                                     "detail": "%d 个队列项指向不存在的脚本：%s" % (len(unknown), unknown[:3])})
        if not items:
            plan["refusals"].append({"gate": "G2", "reason": "queue_empty", "detail": "队列 %s 没有脚本项" % q["name"]})
        sids |= set(items)
        plan["queue_items"] = [scripts[s]["name"] for s in items if s in scripts]
    elif args.script:
        s = resolve_script(args.script)
        target = make_target("script", s["name"], uid=s["sid"])
        target["token_kind"] = "uid" if args.script == s["sid"] else "name"
        plan["steps"] = [{"op": "dispatch_start", "taskId": s["sid"], "mode": "AutoProxy"}]
        sids.add(s["sid"])
    elif args.account:
        names = [x.strip() for x in args.account.split(",") if x.strip()]
        r = resolve_accounts(names)
        for name, why in r["errors"]:
            plan["refusals"].append({"gate": "G2", "reason": "account_unusable", "detail": "%s：%s" % (name, why)})
        if not r["hit"]:
            raise TargetError("没有任何可用账号（见上）")
        # 同 MAA 目录并成一条临时队列（硬约束：同目录的号必须同队列）
        groups = {}
        for m in r["hit"]:
            groups.setdefault(dkey(m["dir"]), []).append(m)
        target = make_target("account", ",".join(m["name"] for m in r["hit"]))
        plan["accounts"] = r["hit"]
        plan["groups"] = [{k: [m["name"] for m in v]} for k, v in sorted(groups.items())]
        for k, ms in sorted(groups.items()):
            plan["temp_queues"].append({"dir": k, "scripts": sorted({m["script"] for m in ms}),
                                        "sids": sorted({m["sid"] for m in ms}),
                                        "accounts": [m["name"] for m in ms]})
        plan["steps"] = [{"op": "queue_add+item+dispatch", "groups": len(groups), "mode": "AutoProxy"}]
        sids |= {m["sid"] for m in r["hit"]}
    return target, sids


def plan_dispatch(args) -> dict:
    """只读规划：解析目标 -> 资源 -> 启用号清单（闸门在 cmd_dispatch 里按序跑）。"""
    plan = {"tool": "mas_ondemand", "action": "dispatch", "generated_at": _now(), "apply": bool(args.apply),
            "target": None, "resources": None, "enabled_accounts": [], "warnings": [], "refusals": [],
            "preflight": None, "single_flight": [], "temp_queues": [], "steps": [], "lease": None}
    try:
        target, sids = _resolve_plan_target(args, plan)
    except TargetError as e:
        plan["target"] = make_target(_kind_of_args(args), _token_of_args(args))
        plan["refusals"].append({"gate": "G2", "reason": "target_not_found", "detail": str(e)})
        return plan
    plan["target"] = target
    scripts = fetch_scripts()
    res = resources_of_scripts(sids, scripts)
    plan["resources"] = res

    # 启用号清单（做为提示：AutoProxy 按脚本跑全部启用号）
    for sid in sorted({m for m in sids}):
        if sid not in scripts:
            continue
        users = fetch_users(sid)
        en = [u["name"] for u in users if u["status"] and u.get("remained_day") != 0]
        plan["enabled_accounts"].append({"script": scripts[sid]["name"], "enabled": en,
                                         "all": [u["name"] for u in users]})
        if not en:
            plan["warnings"].append("脚本 %s 当前 0 个可用启用号 —— 发车会空跑（任务会立即结束）"
                                    % scripts[sid]["name"])
        if len(en) > 1 and args.account:
            plan["warnings"].append("脚本 %s 有 %d 个启用号（本次会全部参与，不只是你点的那一个）：%s"
                                    % (scripts[sid]["name"], len(en), "、".join(en)))
    return plan


def _recent_activity(events: list, dirs: list, minutes: int, scripts: dict, now: dt.datetime | None = None) -> list:
    """app.log 事件里目标目录最近 N 分钟的活动。

    为什么需要：班次跑「队列」时，两个脚本之间 MAA.exe 会消失 1–2 分钟（MAA 收工 → 下一个脚本再拉起）。
    只看进程会把这段空档误判成「槽位空闲」，于是两个 MAA 抢同一个目录（互杀）。
    这里用 app.log 的 maa_launch / emu_launch / script_start 反查目录，做「刚动过 ⇒ 别碰」的补充判据。
    """
    now = now or dt.datetime.now()
    want, out = {dkey(d) for d in (dirs or []) if d}, []
    for ln in events or []:
        m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", ln)
        if not m:
            continue
        try:
            ts = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if ts > now or (now - ts).total_seconds() > minutes * 60:
            continue
        d = ""
        if "启动MAA进程:" in ln:
            d = dkey(os.path.dirname(ln.split("启动MAA进程:", 1)[1].strip()))
        elif "开始启动模拟器" in ln:
            mi = re.search(r"开始启动模拟器 (\d+)", ln)
            if mi:
                for s in (scripts or {}).values():
                    if str(s.get("emu") or "") == mi.group(1):
                        d = dkey(s.get("dir") or "")
                        break
        else:
            mu = UUID_RE.search(ln)
            if mu and mu.group(1) in (scripts or {}):
                d = dkey(scripts[mu.group(1)].get("dir") or "")
        if d and d in want:
            out.append({"t": m.group(1)[11:], "dir": d, "line": ln[-110:]})
    return out


def gate_single_flight(plan: dict, args) -> None:
    st = load_state()
    now = dt.datetime.now()
    conf = check_single_flight(plan["target"], plan["resources"], st, now)
    # 现场：① 目标目录有 MAA.exe 在跑 ⇒ slot_busy；② 最近 N 分钟该目录有活动 ⇒ slot_warm（脚本间空档）
    live_err = ""
    try:
        snap = host_snapshot(events=True, event_n=int(os.environ.get("MAAORCH_ONDEMAND_EVENTS", "60")),
                             timeout=int(args.timeout))
        bd = busy_dirs(snap)
        hit = sorted(set(plan["resources"]["dirs"]) & set(bd.keys()))
        for d in hit:
            conf.append({"kind": "slot_busy", "with": "MAA.exe pid=%s" % ",".join(bd[d]),
                         "detail": "目标目录正在跑 MAA：%s（pid %s）" % (d, ",".join(bd[d]))})
        if not hit and not live_err:
            warm = _recent_activity(snap.get("applog") or [], plan["resources"]["dirs"],
                                    WARM_QUIET_MIN, fetch_scripts(), now)
            for w in warm[:3]:
                conf.append({"kind": "slot_warm", "with": w["t"],
                             "detail": "目标目录 %d 分钟内有活动（%s %s）——班次脚本之间常有空档，别抢"
                                       % (WARM_QUIET_MIN, w["t"], w["line"][-60:])})
    except Exception as e:  # noqa: BLE001
        live_err = "%s: %s" % (type(e).__name__, e)
    plan["single_flight"] = conf
    plan["live_check"] = {"ok": not live_err, "detail": live_err or "ok"}
    if live_err:
        plan["refusals"].append({"gate": "G3", "reason": "live_check_failed",
                                 "detail": "现场进程检查失败（fail-closed，不真发车）：%s" % live_err})
    if conf and not args.force:
        plan["refusals"].append({"gate": "G3", "reason": conf[0]["kind"],
                                 "detail": "; ".join(c["detail"] for c in conf[:3])})
    elif conf:
        plan["warnings"].append("--force 放行单飞冲突（%s）——已记账" % conf[0]["kind"])


def gate_preflight(plan: dict, args) -> None:
    if args.skip_preflight:
        plan["preflight"] = {"verdict": "SKIPPED", "detail": "--skip-preflight 显式跳过（记账）"}
        plan["warnings"].append("preflight 被 --skip-preflight 跳过（REST/槽位/实例/残留/注入/磁盘未检）")
        return
    t = plan["target"]
    if t["kind"] == "queue":
        pf = run_preflight("queue", t["token"], timeout=int(args.preflight_timeout))
    else:
        nm = plan["resources"]["names"][0] if plan["resources"].get("names") else t["token"]
        pf = run_preflight("script", nm, timeout=int(args.preflight_timeout))
    plan["preflight"] = pf
    if pf["verdict"] != "GO":
        plan["refusals"].append({"gate": "G4", "reason": "preflight_%s" % pf["verdict"].lower(),
                                 "detail": "preflight=%s%s%s" % (
                                     pf["verdict"],
                                     ("；不过项：%s" % ",".join(pf["failing"])) if pf["failing"] else "",
                                     ("；" + "；".join(map(str, pf["reasons"][:3]))) if pf["reasons"] else "")})


def _upstream_result(resp) -> dict:
    """AUTO-MAS 应答 -> {ok, code, taskId, reason}。"""
    if not isinstance(resp, dict):
        return {"ok": False, "code": None, "taskId": None, "reason": "bad_response",
                "detail": "应答不是 JSON 对象：%r" % (resp,)[:200]}
    code, msg = resp.get("code"), str(resp.get("message") or "")
    if code == 200 and resp.get("taskId"):
        return {"ok": True, "code": code, "taskId": resp["taskId"], "reason": ""}
    if code == 200:
        return {"ok": False, "code": code, "taskId": None, "reason": "no_taskid",
                "detail": "code=200 但无 taskId（message=%s）" % msg[:200]}
    low = msg.lower()
    if any(k in msg for k in ("已在运行", "正在运行", "正在执行")) or "running" in low:
        return {"ok": False, "code": code, "taskId": None, "reason": "target_running", "detail": msg[:300]}
    if any(k in msg for k in ("占用", "被占", "锁定", "锁定")) or "locked" in low:
        return {"ok": False, "code": code, "taskId": None, "reason": "slot_occupied", "detail": msg[:300]}
    if any(k in msg for k in ("无法找到", "找不到", "不存在", "无效")) or "not found" in low:
        return {"ok": False, "code": code, "taskId": None, "reason": "target_not_found", "detail": msg[:300]}
    return {"ok": False, "code": code, "taskId": None, "reason": "bad_response", "detail": msg[:300]}


def _next_temp_name(existing: set, stamp: str, n: int) -> tuple:
    while True:
        name = "%s-%s-%d" % (TEMP_PREFIX, stamp, n)
        if name not in existing:
            return name, n
        n += 1


def _record_run(plan: dict, task_id: str, temp_queue: dict | None = None, key_suffix: str = "",
                extra: dict | None = None) -> dict:
    """发车成功 → 写共享账本（CLI/MCP 同一份；MCP 端 list_running 也能看到）。

    P1-2：带上实例租约明细（owner/idxs/到期）——`--release` / `--stop` 靠它精确释放。
    """
    st = load_state()
    t = plan["target"]
    # 账号车按 schema 记临时队列 uid（MCP 端同口径）；队列/脚本车用各自 uid
    uid = t.get("uid") or t.get("queue_id") or (temp_queue or {}).get("id") or ""
    key = ledger.key_of(t["kind"], uid, t["token"]) + key_suffix
    ex = dict(extra or {})
    lz = lease_brief(plan.get("lease"))
    if lz.get("idxs"):
        ex.setdefault("lease_owner", lz.get("owner") or "")
        ex.setdefault("lease_idxs", lz.get("idxs") or [])
        ex.setdefault("lease_expires_at", lz.get("expires_at") or "")
    entry = ledger.record(st, t["kind"], t["token"], uid, task_id, "cli", key=key,
                          mode=plan.get("mode") or "AutoProxy", resources=plan.get("resources"),
                          hold_min=HOLD_MIN, run_id=uuid.uuid4().hex[:8], status="open",
                          queue_id=t.get("queue_id") or "",
                          temp_queue=temp_queue, **ex)
    save_state(st)
    return entry


def execute_temp_queue(plan: dict, args) -> dict:
    """按单号：建临时队列（同目录并一条）→ 挂脚本项 → 发车 → 记账。"""
    out = {"temp_queues": [], "taskIds": []}
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M")
    existing = set((fetch_queues() or {}).values())
    n = 1
    for gi, g in enumerate(plan["temp_queues"]):
        name, n = _next_temp_name(existing, stamp, n)
        n += 1
        existing.add(name)
        r = post("/api/queue/add", {})
        qid = r.get("queueId") or (r.get("data") or {}).get("uid")
        if not qid:
            out["temp_queues"].append({"name": name, "ok": False, "reason": "queue_add_no_id",
                                       "detail": json.dumps(r, ensure_ascii=False)[:200]})
            continue
        u = post("/api/queue/update", {"queueId": qid, "data": {"Info": {
            "Name": name, "TimeEnabled": False, "StartUpEnabled": False, "AfterAccomplish": "NoAction"}}})
        rec = {"name": name, "id": qid, "dir": g["dir"], "accounts": g["accounts"],
               "scripts": g["scripts"], "created": _now(), "deleted": False, "src": "cli",
               "note": "mas_ondemand 单号临时队列（跑完用 --cleanup 删）"}
        out["temp_queues"].append(rec)
        st = load_state()
        st.setdefault("temp_queues", []).append(rec)
        save_state(st)
        audit({"kind": "temp_queue_created", "target_kind": "account", "target": plan["target"]["token"],
               "mode": "AutoProxy", "ok": True, "code": 200, "taskId": None, "reason": "",
               "detail": "建临时队列 %s（updated code=%s）" % (name, u.get("code")), "apply": True,
               "queue_id": qid, "temp_queue": name, "dir": g["dir"], "accounts": g["accounts"]})
        for sid in g["sids"]:
            r1 = post("/api/queue/item/add", {"queueId": qid})
            iid = r1.get("queueItemId") or (r1.get("data") or {}).get("uid")
            if not iid:
                rec["item_error"] = "item_add_no_id: %s" % json.dumps(r1, ensure_ascii=False)[:160]
                continue
            post("/api/queue/item/update", {"queueId": qid, "queueItemId": iid,
                                            "data": {"Info": {"ScriptId": sid}}})
        resp = post("/api/dispatch/start", {"taskId": qid, "mode": "AutoProxy"})
        up = _upstream_result(resp)
        rec["dispatch"] = {"ok": up["ok"], "code": up["code"], "taskId": up.get("taskId"),
                           "reason": up.get("reason"), "detail": str(up.get("detail") or "")[:200]}
        if up.get("taskId"):
            out["taskIds"].append(up["taskId"])
        audit({"ts": _now(), "kind": "dispatch_ok" if up["ok"] else "dispatch_failed",
               "target_kind": "account", "target": plan["target"]["token"], "mode": "AutoProxy",
               "ok": bool(up["ok"]), "code": up.get("code"), "taskId": up.get("taskId"),
               "reason": up.get("reason") or "", "detail": str(up.get("detail") or "")[:300],
               "queue_id": qid, "temp_queue": name, "dir": g["dir"], "accounts": g["accounts"],
               "apply": True, "force": bool(args.force), "preflight": _pf_verdict(plan),
               "resources": {"dirs": [g["dir"]], "scripts": g["sids"], "emus": plan["resources"]["emus"]}})
        if up["ok"]:
            # 同 key 多组（同目录并一条队列的语义）：第 2..n 组加 #n 后缀，别互相顶替
            _record_run(plan, up["taskId"], temp_queue={"id": qid, "name": name},
                        key_suffix=("#%d" % (gi + 1)) if gi else "",
                        extra={"queue_name": name, "queue_uid": qid, "accounts": g["accounts"],
                               "dir": g["dir"]})
    return out


def _pf_verdict(plan: dict) -> str:
    pf = plan.get("preflight") or {}
    return str(pf.get("verdict") or "-")


def cmd_dispatch(args) -> int:
    """--queue / --script / --account 的统一入口：规划 -> 闸门 -> （apply）执行。"""
    plan = plan_dispatch(args)
    plan["gate_no_go"] = bool(NO_GO and args.apply)
    if NO_GO and args.apply:
        plan["refusals"].append({"gate": "G0", "reason": "dispatch_disabled",
                                 "detail": "MAAORCH_DISPATCH_NO_GO 置位：拒绝一切真发车"})
    # 可达性（dry-run 也要：计划里要显示 uid）
    ok, why = reachable()
    plan["reachable"] = {"ok": ok, "detail": why}
    if not ok:
        plan["refusals"].append({"gate": "G1", "reason": "rest_unreachable", "detail": why})
    if not plan["refusals"]:
        gate_single_flight(plan, args)
        if not any(r["gate"] == "G3" for r in plan["refusals"]):
            gate_lease(plan, args)                     # G3L（P1-2）：跨族实例单飞（真发车前先只读判）
        if not any(r["gate"] in ("G3", "G3L") for r in plan["refusals"]):
            gate_preflight(plan, args)

    hard = [r for r in plan["refusals"]]
    if not hard:
        if not args.apply:
            plan["result"] = {"dry_run": True, "would_dispatch": plan["target"]["token"],
                              "steps": plan["steps"], "note": "加 --apply 真发车"}
            audit({"kind": "dispatch_plan", "target_kind": plan["target"]["kind"],
                   "target": plan["target"]["token"], "mode": plan["steps"][0].get("mode", "") if plan["steps"] else "",
                   "ok": True, "code": None, "taskId": None, "reason": "", "apply": False,
                   "force": bool(args.force), "preflight": _pf_verdict(plan),
                   "queue_id": plan["target"].get("queue_id") or "", "resources": plan["resources"],
                   "temp_queues": [t.get("name") for t in plan["temp_queues"]]})
            _emit(plan, args, render_dispatch(plan))
            return 0
        try:
            lease = lease_acquire_plan(plan, args)      # G3L（P1-2）：拿不到就停 ⇒ LeaseBusy ⇒ 零上游写
            if not lease.get("ok"):
                raise LeaseBusy("实例租约：%s" % (lease.get("detail") or lease.get("status")))
            with _lock(enabled=True):
                if plan["target"]["kind"] == "account":
                    out = execute_temp_queue(plan, args)
                    plan["result"] = {"ok": all(t.get("dispatch", {}).get("ok") for t in out["temp_queues"]),
                                      "temp_queues": out["temp_queues"], "taskIds": out["taskIds"]}
                else:
                    if plan["target"]["kind"] == "queue":
                        qid = plan["target"]["queue_id"] or resolve_queue(plan["target"]["token"])["uid"]
                    else:
                        qid = resolve_script(plan["target"]["token"])["sid"]
                    resp = post("/api/dispatch/start", {"taskId": qid, "mode": "AutoProxy"})
                    up = _upstream_result(resp)
                    audit({"ts": _now(), "kind": "dispatch_ok" if up["ok"] else "dispatch_failed",
                           "target_kind": plan["target"]["kind"], "target": plan["target"]["token"],
                           "mode": "AutoProxy", "ok": bool(up["ok"]), "code": up.get("code"),
                           "taskId": up.get("taskId"), "reason": up.get("reason") or "",
                           "detail": str(up.get("detail") or "")[:300], "queue_id": qid, "apply": True,
                           "force": bool(args.force), "preflight": _pf_verdict(plan),
                           "resources": plan["resources"]})
                    plan["result"] = {"ok": bool(up["ok"]), "taskId": up.get("taskId"),
                                      "code": up.get("code"), "reason": up.get("reason"),
                                      "detail": up.get("detail")}
                    if up["ok"]:
                        _record_run(plan, up["taskId"])
                    else:
                        plan["result"]["hint"] = "上游拒绝/失败：看 reason（target_running/slot_occupied 常见于目标已在跑）"
        except LeaseBusy as e:
            plan["refusals"].append({"gate": "G3L", "reason": "lease_busy", "detail": str(e)})
            audit({"kind": "dispatch_refused", "target_kind": plan["target"]["kind"],
                   "target": plan["target"]["token"], "mode": "AutoProxy", "ok": False, "code": None,
                   "taskId": None, "reason": "lease_busy", "detail": str(e)[:300], "apply": True,
                   "force": bool(args.force), "preflight": _pf_verdict(plan),
                   "lease": lease_brief(plan.get("lease"))})
        except LockBusy as e:
            plan["refusals"].append({"gate": "LOCK", "reason": "concurrent_write", "detail": str(e)})
            audit({"kind": "dispatch_refused", "target_kind": plan["target"]["kind"],
                   "target": plan["target"]["token"], "mode": "AutoProxy", "ok": False, "code": None,
                   "taskId": None, "reason": "concurrent_write", "detail": str(e)[:300],
                   "apply": True, "force": bool(args.force)})
        except Exception as e:  # noqa: BLE001
            plan["refusals"].append({"gate": "EXEC", "reason": "execute_error",
                                     "detail": "%s: %s" % (type(e).__name__, e)})
            audit({"kind": "dispatch_failed", "target_kind": plan["target"]["kind"],
                   "target": plan["target"]["token"], "mode": "AutoProxy", "ok": False, "code": None,
                   "taskId": None, "reason": "execute_error",
                   "detail": ("%s: %s" % (type(e).__name__, e))[:300], "apply": True,
                   "force": bool(args.force)})
        finally:
            lease_settle(plan, args)                    # G3L 收尾：没发出去就立刻放，发出去就留给 release/TTL
    if plan["refusals"] and not hard:
        # 执行期新增的拒绝（LOCK/EXEC）此前会误报 rc=0 —— 与前置拒绝同一出口
        r0 = plan["refusals"][0]
        plan["result"] = {"ok": False, "refused": r0["reason"], "detail": r0.get("detail")}
        _emit(plan, args, render_dispatch(plan))
        return 2
    if hard:
        plan["result"] = {"ok": False, "refused": plan["refusals"][0]["reason"],
                          "detail": plan["refusals"][0]["detail"]}
        audit({"ts": _now(), "kind": "dispatch_refused",
               "target_kind": plan["target"]["kind"] if plan["target"] else "-",
               "target": plan["target"]["token"] if plan["target"] else "-", "mode": "AutoProxy", "ok": False,
               "code": None, "taskId": None, "reason": plan["refusals"][0]["reason"],
               "detail": "；".join("%s:%s" % (r["gate"], r["detail"]) for r in plan["refusals"])[:400],
               "apply": bool(args.apply), "force": bool(args.force),
               "preflight": _pf_verdict(plan)})
        _emit(plan, args, render_dispatch(plan))
        return 2
    _emit(plan, args, render_dispatch(plan))
    return 0 if (plan.get("result") or {}).get("ok", True) else 2


# ---------------------------------------------------------------------------
# --stop / --wake / --list-running / --cleanup / --release
# ---------------------------------------------------------------------------

def cmd_stop(args) -> int:
    out = {"tool": "mas_ondemand", "action": "stop", "generated_at": _now(), "taskId": args.stop,
           "apply": bool(args.apply), "refusals": [], "warnings": []}
    tid = str(args.stop)
    if tid != "ALL" and not UUID_RE.match(tid):
        out["refusals"].append({"reason": "bad_taskid",
                                "detail": "%r 不是任务 uid —— --stop 必须用 /api/dispatch/start 返回的 task_uid" % tid})
    if tid == "ALL" and not args.force:
        out["refusals"].append({"reason": "stop_all_needs_force",
                                "detail": "taskId=ALL 会中止**全部**任务（含产线班次）——复核后用 --force"})
    if not out["refusals"]:
        try:
            queues = fetch_queues()
            scripts = fetch_scripts()
            if tid in queues:
                out["refusals"].append({"reason": "is_queue_uid",
                                        "detail": "这是队列 uid（%s）——stop 吃的是任务 uid；按队列停请用 AUTO-MAS 界面/取消该任务" % queues[tid]})
            elif tid in scripts:
                out["refusals"].append({"reason": "is_script_uid",
                                        "detail": "这是脚本 uid（%s）——stop 吃的是任务 uid；拿 start 返回的 taskId 来" % scripts[tid]["name"]})
            else:
                # app.log 粗判该任务是否在跑（停已结束任务是 no-op，最好让操作者知道）
                try:
                    snap = host_snapshot(events=True, event_n=200, timeout=int(args.timeout))
                    alive, last = task_alive_hint(snap.get("applog") or [], tid)
                    out["live"] = {"seen": alive, "last": last}
                    if not alive:
                        out["warnings"].append("app.log 尾部未见「%s」在跑（可能已结束；停已结束任务上游返回成功但无动作）" % tid[:8])
                except Exception as e:  # noqa: BLE001
                    out["warnings"].append("app.log 判活失败（%s）——按你给的 taskId 直接停" % e)
        except Exception as e:  # noqa: BLE001
            out["refusals"].append({"reason": "rest_unreachable", "detail": "%s: %s" % (type(e).__name__, e)})
    if not out["refusals"]:
        if not args.apply:
            out["result"] = {"dry_run": True, "would_stop": tid, "note": "加 --apply 真停"}
            try:
                st = load_state()
                recs = (task_records(st, tid) if tid != "ALL"
                        else [e for _d, _k, e in ledger.iter_entries(st)
                              if ledger.is_open(e, dt.datetime.now(), HOLD_MIN)])
                out["lease"] = lease_release_records(recs, False, "stop %s" % tid[:8])
            except Exception as e:  # noqa: BLE001
                out["warnings"].append("租约预览失败（不影响 dry-run）：%s: %s" % (type(e).__name__, e))
        elif NO_GO:
            out["refusals"].append({"reason": "dispatch_disabled", "detail": "MAAORCH_DISPATCH_NO_GO 置位"})
        else:
            try:
                with _lock():
                    resp = post("/api/dispatch/stop", {"taskId": tid})
                up = _upstream_result(resp)
                out["result"] = {"ok": bool(up["ok"] or up.get("code") == 200), "code": up.get("code"),
                                 "reason": up.get("reason"), "detail": up.get("detail")}
                audit({"ts": _now(), "kind": "stop_ok" if out["result"]["ok"] else "stop_failed",
                       "target_kind": "task", "target": tid, "mode": "-", "ok": bool(out["result"]["ok"]),
                       "code": up.get("code"), "taskId": tid, "reason": up.get("reason") or "",
                       "detail": str(up.get("detail") or "")[:300], "apply": True, "force": bool(args.force)})
                if out["result"]["ok"]:
                    # P1-2：停成功 ⇒ 该车不再占实例（只放它记录里那位 owner 的租约；TTL 另有兜底）
                    try:
                        st = load_state()
                        recs = (task_records(st, tid) if tid != "ALL"
                                else [e for _d, _k, e in ledger.iter_entries(st)
                                      if ledger.is_open(e, dt.datetime.now(), HOLD_MIN)])
                        out["lease"] = lease_release_records(recs, True, "stop %s" % tid[:8])
                        save_state(st)
                    except Exception as e:  # noqa: BLE001
                        out["warnings"].append("租约释放失败（TTL 会兜底）：%s: %s" % (type(e).__name__, e))
            except LockBusy as e:
                out["refusals"].append({"reason": "concurrent_write", "detail": str(e)})
            except Exception as e:  # noqa: BLE001
                out["refusals"].append({"reason": "stop_error", "detail": "%s: %s" % (type(e).__name__, e)})
    _emit(out, args, render_stop(out))
    return 2 if out["refusals"] else 0


def task_alive_hint(lines: list, tid: str) -> tuple:
    """从 app.log 事件行粗判任务是否在跑：{seen, last}。"""
    seen, last = False, ""
    for ln in lines:
        if tid in ln:
            last = ln[:160]
            if "任务结束:" in ln or "中止任务:" in ln:
                seen = False
            elif "开始运行任务:" in ln or "创建任务:" in ln:
                seen = True
    return seen, last


def cmd_wake(args) -> int:
    """--wake：ManualReview（只唤醒）。不改配置、不跑链——但唤醒后要客户端点「是」。"""
    plan = {"tool": "mas_ondemand", "action": "wake", "generated_at": _now(), "apply": bool(args.apply),
            "refusals": [], "warnings": [], "target": None, "resources": None, "preflight": None}
    try:
        s = resolve_script(args.wake)
    except TargetError as e:
        plan["refusals"].append({"gate": "G2", "reason": "script_not_found", "detail": str(e)})
        _emit(plan, args, render_dispatch(plan))
        return 2
    plan["target"] = make_target("wake", s["name"], uid=s["sid"])
    scripts = fetch_scripts()
    plan["resources"] = resources_of_scripts({s["sid"]}, scripts)
    users = fetch_users(s["sid"])
    en = [u["name"] for u in users if u["status"] and u.get("remained_day") != 0]
    plan["enabled_accounts"] = [{"script": s["name"], "enabled": en}]
    plan["warnings"].append("ManualReview 唤醒成功后会等 AUTO-MAS 客户端回答「请检查用户代理情况…」"
                            "（无客户端在线会一直挂着、脚本锁不释放）⇒ apply 需 --force 明示")
    plan["steps"] = [{"op": "dispatch_start", "taskId": s["sid"], "mode": "ManualReview"}]
    ok, why = reachable()
    plan["reachable"] = {"ok": ok, "detail": why}
    if not ok:
        plan["refusals"].append({"gate": "G1", "reason": "rest_unreachable", "detail": why})
    if args.apply and not args.force:
        plan["refusals"].append({"gate": "G5", "reason": "wake_needs_force",
                                 "detail": "ManualReview 会等待客户端应答；确认有客户端在线再加 --force"})
    if not plan["refusals"]:
        gate_single_flight(plan, args)
        if not any(r["gate"] == "G3" for r in plan["refusals"]):
            gate_lease(plan, args)                     # G3L（P1-2）：唤醒也占实例，同一条跨族闸门
        if not any(r["gate"] in ("G3", "G3L") for r in plan["refusals"]):
            gate_preflight(plan, args)
    if [r for r in plan["refusals"]]:
        plan["result"] = {"ok": False, "refused": plan["refusals"][0]["reason"],
                          "detail": plan["refusals"][0]["detail"]}
        audit({"kind": "dispatch_refused", "target_kind": "wake",
               "target": plan["target"]["token"] if plan["target"] else str(args.wake), "mode": "ManualReview",
               "ok": False, "code": None, "taskId": None, "reason": plan["refusals"][0]["reason"],
               "detail": "；".join("%s:%s" % (r["gate"], r["detail"]) for r in plan["refusals"])[:400],
               "apply": bool(args.apply), "force": bool(args.force), "preflight": _pf_verdict(plan)})
        _emit(plan, args, render_dispatch(plan))
        return 2
    if not args.apply:
        plan["result"] = {"dry_run": True, "would_wake": s["name"], "note": "加 --apply --force 真发"}
        audit({"kind": "dispatch_plan", "target_kind": "wake", "target": s["name"], "mode": "ManualReview",
               "ok": True, "code": None, "taskId": None, "reason": "", "apply": False,
               "force": bool(args.force), "preflight": _pf_verdict(plan), "resources": plan["resources"]})
        _emit(plan, args, render_dispatch(plan))
        return 0
    if NO_GO:
        plan["refusals"].append({"gate": "G0", "reason": "dispatch_disabled", "detail": "MAAORCH_DISPATCH_NO_GO 置位"})
        audit({"kind": "dispatch_refused", "target_kind": "wake", "target": s["name"], "mode": "ManualReview",
               "ok": False, "code": None, "taskId": None, "reason": "dispatch_disabled",
               "detail": "MAAORCH_DISPATCH_NO_GO 置位", "apply": True, "force": bool(args.force)})
        _emit(plan, args, render_dispatch(plan))
        return 2
    try:
        lease = lease_acquire_plan(plan, args)         # G3L（P1-2）：拿不到就停（零上游写）
        if not lease.get("ok"):
            raise LeaseBusy("实例租约：%s" % (lease.get("detail") or lease.get("status")))
        try:
            with _lock():
                resp = post("/api/dispatch/start", {"taskId": s["sid"], "mode": "ManualReview"})
                up = _upstream_result(resp)
        finally:
            lease_settle(plan, args)                   # 没发出去 ⇒ 立刻放；发出去了 ⇒ 留给 release/TTL
        plan["result"] = {"ok": bool(up["ok"]), "taskId": up.get("taskId"), "code": up.get("code"),
                          "reason": up.get("reason"), "detail": up.get("detail")}
        audit({"ts": _now(), "kind": "dispatch_ok" if up["ok"] else "dispatch_failed", "target_kind": "wake",
               "target": s["name"], "mode": "ManualReview", "ok": bool(up["ok"]), "code": up.get("code"),
               "taskId": up.get("taskId"), "reason": up.get("reason") or "",
               "detail": str(up.get("detail") or "")[:300], "apply": True, "force": bool(args.force),
               "preflight": _pf_verdict(plan), "resources": plan["resources"]})
        if up["ok"]:
            _record_run(plan, up["taskId"])
    except LeaseBusy as e:
        plan["refusals"].append({"gate": "G3L", "reason": "lease_busy", "detail": str(e)})
        audit({"kind": "dispatch_refused", "target_kind": "wake", "target": s["name"], "mode": "ManualReview",
               "ok": False, "code": None, "taskId": None, "reason": "lease_busy", "detail": str(e)[:300],
               "apply": True, "force": bool(args.force), "lease": lease_brief(plan.get("lease"))})
        _emit(plan, args, render_dispatch(plan))
        return 2
    except LockBusy as e:
        plan["refusals"].append({"gate": "LOCK", "reason": "concurrent_write", "detail": str(e)})
        _emit(plan, args, render_dispatch(plan))
        return 2
    except Exception as e:  # noqa: BLE001
        plan["refusals"].append({"gate": "EXEC", "reason": "execute_error", "detail": "%s: %s" % (type(e).__name__, e)})
        _emit(plan, args, render_dispatch(plan))
        return 2
    _emit(plan, args, render_dispatch(plan))
    return 0 if plan["result"].get("ok") else 2


def ondemand_open_rows(st: dict) -> list:
    """共享账本在窗记录（含 MCP 端）→ --list-running 展示行。"""
    return [{"key": r.get("key"), "src": r.get("src") or "?", "kind": r.get("kind"),
             "target": r.get("target"), "ts": r.get("ts"), "taskId": r.get("task_uid"),
             "busy_until": r.get("busy_until"), "superseded_by": r.get("superseded_by") or ""}
            for r in open_runs(st)]


def cmd_list_running(args) -> int:
    out = {"tool": "mas_ondemand", "action": "list_running", "generated_at": _now()}
    try:
        snap = host_snapshot(events=True, event_n=int(args.events), timeout=int(args.timeout))
    except Exception as e:  # noqa: BLE001
        out["error"] = "%s: %s" % (type(e).__name__, e)
        _emit(out, args, "现场快照失败：%s" % out["error"])
        return 2
    scripts = {}
    queues = {}
    with contextlib.suppress(Exception):
        scripts = fetch_scripts()
    with contextlib.suppress(Exception):
        queues = fetch_queues()
    sid_name = {k: v["name"] for k, v in scripts.items()}
    tasks = parse_task_events(snap.get("applog") or [], sid_name, queues)
    st = load_state()
    out.update({"maa": snap["maa"], "vmm": sorted(snap["vmm"].keys(), key=lambda x: int(x) if x.isdigit() else 0),
                "tasks": tasks["tasks"], "events": tasks["events"][-40:],
                "ledger": ledger.ledger_path(), "ondemand_open": ondemand_open_rows(st)})
    _emit(out, args, render_running(out, snap))
    return 0


def parse_task_events(lines: list, sid_name: dict, queue_names: dict) -> dict:
    """app.log 事件行 -> 时间线 + 在跑任务（开始/创建 未见 结束）。"""
    ev, tasks = [], {}
    for ln in lines:
        m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", ln)
        ts = m.group(1)[11:] if m else ""
        kind, key_hit = "", ""
        for key, name in (("创建任务:", "created"), ("开始运行任务:", "running"), ("任务开始:", "script_start"),
                          ("任务结束:", "done"), ("中止任务:", "stopping"), ("已解锁脚本配置", "unlock"),
                          ("跳过任务:", "skip"), ("启动MAA进程:", "maa_launch"), ("开始启动模拟器", "emu_launch")):
            if key in ln:
                kind, key_hit = name, key
                break
        uid = ""
        mu = re.search(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})", ln)
        if mu:
            uid = mu.group(1)
        elif kind in ("script_start", "unlock", "skip"):
            toks = [t for t in re.split(r"[\s,，;；]+", ln.split(key_hit, 1)[-1]) if t]
            if toks:
                uid = toks[0].rstrip(")。，")
        label = ""
        if kind in ("created", "running") and uid:
            label = uid
        elif kind == "script_start" and uid:
            label = "%s (%s)" % (sid_name.get(uid, "?"), uid[:8])
        elif kind == "skip" and uid:
            label = "%s (%s)" % (sid_name.get(uid, "?"), uid[:8])
        elif kind == "unlock" and uid:
            label = "%s (%s)" % (sid_name.get(uid, "?"), uid[:8])
        ev.append({"t": ts, "kind": kind, "uid": uid, "label": label, "raw": ln[-200:]})
        if kind in ("created", "running") and uid:
            t = tasks.setdefault(uid, {"taskId": uid, "created": "", "running": "", "done": "",
                                       "mode": "", "source": "", "scripts": []})
            t[kind] = ts or t[kind]
            mm = re.search(r"模式: (\w+)", ln)
            if mm:
                t["mode"] = mm.group(1)
            ms = re.search(r"触发来源: (\w+)", ln)
            if ms:
                t["source"] = ms.group(1)
        elif kind == "done" and uid in tasks:
            tasks[uid]["done"] = ts or tasks[uid]["done"]
        elif kind == "script_start" and uid:
            cand = [t for t in tasks.values() if not t["done"] and t["running"] and t["running"] <= (ts or "99:99:99")]
            if cand:
                cand.sort(key=lambda t: t["running"])
                cand[-1]["scripts"].append("%s@%s" % (sid_name.get(uid, uid[:8]), ts))
    live = [t for t in tasks.values() if (t["running"] or t["created"]) and not t["done"]]
    live.sort(key=lambda t: t["running"] or t["created"])
    return {"tasks": live, "events": ev}


def cmd_cleanup(args) -> int:
    """删除本工具建的临时队列（默认「临时-按需-*」；给名字/uid 只删那一条）。"""
    out = {"tool": "mas_ondemand", "action": "cleanup", "generated_at": _now(),
           "apply": bool(args.apply), "refusals": [], "warnings": [], "queues": []}
    try:
        queues = fetch_queues()
    except Exception as e:  # noqa: BLE001
        out["refusals"].append({"reason": "rest_unreachable", "detail": "%s: %s" % (type(e).__name__, e)})
        _emit(out, args, render_cleanup(out))
        return 2
    st = load_state()
    def _matches_open(uid: str, name: str):
        return next((r for r in open_runs(st)
                     if (r.get("temp_queue") or {}).get("id") == uid or r.get("queue_uid") == uid
                     or (r.get("temp_queue") or {}).get("name") == name or r.get("queue_name") == name
                     or r.get("target") == name), None)
    cand = []
    if args.cleanup is True:
        # 只按名字前缀（不看 state）：本工具 + MCP 端建的都在这个前缀下
        cand = [(uid, n) for uid, n in queues.items() if n.startswith(TEMP_PREFIX)]
    else:
        token = str(args.cleanup)
        if token in queues:
            cand = [(token, queues[token])]
        else:
            cand = [(uid, n) for uid, n in queues.items() if n == token]
        if not cand:
            out["refusals"].append({"reason": "queue_not_found",
                                    "detail": "找不到队列 %r（--cleanup 不给参数=删全部 %s-*）" % (token, TEMP_PREFIX)})
    for uid, name in cand:
        rec = {"queueId": uid, "name": name}
        if not name.startswith(TEMP_PREFIX):
            rec["skip"] = "非本工具前缀（%s）——不删" % TEMP_PREFIX
            out["queues"].append(rec)
            continue
        open_run = _matches_open(uid, name)
        if open_run and not args.force:
            rec["skip"] = "对应任务可能仍在跑（src=%s busy 至 %s，taskId=%s）——删了会打断任务收尾；确认后 --force" % (
                open_run.get("src") or "?", open_run.get("busy_until"), str(open_run.get("task_uid"))[:8])
            out["queues"].append(rec)
            out["warnings"].append(rec["skip"])
            continue
        if not args.apply:
            rec["dry_run"] = "将删除"
            out["queues"].append(rec)
            continue
        if NO_GO:
            out["refusals"].append({"reason": "dispatch_disabled", "detail": "MAAORCH_DISPATCH_NO_GO 置位"})
            break
        try:
            with _lock():
                r = post("/api/queue/delete", {"queueId": uid})
            rec["delete"] = {"code": r.get("code"), "message": r.get("message")}
            st2 = load_state()
            for tq in st2.get("temp_queues") or []:
                if tq.get("id") == uid:
                    tq["deleted"] = True
                    tq["deleted_at"] = _now()
            for _d, _k, r2 in ledger.iter_entries(st2):
                if (r2.get("temp_queue") or {}).get("id") == uid or r2.get("queue_uid") == uid:
                    r2["temp_queue_deleted"] = True
            save_state(st2)
            audit({"ts": _now(), "kind": "cleanup_ok" if r.get("code") == 200 else "cleanup_failed",
                   "target_kind": "temp_queue", "target": name, "mode": "-", "ok": r.get("code") == 200,
                   "code": r.get("code"), "taskId": None, "reason": "" if r.get("code") == 200 else "delete_failed",
                   "detail": str(r.get("message") or "")[:200], "queue_id": uid, "apply": True,
                   "force": bool(args.force)})
        except Exception as e:  # noqa: BLE001
            rec["delete"] = {"error": "%s: %s" % (type(e).__name__, e)}
        out["queues"].append(rec)
    _emit(out, args, render_cleanup(out))
    return 2 if out["refusals"] else 0


def cmd_release(args, apply_: bool) -> int:
    """人工确认某目标的活已结束：关掉它的在场窗口（解锁同目标/同资源重发）。

    读共享账本真实条目（含 MCP 端发的车；含被顶替未出窗的 prev）。
    """
    out = {"tool": "mas_ondemand", "action": "release", "generated_at": _now(),
           "apply": bool(apply_), "refusals": [], "released": []}
    token = str(args.release)
    st = load_state()
    now = dt.datetime.now()
    runs = []
    for _d, key, e in ledger.iter_entries(st):
        if not ledger.is_open(e, now, HOLD_MIN):
            continue
        if not (e.get("run_id") == token or e.get("target") == token or key == token
                or str(e.get("task_uid") or "") == token):
            continue
        runs.append(e)
        runs.extend([p for p in e.get("prev") or [] if isinstance(p, dict) and ledger.is_open(p, now, HOLD_MIN)])
    if not runs:
        out["refusals"].append({"reason": "no_open_run", "detail": "没有匹配的在窗记录（--list-running 可看在窗记录）"})
        _emit(out, args, render_release(out))
        return 2
    for r in runs:
        if not apply_:
            out["released"].append({"run_id": r.get("run_id"), "src": r.get("src") or "?", "target": r.get("target"),
                                    "busy_until": r.get("busy_until"), "dry_run": True})
            continue
        r["busy_until"] = (now - dt.timedelta(seconds=1)).strftime(TS_FMT)
        r["released_at"] = _now()
        r["released_by"] = "manual_release"
        out["released"].append({"run_id": r.get("run_id"), "src": r.get("src") or "?",
                                "target": r.get("target"), "released_at": r["released_at"]})
    out["lease"] = lease_release_records(runs, apply_, "release %s" % token)
    if apply_:
        save_state(st)
        audit({"ts": _now(), "kind": "release", "target_kind": "-", "target": token, "mode": "-", "ok": True,
               "code": None, "taskId": None, "reason": "", "detail": "人工放行：%d 条在窗记录已关" % len(out["released"]),
               "apply": True})
    _emit(out, args, render_release(out))
    return 0


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------

def render_dispatch(plan: dict) -> str:
    L = []
    t = plan.get("target") or {}
    kind = {"queue": "队列", "script": "脚本", "account": "单号", "wake": "唤醒"}.get(t.get("kind"), t.get("kind") or "-")
    L.append("按需发车（%s）· %s · %s" % ("apply" if plan.get("apply") else "dry-run", kind, t.get("token") or "-"))
    if plan.get("reachable") is not None:
        L.append("  REST: %s (%s)" % ("OK" if plan["reachable"]["ok"] else "UNREACHABLE", plan["reachable"]["detail"]))
    res = plan.get("resources") or {}
    if res:
        L.append("  资源: 目录=%s 实例=%s 脚本=%s" % (
            ",".join(os.path.basename(d) for d in res.get("dirs") or []) or "-",
            ",".join(res.get("emus") or []) or "-",
            "、".join(res.get("names") or []) or "-"))
    for ea in plan.get("enabled_accounts") or []:
        L.append("  启用号(%s): %s" % (ea["script"], "、".join(ea.get("enabled") or []) or "（无）"))
    for g in plan.get("groups") or []:
        for d, accts in g.items():
            L.append("  临时队列 ← %s: %s" % (os.path.basename(d), "、".join(accts)))
    pf = plan.get("preflight")
    if pf:
        L.append("  preflight: %s%s" % (pf.get("verdict"),
                                        ("（不过：%s）" % ",".join(pf["failing"])) if pf.get("failing") else ""))
    for c in plan.get("single_flight") or []:
        L.append("  单飞冲突: %s — %s" % (c.get("kind"), c.get("detail")))
    lz = plan.get("lease")
    if lz:
        L.append("  实例租约: %s（占用=%s；idx=%s；owner=%s）" % (
            "OK" if lz.get("ok") else lz.get("status"),
            ", ".join(str((lz.get("rows") or {}).get(i, {}).get("holder") or "-") for i in (lz.get("idxs") or []))
            or "-", ",".join(lz.get("idxs") or []) or "-", lz.get("owner") or "-"))
    for w in plan.get("warnings") or []:
        L.append("  WARN: %s" % w)
    for r in plan.get("refusals") or []:
        L.append("  拒绝[%s]: %s — %s" % (r.get("gate"), r.get("reason"), r.get("detail")))
    rr = plan.get("result") or {}
    if rr.get("dry_run"):
        L.append("  ⇒ [dry-run] %s；加 --apply 真发车" % rr.get("note", ""))
    elif rr:
        L.append("  ⇒ %s" % json.dumps(rr, ensure_ascii=False)[:400])
    return "\n".join(L)


def render_stop(out: dict) -> str:
    L = ["停任务（%s）· %s" % ("apply" if out.get("apply") else "dry-run", out.get("taskId"))]
    if out.get("live"):
        L.append("  app.log: %s" % ("在跑" if out["live"]["seen"] else "未见在跑"))
    for w in out.get("warnings") or []:
        L.append("  WARN: %s" % w)
    for r in out.get("refusals") or []:
        L.append("  拒绝: %s — %s" % (r.get("reason"), r.get("detail")))
    for r in out.get("lease") or []:
        L.append("  租约: %s" % json.dumps(r, ensure_ascii=False)[:220])
    if out.get("result"):
        L.append("  ⇒ %s" % json.dumps(out["result"], ensure_ascii=False)[:300])
    return "\n".join(L)


def render_running(out: dict, snap: dict) -> str:
    L = ["在跑现场（只读）· %s" % out.get("generated_at")]
    if snap.get("maa"):
        for m in snap["maa"]:
            L.append("  槽位 %-7s pid=%-6s 起 %s" % (os.path.basename(m.get("dir") or m.get("exe") or "?"),
                                                   m.get("pid"), m.get("start")))
    else:
        L.append("  槽位：无 MAA.exe 在跑")
    vmm = sorted(snap.get("vmm") or {}, key=lambda x: int(x) if str(x).isdigit() else 0)
    L.append("  实例在跑: %s" % (", ".join("idx%s" % i for i in vmm) or "（无）"))
    if out.get("tasks"):
        L.append("  在跑任务（app.log 有开始未见结束）:")
        for t in out["tasks"]:
            L.append("    %s 模式=%s 来源=%s 起=%s 脚本=%s" % (t["taskId"][:8], t.get("mode") or "?",
                                                             t.get("source") or "?", t.get("running") or t.get("created"),
                                                             "、".join(t.get("scripts") or []) or "-"))
    else:
        L.append("  在跑任务：app.log 尾部未见（可能刚结束或日志已滚）")
    if out.get("ondemand_open"):
        L.append("  在窗记录（共享账本%s，含 MCP 端）: %s" % (
            " " + (out.get("ledger") or "") if out.get("ledger") else "",
            "; ".join("[%s]%s@%s(busy至 %s)" % (r.get("src") or "?", r.get("target"), r.get("ts"),
                                                r.get("busy_until")) for r in out["ondemand_open"])))
    L.append("  最近事件:")
    for e in (out.get("events") or [])[-15:]:
        L.append("    %s %-12s %s" % (e["t"], e["kind"], e["label"] or e["raw"][:110]))
    return "\n".join(L)


def render_cleanup(out: dict) -> str:
    L = ["清理临时队列（%s）" % ("apply" if out.get("apply") else "dry-run")]
    if not (out.get("queues") or []):
        L.append("  （无候选：没有 %s-* 队列%s）" % (TEMP_PREFIX, "" if out.get("apply") else "；加 --apply 才真删"))
    for q in out.get("queues") or []:
        L.append("  %-28s %s%s" % (q.get("name"), json.dumps({k: v for k, v in q.items()
                                                             if k not in ("name", "queueId")}, ensure_ascii=False)[:180],
                                   " id=%s" % q.get("queueId", "")[:8]))
    for w in out.get("warnings") or []:
        L.append("  WARN: %s" % w)
    for r in out.get("refusals") or []:
        L.append("  拒绝: %s — %s" % (r.get("reason"), r.get("detail")))
    return "\n".join(L)


def render_release(out: dict) -> str:
    L = ["放行在窗记录（%s）" % ("apply" if out.get("apply") else "dry-run")]
    for r in out.get("released") or []:
        L.append("  %s" % json.dumps(r, ensure_ascii=False)[:200])
    for r in out.get("lease") or []:
        L.append("  租约: %s" % json.dumps(r, ensure_ascii=False)[:220])
    for r in out.get("refusals") or []:
        L.append("  拒绝: %s — %s" % (r.get("reason"), r.get("detail")))
    return "\n".join(L)


def _emit(obj, args, text: str) -> None:
    if getattr(args, "json", False):
        print(json.dumps(obj, ensure_ascii=False))
    else:
        print(text)


# ---------------------------------------------------------------------------
# 离线自测（假传输；不碰网络、不碰真 runtime、不发任何真请求）
# ---------------------------------------------------------------------------

class _FakeMAS:
    """AUTO-MAS 传输桩：覆盖本工具用到的全部端点 + 调用流水。"""

    def __init__(self):
        self.reset()

    def reset(self):
        self.calls = []
        self.fail_start = None
        self.scripts = {
            "sid-a": {"Info": {"Name": "脚本A", "Path": r"E:\MAA-Multi\MAA_1"}, "Emulator": {"Index": 1}},
            "sid-b": {"Info": {"Name": "脚本B", "Path": r"E:\MAA-Multi\MAA_2"}, "Emulator": {"Index": 2}},
            "sid-c": {"Info": {"Name": "脚本C", "Path": r"E:\MAA-Multi\MAA_1"}, "Emulator": {"Index": 3}},
        }
        self.users = {
            "sid-a": {"u1": {"Info": {"Name": "号一", "Status": True, "Mode": "简洁", "Server": "Official"}},
                      "u2": {"Info": {"Name": "号二", "Status": False, "Mode": "简洁", "Server": "Official"}},
                      "u5": {"Info": {"Name": "号多", "Status": True, "Mode": "简洁", "Server": "Official"}}},
            "sid-b": {"u3": {"Info": {"Name": "号三", "Status": True, "Mode": "简洁", "Server": "Bilibili"}},
                      "u6": {"Info": {"Name": "号多", "Status": True, "Mode": "简洁", "Server": "Bilibili"}}},
            "sid-c": {},
        }
        self.queues = {"qid-1": {"Info": {"Name": "日常定时9"}},
                       "qid-temp": {"Info": {"Name": "临时-按需-20260101_0000-1"}}}
        self.items = {"qid-1": {"i1": {"Info": {"ScriptId": "sid-a"}}},
                      "qid-temp": {"i2": {"Info": {"ScriptId": "sid-b"}}}}
        self.n_task = 0

    def post(self, path, body=None, timeout=60):
        body = body if isinstance(body, dict) else {}
        self.calls.append((path, body))
        if path == "/api/scripts/get":
            return {"code": 200, "data": self.scripts}
        if path == "/api/scripts/user/get":
            return {"code": 200, "data": self.users.get(str(body.get("scriptId") or ""), {})}
        if path == "/api/queue/get":
            return {"code": 200, "data": self.queues}
        if path == "/api/queue/item/get":
            return {"code": 200, "data": self.items.get(str(body.get("queueId")), {})}
        if path == "/api/queue/add":
            qid = "qid-new-%d" % len(self.calls)
            self.queues[qid] = {"Info": {"Name": ""}}
            self.items[qid] = {}
            return {"code": 200, "queueId": qid, "data": {}}
        if path == "/api/queue/update":
            qid = str(body.get("queueId"))
            self.queues.setdefault(qid, {"Info": {}}).setdefault("Info", {}).update(
                (body.get("data") or {}).get("Info") or {})
            return {"code": 200, "message": "操作成功"}
        if path == "/api/queue/delete":
            self.queues.pop(str(body.get("queueId")), None)
            return {"code": 200, "message": "操作成功"}
        if path == "/api/queue/item/add":
            qid = str(body.get("queueId"))
            iid = "iid-%d" % len(self.calls)
            self.items.setdefault(qid, {})[iid] = {"Info": {}}
            return {"code": 200, "queueItemId": iid}
        if path == "/api/queue/item/update":
            qid, iid = str(body.get("queueId")), str(body.get("queueItemId"))
            self.items.setdefault(qid, {}).setdefault(iid, {"Info": {}}).setdefault("Info", {}).update(
                (body.get("data") or {}).get("Info") or {})
            return {"code": 200, "message": "操作成功"}
        if path == "/api/dispatch/start":
            if self.fail_start:
                r, self.fail_start = self.fail_start, None
                return r
            self.n_task += 1
            return {"code": 200, "status": "success", "message": "操作成功", "taskId": "task-%d" % self.n_task}
        if path == "/api/dispatch/stop":
            return {"code": 200, "status": "success", "message": "操作成功"}
        return {"code": 404, "message": "unknown endpoint %s" % path}


def selftest() -> dict:
    """离线回归：闸门/解析/发车/临时队列/停/清/放行/唤醒 全链路（假传输）。"""
    import io
    import shutil
    import tempfile

    cases = []

    def add(name, fn):
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                fn()
            cases.append({"name": name, "ok": True, "detail": ""})
        except Exception as e:  # noqa: BLE001
            cases.append({"name": name, "ok": False, "detail": "%s: %s" % (type(e).__name__, e)})

    def ck(cond, msg):
        if not cond:
            raise AssertionError(msg)

    tmp = tempfile.mkdtemp(prefix="mas_ondemand_selftest_")
    saved_globals = {k: globals()[k] for k in ("RUNTIME", "LOG", "LOG_CALLS", "LOCK", "NO_GO")}
    saved_fns = {k: globals()[k] for k in ("post", "reachable", "run_preflight", "host_snapshot",
                                           "_mas_dispatch_windows")}
    saved_ledger_runtime = ledger.RUNTIME
    F = _FakeMAS()
    ledger.RUNTIME = tmp                      # 共享账本也指向自测沙箱
    lease_rt = os.path.join(tmp, "lease")     # 实例租约账本也进沙箱（P1-2）：绝不动 runtime/ 真账本
    saved_lease_env = os.environ.get("MAAORCH_LEASE_RUNTIME")
    os.environ["MAAORCH_LEASE_RUNTIME"] = lease_rt
    _lg0 = lease_mod()
    if _lg0 is not None:
        _lg0.set_runtime(lease_rt)             # 胶水在位才切（不在位 ⇒ G3L 全跳过，自测会自曝）
    globals()["RUNTIME"] = tmp
    globals()["LOG"] = os.path.join(tmp, "mas_dispatch_log.jsonl")
    globals()["LOG_CALLS"] = os.path.join(tmp, "mas_ondemand_log.jsonl")
    globals()["LOCK"] = os.path.join(tmp, "mas_dispatch.lock")

    def pf_go(kind, name, timeout=900):
        return {"verdict": "GO", "exit": 0, "failing": [], "reasons": [], "raw": ""}

    def pf_nogo(kind, name, timeout=900):
        return {"verdict": "NO-GO", "exit": 1, "failing": ["slot_idle"], "reasons": ["槽位忙"], "raw": ""}

    def snap_idle(events=False, event_n=80, timeout=120):
        return {"maa": [], "vmm": {}, "applog": []}

    def reset():
        F.reset()
        globals()["post"] = F.post
        globals()["reachable"] = lambda: (True, "code=200")
        globals()["run_preflight"] = pf_go
        globals()["host_snapshot"] = snap_idle
        globals()["_mas_dispatch_windows"] = lambda now=None: []
        globals()["NO_GO"] = False
        for f in ("mas_dispatch_log.jsonl", "mas_ondemand_log.jsonl", "mas_dispatch.lock",
                  "mas_ondemand_ledger.json", "mas_ondemand_ledger.json.tmp",
                  "mas_ondemand_state.json", "mas_ondemand_mcp_state.json",
                  os.path.join("lease", "instance_leases.json"), os.path.join("lease", "instance_leases.lock")):
            with contextlib.suppress(OSError):
                os.remove(os.path.join(tmp, f))

    def NS(**kw):
        d = {"apply": False, "json": True, "force": False, "skip_preflight": False, "timeout": 30,
             "preflight_timeout": 30, "events": 60, "queue": None, "script": None, "account": None,
             "wake": None, "stop": None, "cleanup": None, "release": None, "lease_owner": None}
        d.update(kw)
        return argparse.Namespace(**d)

    def _read_jsonl(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return [json.loads(x) for x in f if x.strip()]
        except OSError:
            return []

    def logs():
        """事件流（mas_dispatch_log.jsonl：真动作）。"""
        return _read_jsonl(os.path.join(tmp, "mas_dispatch_log.jsonl"))

    def logs_calls():
        """按需调用流（mas_ondemand_log.jsonl：含干跑/被拒）。"""
        return _read_jsonl(os.path.join(tmp, "mas_ondemand_log.jsonl"))

    def starts():
        return [b for p, b in F.calls if p == "/api/dispatch/start"]

    # ---- 解析与计划 --------------------------------------------------------
    def c_plan_queue():
        reset()
        p = plan_dispatch(NS(queue="日常定时9"))
        ck(p["target"]["queue_id"] == "qid-1", "队列 uid 解析错：%r" % p["target"])
        ck(p["steps"][0]["taskId"] == "qid-1", "step 错：%r" % p["steps"])
        ck(p["resources"]["dirs"] == [dkey(r"E:\MAA-Multi\MAA_1")], "资源错：%r" % p["resources"])

    def c_plan_bad_uid():
        reset()
        p = plan_dispatch(NS(queue="00000000-0000-0000-0000-000000000000"))
        ck(any("找不到队列" in r["detail"] for r in p["refusals"]), "坏 uid 应被拒：%r" % p["refusals"])

    def c_plan_script_uid():
        reset()
        p = plan_dispatch(NS(script="sid-b"))
        ck(p["target"]["token"] == "脚本B" and p["target"]["token_kind"] == "uid", "脚本 uid 解析错：%r" % p["target"])

    # ---- 发车 dry-run / apply --------------------------------------------
    def c_dry_run_no_write():
        reset()
        rc = cmd_dispatch(NS(queue="日常定时9"))
        ck(rc == 0, "dry-run 应 0，实得 %s" % rc)
        ck(not starts(), "dry-run 不得发 dispatch POST：%r" % F.calls[-3:])
        ck(not [c for c in F.calls if c[0] in WRITE_ENDPOINTS], "dry-run 不得触任何写端点：%r" % F.calls)
        # 审计分流：干跑行只进按需调用流，不进事件流（行数≠发车数不再被污染）
        ck(any(x.get("kind") == "dispatch_plan" for x in logs_calls()), "干跑应落调用流：%r" % logs_calls())
        ck(not logs(), "干跑不得写事件流：%r" % logs())

    def c_apply_queue():
        reset()
        rc = cmd_dispatch(NS(queue="日常定时9", apply=True))
        ck(rc == 0, "apply 应 0，实得 %s" % rc)
        ck(starts() and starts()[0]["taskId"] == "qid-1" and starts()[0]["mode"] == "AutoProxy",
           "dispatch start 入参错：%r" % starts())
        lg = logs()
        ck(any(x["kind"] == "dispatch_ok" and x["source"] == "ondemand" and x["taskId"] == "task-1"
               and x["target_kind"] == "queue" for x in lg), "审计未落 dispatch_ok：%r" % lg)
        ck(any(x.get("kind") == "dispatch_ok" and x.get("src") == "cli" for x in logs_calls()),
           "真动作也应落调用流：%r" % logs_calls())
        st = load_state()
        op = open_runs(st)
        ck(len(op) == 1 and op[0]["target"] == "日常定时9" and op[0]["src"] == "cli"
           and op[0]["task_uid"] == "task-1", "共享账本未记在场：%r" % st)
        ck(os.path.exists(os.path.join(tmp, "mas_ondemand_ledger.json")), "账本文件未落盘")

    def c_apply_script():
        reset()
        rc = cmd_dispatch(NS(script="脚本B", apply=True))
        ck(rc == 0 and starts()[0]["taskId"] == "sid-b", "脚本发车错：rc=%s %r" % (rc, starts()))

    def c_account_apply_temp_queue():
        reset()
        rc = cmd_dispatch(NS(account="号一", apply=True))
        ck(rc == 0, "单号发车应 0，实得 %s" % rc)
        adds = [b for p, b in F.calls if p == "/api/queue/add"]
        ck(len(adds) == 1, "应建 1 条临时队列：%r" % F.calls)
        qid = [q for q, v in F.queues.items() if q.startswith("qid-new-")
               and str((v.get("Info") or {}).get("Name") or "").startswith(TEMP_PREFIX)]
        ck(len(qid) == 1, "临时队列名未按前缀：%r" % F.queues)
        ck(starts() and starts()[0]["taskId"] == qid[0], "发车目标应是临时队列：%r" % starts())
        ck(any(str((v.get("Info") or {}).get("ScriptId")) == "sid-a"
               for v in F.items[qid[0]].values()), "临时队列项应指向该号所在脚本：%r" % F.items[qid[0]])
        st = load_state()
        ck(st["temp_queues"] and st["temp_queues"][-1]["id"] == qid[0], "state 未记临时队列：%r" % st)
        ent = st[ledger.now_str()[:10]].get("a:号一") or {}
        ck(ent.get("src") == "cli" and ent.get("uid") == qid[0] and (ent.get("temp_queue") or {}).get("id") == qid[0],
           "共享账本未记单号车（key/uid/temp_queue）：%r" % st)
        lg = logs()
        ck(any(x.get("temp_queue", "").startswith(TEMP_PREFIX) and x["target_kind"] == "account" for x in lg),
           "审计未带临时队列：%r" % lg)
        ck(any(x.get("verdict") is None and x.get("kind") == "dispatch_ok" and x.get("src") == "cli"
               for x in logs_calls()), "调用流未记单号真动作：%r" % logs_calls())

    def c_account_disabled():
        reset()
        rc = cmd_dispatch(NS(account="号二", apply=True))
        ck(rc == 2, "停用号应拒，实得 %s" % rc)
        ck(not [c for c in F.calls if c[0] in WRITE_ENDPOINTS], "拒绝路径不得写上游：%r" % F.calls)

    def c_account_ambiguous():
        reset()
        rc = cmd_dispatch(NS(account="号多"))
        ck(rc == 2, "同名多脚本应拒，实得 %s" % rc)

    def c_account_unknown():
        reset()
        rc = cmd_dispatch(NS(account="号不存在"))
        ck(rc == 2, "不存在账号应拒，实得 %s" % rc)

    # ---- 单飞 / 槽位 / 班次补窗 --------------------------------------------
    def c_duplicate_inflight():
        reset()
        ck(cmd_dispatch(NS(queue="日常定时9", apply=True)) == 0, "第一次应发成功")
        n0 = len(starts())
        rc = cmd_dispatch(NS(queue="日常定时9", apply=True))
        ck(rc == 2 and len(starts()) == n0, "同目标重复发车应拒且不发 POST：rc=%s" % rc)
        rc2 = cmd_dispatch(NS(queue="日常定时9", apply=True, force=True))
        ck(rc2 == 0 and len(starts()) == n0 + 1, "--force 应放行：rc=%s" % rc2)

    def c_resource_inflight():
        reset()
        ck(cmd_dispatch(NS(script="脚本A", apply=True)) == 0, "先发 脚本A")
        rc = cmd_dispatch(NS(script="脚本C", apply=True))   # 同 MAA_1 目录、不同脚本
        ck(rc == 2, "同目录不同脚本在窗内应拒（resource_inflight）：%s" % rc)

    def c_slot_busy():
        reset()
        globals()["host_snapshot"] = lambda events=False, event_n=80, timeout=120: {
            "maa": [{"pid": "11", "start": "2026-01-01 00:00:00", "exe": r"E:\MAA-Multi\MAA_1\MAA.exe",
                     "dir": r"E:\MAA-Multi\MAA_1"}], "vmm": {}, "applog": []}
        rc = cmd_dispatch(NS(script="脚本A", apply=True))
        ck(rc == 2, "槽位在跑应拒：%s" % rc)
        ck(not starts(), "槽位在跑不得发 POST")

    def c_slot_warm():
        """脚本间空档：目录没 MAA.exe，但 8 分钟内有 maa_launch/模拟器活动 ⇒ 拒。"""
        reset()
        line = "%s.100 | INFO | MAA 自动代理 | 启动MAA进程: E:\\MAA-Multi\\MAA_1\\MAA.exe" % _now()
        globals()["host_snapshot"] = lambda events=False, event_n=80, timeout=120: {
            "maa": [], "vmm": {}, "applog": [line]}
        rc = cmd_dispatch(NS(script="脚本A", apply=True))
        ck(rc == 2, "刚动过的槽位应拒：%s" % rc)
        ck(not starts(), "slot_warm 不得发 POST")
        # 老事件（超过 quiet 窗）⇒ 放行
        old = "%s.100 | INFO | MAA 自动代理 | 启动MAA进程: E:\\MAA-Multi\\MAA_1\\MAA.exe" % (
            dt.datetime.now() - dt.timedelta(minutes=WARM_QUIET_MIN + 5)).strftime(TS_FMT)
        globals()["host_snapshot"] = lambda events=False, event_n=80, timeout=120: {
            "maa": [], "vmm": {}, "applog": [old]}
        rc2 = cmd_dispatch(NS(script="脚本A", apply=True))
        ck(rc2 == 0, "静默窗口外的槽位应放行：%s" % rc2)

    def c_shift_grace():
        reset()
        globals()["_mas_dispatch_windows"] = lambda now=None: [{
            "src": "shift", "queue": "日常定时2", "at": _now(), "busy_until": _now(),
            "resources": {"dirs": [dkey(r"E:\MAA-Multi\MAA_2")], "scripts": ["sid-b"], "emus": ["2"]}}]
        rc = cmd_dispatch(NS(script="脚本B", apply=True))
        ck(rc == 2, "班次补窗内应拒：%s" % rc)

    # ---- preflight / 可达 / NO_GO -----------------------------------------
    def c_preflight_nogo():
        reset()
        globals()["run_preflight"] = pf_nogo
        rc = cmd_dispatch(NS(script="脚本A", apply=True))
        ck(rc == 2, "preflight NO-GO 应拒：%s" % rc)
        ck(not starts(), "preflight 不过不得发 POST")

    def c_preflight_skip():
        reset()
        rc = cmd_dispatch(NS(script="脚本A", skip_preflight=True))
        ck(rc == 0, "--skip-preflight 应放行 dry-run：%s" % rc)

    def c_rest_down():
        reset()
        globals()["reachable"] = lambda: (False, "URLError: refused")
        rc = cmd_dispatch(NS(script="脚本A", apply=True))
        ck(rc == 2, "REST 不可达应 fail-closed：%s" % rc)

    def c_no_go():
        reset()
        globals()["NO_GO"] = True
        rc = cmd_dispatch(NS(script="脚本A", apply=True))
        ck(rc == 2, "NO_GO 应拒：%s" % rc)
        ck(not starts(), "NO_GO 不得发 POST")

    def c_upstream_running():
        reset()
        F.fail_start = {"code": 500, "status": "error", "message": "RuntimeError: 任务 脚本A 已在运行", "taskId": ""}
        rc = cmd_dispatch(NS(script="脚本A", apply=True))
        ck(rc == 2, "上游在跑应记失败：%s" % rc)
        lg = logs()
        ck(any(x["kind"] == "dispatch_failed" and x["reason"] == "target_running" for x in lg),
           "审计未记 target_running：%r" % lg)

    # ---- stop / wake -------------------------------------------------------
    def c_stop_bad_id():
        reset()
        ck(cmd_stop(NS(stop="not-a-uuid", apply=True)) == 2, "非 uuid 应拒")
        ck(cmd_stop(NS(stop="sid-a")) == 2, "脚本 uid 应拒（is_script_uid）")
        ck(not [c for c in F.calls if c[0] == "/api/dispatch/stop"], "拒绝路径不得发 stop")

    def c_stop_ok():
        reset()
        tid = "11111111-2222-3333-4444-555555555555"
        rc = cmd_stop(NS(stop=tid, apply=True))
        ck(rc == 0, "停任务应 0：%s" % rc)
        ck([b for p, b in F.calls if p == "/api/dispatch/stop" and b.get("taskId") == tid], "stop 入参错：%r" % F.calls)
        ck(any(x["kind"] == "stop_ok" for x in logs()), "审计未记 stop_ok")

    def c_stop_all_needs_force():
        reset()
        ck(cmd_stop(NS(stop="ALL", apply=True)) == 2, "ALL 无 --force 应拒")

    def c_wake_needs_force():
        reset()
        rc = cmd_wake(NS(wake="脚本A", apply=True))
        ck(rc == 2, "wake apply 无 --force 应拒：%s" % rc)
        rc2 = cmd_wake(NS(wake="脚本A", apply=True, force=True))
        ck(rc2 == 0 and starts() and starts()[0]["mode"] == "ManualReview", "wake 应发 ManualReview：%s %r" % (rc2, starts()))

    # ---- cleanup / release -------------------------------------------------
    def c_cleanup_flow():
        reset()
        st = load_state()
        # 注入一条 MCP 端发的车（temp_queue 用 qid-temp）——CLI 的守卫也要护住它
        ledger.record(st, "account", "号三", "qid-temp", "task-9", "mcp",
                      key="a:号三", temp_queue={"id": "qid-temp", "name": "临时-按需-20260101_0000-1"},
                      queue_name="临时-按需-20260101_0000-1", queue_uid="qid-temp",
                      hold_min=30, resources={"dirs": ["e:\\maa-multi\\maa_2"], "scripts": ["sid-b"], "emus": ["2"]})
        save_state(st)
        rc = cmd_cleanup(NS(cleanup=True, apply=True))     # 无 --force ⇒ 忙碌窗口内应 skip
        ck(rc == 0, "cleanup 应 0：%s" % rc)
        ck("qid-temp" in F.queues, "忙碌窗口内不得删：%r" % F.queues)
        rc = cmd_cleanup(NS(cleanup=True, apply=True, force=True))
        ck(rc == 0 and "qid-temp" not in F.queues, "force 后应删掉：%r" % F.queues)
        ck("qid-1" in F.queues, "非本工具前缀不得删：%r" % F.queues)
        ck(any(x["kind"] == "cleanup_ok" for x in logs()), "审计未记 cleanup_ok")

    def c_release_flow():
        reset()
        ck(cmd_dispatch(NS(queue="日常定时9", apply=True)) == 0, "先发一车")
        ck(open_runs(load_state()), "应有在窗记录")
        rc = cmd_release(NS(release="日常定时9"), apply_=True)
        ck(rc == 0 and not open_runs(load_state()), "release 后应无在窗记录：%s" % rc)

    # ---- list-running 解析 -------------------------------------------------
    def c_list_running_parse():
        reset()
        lines = [
            "2026-01-01 05:00:00.000 | INFO | 业务调度 | 创建任务: aaaa1111-2222-3333-4444-555555555555, 模式: AutoProxy, 触发来源: manual_task",
            "2026-01-01 05:00:01.000 | INFO | 业务调度 | 开始运行任务: aaaa1111-2222-3333-4444-555555555555, 模式: AutoProxy",
            "2026-01-01 05:00:02.000 | INFO | 业务调度 | 任务开始: sid-a",
            "2026-01-01 05:20:00.000 | INFO | 业务调度 | 任务结束: aaaa1111-2222-3333-4444-555555555555",
            "2026-01-01 05:21:00.000 | INFO | 业务调度 | 创建任务: bbbb1111-2222-3333-4444-555555555555, 模式: AutoProxy, 触发来源: scheduled_task",
            "2026-01-01 05:21:01.000 | INFO | 业务调度 | 开始运行任务: bbbb1111-2222-3333-4444-555555555555, 模式: AutoProxy",
            "2026-01-01 05:21:02.000 | INFO | 业务调度 | 任务开始: sid-a",
        ]
        r = parse_task_events(lines, {"sid-a": "脚本A"}, {})
        ck(len(r["tasks"]) == 1 and r["tasks"][0]["taskId"].startswith("bbbb"), "在跑任务识别错：%r" % r["tasks"])
        ck(r["tasks"][0]["source"] == "scheduled_task", "触发来源解析错：%r" % r["tasks"][0])
        ck(r["tasks"][0]["scripts"] and "脚本A" in r["tasks"][0]["scripts"][0], "当前脚本解析错：%r" % r["tasks"][0])
        alive, _ = task_alive_hint(lines, "bbbb1111-2222-3333-4444-555555555555")
        ck(alive, "task_alive_hint 应判在跑")
        alive2, _ = task_alive_hint(lines, "aaaa1111-2222-3333-4444-555555555555")
        ck(not alive2, "已结束任务不应判在跑")

    # ---- 共享账本 / 跨端可见 / 共锁（t_4ddb3728） -----------------------------
    def c_ledger_migrate_legacy():
        """旧账本（CLI runs 形态 + MCP day→key 形态）首次读到就合并，不丢记录。"""
        reset()
        cli_legacy = {"runs": [{
            "run_id": "aa11", "ts": "2026-09-28 06:31:28", "target_kind": "account", "target": "老号甲",
            "mode": "AutoProxy", "taskId": "aaaa1111-2222-3333-4444-555555555555", "status": "open",
            "queue_id": "", "temp_queue": {"id": "qid-old1", "name": "临时-按需-旧"},
            "resources": {"dirs": ["e:\\maa-multi\\maa_1"], "scripts": ["sid-a"], "emus": ["1"]},
            "busy_until": (dt.datetime.now() + dt.timedelta(minutes=30)).strftime(TS_FMT)}],
            "temp_queues": [{"name": "临时-按需-旧", "id": "qid-old1", "deleted": False}]}
        mcp_legacy = {"meta": {"v": 1}, "2026-09-28": {"s:sid-b": {
            "kind": "script", "target": "脚本B", "uid": "sid-b", "at": _now(),
            "taskId": "bbbb1111-2222-3333-4444-555555555555",
            "resources": {"dirs": ["e:\\maa-multi\\maa_2"], "scripts": ["sid-b"], "emus": ["2"]},
            "busy_until": (dt.datetime.now() + dt.timedelta(minutes=30)).strftime(TS_FMT)}}}
        with open(os.path.join(tmp, "mas_ondemand_state.json"), "w", encoding="utf-8") as f:
            json.dump(cli_legacy, f, ensure_ascii=False)
        with open(os.path.join(tmp, "mas_ondemand_mcp_state.json"), "w", encoding="utf-8") as f:
            json.dump(mcp_legacy, f, ensure_ascii=False)
        st = load_state()
        op = open_runs(st)
        by_key = {r["key"]: r for r in op}
        ck(by_key.get("a:老号甲", {}).get("src") == "cli", "CLI 旧账未合并：%r" % op)
        ck(by_key.get("s:sid-b", {}).get("src") == "mcp", "MCP 旧账未合并：%r" % op)
        ck(any(x.get("id") == "qid-old1" for x in st.get("temp_queues") or []), "旧临时队列登记未合并：%r" % st)
        ck(os.path.exists(os.path.join(tmp, "mas_ondemand_state.json")), "旧账应保留不删")
        ck(set(st.get("meta", {}).get("legacy_merged", {})) >=
           {os.path.join(tmp, "mas_ondemand_state.json"), os.path.join(tmp, "mas_ondemand_mcp_state.json")},
           "meta 未记迁移指纹")
        st2 = load_state()   # 二次读：指纹未变 ⇒ 不重复合并、不产生 prev 杂物
        all_ents = [e for _d, _k, e in ledger.iter_entries(st2)]
        ck(len(open_runs(st2)) == 2 and not any(e.get("prev") for e in all_ents),
           "重复合并产生了重复记录：%r" % st2)

    def c_cross_visible_list_running():
        """MCP 端发的车 ⇒ CLI --list-running 能看到（共享账本，一条断言）。"""
        reset()
        st = load_state()
        ledger.record(st, "script", "脚本B", "sid-b", "cccc1111-2222-3333-4444-555555555555", "mcp",
                      key="s:sid-b", hold_min=30,
                      resources={"dirs": ["e:\\maa-multi\\maa_2"], "scripts": ["sid-b"], "emus": ["2"]})
        save_state(st)
        ctx = io.StringIO()
        with contextlib.redirect_stdout(ctx):
            rc = cmd_list_running(NS())     # json=True → 单行 JSON
        ck(rc == 0, "--list-running 应 0：%s" % rc)
        out = json.loads([x for x in ctx.getvalue().splitlines() if x.strip()][-1])
        rows = out.get("ondemand_open") or []
        hit = [r for r in rows if r.get("target") == "脚本B"]
        ck(hit and hit[0].get("src") == "mcp" and hit[0].get("taskId") == "cccc1111-2222-3333-4444-555555555555",
           "CLI 未看到 MCP 端发的车：%r" % rows)
        ck((out.get("ledger") or "").endswith("mas_ondemand_ledger.json"), "输出未带共享账本路径：%r" % out.get("ledger"))
        # 同一份账里 CLI 真发的车也可见（同端一致性；两端共读一份文件）
        ck(cmd_dispatch(NS(script="脚本A", apply=True)) == 0, "CLI 真发一车")
        rows2 = ondemand_open_rows(load_state())
        ck({r.get("src") for r in rows2} == {"cli", "mcp"}, "同一账本应同时含两端记录：%r" % rows2)

    def c_shared_lock_busy():
        """与 cron/MCP 共锁：锁被占（模拟另一端在发车）时 CLI apply 拒发、零写请求。"""
        reset()
        ck(globals()["LOCK"] == os.path.join(tmp, "mas_dispatch.lock"), "锁路径未指向共享 mas_dispatch.lock")
        with _lock():     # 模拟 cron 调度器 / MCP 端正持锁
            rc = cmd_dispatch(NS(script="脚本A", apply=True))
        ck(rc == 2, "锁被占应拒：%s" % rc)
        ck(not starts(), "锁被占不得发 dispatch POST：%r" % starts())
        ck(any(x.get("reason") == "concurrent_write" for x in logs_calls()), "未记 concurrent_write：%r" % logs_calls())
        # 锁释放后可正常发（同一把锁的加解锁闭环）
        rc2 = cmd_dispatch(NS(script="脚本A", apply=True))
        ck(rc2 == 0 and starts(), "锁释放后应放行：%s" % rc2)

    # ---- 实例租约 G3L（P1-2 跨族单飞）--------------------------------------
    def lease_lib():
        m = lease_mod()
        assert m is not None, "租约胶水应可用（tools/mas_lease_glue.py）"
        return m

    def lease_state():
        return lease_lib().mod().load()

    def c_lease_busy_zero_write():
        """别人占着实例：dry-run 只提示；apply 拒发 rc=2 且零上游写（跨族单飞的判据）。"""
        reset()
        LG = lease_lib()
        pre = LG.acquire_all(["1"], "t_ondemand_holder", "别族的活（selftest）", ttl_s=300, src="selftest")
        ck(pre.get("ok"), "前置：替身应先拿到租约：%r" % pre)
        try:
            rc_dry = cmd_dispatch(NS(script="脚本A"))
            ck(rc_dry == 2, "dry-run 也要判 G3L（计划不成立 ⇒ rc=2，与 G3/G4 同口径）：%s" % rc_dry)
            ck(not starts() and not [c for c in F.calls if c[0] in WRITE_ENDPOINTS],
               "dry-run 拒发不得写上游：%r" % F.calls)
            plan = plan_dispatch(NS(script="脚本A"))
            gate_lease(plan, NS(script="脚本A", lease_owner="t_ondemand_self"))
            ck(any(r["gate"] == "G3L" for r in plan["refusals"]), "只读闸门应判 G3L 拒绝：%r" % plan["refusals"])
            rc = cmd_dispatch(NS(script="脚本A", apply=True, lease_owner="t_ondemand_self"))
            ck(rc == 2, "租约被占 ⇒ apply 必须拒发：%s" % rc)
            ck(not starts(), "拒发必须零上游写：%r" % starts())
            ck(not [c for c in F.calls if c[0] in WRITE_ENDPOINTS], "拒发不得触写端点：%r" % F.calls)
            ck(any(x.get("kind") == "dispatch_refused" and x.get("reason") == "lease_busy" for x in logs_calls()),
               "审计应记 lease_busy：%r" % logs_calls()[-3:])
            ck(not open_runs(load_state()), "拒发不该落账本：%r" % load_state())
            ck(not any(x.get("kind") == "dispatch_ok" for x in logs()), "不发车的车不得进事件流：%r" % logs())
        finally:
            LG.release_all(["1"], owner_id="t_ondemand_holder")
        ck(cmd_dispatch(NS(script="脚本A", apply=True, lease_owner="t_ondemand_self")) == 0, "空窗后应能正常发车")

    def c_lease_held_then_release():
        """apply ⇒ 租约占住实例（明细进在场记录）；--release ⇒ 记录关 + 租约真释放。"""
        reset()
        rc = cmd_dispatch(NS(script="脚本A", apply=True, lease_owner="t_ondemand_self"))
        ck(rc == 0, "apply 应成功：%s" % rc)
        ck(lease_state()["leases"], "发车应占着实例：%r" % lease_state())
        ent = list(lease_state()["leases"].values())[0]
        ck(ent.get("owner") == "t_ondemand_self", "持有者应是本车：%r" % ent)
        rec = [e for _d, _k, e in ledger.iter_entries(load_state())][0]
        ck(rec.get("lease_owner") == "t_ondemand_self" and rec.get("lease_idxs") == ["1"],
           "在场记录应带租约明细：%r" % rec)
        ck(rec.get("lease_expires_at"), "在场记录应带到期时间：%r" % rec)
        ck(cmd_release(NS(release="脚本A"), apply_=False) == 0 and lease_state()["leases"],
           "release dry-run 不得真放：%r" % lease_state()["leases"])
        ck(cmd_release(NS(release="脚本A"), apply_=True) == 0, "release apply 应成功")
        ck(not lease_state()["leases"], "release 后租约必须空：%r" % lease_state()["leases"])
        ck(any(x.get("kind") == "lease_release" for x in logs_calls()), "应落 lease_release 审计")
        ck(cmd_release(NS(release="脚本A"), apply_=True) == 2, "已放行的目标再 release 应报无在窗记录")

    def c_lease_same_owner_renews():
        """同主再发同实例：G3L 判 self ⇒ 放行（续租，不误拒自己、也不抢别人）。"""
        reset()
        ck(cmd_dispatch(NS(script="脚本A", apply=True, lease_owner="t_ondemand_self")) == 0, "第一车应成功")
        exp1 = list(lease_state()["leases"].values())[0]["expires_at"]
        rc = cmd_dispatch(NS(script="脚本A", apply=True, force=True, lease_owner="t_ondemand_self"))
        ck(rc == 0, "同主再发应放行（--force 破 G3 重复；G3L 判 self）：%s" % rc)
        ent = list(lease_state()["leases"].values())[0]
        ck(ent.get("owner") == "t_ondemand_self" and str(ent.get("expires_at")) >= str(exp1),
           "应是续租（持有者与到期时间不退化）：%r" % ent)

    def c_lease_failed_dispatch():
        """上游发车失败（一个 taskId 都没拿到）⇒ 租约立刻收回，别白占实例一个 TTL。"""
        reset()
        F.fail_start = {"code": 500, "status": "error", "message": "RuntimeError: 任务 脚本A 已在运行", "taskId": ""}
        rc = cmd_dispatch(NS(script="脚本A", apply=True, lease_owner="t_ondemand_self"))
        ck(rc == 2, "上游失败应 rc=2：%s" % rc)
        ck(not lease_state()["leases"], "发车失败必须立刻放租约：%r" % lease_state()["leases"])
        ck(any(x.get("kind") == "lease_release" for x in logs_calls()), "应落 lease_release 审计")

    def c_lease_stop_releases():
        """--stop 成功 ⇒ 该车不再占实例（按在场记录里的 owner 精确放）。"""
        reset()
        ck(cmd_dispatch(NS(script="脚本A", apply=True, lease_owner="t_ondemand_self")) == 0, "先发一车")
        st0 = load_state()
        rec = [e for _d, _k, e in ledger.iter_entries(st0)][0]
        tid = "11111111-2222-3333-4444-555555555555"      # --stop 只吃 uuid：把沙箱 taskId 换成 uuid
        rec["task_uid"] = tid
        save_state(st0)
        ck(lease_state()["leases"], "应在租")
        ck(cmd_stop(NS(stop=tid, apply=True)) == 0, "停任务应 0")
        ck(not lease_state()["leases"], "停成功应释放该车租约：%r" % lease_state()["leases"])
        ck(any(x.get("kind") == "lease_release" for x in logs_calls()), "应落 lease_release 审计")

    def c_lease_no_target_skips():
        """脚本没有 Emulator.Index：没有可协调实例 ⇒ 跳过 G3L（发车照走，不空占租约）。"""
        reset()
        F.scripts["sid-a"]["Emulator"] = {}
        rc = cmd_dispatch(NS(script="脚本A", apply=True, lease_owner="t_ondemand_self"))
        ck(rc == 0, "无实例号应照发（跳过租约）：%s" % rc)
        ck(starts(), "应真发车：%r" % starts())
        ck(not lease_state()["leases"], "不该留下租约：%r" % lease_state()["leases"])

    add("plan_queue_name", c_plan_queue)
    add("plan_bad_uid_refused", c_plan_bad_uid)
    add("plan_script_uid", c_plan_script_uid)
    add("dry_run_no_write", c_dry_run_no_write)
    add("apply_queue_audit_state", c_apply_queue)
    add("apply_script", c_apply_script)
    add("account_temp_queue_apply", c_account_apply_temp_queue)
    add("account_disabled_refused", c_account_disabled)
    add("account_ambiguous_refused", c_account_ambiguous)
    add("account_unknown_refused", c_account_unknown)
    add("duplicate_inflight_refused", c_duplicate_inflight)
    add("resource_inflight_refused", c_resource_inflight)
    add("slot_busy_refused", c_slot_busy)
    add("slot_warm_refused_then_quiet_ok", c_slot_warm)
    add("shift_grace_refused", c_shift_grace)
    add("preflight_nogo_refused", c_preflight_nogo)
    add("preflight_skip_allowed", c_preflight_skip)
    add("rest_down_fail_closed", c_rest_down)
    add("no_go_hard_gate", c_no_go)
    add("upstream_running_logged", c_upstream_running)
    add("stop_bad_id_refused", c_stop_bad_id)
    add("stop_ok_audited", c_stop_ok)
    add("stop_all_needs_force", c_stop_all_needs_force)
    add("wake_needs_force", c_wake_needs_force)
    add("cleanup_busy_guard_and_force", c_cleanup_flow)
    add("release_reopens_target", c_release_flow)
    add("list_running_parse", c_list_running_parse)
    add("ledger_migrates_legacy_books", c_ledger_migrate_legacy)
    add("ledger_cross_visible_in_list_running", c_cross_visible_list_running)
    add("shared_lock_blocks_cli_apply", c_shared_lock_busy)
    add("lease_busy_refused_zero_write", c_lease_busy_zero_write)
    add("lease_held_then_release_frees", c_lease_held_then_release)
    add("lease_same_owner_renews", c_lease_same_owner_renews)
    add("lease_released_on_failed_dispatch", c_lease_failed_dispatch)
    add("lease_stop_releases", c_lease_stop_releases)
    add("lease_no_target_skips", c_lease_no_target_skips)

    if saved_lease_env is None:
        os.environ.pop("MAAORCH_LEASE_RUNTIME", None)
    else:
        os.environ["MAAORCH_LEASE_RUNTIME"] = saved_lease_env
    _lg1 = lease_mod()
    if _lg1 is not None:
        _lg1.set_runtime(None)                # 收回隔离：进程内恢复默认账本路径
    globals().update(saved_globals)
    globals().update(saved_fns)
    ledger.RUNTIME = saved_ledger_runtime
    shutil.rmtree(tmp, ignore_errors=True)
    failed = [c for c in cases if not c["ok"]]
    return {"ok": not failed, "passed": len(cases) - len(failed), "failed": len(failed), "cases": cases}


def cmd_selftest(as_json: bool) -> int:
    rep = selftest()
    if as_json:
        print(json.dumps(rep, ensure_ascii=False))
    else:
        print("SELFTEST=%s (%d/%d)" % ("OK" if rep["ok"] else "FAIL", rep["passed"],
                                       rep["passed"] + rep["failed"]))
        for c in rep["cases"]:
            print("  %s %s%s" % ("PASS" if c["ok"] else "FAIL", c["name"],
                                 ("" if c["ok"] else "  ← " + c["detail"])))
    return 0 if rep["ok"] else 2


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="按需发车（AUTO-MAS）：队列 / 脚本 / 单号 / 唤醒 / 停任务 / 看在跑")
    grp = ap.add_mutually_exclusive_group()
    grp.add_argument("--queue", help="按队列发（队列名或 uid）")
    grp.add_argument("--script", help="按脚本发（脚本名或 uid；跑该脚本全部启用号）")
    grp.add_argument("--account", help="按单号发（账号名，逗号分隔）——自动建 临时-按需-* 队列")
    grp.add_argument("--wake", help="ManualReview 只唤醒（脚本名或 uid；不改配置、不跑链）")
    grp.add_argument("--stop", help="停任务（必须是 /api/dispatch/start 返回的 task_uid；ALL 需 --force）")
    grp.add_argument("--list-running", action="store_true", help="只读：在跑的 MAA 槽位/任务/实例")
    grp.add_argument("--cleanup", nargs="?", const=True, default=None,
                     help="删本工具建的 临时-按需-* 队列（可给名字/uid；默认 dry-run）")
    grp.add_argument("--release", help="人工确认跑完后放行：目标名或 run_id（默认 dry-run）")
    grp.add_argument("--selftest", action="store_true", help="离线自测（假传输，不碰网络/runtime）")
    ap.add_argument("--apply", action="store_true", help="真发车/真停/真删（缺省 dry-run）")
    ap.add_argument("--force", action="store_true", help="显式放行被拦的闸门（记账 force=true；含 stop ALL / wake）")
    ap.add_argument("--skip-preflight", action="store_true", help="跳过 preflight（记账；不推荐）")
    ap.add_argument("--lease-owner", help="实例租约持有者（G3L；缺省 MAAORCH_LEASE_OWNER → HERMES_KANBAN_TASK → ondemand:cli）")
    ap.add_argument("--json", action="store_true", help="单行 JSON 输出")
    ap.add_argument("--events", type=int, default=80, help="--list-running：app.log 事件条数（默认 80）")
    ap.add_argument("--timeout", type=int, default=240, help="ssh 快照超时秒（默认 240）")
    ap.add_argument("--preflight-timeout", type=int, default=900, help="preflight 子进程超时秒（默认 900）")
    a = ap.parse_args(argv)

    try:
        if a.selftest:
            return cmd_selftest(a.json)
        if a.list_running:
            return cmd_list_running(a)
        if a.stop:
            return cmd_stop(a)
        if a.wake:
            return cmd_wake(a)
        if a.cleanup is not None:
            return cmd_cleanup(a)
        if a.release:
            return cmd_release(a, a.apply)
        if a.queue or a.script or a.account:
            return cmd_dispatch(a)
    except TargetError as e:
        print("目标解析失败：%s" % e, file=sys.stderr)
        return 2
    except urllib.error.URLError as e:
        print("上游不可达：%s" % e, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
