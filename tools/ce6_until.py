#!/usr/bin/env python3
"""反复跑一局，直到「MAA 实际编进的干员」正好是我们要的那套 —— 让通关可复现。

为什么（2026-09-21）：同一份作业 v40 在 v40b 通关（编进 涤火杰西卡+凛御银灰+伊内丝+琴柳+乌尔比安），
v40c 却失败（MAA 的候选顺延选了 **年 + 棘刺**，输出差一大截）。MAA 的自动编队每局会掉人并顺延到候选，
所以"作业文件"不等于"这一局实际用的人"。而**编队失败不花理智** ⇒ 最省的办法就是：跑、看编队、不对就重跑。

判据只读 MAA 自己的事件流（`BattleFormationSelected`），并且只读**本次进程新增**的行（避免读到上一局）。

用法:
    python tools/ce6_until.py --job data/copilot/CE-6_v40.json \
        --want 涤火杰西卡 凛御银灰 伊内丝 琴柳 乌尔比安 --tries 4 [--tag v40]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import time

PROJ = pathlib.Path(r"D:\1-2\MAAOrch")
CORE = PROJ / "runtime" / "maa_cli" / "state" / "debug" / "asst.log"
OUT = PROJ / "runtime" / "_ce6"


def selected_since(pos0: int) -> tuple[dict[str, str], int]:
    """读 pos0 之后新增的 BattleFormationSelected 事件。"""
    if not CORE.exists():
        return {}, pos0
    with open(CORE, "rb") as f:
        f.seek(pos0)
        data = f.read()
    pos1 = pos0 + len(data)
    got = {}
    for line in data.decode("utf-8", "replace").splitlines():
        i = line.find("{")
        if i < 0 or "BattleFormationSelected" not in line:
            continue
        try:
            d, _ = json.JSONDecoder().raw_decode(line[i:])
        except Exception:
            continue
        det = d.get("details") or {}
        if d.get("what") == "BattleFormationSelected":
            got[det.get("group_name")] = det.get("selected")
    return got, pos1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    ap.add_argument("--want", nargs="+", required=True, help="期望被编进队的干员名（集合比较）")
    ap.add_argument("--tries", type=int, default=4)
    ap.add_argument("--tag", default="until")
    ap.add_argument("--record", type=int, default=180)
    args = ap.parse_args()

    want = set(args.want)
    for k in range(1, args.tries + 1):
        tag = "%s%d" % (args.tag, k)
        pos0 = CORE.stat().st_size if CORE.exists() else 0
        print("\n=== 第 %d/%d 次：%s ===" % (k, args.tries, tag), flush=True)
        r = subprocess.run([sys.executable, str(PROJ / "tools" / "ce6_attempt.py"),
                            "--job", args.job, "--tag", tag, "--record", str(args.record)],
                           cwd=str(PROJ), capture_output=True, text=True, errors="replace")
        tail = (r.stdout or "").strip().splitlines()
        for l in tail[-4:]:
            print("   " + l)
        got, _ = selected_since(pos0)
        sel = set(got.values())
        missing = want - sel
        extra = sel - want
        print("   本局编进：%s" % ("、".join(sorted(sel)) or "（无记录）"))
        if not missing and not extra:
            print("   ✓ 编队与预期一致 ⇒ 这一局的证据可用")
            # 把该局的编队固化下来，便于以后核对
            (OUT / f"{tag}.lineup.json").write_text(
                json.dumps({"want": sorted(want), "got": got}, ensure_ascii=False, indent=1), encoding="utf-8")
            return 0
        print("   ✗ 与预期不符（缺 %s / 多 %s）⇒ 重跑（编队失败不花理智）"
              % ("、".join(sorted(missing)) or "无", "、".join(sorted(extra)) or "无"))
        time.sleep(2)
    print("\n× %d 次都没抽到期望编队；请把候选列表换成更强的备选，或直接用 --no-formation + 手动队伍。" % args.tries)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
