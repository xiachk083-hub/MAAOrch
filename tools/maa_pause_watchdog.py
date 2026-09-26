#!/usr/bin/env python3
"""MAA 战斗暂停看门狗 — 自动解除 MAA 部署阶段的「游戏被暂停却不动」死锁。

背景（2026-09-15 实测）:
    MAA 6.17.5 在 15-13 险地第一次部署时把游戏点成暂停，随后
    BattleProcessTask::do_action 空转 403 秒（只在 update_deployment/pause_button_analyze
    之间循环，不再发任何点击），直到人工点 ▶ 才继续 —— 结果是白白丢掉一局。

判定（两条同时满足才插手，避免误触 MAA 正在进行的部署）:
    1. 画面连续 FROZEN_N 次几乎完全静止（暂停时画面定格）
    2. MAA 的 gui.log 连续 STALL_S 秒没有新内容（步骤日志停滞）

用法:
    python tools/maa_pause_watchdog.py --minutes 25
"""
from __future__ import annotations
import argparse
import os
import sys
import time
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

import numpy as np  # noqa: E402

from services.ai_agent.percept import Perception  # noqa: E402
from services.ai_agent.actuator import Actuator, Session  # noqa: E402

DEFAULT_LOG = r"D:\Xiach\M-A\MAA\MAA-v6.12.2-win-x64-1\debug\gui.log"
PLAY_BTN = (1215, 50)          # 暂停后右上角 ▶（继续）
FROZEN_DIFF = 0.0015
FROZEN_N = 3
STALL_S = 20.0
COOLDOWN_S = 20.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=25.0)
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--log", default=DEFAULT_LOG)
    ap.add_argument("--emu", type=int, default=1)
    ap.add_argument("--out", default=str(PROJ / "logs" / "agent" / "watchdog.log"))
    args = ap.parse_args()

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    logf = open(args.out, "a", encoding="utf-8")

    def say(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        print(line, flush=True)
        logf.write(line + "\n")
        logf.flush()

    p = Perception.for_emu(args.emu, instance=1, client="Official")
    act = Actuator(p, session=Session("watchdog"), post_delay=0.8)

    def maa_size() -> int:
        try:
            return os.path.getsize(args.log)
        except OSError:
            return -1

    # 只看本次启动之后新写入的日志：上一局的「已开始行动/战斗出错」还在文件里，
    # 按偏移量切片可以避免拿旧状态做判断（实测踩过这一个坑）。
    start_offset = 0
    try:
        start_offset = os.path.getsize(args.log)
    except OSError:
        pass

    def maa_tail_text() -> str:
        try:
            size = os.path.getsize(args.log)
            base = start_offset if size >= start_offset else 0
            with open(args.log, "rb") as f:
                f.seek(base)
                return f.read().decode("utf-8", "replace")
        except OSError:
            return ""

    say(f"看门狗启动：{args.minutes:.0f} 分钟内每 {args.interval:.0f}s 巡检一次")
    deadline = time.time() + args.minutes * 60
    prev = None
    frozen = 0
    last_size = maa_size()
    last_growth = time.time()
    last_tap = 0.0
    battle_seen = False
    reported_state = ""

    while time.time() < deadline:
        time.sleep(args.interval)
        try:
            img = p.shot()
        except Exception as e:  # 模拟器抖动/重连
            say(f"截图失败：{type(e).__name__} {e}")
            continue

        size = maa_size()
        if size != last_size:
            last_size = size
            last_growth = time.time()

        tail = maa_tail_text()
        if not battle_seen and "已开始行动" in tail:
            battle_seen = True
            say("检测到「已开始行动」— 战斗已开始，看门狗进入保护")
        if "战斗出错" in tail[-4000:] or "任务完成" in tail[-4000:] or "任务出错" in tail[-4000:]:
            say("MAA 任务已结束，看门狗退出")
            break

        if not battle_seen:
            continue

        frozen = frozen + 1 if (prev is not None and float(np.abs(
            img.astype("float32") - prev.astype("float32")).mean()) < FROZEN_DIFF) else 0
        prev = img

        stall = time.time() - last_growth
        if frozen >= FROZEN_N and stall >= STALL_S and time.time() - last_tap > COOLDOWN_S:
            try:
                act.tap(*PLAY_BTN)
                last_tap = time.time()
                say(f"画面静止 {frozen} 次 + 日志停滞 {stall:.0f}s → 已点 ▶ 解除暂停")
                frozen = 0
                prev = None
            except Exception as e:
                say(f"点 ▶ 失败：{type(e).__name__} {e}")

    say("看门狗退出")
    logf.close()


if __name__ == "__main__":
    main()
