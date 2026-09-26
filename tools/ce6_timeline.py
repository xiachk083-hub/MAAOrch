"""从 MAA 事件流（asst.log）拉一份带时间戳的「这一局都干了什么」时间线。

为什么单独做（2026-09-20）：`--log-file` 有时是空的 ✗，而 asst.log 里其实有
BattleProcessTask 的 action/target 事件；把它们按时间排出来，就能对齐录像里的
「第几秒下了谁」，判断开局是否按轴落地、后段谁先崩。

用法:
    python tools/ce6_timeline.py --minutes 19:33 19:34 [--tag v22a]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re

PROJ = pathlib.Path(r"D:\1-2\MAAOrch")
CORE = PROJ / "runtime" / "maa_cli" / "state" / "debug" / "asst.log"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", nargs="+", required=True, help="分钟前缀，如 19:33 19:34")
    ap.add_argument("--tag", default="")
    ap.add_argument("--all", action="store_true", help="打印所有事件（默认只打印战斗动作）")
    args = ap.parse_args()

    # ⚠️ asst.log 的时间戳是 `[2026-09-20 19:34:09.764]`（先日期再空格）——
    #    用 `\[(\d\d:\d\d:\d\d` 会永远匹配不上 ✗（2026-09-20 我自己在这里空转了两轮）
    pat = re.compile(r"\[\d{4}-\d\d-\d\d (\d\d:\d\d:\d\d\.\d+)\]")
    keep = tuple(args.minutes)
    rows = []
    for line in CORE.open(encoding="utf-8", errors="replace"):
        m = pat.search(line)
        if not m or not m.group(1).startswith(keep):
            continue
        # ⚠️ 别用正则截 JSON 大括号（`\{.*?\}` 会在嵌套的 details 里截断 ⇒ json.loads 报
        #    "Expecting ',' delimiter" ✗，2026-09-20 我自己在这上面空转了三轮）。
        #    正确做法：交给 JSON 解析器自己吃（raw_decode 能处理嵌套 + 尾部多余字符）。
        i = line.find("{")
        if i < 0:
            continue
        try:
            d, _ = json.JSONDecoder().raw_decode(line[i:])
        except Exception:
            continue
        cls = (d.get("class") or "").split("::")[-1]
        det = d.get("details") or {}
        what = d.get("what", "")
        act = det.get("action", "")
        tgt = det.get("target", "")
        if what == "BattleFormationSelected":
            rows.append((m.group(1), "编队", "%s → %s" % (det.get("group_name"), det.get("selected"))))
        elif act:
            rows.append((m.group(1), "战斗", "%s %s" % (act, tgt)))
        elif isinstance(det.get("opers"), dict):
            for g, lst in det["opers"].items():
                for o in lst or []:
                    rows.append((m.group(1), "编队失败", "%s → %s(%s)" % (g, o.get("name"), o.get("reason"))))
        elif args.all:
            rows.append((m.group(1), cls, json.dumps(det, ensure_ascii=False)[:110]))

    print("=== %s ===" % (args.tag or "时间线"))
    t0 = rows[0][0] if rows else ""
    for t, kind, msg in rows:
        print("  %s  %-6s %s" % (t, kind, msg))
    if rows:
        print("共 %d 条；首 %s 末 %s" % (len(rows), t0, rows[-1][0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
