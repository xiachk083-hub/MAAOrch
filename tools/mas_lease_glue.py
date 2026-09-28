#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""mas_lease_glue.py —— 实例租约的「消费者侧」薄胶水（P1-2 跨族单飞接入用）。

机制只有一套：`tools/mas_instance_lease.py`（t_65b69753 交付；账本 `runtime/instance_leases.json`）。
本模块**不新造机制**，只把四个消费者（`mas_run_profile.py` / `mas_ondemand.py` /
`mas_ondemand_mcp.py` / `mas_preflight.py`）要用到的同一口径收在一处：

  1. owner 口径：`MAAORCH_LEASE_OWNER` > `HERMES_KANBAN_TASK`（看板 worker 天然是卡 id）> 调用方默认值；
  2. host 口径：交给 `mas_instance_lease.default_host()`（`MAAORCH_LEASE_HOST` > `MAAORCH_SSH_HOST` 去 user@）；
  3. `check / acquire / renew / release` 的批量封装（`resources.emus` → idxs），行级 detail **原样沿用**
     租约工具输出（含持有者卡号与到期时间）——拒绝文案直接抄它，别再自己编；
  4. 「拿不到就停」的判据：`ok=True` 只在全部目标「空着或自己的」时成立。

不做：不发车、不启停实例、不改任何配置、不写第二本账、不做等待排队策略（要等就传 `wait_s`）。

失败语义（一律返回结构化结果，不抛异常，调用方自己决定 fail-closed 还是跳过）：
  * 目标没有实例号（脚本没写 Emulator.Index）→ `status="no_target"`，按「跳过」处理；
  * `mas_instance_lease.py` 不在位 → `status="unavailable"`（与 `mas_farm_orchestrator.py` 的
    「跳过 + 说明」同口径）；
  * 其它异常 → `ok=False` / `status="error"`（调用方按拒绝处理）；
  * owner 为空 → `ok=False` / `status="no_owner"`（不猜持有者）。

文档：`docs/MAS_INSTANCE_LEASE.md` §5（逐族接入点）、§6（同族锁保留、跨族用租约、不混用）。
"""
from __future__ import annotations

import importlib
import os
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

LEASE_TOOL = os.path.join(TOOLS, "mas_instance_lease.py")
OWNER_ENV = "MAAORCH_LEASE_OWNER"
CARD_ENV = "HERMES_KANBAN_TASK"
DEFAULT_TTL_S = 1800
MIN_TTL_S = 1
MAX_TTL_S = 86400
SRC = "lease_glue"
UNSET = "—"

_mod = None
_tried = False


# ------------------------------------------------------------------ 基础
def mod():
    """`mas_instance_lease` 模块（首次调用时 import；不在位返回 None）。"""
    global _mod, _tried
    if not _tried:
        _tried = True
        if os.path.exists(LEASE_TOOL):
            _mod = importlib.import_module("mas_instance_lease")
    return _mod


def available() -> bool:
    return mod() is not None


def set_runtime(rt) -> None:
    """测试/隔离用：切换租约账本目录（等价 `MAAORCH_LEASE_RUNTIME`）。"""
    L = mod()
    if L is not None:
        L.set_runtime(rt)


def owner(default=None) -> str:
    """持有者口径：`MAAORCH_LEASE_OWNER` > `HERMES_KANBAN_TASK` > default（可空串）。"""
    for v in (os.environ.get(OWNER_ENV), os.environ.get(CARD_ENV), default):
        s = str(v or "").strip()
        if s:
            return s
    return ""


def owner_any(default=None):
    """同 owner()，但没有就给 None（给「严格模式」的只读 check 用）。"""
    return owner(default) or None


def host() -> str:
    L = mod()
    if L is not None:
        return L.default_host()
    h = str(os.environ.get("MAAORCH_LEASE_HOST", "")).strip()
    if h:
        return h
    ssh = str(os.environ.get("MAAORCH_SSH_HOST", "Administrator@100.79.173.69")).strip()
    return (ssh.split("@", 1)[1] if "@" in ssh else ssh) or "local"


def idxs_of(resources) -> list:
    """`resources.emus` → 保序去重的数字字符串列表（非数字丢弃，不猜）。"""
    out = []
    for v in (resources or {}).get("emus") or []:
        s = str(v or "").strip()
        if s.isdigit() and s not in out:
            out.append(s)
    return out


def detail_of(rows: dict, idxs=None) -> str:
    """把行级结果折成一行（`idx13 被 t_x 占着（用途：…；到期 …）；idx14 空着`）。"""
    parts = []
    for i in list(idxs if idxs is not None else rows.keys()):
        r = (rows or {}).get(str(i))
        if not r:
            continue
        parts.append("idx%s %s" % (i, r.get("detail") or r.get("status") or UNSET))
    return "；".join(parts)


def holder_of(rows: dict) -> dict:
    """第一台被占的持有者信息（拒绝文案/审计用）。"""
    for i, r in (rows or {}).items():
        if r.get("status") == "busy" and r.get("holder"):
            return {"idx": i, "owner": r.get("holder"), "detail": r.get("detail")}
    return {}


# ------------------------------------------------------------------ check / acquire / renew / release
def check_all(idxs, owner_id=None) -> dict:
    """逐台只读 check。返回 {ok,status,rows,bad,detail,host,tool}。

    rows[idx] = {status: free|self|busy|error, detail, holder(持有者 owner)}
    `ok=True` 只在「全部空着或自己的」时成立；status ∈ free|busy|error|no_target|unavailable。
    """
    idxs = [str(i) for i in (idxs or [])]
    L = mod()
    if not idxs:
        return {"ok": True, "status": "no_target", "rows": {}, "bad": [], "tool": L is not None,
                "detail": "无目标实例（脚本没有 Emulator.Index）——跳过"}
    if L is None:
        return {"ok": True, "status": "unavailable", "rows": {}, "bad": [], "tool": False,
                "detail": "mas_instance_lease.py 不在位——跳过"}
    h = host()
    rows, bad = {}, []
    for i in idxs:
        try:
            r = L.check(h, i, owner=owner_id) or {}
            rows[i] = {"status": str(r.get("status") or "?"), "detail": r.get("detail"),
                       "holder": (r.get("holder") or {}).get("owner")}
        except Exception as e:  # noqa: BLE001
            rows[i] = {"status": "error", "detail": "%s: %s" % (type(e).__name__, e), "holder": None}
        if rows[i]["status"] not in ("free", "self"):
            bad.append(i)
    status = "free" if not bad else ("error" if any(rows[i]["status"] == "error" for i in bad) else "busy")
    return {"ok": not bad, "status": status, "rows": rows, "bad": bad, "host": h, "tool": True,
            "detail": detail_of(rows, idxs)}


def acquire_all(idxs, owner_id, purpose, ttl_s: int = DEFAULT_TTL_S, src: str = SRC,
                note: str = "", wait_s: float = 0.0, force: bool = False) -> dict:
    """批量取租（全有或全无；同主=续租）。返回租约工具的结果 + host/idxs。

    `ok=False` 时 `detail`/`holder` 就是拒绝文案（持有者卡号 + 到期时间）。
    """
    idxs = [str(i) for i in (idxs or [])]
    owner_id = str(owner_id or "").strip()
    ttl_s = int(min(MAX_TTL_S, max(MIN_TTL_S, int(ttl_s or DEFAULT_TTL_S))))
    L = mod()
    if not idxs:
        return {"ok": True, "status": "no_target", "idxs": [], "detail": "无目标实例——跳过"}
    if L is None:
        return {"ok": True, "status": "unavailable", "idxs": idxs, "detail": "mas_instance_lease.py 不在位——跳过"}
    if not owner_id:
        return {"ok": False, "status": "no_owner", "idxs": idxs, "tool": True,
                "detail": "租约需要持有者：MAAORCH_LEASE_OWNER / HERMES_KANBAN_TASK / --lease-owner 全空"}
    h = host()
    try:
        if float(wait_s or 0) > 0:
            r = L.acquire_many_wait(h, idxs, owner_id, purpose, ttl_s=ttl_s, force=force,
                                    wait_s=float(wait_s), note=note, src=src)
        else:
            r = L.acquire_many(h, idxs, owner_id, purpose, ttl_s=ttl_s, force=force, note=note, src=src)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status": "error", "idxs": idxs, "host": h,
                "detail": "%s: %s" % (type(e).__name__, e)}
    r = dict(r or {})
    r.setdefault("idxs", idxs)
    r["host"] = h
    return r


def release_all(idxs, owner_id=None, note: str = "") -> dict:
    """批量释放。给了 owner_id 只放自己的（不符 => not_owner）；不给则强放（人工确认收尾用）。"""
    idxs = [str(i) for i in (idxs or [])]
    L = mod()
    if not idxs:
        return {"ok": True, "status": "no_target", "released": [], "detail": "无目标实例——跳过"}
    if L is None:
        return {"ok": True, "status": "unavailable", "detail": "mas_instance_lease.py 不在位——跳过"}
    h = host()
    out = {"ok": True, "status": "released", "host": h, "released": [], "failed": [], "detail": ""}
    for i in idxs:
        try:
            r = L.release(h, i, owner=(owner_id or None), note=note) or {}
            if r.get("ok"):
                out["released"].append(i)
            else:
                out["failed"].append({"idx": i, "status": r.get("status"), "detail": r.get("detail")})
        except Exception as e:  # noqa: BLE001
            out["failed"].append({"idx": i, "status": "error", "detail": "%s: %s" % (type(e).__name__, e)})
    if out["failed"]:
        out["ok"] = False
        out["status"] = "partial"
    out["detail"] = "释放 %d 台%s" % (len(out["released"]),
                                     ("；失败 %d 台：%s" % (len(out["failed"]), detail_of(
                                         {f["idx"]: {"detail": f.get("detail"), "status": f.get("status")}
                                          for f in out["failed"]}))) if out["failed"] else "")
    return out


def renew_all(idxs, owner_id, ttl_s=None) -> dict:
    """批量续租（只有持有者能续；过期/丢失 => ok=False，调用方自己决定要不要重新 acquire）。"""
    idxs = [str(i) for i in (idxs or [])]
    L = mod()
    if not idxs:
        return {"ok": True, "status": "no_target", "detail": "无目标实例——跳过"}
    if L is None:
        return {"ok": True, "status": "unavailable", "detail": "mas_instance_lease.py 不在位——跳过"}
    h = host()
    out = {"ok": True, "status": "renewed", "host": h, "renewed": [], "failed": []}
    for i in idxs:
        try:
            r = L.renew(h, i, owner=str(owner_id or ""), ttl_s=(int(ttl_s) if ttl_s else None)) or {}
            if r.get("ok"):
                out["renewed"].append({"idx": i, "expires_at": (r.get("lease") or {}).get("expires_at")})
            else:
                out["failed"].append({"idx": i, "status": r.get("status"), "detail": r.get("detail")})
        except Exception as e:  # noqa: BLE001
            out["failed"].append({"idx": i, "status": "error", "detail": "%s: %s" % (type(e).__name__, e)})
    if out["failed"]:
        out["ok"] = False
        out["status"] = "partial"
    out["detail"] = "续租 %d 台%s" % (len(out["renewed"]),
                                     ("；失败 %d 台" % len(out["failed"])) if out["failed"] else "")
    return out


def refuse(prefix: str, res: dict) -> str:
    """拒绝话术：`实例租约：idx13 被 t_x 占着（用途：…；到期 …）`（detail 原样来自租约工具）。"""
    return "%s：%s" % (prefix, str((res or {}).get("detail") or (res or {}).get("status") or UNSET))
