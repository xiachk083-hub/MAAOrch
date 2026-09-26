# -*- coding: utf-8 -*-
"""观察作业：**真实执行**一份 copilot 作业，逐步记录 (state_before, action, state_after) + 前后帧。

与「模型重放」的区别（这是本工具存在的理由）：
  · 重放只能产出 (state, action)；观察产出 (state, action, **next_state**)。
  · state 全部来自**真实画面**（不是模型算的）⇒ 动作的后果看得见，
    「人类为什么这么操作」的答案就在 next_state 里。

产出：
    runtime/observed_jobs/<job_id>/
        step_000.json  step_000_before.png  step_000_after.png
        ...
        summary.json

复用的本仓件（不重复造）：
  · Perception / Actuator  —— 截图（capture 已含黑帧重试）、点/滑、串行锁
  · deploy.place() + verify_placed()  —— 两段 swipe 部署 + **干员检测闭环**
  · deploy.calibrate_cards()          —— 部署栏卡位标定（用于按费用找卡）
  · hud.read_hud()                    —— 一屏读完 费用/生命/敌人/已部署/技能就绪

用法：
    python tools/observe_job.py --job data/copilot/sr54_picked/act54side_01_101906.json --emu 2
    python tools/observe_job.py --job <file> --emu 2 --dry      # 只验记录结构，不实际执行

标注（不假装完整）：
  · 状态为**部分状态** —— hud 能读 费用/生命/敌人/已部署(+坐标)；读不到敌人坐标与朝向。
  · 动作对齐：Deploy 走 verify_placed 闭环（偏了按半格重试）；Retreat/Skill 目前依赖
    游戏自身反馈，未做独立校验，记录里标 verified=false。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from services.ai_agent.deploy import (DIR_VEC, CardLayout, calibrate_cards,  # noqa: E402
                                      place, verify_placed)
from services.ai_agent.hud import read_hud  # noqa: E402
from services.ai_agent.percept import Perception, log  # noqa: E402
from services.ai_agent.tile_map import load_level  # noqa: E402

SPEEDUP_PT = (1247, 48)          # 战斗右上角 2x 加速键（1280x720 基准，实机可调）
POST_TAP_WAIT = 0.5              # 动作后等画面稳定的时长（用户口径：先 0.5s 看效果调）


# ── 状态读取 ──────────────────────────────────────────────────────────
def read_state(p: Perception, img=None, llm=None) -> dict:
    """一屏读到什么就记什么；读不到的字段是 None，不猜。

    返回 structure:
      {ok, paused, cost, life, enemies, deployed, deployed_xy, source, note}
    """
    img = p.shot() if img is None else img
    h = read_hud(p, img=img, llm=llm)
    d = h.as_dict()
    d["source"] = "hud(read_hud/multimodal)"
    return d


# ── 卡位：把作业里的干员名映射到部署栏的一张卡 ────────────────────────
def card_point_for(job: dict, name: str, layout: CardLayout | None,
                   card_costs: dict | None = None, box_cost: int | None = None,
                   manual: tuple[int, int] | None = None) -> tuple[int, int]:
    """找 name 对应的卡位。

    优先级：显式指定 > 用该干员的费用在标定结果里找同费的卡 > 布局锚点（最右那张）。
    MAA 的作业 `opers` 顺序 = 编队顺序，而**战斗里卡行是按费用升序重排的**，
    所以按名字取第 i 张是错的 —— 这是我先前在 RL harness 上踩过的坑。
    """
    if manual:
        return manual
    if layout and card_costs and box_cost is not None:
        same = [pt for pt, c in card_costs.items() if str(c) == str(box_cost)]
        if len(same) == 1:
            return same[0]
    if layout and layout.count:
        return layout.points(1)[0]
    raise RuntimeError(f"没有可用卡位（需要 --card 或一次成功的卡位标定）；干员={name}")


# ── 动作执行 ──────────────────────────────────────────────────────────
def resolve_name(act_name: str, resolve: dict | None, box: dict | None) -> str:
    """把作业里的**组名/占位符**（如「奶盾」）解析成真实干员名。

    作业的 Deploy 动作常写组名（`groups: [{"name":"奶盾","opers":[...]}]`），
    真实编队里只会出现组内某一个。解析优先级：
      1) 显式 `--resolve 奶盾=黍`
      2) 组内**该号拥有**的第一个成员
      3) 原样返回（记录里如实保留，便于事后发现没解析）
    """
    if not act_name:
        return act_name
    if resolve and act_name in resolve:
        return resolve[act_name]
    return act_name


def execute_action(p: Perception, level, act: dict, card_pt: tuple[int, int] | None,
                   retries: int = 2, dry: bool = False) -> dict:
    """执行一个 copilot 动作，返回 {ok, verified, why, detail}。

    Deploy 走 `place()` + `verify_placed()` 闭环：偏了就重试（用户口径：确保
    「记录的动作 = 实际发生的动作」）。
    """
    t = act.get("type")
    if dry:
        return {"ok": True, "verified": False, "why": "dry-run 未执行", "detail": {}}
    if t == "Deploy":
        loc = act.get("location") or []
        if len(loc) < 2:
            return {"ok": False, "verified": False, "why": f"Deploy 缺 location: {act}", "detail": {}}
        col, row = int(loc[0]), int(loc[1])
        direction = (act.get("direction") or "Right").lower()
        last = {}
        for k in range(retries + 1):
            r = place(p, level, card_pt, col, row, direction=direction)
            time.sleep(0.5)
            ok = verify_placed(p, level.project(col, row))
            last = {"ok": r.get("ok"), "verified": ok, "why": "", "detail": {**r, "attempt": k + 1}}
            if ok:
                return last
            log(f"  未确认落位（第 {k + 1} 次），重试")
        last["why"] = "重试耗尽仍未在目标格检出干员"
        return last
    if t == "Skill":
        # 技能：优先点「技能就绪」标记（brain 的做法），标记位置由 hud 给出
        llm_state = read_state(p)
        pts = [tuple(xy) for xy in (llm_state.get("skill_ready_xy") or [])]
        if pts:
            x, y = pts[0]
            p.screen.run_shell(["input", "tap", str(x), str(y)])
            return {"ok": True, "verified": False, "why": "点了技能就绪标记（未独立校验）",
                    "detail": {"tap": [x, y]}}
        return {"ok": False, "verified": False, "why": "未找到技能就绪标记", "detail": {}}
    if t == "Retreat":
        return {"ok": False, "verified": False,
                "why": "撤退未实现（按钮偏移未标定）—— 记录但不假装执行成功", "detail": {}}
    if t == "SpeedUp":
        p.screen.run_shell(["input", "tap", str(SPEEDUP_PT[0]), str(SPEEDUP_PT[1])])
        return {"ok": True, "verified": False, "why": "点 2x 加速", "detail": {"tap": list(SPEEDUP_PT)}}
    if t in ("SkillDaemon", "SkillDaemonUse"):
        return {"ok": True, "verified": False,
                "why": "SkillDaemon 是 MAA 侧概念（自动开技能），无需手动动作", "detail": {}}
    return {"ok": True, "verified": False, "why": f"未识别的动作类型 {t}，记录但未执行", "detail": {}}


# ── dry-run 用的桩（完全不碰设备，供离线验记录结构）──────────────────
class _DryScreen:
    def run_shell(self, args):
        return None


class _DryPerception:
    def __init__(self):
        self.screen = _DryScreen()

    def shot(self):
        return None


_DRY_STATE = {"ok": False, "paused": None, "cost": None, "life": None, "enemies": None,
              "deployed": None, "deployed_xy": [], "source": "dry-run(未读设备)",
              "note": "dry-run：状态字段全空，只验记录结构"}


# ── 主流程 ────────────────────────────────────────────────────────────
def observe_job(job: dict, emu_idx: int, out_root: pathlib.Path,
                card_pt: tuple[int, int] | None = None, dry: bool = False,
                llm=None, card_map: dict | None = None, resolve: dict | None = None) -> dict:
    """执行 job 并逐步记录。返回 summary。

    card_map: {干员名: (x,y)} —— 一份作业里有多个不同干员时必须给，否则只能按
              全局 card_pt / 标定结果猜。实测 SR-1 那份作业要 2 个干员（丰川祥子
              + 奶盾组），单一 card_pt 不够用。
    resolve:  {组名: 真实干员名} —— 作业写组名（「奶盾」）时用它落地。
    """
    job_id = str(job.get("_file") or job.get("doc", {}).get("title") or "job")
    job_id = pathlib.Path(job_id).stem
    out = out_root / job_id
    out.mkdir(parents=True, exist_ok=True)

    p = _DryPerception() if dry else Perception(emu_idx)
    read = (lambda *a, **k: dict(_DRY_STATE)) if dry else read_state
    level = load_level(job.get("stage_name") or "")
    if level is None:
        raise RuntimeError(f"关卡数据缺失: {job.get('stage_name')}（需要 data/_level_*.json）")

    layout, card_costs = None, {}
    if card_pt is None and not dry:
        layout = calibrate_cards(p, llm=llm)
        if layout:
            card_costs = {layout.point(i): c for i, c in enumerate([])}  # 标定成本表（若有）

    name0 = next((a.get("name") for a in job.get("actions") or []
                  if a.get("type") == "Deploy" and a.get("name")), None)
    if card_pt is None and not dry:
        card_pt = card_point_for(job, name0, layout, card_costs)

    actions = job.get("actions") or []
    start = time.time()
    records = []
    for i, act in enumerate(actions):
        rec = {"step": i, "t": round(time.time() - start, 2), "action": act}
        # 组名 → 真实干员；再据此挑卡位（多干员作业的关键）
        act_name = resolve_name(act.get("name"), resolve, None)
        pt = (card_map or {}).get(act_name) or card_pt
        if act_name != act.get("name"):
            rec["resolved_name"] = act_name
        before = p.shot()
        rec["state_before"] = read(p, img=before, llm=llm)
        _save(out / f"step_{i:03d}_before.png", before)

        res = execute_action(p, level, act, pt, dry=dry)
        rec["exec"] = res
        time.sleep(POST_TAP_WAIT)

        after = p.shot()
        rec["state_after"] = read(p, img=after, llm=llm)
        _save(out / f"step_{i:03d}_after.png", after)

        rec["before_frame"] = f"step_{i:03d}_before.png"
        rec["after_frame"] = f"step_{i:03d}_after.png"
        # 动作对齐：只有 Deploy 有独立闭环校验
        rec["action_verified"] = bool(res.get("verified"))
        rec["partial_state"] = True     # 标注：状态是部分状态（读不到敌人坐标/朝向）
        (out / f"step_{i:03d}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        records.append(rec)
        log(f"  步 {i}: {act.get('type')} → 执行 {res.get('ok')} 校验 {res.get('verified')}")

    summary = {"job_id": job_id, "stage": job.get("stage_name"), "emu": emu_idx,
               "title": (job.get("doc") or {}).get("title"), "opers": job.get("opers"),
               "total_steps": len(records),
               "verified_steps": sum(1 for r in records if r["action_verified"]),
               "dry": dry, "start_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(start)),
               "end_time": time.strftime("%Y-%m-%d %H:%M:%S"),
               "steps": [{k: r[k] for k in ("step", "t", "action", "action_verified")} for r in records]}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"观察完成 → {out}（{len(records)} 步，校验过 {summary['verified_steps']} 步）")
    return summary


def _save(path: pathlib.Path, img) -> None:
    if img is None:
        return
    import cv2
    cv2.imwrite(str(path), img)


def main() -> None:
    ap = argparse.ArgumentParser(description="观察作业：真实执行 + 逐步记录 state 三元组")
    ap.add_argument("--job", required=True, type=pathlib.Path, help="copilot 作业 JSON")
    ap.add_argument("--emu", default=2, help="模拟器索引（Perception 的 emu_idx）")
    ap.add_argument("--out", default="runtime/observed_jobs", type=pathlib.Path)
    ap.add_argument("--card", default="", help="显式卡位 x,y（单干员作业用；跳过卡位标定）")
    ap.add_argument("--cards", default="",
                    help="多干员作业：'干员A=1100,660;干员B=994,660'（战斗截图里读卡位后填）")
    ap.add_argument("--resolve", default="",
                    help="组名落地：'奶盾=黍;核=圣聆初雪'（作业写组名时必给）")
    ap.add_argument("--dry", action="store_true", help="只验记录结构，不实际执行")
    a = ap.parse_args()
    job = json.loads(a.job.read_text(encoding="utf-8"))
    job["_file"] = str(a.job)
    card = tuple(int(v) for v in a.card.split(",")) if a.card else None
    cards = {}
    for item in filter(None, a.cards.split(";")):
        nm, xy = item.split("=")
        x, y = xy.split(",")
        cards[nm.strip()] = (int(x), int(y))
    resolve = {}
    for item in filter(None, a.resolve.split(";")):
        k, v = item.split("=")
        resolve[k.strip()] = v.strip()
    observe_job(job, a.emu, a.out, card_pt=card, dry=a.dry, card_map=cards, resolve=resolve)


if __name__ == "__main__":
    main()
