#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**剧情跳过看门狗** —— 2026-09-24 实测得出：EA-1（从没打过的关）点「开始行动」后会先播**剧情**，
卡住整个流程（MAA 在等战斗，屏幕上却是对话），那次就是这样"关卡打完了却判 failed"。

实测得到的完整配方（两处坐标都是在 1280x720 截图里定的）：
    ① 剧情界面右上角有「**跳过 ▶**」 → 点 (1200, 53)
    ② 弹出「**是否跳过剧情?**」→ 点右边那个 **⊙ 确认** → (796, 462)（左边 ⊗ 是取消，≈(476,462)）
    ③ 跳过之后才会进战斗/结算

**状态机在常驻服务里**（`tools/maaorch_service.py` 的 `GET /skipstory`）：一次调用 = 检测 + 点跳过
+ 认弹窗 + 点确认，所以这个脚本现在只是**一层薄薄的循环**（服务不在时才自己拼 `/look` + `/tap`）。
这样满足"极致快"的三条硬要求：**不 spawn 子进程**（本脚本自身就是那个"一个进程"）、
**不全屏 OCR**（只看 ROI 1050,20,1279,90）、**不固定 sleep 长等**（tick 间隔默认 1.2s）。

通常不需要手动跑它：`tools/event_sweep.mjs` 横扫时会自带看门狗（优先 `/skipstory`，
服务不可用才 spawn 本脚本一次）。手动跑它的场合是"我自己点一份作业跑，没人替我看剧情"。

用法:
    python tools/story_skip.py                # 前台跑（Ctrl-C 停）
    python tools/story_skip.py --minutes 12   # 后台跑，最长 12 分钟
    python tools/story_skip.py --once         # 只看一次（探针/自测用）
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request

SVC = "http://127.0.0.1:8765"
SKIP_BTN = (1200, 53)                     # 「跳过 ▶」
CONFIRM = (796, 462)                      # 「是否跳过剧情?」右侧确认 ⊙
ROI = "1050,20,1279,90"                   # 只看右上角这一小块（"跳过"两个字在这儿）


def svc(path: str, timeout: float = 5.0):
    """调服务；连不上/超时/非 JSON 一律返回 None（调用方走兜底）。"""
    try:
        with urllib.request.urlopen(SVC + path, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:                                                    # noqa: BLE001
        return None


def tap(x: int, y: int) -> None:
    svc(f"/tap?x={x}&y={y}", timeout=4)


def one_round() -> dict:
    """看一次 + （有剧情就）跳过。返回 {found, how}；服务端点缺失时自己拼 look+tap。"""
    j = svc("/skipstory", timeout=5)
    if j is not None:
        return {"found": bool(j.get("found")), "how": "skipstory", "taps": j.get("taps")}
    seen = svc(f"/look?roi={ROI}", timeout=5)
    if seen is None:
        return {"found": False, "how": "服务不可用", "taps": None}
    found = "跳过" in "".join(seen.get("texts") or [])
    if found:
        tap(*SKIP_BTN)
        time.sleep(0.8)
        tap(*CONFIRM)
    return {"found": found, "how": "look+tap", "taps": None}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=0, help="0 = 一直跑")
    ap.add_argument("--interval", type=float, default=1.2, help="tick 间隔秒（默认 1.2）")
    ap.add_argument("--once", action="store_true", help="只查一次就退出（探针用）")
    a = ap.parse_args()
    t_end = time.time() + a.minutes * 60 if a.minutes else None
    n = 0
    how = "?"
    print("剧情看门狗启动（发现「跳过」就点 → 再点确认）", flush=True)
    while t_end is None or time.time() < t_end:
        r = one_round()
        how = r["how"]
        if r["found"]:
            n += 1
            print(f"[{time.strftime('%H:%M:%S')}] 发现剧情 ⇒ 跳过（第 {n} 次，通道 {how}）", flush=True)
            time.sleep(0.9)                       # 跳过后让画面推进，别连着点
            if a.once:
                break
        if a.once:
            break
        time.sleep(a.interval)
    print(f"看门狗退出，共跳过 {n} 次（通道 {how}）", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
