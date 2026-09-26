#!/usr/bin/env python3
"""教 agent 认界面 — 用 MAA 任务库扫当前画面，把认出来的元素学成这个界面的锚点。

"不会的就找 MAA 的定义去学"：MAA 的 tasks.json 是人工标注过的 UI 地图
（每个可点元素都有 roi + 模板），拿它刷一屏就知道这一屏有什么、在哪。
学到的锚点存 data/agent_anchors.json，brain 会在下一次观察时直接用。

用法:
    # 学当前实机画面（只读，不发输入）
    python tools/ai_agent_discover.py --emu 1 --client Official
    # 学一张历史截图
    python tools/ai_agent_discover.py --image logs/agent/emu1_step2.png --client Official
    # 只扫名字含关键字的任务（大屏更快）
    python tools/ai_agent_discover.py --emu 1 --filter Terminal
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from services.ai_agent.percept import (  # noqa: E402
    Perception, ResourcePaths, discover_tasks, log,
)

STORE = PROJ / "data" / "agent_anchors.json"


def load_store() -> dict:
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_store(d: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="用 MAA 任务库学习当前界面的锚点")
    ap.add_argument("--emu", default=None)
    ap.add_argument("--image", default=None)
    ap.add_argument("--instance", default="1")
    ap.add_argument("--client", default="Official")
    ap.add_argument("--filter", default=None, help="只扫任务名含该关键字的")
    ap.add_argument("--min-score", type=float, default=0.9)
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--budget", type=float, default=90.0, help="扫描时间预算（秒）")
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args()

    if args.image:
        p = Perception.offline(args.image, instance=args.instance, client=args.client)
        src = args.image
    else:
        if args.emu is None:
            ap.error("需要 --emu 或 --image")
        p = Perception.for_emu(args.emu, instance=args.instance, client=args.client)
        src = f"emu#{args.emu}"

    img = p.shot()
    st = p.state(img, use_ocr=False)
    state = st["verdict"]
    print(f"感知源 {src} | 客户端 {p.paths.client} | 状态: {state}")

    t0 = time.time()
    hits = discover_tasks(img, paths=p.paths, client=args.client,
                          min_score=args.min_score, limit=args.limit,
                          budget_s=args.budget, name_filter=args.filter)
    print(f"\n扫到 {len(hits)} 个元素（{time.time() - t0:.1f}s）:")
    for h in hits:
        print(f"  {h['score']:.3f}  {h['task']:34s} @{h['center']}  "
              f"模板 {h['template']}")

    if hits and not args.no_save:
        store = load_store()
        bucket = store.setdefault(state, [])
        by_task = {a.get("spec", {}).get("task"): a for a in bucket}
        for h in hits:
            by_task[h["task"]] = {"label": h["task"], "spec": h["spec"],
                                  "center": h["center"], "score": h["score"],
                                  "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
        store[state] = list(by_task.values())
        save_store(store)
        print(f"\n已写入 {STORE}（界面「{state}」现有 {len(store[state])} 个锚点）")
    elif not hits:
        print("\n没扫到任何已标注元素 —— 这一屏 MAA 也没标注过，需要人教（或走 LLM 看图兜底）")


if __name__ == "__main__":
    main()
