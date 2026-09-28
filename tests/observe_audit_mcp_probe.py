#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""mas-observe MCP 的 audit_waterline 现场探针（**只读**：不 rotate/prune、零写入仓库）。

用途：验证 t_537fbff2 加进 mas-observe 的第 9 个工具在**真实 MCP stdio 面**上：
  ① tools/list 白名单含 audit_waterline（9 个）；
  ② 真机调用 ⇒ ok=true 且 audit_status 是 `AUDIT_STATUS=OK|WARN|CRIT …` 判据行（<1s）；
  ③ 把 MAAORCH_AUDIT_DIR 指到**假统一流（11 MiB）**再拉一次 server ⇒ 判据必须变 WARN
     （证明「造超阈值场景 ⇒ WARN 真出现」在 MCP 面成立，而不只是 CLI 面）。

运行（必须 Python312，mcp client 在侧）：
  C:\Users\xiach\AppData\Local\Programs\Python\Python312\python.exe tests\observe_audit_mcp_probe.py
退出码：0 = 全绿；2 = 有 FAIL。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, "tools", "mas_observe_mcp.py")
PY312 = r"C:\Users\xiach\AppData\Local\Programs\Python\Python312\python.exe"
PY = PY312 if os.path.exists(PY312) else sys.executable
WANT_TOOLS = ["mas_preflight", "mas_report", "mas_idle_emulators", "mas_client_state", "mas_identity_trail",
              "mas_interrupt_guard", "event_calendar", "mas_kanban_health", "audit_waterline"]
RESULTS: list = []


def ck(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append({"name": name, "ok": bool(cond), "detail": str(detail)[:400]})
    print("  %-46s %s%s" % (name, "PASS" if cond else "FAIL", ("  " + str(detail)[:280]) if not cond else ""))


def fake_audit_stream(dirpath: str, mb: float = 11.0) -> str:
    """造一个假统一流（合法 v1 记录，体积≈mb MiB）。返回 live 路径。"""
    os.makedirs(dirpath, exist_ok=True)
    p = os.path.join(dirpath, "mas_audit.jsonl")
    rec = json.dumps({"v": 1, "ts": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                      "actor": "tool:observe_probe", "action": "audit.probe", "target": "-",
                      "result": "info", "reason": "", "params": {"pad": "y" * 300}},
                     ensure_ascii=False)
    with open(p, "w", encoding="utf-8") as f:
        f.write((rec + "\n") * max(2, int(mb * 1024 * 1024 / (len(rec) + 1))))
    return p


def fake_archive(audit_dir: str) -> None:
    """假归档一份（压掉「归档缺失」分支，让 11MiB 场景落在 live>10MiB 这条 WARN 上）。"""
    import gzip
    os.makedirs(os.path.join(audit_dir, "archive"), exist_ok=True)
    rec = json.dumps({"v": 1, "ts": "2026-09-20 00:00:00", "actor": "tool:observe_probe",
                      "action": "audit.probe", "target": "-", "result": "info", "reason": ""},
                     ensure_ascii=False) + "\n"
    with gzip.open(os.path.join(audit_dir, "archive", "mas_audit-20260920-000000.jsonl.gz"), "wb") as f:
        f.write((rec * 20).encode("utf-8"))


async def session_call(env_extra: dict, want_warn: bool, label: str, fake_dir: str = "") -> None:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    env = dict(os.environ)
    env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    env.update(env_extra)
    params = StdioServerParameters(command=PY, args=[SERVER], env=env, cwd=ROOT)

    async def call(session) -> dict:
        res = await session.call_tool("audit_waterline", {})
        txt = "".join(getattr(c, "text", "") or "" for c in res.content)
        try:
            return json.loads(txt)
        except ValueError:
            return {"_raw": txt[:400]}

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            if not want_warn:      # 只在第一次校验白名单（两次 server 相同）
                ck("tools/list 含 audit_waterline（共 9 个）", names == WANT_TOOLS, str(names))
            data = await call(session)
            line = str(data.get("audit_status") or "")
            print("      [%s] %s" % (label, line[:200]))
            print("      [%s] verdict=%s elapsed_s=%s live_mb=%s archives_n=%s" % (
                label, data.get("verdict"), data.get("elapsed_s"), data.get("live_mb"), data.get("archives_n")))
            if want_warn:
                ck("假 11MiB 统一流（归档在）⇒ MCP 面判据 = WARN",
                   data.get("verdict") == "WARN" and line.startswith("AUDIT_STATUS=WARN") and data.get("ok") is True,
                   json.dumps(data, ensure_ascii=False)[:300])
                # 抽掉归档：同一 server 进程内再调一次 ⇒ 判据必须升级成 CRIT（归档缺失分支）
                shutil.rmtree(os.path.join(fake_dir, "archive"), ignore_errors=True)
                d2 = await call(session)
                l2 = str(d2.get("audit_status") or "")
                print("      [%s·抽归档后] %s" % (label, l2[:200]))
                ck("假 11MiB 且归档缺失 ⇒ MCP 面判据升级为 CRIT",
                   d2.get("verdict") == "CRIT" and l2.startswith("AUDIT_STATUS=CRIT"),
                   json.dumps(d2, ensure_ascii=False)[:300])
            else:
                ck("真机判据行格式（AUDIT_STATUS=… 开头）", line.startswith("AUDIT_STATUS="), txt_short(data)[:200])
                ck("判据 ∈ {OK,WARN,CRIT}", data.get("verdict") in ("OK", "WARN", "CRIT"),
                   str(data.get("verdict")))
                ck("工具自报耗时 <1s（无网络、纯本机）", float(data.get("elapsed_s") or 9) < 1.0,
                   "elapsed_s=%s" % data.get("elapsed_s"))


def txt_short(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False)


def main() -> int:
    print("mas-observe audit_waterline MCP 探针 @%s" % dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    tmp = tempfile.mkdtemp(prefix="observe_audit_probe_")
    try:
        fake_dir = os.path.join(tmp, "audit")
        fake = fake_audit_stream(fake_dir, 11.0)
        fake_archive(fake_dir)
        print("  假统一流: %s（%d 字节）+ 假归档 1 份" % (fake, os.path.getsize(fake)))
        asyncio.run(session_call({}, want_warn=False, label="真机"))
        asyncio.run(session_call({"MAAORCH_AUDIT_DIR": fake_dir},
                                 want_warn=True, label="假流", fake_dir=fake_dir))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    bad = [r for r in RESULTS if not r["ok"]]
    print("\nMCP_PROBE=%s (%d/%d)" % ("PASS" if not bad else "FAIL", len(RESULTS) - len(bad), len(RESULTS)))
    return 0 if not bad else 2


if __name__ == "__main__":
    sys.exit(main())
