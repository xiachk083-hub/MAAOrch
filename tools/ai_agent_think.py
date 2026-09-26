#!/usr/bin/env python3
"""让 AI 看一眼并决定下一步 — "教它玩"的实际入口。

默认**只决策不执行**（打印它看到的画面描述 + 决定 + 理由），方便你复核它是不是
看懂了；加 --execute 才真的点下去，并且每步留证（logs/agent/<会话>/）。

用法:
    # 离线看一张历史截图（不碰模拟器，最安全）
    python tools/ai_agent_think.py --image logs/agent/live_210230.png --client YoStarJP \
        --goal "打赢这场战斗"

    # 实机看一眼（只读，不发输入）
    python tools/ai_agent_think.py --emu 0 --client YoStarJP --goal "进入活动关卡列表"

    # 真执行一步（会点；需要该模拟器上没有别的 MAA 在操作）
    python tools/ai_agent_think.py --emu 1 --client YoStarJP --goal "..." --execute
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from services.ai_agent.brain import Brain, observe  # noqa: E402
from services.ai_agent.llm import LLM  # noqa: E402
from services.ai_agent.percept import Perception  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="AI 决策一步（默认只决策不执行）")
    ap.add_argument("--emu", default=None, help="模拟器索引（MuMuManager -v）")
    ap.add_argument("--image", default=None, help="离线截图路径（与 --emu 二选一）")
    ap.add_argument("--instance", default="1")
    ap.add_argument("--client", default="Official")
    ap.add_argument("--goal", default="", help="自然语言目标，例如 '进入活动关卡 XX 的详情页'")
    ap.add_argument("--battle", action="store_true", help="强制读取战斗信号")
    ap.add_argument("--execute", action="store_true", help="真执行（会点下去）")
    ap.add_argument("--forbid", default="", help="禁止点击的目标关键字，逗号分隔（如 StartButton1,开始行动）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    if args.image:
        p = Perception.offline(args.image, instance=args.instance, client=args.client)
        src = args.image
    else:
        if args.emu is None:
            ap.error("需要 --emu 或 --image")
        p = Perception.for_emu(args.emu, instance=args.instance, client=args.client)
        src = f"emu#{args.emu}"

    llm = LLM()
    brain = Brain(llm)
    print(f"感知源: {src} | 客户端 {p.paths.client} | LLM: "
          f"{'已配置 ' + llm.cfg['model'] if llm.ready else '未配置（将只能靠硬规则）'}")

    img = p.shot()
    obs = observe(p, img, battle=args.battle)
    forbid = tuple(x.strip() for x in (args.forbid or "").split(",") if x.strip())
    act = brain.decide(p, goal=args.goal, img=img, battle=args.battle, forbid=forbid)

    if args.json:
        print(json.dumps({"observation": obs.describe(),
                          "action": {"kind": act.kind, "target": act.target,
                                     "verify": act.verify, "why": act.why,
                                     "confidence": act.confidence, "source": act.source,
                                     "resolved": act.resolved.as_dict() if act.resolved else None}},
                         ensure_ascii=False, indent=2))
    else:
        print("\n── AI 看到的 ──")
        print(obs.describe())
        print("\n── AI 的决定 ──")
        print(f"动作: {act.kind}  目标: {act.target}")
        print(f"理由: {act.why}")
        print(f"置信度: {act.confidence}  来源: {act.source}")
        if act.resolved:
            print(f"本地落点: {act.resolved.label} @{act.resolved.center} "
                  f"(分数 {act.resolved.score:.2f})")
        if act.kind == "give_up" and act.extras.get("raw"):
            print(f"模型原始输出: {json.dumps(act.extras['raw'], ensure_ascii=False)[:300]}")

    if args.execute:
        if act.kind == "give_up" or act.target is None:
            print("\n[execute] 动作是 give_up/无目标，不执行")
            return
        from services.ai_agent.actuator import Actuator, Session
        sess = Session(f"think_{args.goal[:20] or 'step'}")
        actr = Actuator(p, session=sess)
        print(f"\n[execute] 执行 {act.kind} {act.target} …（留证 {sess.dir}）")
        if act.kind == "back":
            actr.back()
            print("[execute] 已按返回")
        elif act.kind == "swipe" and isinstance(act.target, dict) and act.target.get("swipe"):
            r = actr.swipe_do(*act.target["swipe"], verify=act.verify, note=act.why[:30])
            print(f"[execute] {r.ok} {r.detail}")
        elif act.kind == "tap":
            r = actr.do(act.target, verify=act.verify, note=act.why[:30])
            print(f"[execute] {r.ok} {r.detail}")
        else:
            print(f"[execute] 动作 {act.kind} 暂不支持执行")
    else:
        print("\n（只决策未执行；加 --execute 才会真的点）")


if __name__ == "__main__":
    main()
