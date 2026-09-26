#!/usr/bin/env python3
"""EX 关打关驱动：点节点 → 自动重试作业 → 收尾 → 再试，直到三星。

为什么这样组合（2026-09-15 实战）:
  · MAA 的关卡导航只在 GUI 层（`resource/tasks/Stages/*.json` 里 GA 只写到 GA-8，EX 关没有）
    → 命令行到不了 EX 关，导航必须由 adb 点；
  · 失败/不可用要"判定后立刻换下一份"，不能硬等 → 用 maa_job_fallback（日志判定 + 三星核验）；
  · 失败会返还理智，所以可以反复试到过。

用法:
    python tools/ga_ex_drive.py --emu 2 --node 390,165 --codes 46899,32245,39633 --rounds 3
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))


def tap(emu: str, x: int, y: int, wait: float = 2.0) -> None:
    from services.ai_agent.percept import AdbScreen
    sc = AdbScreen(emu)
    sc.ensure_connected()
    sc._run(["shell", "input", "tap", str(x), str(y)])
    time.sleep(wait)


def dismiss(emu: str) -> None:
    """把结算/失败界面点掉，并退回关卡地图（左上角返回键按两下）"""
    for pos in ((640, 660), (640, 660), (640, 400)):      # 点掉结算/失败
        tap(emu, *pos, wait=1.2)
    for _ in range(2):                                     # 返回 → EX 地图
        tap(emu, 40, 40, wait=1.4)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--emu", default="2")
    ap.add_argument("--node", required=True, help="关卡节点坐标 x,y")
    ap.add_argument("--codes", required=True, help="逗号分隔的作业号（按优先级）")
    ap.add_argument("--rounds", type=int, default=3, help="最多打几轮")
    ap.add_argument("--out", default="data/copilot/ex_drive_report.json")
    args = ap.parse_args()
    nx, ny = (int(v) for v in args.node.split(","))

    report = []
    for r in range(1, args.rounds + 1):
        print(f"\n=== 第 {r}/{args.rounds} 轮：点节点({nx},{ny}) → 跑作业 ===", flush=True)
        tap(args.emu, nx, ny, wait=2.5)
        out = PROJ / args.out
        cmd = [sys.executable, str(PROJ / "tools" / "maa_job_fallback.py"),
               "--codes", args.codes, "--emu", args.emu, "--out", str(out), "--max-tries", "3"]
        subprocess.run(cmd, timeout=2400)
        try:
            res = json.loads(out.read_text(encoding="utf-8"))
        except Exception:
            res = {}
        print(f"    结果：{res.get('result')}", flush=True)
        report.append({"round": r, "result": res.get("result"), "tries": res.get("tries")})
        if res.get("result", "").startswith("成功"):
            print("    ✓ 已三星，收工", flush=True)
            break
        dismiss(args.emu)          # 失败 → 退回地图再来一轮
    (PROJ / args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n驱动报告：{PROJ / args.out}")


if __name__ == "__main__":
    main()
