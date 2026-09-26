"""从 asst.log 里抽出 Copilot 的部署/技能动作时间线（带真实时刻），用于复盘。

用法:
    python tools/bi_log_timeline.py --tag bi3a           # 自动取那次跑局的时间窗
    python tools/bi_log_timeline.py --from "01:04:20" --to "01:07:10"
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re

PROJ = pathlib.Path(r"D:\1-2\MAAOrch")
LOGS = [PROJ / "runtime/maa_cli/state/debug/asst.log",
        PROJ / "runtime/maa_cli/data/lib/debug/asst.log"]


def parse_line(line: str):
    m = re.match(r"\[(\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d\.\d+)\]", line)
    if not m:
        return None, None
    t = m.group(2)
    i = line.find("SubTaskExtraInfo ")
    if i < 0:
        return t, None
    tail = line[i + len("SubTaskExtraInfo "):]
    try:
        obj, _ = json.JSONDecoder().raw_decode(tail)
    except Exception:
        return t, None
    if obj.get("what") != "CopilotAction":
        return t, None
    return t, obj.get("details") or {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="t0", default="00:00:00")
    ap.add_argument("--to", dest="t1", default="23:59:59")
    ap.add_argument("--tag", help="用 attempt.log 里的起止时间自动定窗（可选）")
    ap.add_argument("--grep", default=None, help="只看含该关键词的行（动作名/干员名）")
    args = ap.parse_args()

    t0, t1 = args.t0, args.t1
    if args.tag:
        p = PROJ / "runtime/_ce6" / f"{args.tag}.attempt.log"
        txt = p.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"\[Copilot\] (\d\d:\d\d:\d\d) - (\d\d:\d\d:\d\d)", txt)
        if m:
            t0, t1 = m.group(1), m.group(2)
            print("（从 %s 取时间窗 %s ~ %s）" % (p.name, t0, t1))

    rows = []
    for lg in LOGS:
        if not lg.exists():
            continue
        with lg.open("r", encoding="utf-8", errors="replace") as f:
            last_day = None
            for line in f:
                if t0[:2] not in line[:40] and last_day is None:
                    pass
                t, det = parse_line(line)
                if t is None or det is None:
                    continue
                if not (t0 <= t <= t1):
                    continue
                a = det.get("action") or det.get("type") or "?"
                tgt = det.get("target") or det.get("name") or ""
                rows.append((t, a, tgt, det.get("doc") or ""))
        if rows:
            break
    if not rows:
        print("窗口内没有 CopilotAction 记录（检查 --from/--to 或日志文件）")
        return 1
    print("共 %d 条动作：" % len(rows))
    base = rows[0][0]
    def secs(t):
        h, m, s = t.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    b = secs(base)
    for t, a, tgt, doc in rows:
        if args.grep and args.grep not in (a + tgt + doc):
            continue
        print("  +%6.1fs  %-10s %-14s %s" % (secs(t) - b, a, tgt, doc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
