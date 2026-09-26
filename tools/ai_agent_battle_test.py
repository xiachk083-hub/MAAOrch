#!/usr/bin/env python3
"""部署手势验证器 — 自动进演习战斗，把"选中卡 → 落到格子"的几种手势全试一遍。

为什么要自动化：一场演习只有约 3 分钟，人工一步步盯必然来不及；而进战斗本身
要 2-3 分钟导航。写成脚本才能反复试、每次都留下证据。

判据（必须可观测，不然等于没验）:
  · 主力判据 = **费用下降**（`BattleCostData` 实测能读准："42" 0.9996）
  · 辅助判据 = 干员检测在目标格附近出现
  · 两者都没有 = 这次手势没生效

用法:
    python tools/ai_agent_battle_test.py --emu 1 --rounds 2
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
from services.ai_agent.tile_map import load_level  # noqa: E402

_SYS_CLASS = """你在看明日方舟战斗截图（1280x720），左侧打开了某干员的详情面板。
只输出 JSON: {"name":"干员名","class":"职业","cost":整数, "panel":true/false}
class 取值: 先锋/近卫/狙击/术师/重装/医疗/辅助/特种。认不出给 "?"，不要猜。"""
from services.ai_agent.percept import task_roi  # noqa: E402


def read_cost(p, img=None, tries: int = 3):
    """读费用：同一帧多读几次取一致值。

    实测踩坑：单次把 12 读成 72（艺术字体 1→7 误认），而"费用不变"恰好是
    我们判断"部署失败"的依据 —— 读数不稳会直接导致误判。
    """
    import time as _t
    img = p.shot() if img is None else img
    roi = task_roi("BattleCostData", p.paths.client)
    if not roi:
        return None
    vals = []
    for i in range(tries):
        h = p.ocr.read_region(img, roi, upscale=3, min_score=0.2)
        if h:
            d = "".join(ch for ch in h.label if ch.isdigit())
            if d:
                vals.append(d)
        _t.sleep(0.2)
    if not vals:
        return None
    best = max(set(vals), key=vals.count)
    return best if vals.count(best) >= 2 else vals[0]
from services.ai_agent.percept import Perception, log  # noqa: E402

OUT = PROJ / "logs" / "agent"


class BattleTester:
    def __init__(self, emu, instance="1", client="Official") -> None:
        self.p = Perception.for_emu(emu, instance=instance, client=client)
        self.act = Actuator(self.p, session=Session("battle_test"), post_delay=1.2)
        self.bv = BattleVision(self.p.paths)

    # ── 状态与导航 ──
    def st(self) -> str:
        return self.p.state(self.p.shot())["verdict"]

    def shot(self, tag: str):
        img = self.p.shot()
        cv2.imwrite(str(OUT / f"bt_{tag}.png"), img)
        return img

    def enter_battle(self, timeout_s: float = 150) -> bool:
        """从任意位置走到演习战斗里。"""
        dead = 0
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            s = self.st()
            if "战斗" in s:
                # 本地状态对战斗有误判（暗色地图/失败页/弹窗都命中过），
                # 所以进战斗必须由视觉确认（用户 2026-09-14: OCR 不够用，
                # 需要多模态）—— 本钱花在这里最值
                if self.p.is_in_battle():
                    return True
                log("本地判为战斗中，但视觉说不是 → 继续导航")
                self.act.back(); time.sleep(2)
                continue
            if "登录失效" in s or "疑似弹窗遮罩" in s:
                dead += 1
                log(f"⚠ {s} —— 尝试恢复（第 {dead} 次）")
                if dead > 2:
                    return False
                h = vision_locate(self.p.shot(), "弹窗里的确认/继续按钮（白色圆形对勾）")
                self.act.tap(*(h.center if h else (640, 492)))
                time.sleep(4)
                self.act.tap(640, 510)      # 开始唤醒
                time.sleep(8)
                continue
            if "编队" in s:
                self.act.tap(1100, 490); time.sleep(5); continue
            if self.p.battle_over():
                log("战斗已结束 → 退回详情页准备重开")
                self.act.tap(640, 620); time.sleep(2)      # 结算页常见确认位置
                h = vision_locate(self.p.shot(), "结算/失败页面上的「确认」「返回」「离开」类按钮")
                if h:
                    self.act.tap(*h.center); time.sleep(2.5)
                continue
            if "详情页" in s:
                self.act.tap(997, 667); time.sleep(3); continue     # 演习
            if "剧情" in s:
                for _ in range(20):
                    img = self.p.shot()
                    if "战斗" in self.p.state(img)["verdict"]:
                        return True
                    c = self.p.find_task_text("SkipThePreBattlePlotConfirm", img=img)
                    if c:
                        self.act.tap(*c.center)
                    else:
                        k = self.p.find_task_text("SkipThePreBattlePlot", img=img)
                        self.act.tap(*(k.center if k else (640, 360)))
                    time.sleep(1.1)
                continue
            if "主界面" in s:
                # 捷径: 主界面右上角那块面板就显示「当前 <关卡>」，点它直进详情页
                h = vision_locate(self.p.shot(), "主界面右上角白色面板里显示「当前」的那一关卡卡片（例如写着 15-11 的那行）")
                if h:
                    log(f"点主界面当前关卡卡片 {h.center}")
                    self.act.tap(*h.center); time.sleep(3)
                    if "详情页" in self.st():
                        continue
                self.act.tap(970, 161); time.sleep(3); continue      # 终端卡
            # 其它页面：点主题卡 / 弹窗 / 返回
            self.act.tap(720, 185); time.sleep(2.5)
            self.act.tap(640, 485); time.sleep(2.5)
            h = vision_locate(self.p.shot(), "名为「15-11」的关卡节点，或者能进入 15 章关卡列表的入口")
            if h:
                self.act.tap(*h.center); time.sleep(3)
            else:
                self.act.back(); time.sleep(2)
        return False

    # ── 手势矩阵 ──
    def _drag(self, method: str, cp, tp) -> None:
        if method == "tap":
            self.act.tap(*tp)
        elif method == "motionevent":
            mid = ((cp[0] + tp[0]) // 2, (cp[1] + tp[1]) // 2)
            for cmd in (["input", "motionevent", "DOWN", str(cp[0]), str(cp[1])],
                        ["input", "motionevent", "MOVE", str(mid[0]), str(mid[1])],
                        ["input", "motionevent", "MOVE", str(tp[0]), str(tp[1])],
                        ["input", "motionevent", "UP", str(tp[0]), str(tp[1])]):
                self.p.screen.run_shell(cmd)
                time.sleep(0.15)
        else:                                    # swipe_ms 形式
            ms = method.split("_")[1]
            self.p.screen.run_shell(["input", "swipe", str(cp[0]), str(cp[1]),
                                     str(tp[0]), str(tp[1]), ms])

    def read_card_class(self, card_idx, layout=None) -> dict:
        """读已选中卡的面板（名字/职业）。本地 OCR 读不出艺术字，交给多模态。

        card_idx 为 int 时按布局取点并点一下；为 (x,y) 元组时假定已选中，直接读。
        """
        from services.ai_agent.llm import LLM
        cp = card_idx if isinstance(card_idx, (tuple, list)) else layout.point(card_idx)
        if not isinstance(card_idx, (tuple, list)):
            self.act.tap(*cp)
            time.sleep(1.2)
        img = self.p.shot()
        x, y, w, h = deploy.PANEL_ROI
        crop = img[y:y + h, x:x + w].copy()
        r = LLM().ask_json(_SYS_CLASS, "读出这个干员详情面板里的干员名与职业。", image=crop,
                           vision=True, max_tokens=8000)
        d = (r.get("json") or {}) if r.get("ok") else {}
        info = {"idx": card_idx, "point": list(cp), "name": d.get("name"),
                "class": d.get("class"), "cost": d.get("cost"), "panel": d.get("panel")}
        log(f"  卡#{card_idx} → {info}")
        return info

    def deploy_v2(self, layout, max_cards: int = 4) -> dict:
        """闭环部署：选卡 → 读职业 → 格点表挑合法格 → 投影 → 点/拖 → 视觉 HUD 判定。

        为什么要格点表算格子: 让视觉在高亮格里挑，实测挑到的是"蓝色三角标记"，
        点了不生效（那可能是 PRTS 标记或范围预览）。格点表 + 投影是我们**验证过**
        的换算（像素级吻合），比看图猜稳。
        """
        lv = load_level("一段长梦将醒")
        if lv is None:
            return {"ok": False, "why": "格点表加载失败"}
        log(f"关卡 {lv.name} {lv.width}x{lv.height} 地面位 {len(lv.ground_spots)} 高台位 {len(lv.high_spots)}")
        if self.p.is_frozen():
            self.p.resume_if_paused(); time.sleep(2)
        _ = layout.count
        for attempt in range(max_cards):
            if self.p.is_frozen() and not self.p.battle_over():
                self.p.resume_if_paused(); time.sleep(2)
            before = read_hud(self.p)
            if not before.ok:
                return {"ok": False, "why": f"HUD 视觉读数失败: {before.error}"}
            if before.cost is None:
                return {"ok": False, "why": "读不到部署费用，可能不在战斗界面"}
            cp = deploy.select_any_card(self.p)
            if cp is None:
                return {"ok": False, "why": "底部栏里没有能选中的卡（费用不足？）"}
            info = self.read_card_class(cp)
            cls = (info.get("class") or "")
            ranged = cls in ("狙击", "术师", "辅助", "医疗")
            spots = lv.high_spots if ranged else lv.ground_spots
            if not spots:
                log(f"  卡#{idx} 是{ranged and '远程' or '近战'}，但没有对应的可部署格")
                continue
            # 挑"离蓝门最近"的合法格（堵门思路；格点表里的 ends 即蓝门）
            ends = lv.ends or []
            best = None
            for c, r_ in spots:
                dist = min((abs(c - ec) + abs(r_ - er)) for ec, er in ends) if ends else 0
                if best is None or dist < best[0]:
                    best = (dist, c, r_)
            col, row = best[1], best[2]
            for side in (False, True):
                tp = lv.project(col, row, side=side)
                if not tp:
                    continue
                log(f"  卡@{cp}({info.get('name')}/{cls}) → 格({col},{row}) 投影{'侧视' if side else '正视'} {tp}")
                # 注意: **不要**再点一次卡 —— select_any_card 已经把它选中了，
                # 再点一下是"取消选中"，后面点格子自然没反应（2026-09-14 实测:
                # 这正是连续多轮部署失败的真正原因）
                self.act.tap(*tp); time.sleep(2.0)
                after = read_hud(self.p)
                self.shot(f"deploy_{attempt}_{'side' if side else 'norm'}")
                log(f"    HUD: 费用 {before.cost}→{after.cost} 剩余 {before.deploy_left}→{after.deploy_left} "
                    f"我方 {before.deployed}→{after.deployed}")
                if (after.deployed or 0) > (before.deployed or 0):
                    return {"ok": True, "card": info, "tile": [col, row],
                            "method": f"tap-tile-{'side' if side else 'normal'}",
                            "evidence": f"我方干员 {before.deployed}→{after.deployed}"}
                if before.cost is not None and after.cost is not None and after.cost < before.cost:
                    return {"ok": True, "card": info, "tile": [col, row],
                            "method": f"tap-tile-{'side' if side else 'normal'}",
                            "evidence": f"费用 {before.cost}→{after.cost}"}
        return {"ok": False, "why": f"前 {max_cards} 张卡都没能落下"}

    def try_deploy(self, layout, card_idx: int, methods) -> dict:
        """对一张卡依次尝试各手势，返回 {ok, method, evidence}。"""
        cp = layout.point(card_idx)
        # 部署前先确保战斗在跑：暂停时不能部署、费用也不涨（实测把 4 种手势
        # 全测成了"无效"，白跑一轮）
        if self.p.is_frozen():
            log("  战斗处于暂停/静止 → 恢复")
            self.p.resume_if_paused()
            time.sleep(2)
            if self.p.is_frozen():
                return {"ok": False, "why": "战斗仍处于暂停/静止，无法部署"}
        for m in methods:
            img = self.shot("pre")
            cost0 = read_cost(self.p, img)
            self.act.tap(*cp); time.sleep(1.4)           # 选中卡（画面会变）
            sel = self.p.shot()
            tile = vision_locate(sel, "画面上被高亮为可部署的格子（发亮的方块）。给出其中最靠近战场中央的一个的中心坐标；若没有高亮格子就说明没有")
            if not tile:
                return {"ok": False, "why": "选卡后没有可部署高亮格（卡未选中/费用不足/该干员无处可放）"}
            log(f"  [{m}] 卡#{card_idx}@{cp} 费用={cost0} → 目标格 {tile.center}（{tile.label[:30]}）")
            self._drag(m, cp, tile.center)
            time.sleep(2.0)
            img2 = self.shot(f"after_{m}")
            cost1 = read_cost(self.p, img2)
            ops = self.bv.operator.detect(img2)
            near = [o for o in ops
                    if abs(o.center[0] - tile.center[0]) < 80 and abs(o.center[1] - tile.center[1]) < 80]
            log(f"      费用 {cost0} → {cost1} | 目标格附近干员 {[(o.center, round(o.score, 2)) for o in near]}")
            if cost0 and cost1 and cost0.isdigit() and cost1.isdigit() and int(cost1) < int(cost0):
                return {"ok": True, "method": m, "evidence": f"费用 {cost0}→{cost1}"}
            if near:
                return {"ok": True, "method": m, "evidence": f"目标格附近检出干员 {near[0].center}"}
        return {"ok": False, "why": "所有手势都没生效（费用未降、目标格无干员）"}

    # ── 主流程 ──
    def run(self, methods, card_idx=0) -> dict:
        result = {"ts": time.strftime("%Y-%m-%d %H:%M:%S")}
        if not self.enter_battle():
            result["error"] = "进不去战斗（可能是登录失效或导航失败）"
            return result
        result["entered"] = True
        img = self.shot("battle_start")
        # 优先用本地固定布局；只有它明显不对（比如卡数变化）才回头问视觉
        layout = deploy.DEFAULT_LAYOUT
        log(f"用本地固定布局: y={layout.y} 锚点={layout.anchor_x} 间距={layout.pitch} 卡数={layout.count}")
        result["layout"] = None if not layout else {
            "y": layout.y, "anchor_x": layout.anchor_x,
            "pitch": layout.pitch, "count": layout.count}
        if not layout:
            result["error"] = "部署栏标定失败"
            return result
        result["deploy"] = (self.deploy_v2(layout) if not methods
                            else self.try_deploy(layout, card_idx, methods))
        result["end_state"] = self.st()
        return result


def main() -> None:
    ap = argparse.ArgumentParser(description="部署手势验证器")
    ap.add_argument("--emu", default="1")
    ap.add_argument("--instance", default="1")
    ap.add_argument("--client", default="Official")
    ap.add_argument("--methods", default="",
                    help="留空=用格点表闭环部署(v2)；给了才走旧的手势矩阵")
    ap.add_argument("--card", type=int, default=0)
    ap.add_argument("--rounds", type=int, default=1)
    args = ap.parse_args()

    t = BattleTester(args.emu, args.instance, args.client)
    methods = [m.strip() for m in args.methods.split(",") if m.strip()]
    out = []
    for r in range(args.rounds):
        log(f"=== 第 {r + 1}/{args.rounds} 轮 ===")
        res = t.run(methods, card_idx=args.card)
        out.append(res)
        log(f"结果: {json.dumps(res, ensure_ascii=False)}")
        if res.get("deploy", {}).get("ok"):
            break
    path = PROJ / "logs" / "agent" / "battle_test_result.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写 {path}")
    for r in out:
        print(json.dumps(r, ensure_ascii=False)[:300])


if __name__ == "__main__":
    main()
