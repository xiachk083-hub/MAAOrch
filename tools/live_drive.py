#!/usr/bin/env python3
"""全程在线打关驱动 —— 盯屏（~0.8s/帧）+ 状态一变立刻动作。

用户口径（2026-09-15）: "你不能等他主动，他失败了，他不会自己处理，你要全程到"
  · 失败 → 1 秒内点掉结算 + 立刻起下一把（下一份作业 / 下一版编排）
  · 通关 → 立刻点掉结算 + 退回地图 + 点下一个节点
  · 战斗中 → 只盯屏，什么都不做（别抢 MAA 的控制权）

状态判别用 tools/screen_state.py（模板匹配，~50ms/帧，不依赖 LLM）。

用法:
  python tools/live_drive.py --emu 2 --stage act16side_ex02 --node 390,258 \
      --codes 83790,84728,50329 --next-node 390,350
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
sys.path.insert(0, str(PROJ / "tools"))
import screen_state as SS  # noqa: E402


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--emu", default="2")
    ap.add_argument("--stage", required=True)
    ap.add_argument("--node", required=True, help="本关节点 x,y")
    ap.add_argument("--codes", default="", help="作业号，失败依次换")
    ap.add_argument("--local", default="", help="本地作业 json，逗号分隔（与 --codes 二选一）")
    ap.add_argument("--next-node", default="", help="通关后自动点下一个节点 x,y")
    ap.add_argument("--max-attempts", type=int, default=6)
    ap.add_argument("--job-timeout", type=float, default=420.0)
    ap.add_argument("--out", default="data/copilot/live_drive.json")
    args = ap.parse_args()

    from services.ai_agent.percept import AdbScreen
    sc = AdbScreen(args.emu)
    sc.ensure_connected()

    def tap(x, y, w=0.6):
        sc._run(["shell", "input", "tap", str(x), str(y)])
        time.sleep(w)

    cands = ([f"prts://{c.strip()}" for c in args.codes.split(",") if c.strip()] if args.codes
             else [x.strip() for x in args.local.split(",") if x.strip()])
    if not cands:
        raise SystemExit("需要 --codes 或 --local")
    node = tuple(int(v) for v in args.node.split(","))
    nxt = tuple(int(v) for v in args.next_node.split(",")) if args.next_node else None

    events, attempt, state, handled = [], 0, "start", None
    stuck_since = time.time()
    log(f"全程在线驱动启动：{args.stage} 节点{node} 候选{len(cands)} 份")

    # 启动即开打：先确保在关卡页，再起第一把（旧版只在"失败"分支起，导致第一把没人起）
    tap(*node, w=1.8)
    log(f"起第一把：{cands[0]}")
    subprocess.Popen([sys.executable, str(PROJ / "tools" / "maa_copilot_run.py"),
                      "--emu", args.emu,
                      "--copilot" if cands[0].endswith(".json") else "--codes", cands[0],
                      "--timeout", str(args.job_timeout)],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3)

    t_end = time.time() + 3600
    while time.time() < t_end and attempt < args.max_attempts:
        f = sc.capture()
        if f is None:
            time.sleep(0.8)
            continue
        st, conf = SS.state_of(f)
        if st != state:
            log(f"状态 → {st}（{conf}）")
            state = st
            handled = None                      # 跳变 → 允许处理一次
            events.append({"ts": time.strftime("%H:%M:%S"), "state": st, "attempt": attempt})
            stuck_since = time.time()
        if handled == state:                    # 同一状态已处理过 → 只盯不动手
            time.sleep(0.8)
            continue

        if st == "fail":
            handled = st
            tap(640, 660); tap(640, 660)
            attempt += 1
            log(f"第 {attempt} 次尝试失败 → 立刻换下一份")
            for _ in range(2):
                tap(40, 40, 0.8)
            tap(*node, w=1.6)
            if attempt < len(cands):
                job = cands[attempt]
                log(f"起下一把：{pathlib.Path(job).name}")
                subprocess.Popen([sys.executable, str(PROJ / "tools" / "maa_copilot_run.py"),
                                  "--emu", args.emu,
                                  "--copilot" if job.endswith(".json") else "--codes", job,
                                  "--timeout", str(args.job_timeout)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(2)
        elif st == "win":
            handled = st
            stars = None
            try:
                from maa_job_fallback import verify_three_star
                stars = verify_three_star(int(args.emu))
            except Exception:
                pass
            log(f"通关画面（三星核验={stars}）")
            events.append({"ts": time.strftime("%H:%M:%S"), "state": "win", "three_star": stars})
            tap(640, 660); tap(640, 660); tap(640, 400)
            if nxt:
                tap(40, 40, 0.8); tap(40, 40, 0.8)
                log(f"→ 点下一个节点 {nxt}")
                tap(*nxt, w=1.6)
                break
            break
        elif st == "form":
            handled = st
            log("识别到编队页 → 点确认，交给 MAA 继续")
            tap(1000, 678, 1.2)
        else:                                   # battle / other / exmap：只盯屏；但卡太久要自愈
            if st != "battle" and time.time() - stuck_since > 8:
                log(f"状态 {st} 卡住 >8s → 自愈：确认→开始行动→返回→重开这一把")
                tap(1000, 678, 1.0); tap(1000, 660, 1.0)
                tap(40, 40, 0.8); tap(40, 40, 0.8)
                tap(*node, w=1.6)
                job = cands[min(attempt, len(cands) - 1)]
                subprocess.Popen([sys.executable, str(PROJ / "tools" / "maa_copilot_run.py"),
                                  "--emu", args.emu,
                                  "--copilot" if job.endswith(".json") else "--codes", job,
                                  "--timeout", str(args.job_timeout)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                stuck_since = time.time()
            time.sleep(0.8)

    pathlib.Path(args.out).write_text(json.dumps({"stage": args.stage, "events": events},
                                                 ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"事件日志：{args.out}")


if __name__ == "__main__":
    main()
