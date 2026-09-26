#!/usr/bin/env python3
"""测「MAA 的自动编队能不能把某个人编进队」—— 失败在开打之前 ⇒ **不花理智**，可以放心批量探。

由来（2026-09-20）：v3/v10 两轮里 MAA 对**号上确实有**的干员报 `reason=Missing` ✗
（乌尔比安/圣约送葬人/仇白/桃金娘），直接把作业卡死在赛前。原因未查明，但绕法很便宜：
先探明哪些人 MAA 编得进去，轴就只用这些人。

判据只用 MAA 自己的事件流：
  `BattleFormationSelected {group_name, selected}` = 编成了 ✓
  `SubTaskError {..., "opers": {"<组名>": [{"name": X, "reason": "Missing"}]}}` = 编不进去 ✗
跑法：给一份名单 → 生成单人组的探测作业 → 跑到 `BattleQuickFormationConfirm` 就**杀掉 MAA**（那一刻还没点开始行动 ✓ 不花理智 ✓）。

用法:
    python tools/maa_form_probe.py --opers 桃金娘 芬 清道夫 临光 星熊 乌尔比安 仇白 史尔特尔
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import time

PROJ = pathlib.Path(r"D:\1-2\MAAOrch")
MC = pathlib.Path.home() / "AppData/Local/Microsoft/WinGet/Links/maa-cli.exe"
CORE = PROJ / "runtime" / "maa_cli" / "state" / "debug" / "asst.log"
OUT = PROJ / "runtime" / "_ce6"
# 八个互不相同的可部署格（CE-6 实测 ✓），只用来满足作业结构，不是真部署
CELLS = [(2, 1), (2, 2), (2, 3), (3, 1), (3, 2), (3, 3), (4, 1), (4, 2)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--opers", nargs="+", required=True)
    ap.add_argument("--tag", default="probe")
    args = ap.parse_args()

    opers = args.opers[:8]          # characterLimit=8 ⇒ 一次最多探 8 个
    job = {
        "stage_name": "wk_melee_6", "minimum_required": "v4.0.0",
        "doc": {"title": "form probe", "details": "探测哪些干员能被 MAA 自动编队编进队"},
        "opers": [],
        "groups": [{"name": "P%d" % i, "opers": [{"name": n}]} for i, n in enumerate(opers)],
        "actions": [{"type": "SpeedUp"}] + [
            {"type": "Deploy", "name": "P%d" % i, "location": list(CELLS[i]), "direction": "Right"}
            for i in range(len(opers))] + [{"type": "SkillDaemon"}],
    }
    jp = OUT / f"{args.tag}.json"
    jp.write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
    print("探测作业 →", jp, "成员:", opers)

    pos0 = CORE.stat().st_size if CORE.exists() else 0
    env = {**os.environ, "MAA_DATA_DIR": str(PROJ / "runtime/maa_cli/data"),
           "MAA_CONFIG_DIR": str(PROJ / "runtime/maa_cli/config"),
           "MAA_STATE_DIR": str(PROJ / "runtime/maa_cli/state"),
           "MAA_CACHE_DIR": str(PROJ / "runtime/maa_cli/cache")}
    p = subprocess.Popen([str(MC), "copilot", str(jp), "-p", "ce6", "--batch", "--formation",
                          f"--log-file={OUT / (args.tag + '.log')}"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)

    ok, miss, done = {}, {}, False
    t0 = time.time()
    while time.time() - t0 < 240:
        if p.poll() is not None:
            break
        if CORE.exists():
            with open(CORE, encoding="utf-8", errors="replace") as f:
                f.seek(pos0)
                for line in f:
                    mm = re.search(r'\| (\w+) (\{.*\})\s*$', line.strip())
                    if not mm:
                        continue
                    try:
                        d = json.loads(mm.group(2))
                    except Exception:
                        continue
                    det = d.get("details") or {}
                    if d.get("what") == "BattleFormationSelected":
                        ok[det.get("group_name")] = det.get("selected")
                    if isinstance(det.get("opers"), dict):
                        for g, lst in det["opers"].items():
                            for o in lst or []:
                                miss[g] = "%s(%s)" % (o.get("name"), o.get("reason"))
                    if "BattleQuickFormationConfirm" in line:
                        done = True
            if done:
                break
        time.sleep(2)
    p.kill()
    try:
        p.wait(timeout=15)
    except Exception:
        pass
    print("\n=== 结果（%s）===" % ("编队完成、已拦在开打前 ✓ 未花理智" if done else "未到确认阶段（超时/提前退出）"))
    for i, n in enumerate(opers):
        g = "P%d" % i
        if g in ok:
            print("  ✓ %-12s 编进去了（MAA 选的是 %s）" % (n, ok[g]))
        elif g in miss:
            print("  ✗ %-12s MAA 报 %s" % (n, miss[g]))
        else:
            print("  ? %-12s 没有明确记录" % n)
    good = [n for i, n in enumerate(opers) if ("P%d" % i) in ok]
    print("\n可用名单（%d 个）: %s" % (len(good), " ".join(good)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
