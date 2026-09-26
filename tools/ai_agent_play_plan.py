#!/usr/bin/env python3
"""按计划打一局 — AI 自己执行"从人工作业学来的"部署计划。

数据流: 作业站的人工作业 → data/plans/<stage>_from_job.json（位置/朝向照抄，
干员按职能从账号当前编队里替换）→ 本脚本执行。

为什么不让 MAA 抄作业: MAA 的 Copilot 需要先把作业要的干员**选进编队**
（`BattleProcessTask` 用 DLX 精确覆盖把作业的组映射到编队成员），
而 391 名干员的账号里自动编队建不出可行解 → 部署指令被静默跳过 → 零动作必输
（2026-09-14 三次实测）。我们这套走的是"自己选卡读名 + 格点表投影落子"，
绕开编队环节，且每一步都用视觉 HUD 判定成败。

用法:
    python tools/ai_agent_play_plan.py --emu 1 --plan data/plans/15-11_from_job.json --drill
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

import cv2  # noqa: E402

from services.ai_agent import deploy  # noqa: E402
from services.ai_agent.actuator import Actuator, Session  # noqa: E402
from services.ai_agent.battle_vision import BattleVision  # noqa: E402
from services.ai_agent.brain import vision_locate  # noqa: E402
from services.ai_agent.hud import read_hud  # noqa: E402
from services.ai_agent.llm import LLM  # noqa: E402
from services.ai_agent.percept import Perception, log  # noqa: E402
from services.ai_agent.tile_map import load_level  # noqa: E402

OUT = PROJ / "logs" / "agent"
_SYS_NAME = """你在看明日方舟战斗截图左侧的干员详情面板。只输出 JSON:
{"name":"干员名","class":"职业","cost":整数}
职业取值: 先锋/近卫/狙击/术师/重装/医疗/辅助/特种。认不出给 "?"，不要猜。"""


def read_panel_name(p: Perception, llm: LLM) -> dict:
    img = p.shot()
    x, y, w, h = deploy.PANEL_ROI
    crop = img[y:y + h, x:x + w].copy()
    r = llm.ask_json(_SYS_NAME, "读出面板里的干员名与职业。", image=crop,
                     vision=True, max_tokens=8000)
    return (r.get("json") or {}) if r.get("ok") else {}


def find_card_for(wants: list[str], squad_order: list[str],
                  act: Actuator, p: Perception, llm: LLM,
                  img=None) -> tuple[int, int] | None:
    """找一张卡：**先头像匹配**（毫秒级、不依赖顺序假设），找不到再退回索引。

    为什么不能只靠索引: 部署栏是**按费用升序**排的（实测 3/8/12/13/14/17/20/21/21/21/22/30），
    编队页顺序与它不一致 —— 索引会把卡点到别人身上。所以"用当前编队的干员替换作业
    里的干员"这条路，识别必须走头像（MAA 也是这么做的）。
    """
    hits = deploy.match_cards(p, list(wants), img=img)
    by_name = {h["name"]: h for h in hits}
    for w in wants:                       # 按 pick 的偏好顺序取第一个命中的
        if w in by_name:
            h = by_name[w]
            log(f"  头像选卡: {h['name']} @{h['point']} 分数 {h['score']}")
            return tuple(h["point"])
    if hits:
        h = hits[0]
        log(f"  头像选卡（非首选）: {h['name']} @{h['point']} 分数 {h['score']}")
        return tuple(h["point"])
    for w in wants:
        if w in squad_order:
            i = squad_order.index(w)
            pt = deploy.card_point_by_index(i)
            log(f"  索引选卡(退回，头像没命中): {w} = 编队第 {i+1} 位 → {pt}")
            return pt
    log(f"  部署栏里没有 {wants}（头像没命中、编队索引也没有），跳过")
    return None


def read_cost_local(p: Perception, tries: int = 3) -> str | None:
    """本地读部署费用（多次取一致值）。实测"12"会被读成"72"，所以取多数。"""
    from services.ai_agent.percept import task_roi
    roi = task_roi("BattleCostData", p.paths.client)
    if not roi:
        return None
    vals = []
    for _ in range(tries):
        h = p.ocr.read_region(p.shot(), roi, upscale=3, min_score=0.2)
        if h:
            d = "".join(c for c in h.label if c.isdigit())
            if d:
                vals.append(d)
    if not vals:
        return None
    best = max(set(vals), key=vals.count)
    return best if vals.count(best) >= 2 else None


def main() -> None:
    ap = argparse.ArgumentParser(description="按计划打一局")
    ap.add_argument("--emu", default="1")
    ap.add_argument("--instance", default="1")
    ap.add_argument("--client", default="Official")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--drill", action="store_true", help="先进演习（零理智成本）")
    ap.add_argument("--max-cost-wait", type=float, default=90.0)
    ap.add_argument("--tries", type=int, default=4, help="每步落子最多试几次（等费用）")
    args = ap.parse_args()

    plan = json.loads((PROJ / args.plan).read_text(encoding="utf-8"))
    # 关卡从计划里取（别写死：主线 15 章的 stageId 与显示代号差 1，
    # main_15-11 才是游戏里的 15-12，写死会拿错格点表）
    lv = load_level(plan.get("stage") or plan.get("stage_name_display") or "")
    if lv is None:
        sys.exit("格点表加载失败")
    p = Perception.for_emu(args.emu, instance=args.instance, client=args.client)
    act = Actuator(p, session=Session("play_plan"), post_delay=1.2)
    llm = LLM()
    bv = BattleVision(p.paths)
    OUT.mkdir(parents=True, exist_ok=True)

    def st() -> str:
        return p.state(p.shot(), use_ocr=True)["verdict"]

    def shot(tag: str):
        img = p.shot()
        cv2.imwrite(str(OUT / f"plan_{tag}.png"), img)
        return img

    log(f"计划: {plan.get('stage_name_display')} 共 {len(plan['steps'])} 步")
    log(f"当前状态: {st()}")

    # 进入战斗（若已在战斗则直接用）
    if "战斗" not in st() or not p.is_in_battle():
        for i in range(6):
            s = st()
            if p.is_in_battle():
                break
            if "详情页" in s:
                act.tap(997, 667); time.sleep(3)
            elif "编队" in s:
                act.tap(1100, 490); time.sleep(5)
            elif "主界面" in s:
                act.tap(970, 161); time.sleep(3)
            else:
                h = vision_locate(p.shot(), "「开始行动」或继续/确认按钮，或者主题曲卡片")
                act.tap(*(h.center if h else (720, 185))); time.sleep(3)
        for i in range(18):
            if p.is_in_battle():
                break
            img = p.shot()
            k = p.find_task_text("SkipThePreBattlePlot", img=img)
            c = p.find_task_text("SkipThePreBattlePlotConfirm", img=img)
            act.tap(*((c or k).center if (c or k) else (640, 360)))
            time.sleep(1.2)
    if not p.is_in_battle():
        log("没能进入战斗，退出")
        return
    log("已在战斗中，开始按计划部署")
    shot("battle_start")

    squad_order = list(plan.get("squad_order") or [])
    # 卡位识别优先头像匹配（部署栏是按费用升序排的，编队顺序索引会点错人 ——
    # 2026-09-15 实机实测）。只有连头像目录都没有时，才花一次视觉去校验索引前提。
    if not deploy.avatar_dirs():
        if squad_order:
            act.tap(*deploy.card_point_by_index(0)); time.sleep(1.0)
            info = read_panel_name(p, llm)
            got = str(info.get("name") or "")
            log(f"校验卡序: 第 1 张 = {got!r}（编队第 1 位应为 {squad_order[0]}）")
            if got and got not in squad_order[0] and squad_order[0] not in got:
                log("⚠ 卡序与编队顺序不一致 —— 索引选卡不可靠")
                squad_order = []

    deployed = 0
    for idx, step in enumerate(plan["steps"], 1):
        col, row = step["location"]
        direction = (step["direction"] or "Right").lower()
        wants = list(step["pick"])
        tp = lv.project(col, row)
        log(f"[{idx}/{len(plan['steps'])}] 作业位 {step['from_job']} → 用 {wants} 放 格({col},{row}) 朝 {direction}"
            f"（投影 {tp}）")
        # 等费用
        t0 = time.time()
        while time.time() - t0 < args.max_cost_wait:
            if p.is_frozen():
                p.resume_if_paused(); time.sleep(1.5)
            cost = read_cost_local(p)
            if cost and cost.isdigit() and int(cost) >= 12:
                log(f"  费用够了（{cost}）")
                break
            time.sleep(1.5)
        cp = find_card_for(wants, squad_order, act, p, llm)
        if cp is None:
            log("  跳过这一步（编队里没有对应的人）")
            continue
        # 选卡本身就是**费用门**：费用不够时游戏不让选，画面不变化
        # （比"费用 ≥ 某个写死的阈值"准，阈值对不同干员根本不一样）
        ok = False
        cost_before = read_cost_local(p)
        for attempt in range(1, args.tries + 1):
            if attempt > 1:
                time.sleep(2.0)
                cost_before = read_cost_local(p)
            if not deploy.select_card(p, cp):
                log(f"  第 {attempt} 次：卡没选中（费用 {cost_before}，多半还不够）")
                continue
            # 落子 = MAA 同款两次滑动（卡→格 定位置，格→方向 定朝向）
            r = deploy.place(p, lv, cp, col, row, direction=direction)
            time.sleep(0.7)
            shot(f"step{idx}_try{attempt}")
            cost_after = read_cost_local(p)
            ok = bool(cost_before and cost_after and cost_before.isdigit()
                      and cost_after.isdigit() and int(cost_after) < int(cost_before))
            if ok:
                log(f"  费用 {cost_before} → {cost_after} ✓ 已落下（第 {attempt} 次，"
                    f"投影 {r.get('tile_px')} 朝 {direction}）")
                break
            log(f"  第 {attempt} 次没落下：费用 {cost_before} → {cost_after}，"
                f"投影 {r.get('tile_px')} 朝 {direction}")
        if ok:
            deployed += 1
        # 顺手开技能
        for m in bv.skill_ready_markers(p.shot()):
            if m.get("ready"):
                act.tap(*m["center"]); log(f"  点技能 @{m['center']}")
                time.sleep(0.5)
    log(f"部署结束：本轮落下 {deployed} 个。最终 HUD: {read_hud(p).as_dict()}")
    shot("final")


if __name__ == "__main__":
    main()
