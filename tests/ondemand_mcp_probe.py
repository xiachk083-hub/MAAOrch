# -*- coding: utf-8 -*-
r"""mas-ondemand MCP 现场探针（**只读 + dry-run**：不发车、不建队列、不停任何任务）。

用途：验证 tools/mas_ondemand_mcp.py 的真实 MCP 面（stdio 协议、工具清单、dry-run 语义）
是否如文档所述。全部调用都是只读或 dry-run（apply 一律缺省 false），唯一的例外是
`stop_task(task_id="ALL"|队列uid|随机UUID)` 这些**预期被拒**的守护路径 —— 拒绝本身即验证。

运行（必须 Python312，mcp client 在侧）：
  C:\Users\xiach\AppData\Local\Programs\Python\Python312\python.exe tests\ondemand_mcp_probe.py
退出码：0 = 全绿；2 = 有 FAIL。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, "tools", "mas_ondemand_mcp.py")
PY312 = r"C:\Users\xiach\AppData\Local\Programs\Python\Python312\python.exe"
PY = PY312 if os.path.exists(PY312) else sys.executable

RESULTS: list = []


def ck(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append({"name": name, "ok": bool(cond), "detail": str(detail)[:400]})
    print("  %-38s %s%s" % (name, "PASS" if cond else "FAIL", ("  " + str(detail)[:200]) if not cond else ""))


async def main() -> int:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    env = dict(os.environ)
    env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    params = StdioServerParameters(command=PY, args=[SERVER], env=env, cwd=ROOT)

    print("mas-ondemand MCP 现场探针 @%s" % dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # ① 工具清单（白名单 6 个）
            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            ck("tools/list 白名单", names == ["dispatch_account", "dispatch_queue", "dispatch_script",
                                              "list_running", "ondemand_preflight", "stop_task"],
               str(names))

            async def call(tool: str, args: dict) -> dict:
                res = await session.call_tool(tool, args)
                txt = "".join(getattr(c, "text", "") or "" for c in res.content)
                try:
                    return json.loads(txt)
                except Exception:  # noqa: BLE001
                    return {"_raw": txt[:800]}

            # ② list_running（只读；含远端巡检）
            lr = await call("list_running", {"mask_accounts": False, "with_users": True})
            ck("list_running 可达", bool(lr.get("ok") and lr.get("reachable")), json.dumps(lr)[:300])
            slots = lr.get("maa_slots") or []
            ck("list_running 给出在跑槽位", isinstance(slots, list),
               "slots=%s remote_error=%s" % (len(slots), lr.get("remote_error")))
            print("      emulators_up=%s slots=%d live_not_tracked=%s" % (
                lr.get("emulators_up"), len(slots), lr.get("live_not_tracked")))
            for s in slots[:4]:
                print("      %-22s pids=%s scripts=%s" % (
                    s.get("dir"), s.get("pids"), [x.get("name") for x in (s.get("scripts") or [])]))
            acct = ""
            for s in slots:
                for sc in (s.get("scripts") or []):
                    for u in (sc.get("users_enabled") or []):
                        acct = acct or u
            ck("list_running 槽位→脚本映射可解", bool(slots and any(s.get("scripts") for s in slots)),
               "空映射（可能真的没在跑）")

            # ③ dispatch_queue dry-run（班次队列 → 委托 mas_dispatch）
            dq = await call("dispatch_queue", {"name": "日常定时1", "apply": False})
            ck("dispatch_queue dry-run", bool(dq.get("dry_run") and not dq.get("dispatched")), json.dumps(dq)[:300])
            dq2 = await call("dispatch_queue", {"name": "不存在的队列-探针", "apply": False})
            ck("dispatch_queue 未知队列拒绝", dq2.get("refused") == "unknown_queue", json.dumps(dq2)[:200])

            # ④ dispatch_script dry-run（取在跑槽位里的脚本名；在跑=大概率 busy 窗口内 → 冲突守护）
            cands, seen = [], set()
            for s in slots:
                for sc in (s.get("scripts") or []):
                    nm = sc.get("name") or ""
                    if nm and nm not in seen:
                        seen.add(nm)
                        cands.append(nm)
            picked, ds = "", {}
            for nm in cands[:4]:
                ds = await call("dispatch_script", {"name": nm, "apply": False})
                if ds.get("dry_run") and not ds.get("dispatched"):
                    picked = nm
                    break
            if picked:
                ck("dispatch_script dry-run", True, "script=%s" % picked)
            elif ds.get("refused") in ("conflict_inflight", "duplicate_today", "guard_window"):
                ck("dispatch_script dry-run", True,
                   "候选全在 busy 窗口内 → 守护生效: refused=%s（%s）" % (ds.get("refused"), ds.get("detail")))
            else:
                ck("dispatch_script dry-run", False,
                   "script candidates=%s last=%s" % (cands[:4], json.dumps(ds)[:400]))
            if cands:
                dsn = await call("dispatch_script", {"name": "不存在的脚本-探针", "apply": False})
                ck("dispatch_script 未知脚本拒绝", dsn.get("refused") == "unknown_script",
                   json.dumps(dsn)[:200])

            # ⑤ dispatch_account dry-run（在跑槽位的启用用户；同上，busy 窗口内会走冲突守护）
            if acct:
                da = await call("dispatch_account", {"name": acct, "apply": False})
                ok_acct = bool(da.get("dry_run")) or da.get("refused") in (
                    "conflict_inflight", "duplicate_today", "guard_window", "dir_busy")
                ck("dispatch_account dry-run", ok_acct,
                   "account=%s %s" % (acct, json.dumps(da)[:400]))
                print("      account plan: refused=%s dir_busy=%s emu_up=%s script=%s" % (
                    da.get("refused"), da.get("dir_busy"), da.get("emulator_running"), da.get("script")))
            else:
                ck("dispatch_account dry-run", False, "现场没有在跑槽位 → 拿不到账号名，跳过")

            # ⑥ stop_task 守护路径（全部预期被拒，不发任何 stop）
            s1 = await call("stop_task", {"task_id": "ALL", "apply": True})
            ck("stop_task 拒 ALL", s1.get("refused") == "refuse_all", json.dumps(s1)[:200])
            s2 = await call("stop_task", {"task_id": "not-a-uuid"})
            ck("stop_task 拒非 UUID", s2.get("refused") == "not_task_uid", json.dumps(s2)[:200])
            s3 = await call("stop_task", {"task_id": "00000000-0000-0000-0000-000000000000", "apply": True})
            ck("stop_task 台账外 fail-closed", s3.get("refused") == "unknown_task_uid", json.dumps(s3)[:300])

            # ⑦ ondemand_preflight（只读，约 30s）
            pf = await call("ondemand_preflight", {"target": "日常定时1", "skip_screen": True})
            ck("ondemand_preflight 返回结论", pf.get("preflight") in ("GO", "NO-GO"),
               json.dumps({k: pf.get(k) for k in ("preflight", "ok", "error", "failing_checks")})[:400])
            print("      preflight=%s failing=%s reasons=%s" % (
                pf.get("preflight"), pf.get("failing_checks"), (pf.get("reasons") or [])[:2]))
            checks = pf.get("checks") or {}
            for k, v in checks.items():
                print("        [%-4s] %-12s %s" % (v.get("status"), k, str(v.get("detail"))[:90]))

    bad = [r for r in RESULTS if not r["ok"]]
    print("\nPROBE: %s (%d/%d)" % ("OK" if not bad else "FAIL", len(RESULTS) - len(bad), len(RESULTS)))
    return 0 if not bad else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
