# -*- coding: utf-8 -*-
r"""mas-observe MCP 探针 · mas_kanban_health 专项（**只读**：不改板、不写盘、不派发）。

用途：用真实 stdio MCP 协议验证 tools/mas_observe_mcp.py 的新工具 `mas_kanban_health`
（kanban 看板治理体检，单一真源 = tools/kanban_health.py）：
  ① tools/list 含该工具（与 server 源码 `_KNOWN_TOOLS` 冻结契约一致；face 随并发接线演进，7→9）；② 默认模式返回 `KANBAN_HEALTH=` 一行（真值）；
  ③ board 参数收窄到单板；④ json=true 附带机读对象（schema/findings）；
  ⑤ 错 slug ⇒ ok=false 且不抛裸异常；⑥ 只读证明：runtime/reports 无新 kanban_health_* 产物。

运行（Python312，mcp client 在侧）：
  C:\Users\xiach\AppData\Local\Programs\Python\Python312\python.exe tests\kanban_health_mcp_probe.py
退出码：0 = 全绿；2 = 有 FAIL。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, "tools", "mas_observe_mcp.py")
PY312 = r"C:\Users\xiach\AppData\Local\Programs\Python\Python312\python.exe"
PY = PY312 if os.path.exists(PY312) else sys.executable

RESULTS: list = []


def ck(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append({"name": name, "ok": bool(cond), "detail": str(detail)[:400]})
    print("  %-40s %s%s" % (name, "PASS" if cond else "FAIL", ("  " + str(detail)[:200]) if not cond else ""))


def reports_snapshot() -> set:
    return set(glob.glob(os.path.join(ROOT, "runtime", "reports", "kanban_health_*")))


def _norm_ts(line) -> str:
    """比较两次调用的行时，抹掉各自生成的 ts（每次运行都会变）。"""
    return re.sub(r"ts=[^,]*", "ts=*", str(line or ""))


async def main() -> int:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    env = dict(os.environ)
    env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    params = StdioServerParameters(command=PY, args=[SERVER], env=env, cwd=ROOT)

    print("mas_kanban_health MCP 探针 @%s" % dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    before = reports_snapshot()
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # ① 工具清单（冻结契约 = server 源码 _KNOWN_TOOLS；face 随并发接线演进，7→9 含 kanban_health/audit_waterline）
            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            src = open(SERVER, encoding="utf-8").read()
            m = re.search(r"_KNOWN_TOOLS\s*=\s*\[(.*?)\]", src, re.S)
            want = sorted(re.findall(r'"([^"]+)"', m.group(1))) if m else []
            ck("tools/list 含 mas_kanban_health", "mas_kanban_health" in names, str(names))
            ck("tools/list 与 _KNOWN_TOOLS 冻结契约一致", names == want, "got=%s want=%s" % (names, want))

            async def call(tool: str, args: dict) -> dict:
                res = await session.call_tool(tool, args)
                txt = "".join(getattr(c, "text", "") or "" for c in res.content)
                try:
                    return json.loads(txt)
                except Exception:  # noqa: BLE001
                    return {"_raw": txt[:800]}

            # ② 默认模式：一行真值
            r1 = await call("mas_kanban_health", {})
            line = r1.get("line") or ""
            ck("默认调用 ok=true", r1.get("ok") is True, json.dumps(r1)[:300])
            ck("返回 KANBAN_HEALTH= 行", line.startswith("KANBAN_HEALTH="), line)
            ck("status ∈ OK/WARN/CRIT", r1.get("status") in ("OK", "WARN", "CRIT"), str(r1.get("status")))
            c = r1.get("counts") or {}
            ck("counts 数字齐全", all(isinstance(c.get(k), int) for k in
                                       ("boards", "cards", "running", "dup", "zombie", "blocked_stale",
                                        "fail", "orphan", "P1", "P2", "P3")), json.dumps(c))
            print("      line=%s" % line[:190])

            # ③ board 参数收窄
            r2 = await call("mas_kanban_health", {"board": "maa-parallel-benchmark-20260926"})
            ck("board=单板 ⇒ boards=1", (r2.get("counts") or {}).get("boards") == 1, json.dumps(r2)[:300])
            ck("board=单板 ⇒ line 含 boards=1", "boards=1," in (r2.get("line") or ""), r2.get("line"))

            # ④ json=true 机读对象
            r3 = await call("mas_kanban_health", {"json": True})
            doc = r3.get("json") or {}
            ck("json 模式 ok=true", r3.get("ok") is True, json.dumps({k: r3.get(k) for k in
                                                                      ("ok", "status", "exit_code", "error")})[:300])
            ck("json.schema 正确", doc.get("schema") == "maaorch.kanban_health/1", str(doc.get("schema")))
            boards = doc.get("boards") or []
            ck("json.boards[].findings[] 结构", bool(boards) and all("findings" in b for b in boards),
               "boards=%d" % len(boards))
            ck("json 模式 line 格式合规且与 status 一致（跨调用计数可变动，不做逐字比对）",
               bool(re.match(r"^KANBAN_HEALTH=ts=.*,boards=\d+,cards=\d+,.*,status=(OK|WARN|CRIT)$", r3.get("line") or ""))
               and (r3.get("line") or "").endswith("status=%s" % r3.get("status")),
               "json=%s" % _norm_ts(r3.get("line"))[:160])
            s1 = (doc.get("totals") or {}).get("P1")
            ck("json.totals.P1 与 line P1 一致（同一调用内）", s1 == (r3.get("counts") or {}).get("P1"),
               "json=%s line=%s" % (s1, (r3.get("counts") or {}).get("P1")))
            n_find = sum(len(b.get("findings") or []) for b in boards)
            p12 = [f for b in boards for f in (b.get("findings") or []) if f.get("sev") in ("P1", "P2")]
            print("      findings=%d（P1/P2=%d）；前 3 条 P1/P2 标题示例：%s" % (
                n_find, len(p12), [f.get("title") for f in p12[:3]]))

            # ⑤ 错 slug ⇒ fail-closed，不抛裸异常
            r4 = await call("mas_kanban_health", {"board": "no-such-board-xyz"})
            ck("错 slug ⇒ ok=false + error", r4.get("ok") is False and bool(r4.get("error")),
               json.dumps(r4)[:300])

            # ⑥ 只读证明：无新产物落盘
            after = reports_snapshot()
            ck("只读：runtime/reports 无新 kanban_health_* 产物", before == after,
               "new=%s" % sorted(after - before)[:3])

    bad = [r for r in RESULTS if not r["ok"]]
    print("\nPROBE: %s (%d/%d)" % ("OK" if not bad else "FAIL", len(RESULTS) - len(bad), len(RESULTS)))
    return 0 if not bad else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
