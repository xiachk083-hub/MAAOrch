#!/usr/bin/env python3
"""从人工作业生成"AI 自己能执行的部署计划"+ 把关卡格点渲染成图。

为什么要有这一步:
  作业站上的作业是**给 MAA 用的**（它靠 AutoSquad 把组名映射到编队成员），
  我们的 agent 走的是另一条路（自己选卡、自己落子），所以要把作业翻译成
  `data/plans/<关>_from_job.json`：位置与朝向照抄（那是作者验证过的），
  干员按职能从**本场编队**里挑（`pick` 是候选名单，按编队顺序命中第一个）。

同时渲染一张关卡图（格点 + 可部署位 + 红蓝门 + 计划落点），用途有两个:
  · 人眼复核"我们的格点表和地图对得上"（对着 PRTS.Map 看）
  · 出问题时一眼看出是哪个格子的投影算歪了

用法:
    # 从作业站取作业 → 生成计划（附渲染图）
    python tools/ai_agent_stage_plan.py --copilot 49629 --stage main_15-11 \
        --out data/plans/15-12_from_job.json
    # 只渲染某个关卡
    python tools/ai_agent_stage_plan.py --stage main_15-11 --render-only
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from services.ai_agent.tile_map import load_level  # noqa: E402

OUT = PROJ / "logs" / "agent"
API = "https://prts.maa.plus/copilot/get/"

# 作业里出现的"组名"→ 我们这边按什么候选去编队里找。组名是作业作者自定义的
# （奶盾/输出/速狙…），不在组里就当成干员名直接用。
_GROUP_HINT = ("奶盾", "输出", "奶", "盾", "先锋", "术师", "狙击", "近卫", "医疗",
               "辅助", "特种", "重装", "速狙", "法", "任意")


def fetch_copilot(cid: int) -> dict:
    import urllib.request
    with urllib.request.urlopen(API + str(cid), timeout=30) as r:
        payload = json.loads(r.read().decode("utf-8"))
    if payload.get("status_code") != 200:
        raise SystemExit(f"作业 {cid} 拉取失败: {payload.get('status_code')}")
    return json.loads(payload["data"]["content"])


def to_plan(cop: dict, stage: str, cid: int) -> dict:
    groups = {g["name"]: [o["name"] for o in g.get("opers", [])] for g in cop.get("groups", [])}
    # 单干员（无 groups）的作业：opers 列表就是候选
    groups.update({o["name"]: [o["name"]] for o in (cop.get("opers") or [])})
    steps = []
    for a in cop.get("actions", []):
        if a.get("type") != "Deploy":
            continue          # SpeedUp / SkillDaemon 由执行侧统一处理
        name = a.get("name") or ""
        steps.append({
            "from_job": name,
            "pick": groups.get(name) or [name],
            "location": list(a["location"]),
            "direction": a.get("direction") or "Right",
            "after_kills": a.get("kills"),
        })
    return {
        "stage": stage,
        "stage_name_display": f"作业 {cid}：{(cop.get('doc') or {}).get('title', '')}",
        "source": f"prts.maa.plus/copilot/get/{cid}（位置/朝向照抄，干员按职能从本场编队挑）",
        "squad_order": [],          # 进场前读编队页填
        "skill_policy": "技能就绪就点（skill_ready 标记）",
        "steps": steps,
    }


def render(stage: str, plan: dict | None, out_png: pathlib.Path) -> pathlib.Path:
    """把格点图画出来：灰=不可部署，绿=地面可部署，蓝=高台可部署，红=红门，青=蓝门。"""
    import cv2
    import numpy as np
    lv = load_level(stage)
    if lv is None:
        raise SystemExit(f"格点表加载失败: {stage}")
    cell = 64
    W, H = lv.width * cell, lv.height * cell
    img = np.zeros((H, W, 3), np.uint8)
    for c, r, t in lv.all_cells():
        x0, y0 = c * cell, r * cell
        color = (60, 60, 60)
        if t.ground_deployable:
            color = (60, 150, 60)
        elif t.high_deployable:
            color = (150, 110, 40)
        cv2.rectangle(img, (x0 + 2, y0 + 2), (x0 + cell - 2, y0 + cell - 2), color, -1)
        if t.is_start:
            cv2.circle(img, (x0 + cell // 2, y0 + cell // 2), 12, (0, 0, 230), -1)
        if t.is_end:
            cv2.circle(img, (x0 + cell // 2, y0 + cell // 2), 12, (230, 200, 0), -1)
        cv2.putText(img, f"{c},{r}", (x0 + 4, y0 + 14), cv2.FONT_HERSHEY_PLAIN, 0.9,
                    (200, 200, 200), 1)
    for i, s in enumerate((plan or {}).get("steps", []), 1):
        c, r = s["location"]
        x0, y0 = c * cell, r * cell
        p = lv.project(c, r)
        cv2.rectangle(img, (x0 + 4, y0 + 4), (x0 + cell - 4, y0 + cell - 4), (0, 220, 255), 2)
        cv2.putText(img, f"{i}:{s['from_job'][:4]}", (x0 + 4, y0 + cell - 8),
                    cv2.FONT_HERSHEY_PLAIN, 1.0, (0, 220, 255), 1)
        print(f"  step{i} {s['from_job']:6s} 格({c},{r}) 朝{s['direction']:5s} → 屏幕 {p}")
    out_png.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_png), img)
    print(f"格点图: {out_png}（{lv.width}x{lv.height}，{lv.filename}）")
    return out_png


def main() -> None:
    ap = argparse.ArgumentParser(description="作业 → AI 执行计划 + 关卡格点图")
    ap.add_argument("--copilot", type=int, help="作业站 id")
    ap.add_argument("--stage", required=True, help="格点表 key（如 main_15-11）")
    ap.add_argument("--out", help="计划输出路径")
    ap.add_argument("--render-only", action="store_true")
    args = ap.parse_args()

    plan = None
    if not args.render_only:
        if not args.copilot:
            sys.exit("--copilot 或 --render-only 二选一")
        cop = fetch_copilot(args.copilot)
        plan = to_plan(cop, args.stage, args.copilot)
        dst = pathlib.Path(args.out) if args.out else PROJ / "data" / "plans" / f"{args.stage}_from_job.json"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"计划: {dst}（{len(plan['steps'])} 步）")
    elif args.stage:
        p = PROJ / "data" / "plans" / f"{args.stage}_from_job.json"
        plan = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    tag = (args.stage or "stage").replace("/", "_")
    render(args.stage, plan, OUT / f"stage_map_{tag}.png")


if __name__ == "__main__":
    main()
