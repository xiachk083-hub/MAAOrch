#!/usr/bin/env python3
"""离线回归 — 用历史截图验收感知层（不连模拟器、不发任何输入）。

验收四件事（P0 的退出条件）:
  1. 状态判别：本地状态分类器在真实截图上的分布是否合理（有没有大量"未知界面"）
  2. 元素定位：模板/OCR 能不能给出**坐标**，且不误命中
     —— 仓库现有实现把 minMaxLoc 的坐标丢掉了（"AI 点不中东西"的根因）；
     而小模板全图扫必然误命中（实测 EndOfAction 在 20 张非结算页上全"命中"），
     所以定位必须按 MAA 任务 roi 限定搜索窗口。
  3. 战斗视觉：skill_ready_cls 用 MaaCore 自己落盘的 66 张标签样本标定预处理
     （准确率必须接近 1.0，对不上说明通道顺序/归一化错了）；operators_det
     用真实战斗截图验证 letterbox+rgb 预处理下的检出稳定性。
  4. 基线：结果写 data/agent_offline_baseline.json，后续改动对比它。

用法:
    python tools/ai_agent_offline_check.py                 # 全量（无 OCR，快）
    python tools/ai_agent_offline_check.py --ocr           # 加 OCR 定位抽查（慢）
    python tools/ai_agent_offline_check.py --limit 60 --no-onnx
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from collections import Counter

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from services.ai_agent.percept import (  # noqa: E402
    ResourcePaths, Perception, TemplateMatcher, WINDOW_H, WINDOW_W, imread, log,
)

# 历史截图来源（2026-09-14 盘点：约 330 张 1280x720 真实截图）
SHOT_DIRS = [
    "logs/rl_long",              # 120 张肉鸽局内（含真实战斗）
    "logs/probe_shots",
    "debug/shots",
    "debug/scratch_20260814",
    "_sanity_check",
    "_stamina_shots",
    "_stamina_shots2",
    "_anni_check",
    "services/maa/instances/1/debug/interface",
    "services/maa/instances/1/debug/roguelike",
]

# 定位用例。expect=None 表示"不限定状态，只要给出坐标即可"（用于肉鸽类界面）。
TEMPLATE_TABLE = [
    {"disp": "开始行动", "tpl": "StartButton1", "expect": "关卡详情页", "thr": 0.8},
    {"disp": "全权委托可用", "tpl": "UsePrts", "expect": "关卡详情页", "thr": 0.7},
    {"disp": "代理锁定", "tpl": "UnableToAgent2", "expect": "关卡详情页", "thr": 0.6},
    {"disp": "战斗中标记", "tpl": "BattleOfficiallyBegin", "expect": "战斗中", "thr": 0.7},
    {"disp": "结算", "tpl": "EndOfAction", "expect": "结算页", "thr": 0.5},
    {"disp": "确认弹窗", "tpl": "PopupConfirm", "expect": "确认弹窗", "thr": 0.6,
     "roi": (630, 400, 650, 160)},          # 任务 OfflineConfirmAfterBattle 的 roi
    {"disp": "掉线弹窗", "tpl": "OfflineConfirm", "expect": "掉线弹窗", "thr": 0.6},
    {"disp": "肉鸽开始行动", "tpl": "Roguelike@StartAction", "expect": None, "thr": 0.7},
    {"disp": "肉鸽快速编队", "tpl": "Roguelike@QuickFormation", "expect": None, "thr": 0.7},
    {"disp": "肉鸽战斗撤退", "tpl": "RoguelikeBattleAbandon", "expect": None, "thr": 0.75},
]


def collect_images(limit: int | None = None) -> list[pathlib.Path]:
    """收集 1280x720 的真实截图（裁剪图/损坏文件跳过）。"""
    out: list[pathlib.Path] = []
    for rel in SHOT_DIRS:
        d = PROJ / rel
        if not d.exists():
            continue
        for f in sorted(d.rglob("*")):
            if f.suffix.lower() not in (".png", ".jpg", ".jpeg"):
                continue
            img = imread(f)          # 中文路径安全（cv2.imread 会静默失败）
            if img is None:
                continue
            if img.shape[1] == WINDOW_W and img.shape[0] == WINDOW_H:
                out.append(f)
            if limit and len(out) >= limit:
                return out
    return out


def check_states(images: list[pathlib.Path], use_ocr: bool) -> dict:
    """① 状态判别分布 + ② 模板定位（带坐标、按 roi 限定）。"""
    tmpl = TemplateMatcher(ResourcePaths(1))
    verdicts: Counter[str] = Counter()
    unknown: list[str] = []
    stat: dict[str, dict] = {e["tpl"]: {"case": e["disp"], "expect": e["expect"],
                                        "in_state": 0, "anywhere": 0, "outside": 0,
                                        "scores": [], "sample": None}
                             for e in TEMPLATE_TABLE}
    t0 = time.time()
    for i, path in enumerate(images, 1):
        p = Perception.offline(path, instance=1)
        img = p.shot()
        verdict = p.state(img, use_ocr=use_ocr)["verdict"]
        verdicts[verdict] += 1
        if verdict == "未知界面":
            unknown.append(str(path.relative_to(PROJ)))
        for e in TEMPLATE_TABLE:
            s = stat[e["tpl"]]
            hit = tmpl.find(img, e["tpl"], threshold=e["thr"], roi=e.get("roi"))
            if not hit:
                continue
            s["anywhere"] += 1
            if e["expect"] is None:
                s["in_state"] += 1
            elif e["expect"] in verdict:
                s["in_state"] += 1
            else:
                s["outside"] += 1
            s["scores"].append(round(hit.score, 3))
            if s["sample"] is None:
                s["sample"] = {"image": str(path.relative_to(PROJ)),
                               "center": list(hit.center), "box": list(hit.box),
                               "score": round(hit.score, 3)}
        if i % 25 == 0:
            log(f"  状态+定位 {i}/{len(images)}（{time.time() - t0:.0f}s）")
    summary = {}
    for tpl, s in stat.items():
        scores = sorted(s["scores"])
        summary[tpl] = {
            "case": s["case"], "expect": s["expect"],
            "in_state_hits": s["in_state"], "any_hits": s["anywhere"],
            "outside_expected_state": s["outside"],
            "median_score": round(scores[len(scores) // 2], 3) if scores else None,
            "sample": s["sample"],
        }
    return {"verdicts": dict(verdicts.most_common()), "unknown_images": unknown[:20],
            "templates": summary, "elapsed": round(time.time() - t0, 1)}


def check_ocr(images: list[pathlib.Path], limit: int = 12) -> dict:
    """按钮定位抽查：真详情页能否定位到「开始行动」并给出坐标。

    两个数据都记（诚实对比）:
      raw_ocr     —— 全图 OCR 找「开始行动」。检测阶段在红底白字按钮上会给出
                     错误的框（实测只框到下半条，识别成"后丁"），这条路基本不可用。
      task_button —— 用 MAA 任务定义（StartButton1 的 roi + text 列表）直读 roi。
                     这条是可用路径，AI 点"开始行动"就靠它。

    挑样本用硬信号（全权委托/锁定图标），不用状态判别 —— 后者的金色兜底会把
    主界面误当详情页（离线回归已实测），拿它挑样本等于自欺。
    """
    tmpl = TemplateMatcher(ResourcePaths(1))
    detail_shots = []
    for path in images:
        img = imread(path)
        if img is None:
            continue
        hard = (tmpl.find(img, "UsePrtsSuccessCheck", threshold=0.9)
                or tmpl.find(img, "UnableToAgent2", threshold=0.6)
                or tmpl.find(img, "UsePrts", threshold=0.7))
        if hard is not None:
            detail_shots.append(path)
        if len(detail_shots) >= limit:
            break
    out = {"shots": len(detail_shots), "raw_ocr_hits": 0, "task_button_hits": 0,
           "samples": []}
    for path in detail_shots:
        p = Perception.offline(path, instance=1)
        img = p.shot()
        raw = p.ocr.find_text(img, "开始行动")
        btn = p.find_task_text("StartButton1", img=img)
        out["raw_ocr_hits"] += 1 if raw else 0
        out["task_button_hits"] += 1 if btn else 0
        if btn:
            out["samples"].append({"image": str(path.relative_to(PROJ)),
                                   "center": list(btn.center),
                                   "score": round(btn.score, 3)})
        log(f"  按钮 {path.name}: 全图OCR={'✓' if raw else '✗'} "
            f"任务roi直读={'✓' if btn else '✗'}"
            f"{(' ' + repr(btn.label) + '@' + str(btn.center)) if btn else ''}")
    return out


def check_battle_vision(images: list[pathlib.Path], probe_shots: int = 6) -> dict:
    """③ 战斗视觉：技能就绪标定 + 干员检测在真实战斗上的稳定性。"""
    import cv2
    from services.ai_agent.battle_vision import BattleVision
    bv = BattleVision(ResourcePaths(1))
    out: dict = {"skill_ready": bv.skill.calibrate(instance=1, verbose=True)}

    # 用战斗 HUD 标记（比状态判别更硬的信号）挑真实的战斗截图
    tmpl = TemplateMatcher(ResourcePaths(1))
    battle: list[tuple[str, float]] = []
    judged: list[dict] = []
    for path in images:
        img = imread(path)
        if img is None:
            continue
        hud = tmpl.find(img, "BattleOfficiallyBegin", threshold=0.7)
        if hud is None:
            continue
        ops = bv.operator.detect(img)
        judged.append({"image": str(path.relative_to(PROJ)),
                       "hud_score": round(hud.score, 3),
                       "operators": len(ops),
                       "centers": [list(o.center) for o in ops],
                       "scores": [round(o.score, 3) for o in ops]})
        if len(judged) >= probe_shots:
            break
    out["operator_detect"] = judged
    # 朝向模型是四分类且类别顺序未标定，只证明能跑通
    crop = imread(images[0])[:96, :96] if images else None
    if crop is None:
        import numpy as np
        crop = np.zeros((96, 96, 3), dtype="uint8")
    out["direction_smoke"] = bv.direction.predict(crop)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="AI 感知层离线回归")
    ap.add_argument("--limit", type=int, default=None, help="最多用多少张截图")
    ap.add_argument("--ocr", action="store_true", help="加 OCR 定位抽查（慢）")
    ap.add_argument("--no-onnx", action="store_true", help="跳过战斗视觉校准")
    args = ap.parse_args()

    log("=" * 60)
    images = collect_images(limit=args.limit)
    log(f"收集到 {len(images)} 张 1280x720 可用截图")
    if not images:
        log("没有可用截图，退出")
        return
    report: dict = {"images": len(images), "ts": time.strftime("%Y-%m-%d %H:%M:%S")}

    log("① 状态判别 + ② 模板定位（带坐标、roi 限定）…")
    report["state_and_template"] = check_states(images, use_ocr=False)
    if args.ocr:
        log("②+ OCR 定位抽查…")
        report["ocr"] = check_ocr(images)
    if not args.no_onnx:
        log("③ 战斗视觉标定与验证…")
        report["battle_vision"] = check_battle_vision(images)

    out = PROJ / "data" / "agent_offline_baseline.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 60)
    print(f"状态分布（{len(images)} 张）:")
    for k, v in report["state_and_template"]["verdicts"].items():
        print(f"  {v:4d}  {k}")
    print("\n模板定位（带坐标）:")
    for tpl, s in report["state_and_template"]["templates"].items():
        print(f"  {s['case']:8s} {tpl:26s} 期望态 {s['in_state_hits']:3d} / 任意 {s['any_hits']:3d}"
              f"  期望态外 {s['outside_expected_state']:3d}  中位分 {s['median_score']}"
              f"  例 {s['sample']['center'] if s['sample'] else '-'}")
    if "ocr" in report:
        o = report["ocr"]
        print(f"\n按钮定位（{o['shots']} 张真详情页）: 全图 OCR {o['raw_ocr_hits']} 命中 / "
              f"任务 roi 直读 {o['task_button_hits']} 命中"
              f"（后者才是可用路径：{o['samples'][:1]}）")
    if "battle_vision" in report:
        bv = report["battle_vision"]
        sr = bv.get("skill_ready", {})
        print(f"\n战斗视觉: 技能就绪预处理 {sr.get('skill_ready_prep')} "
              f"准确率 {sr.get('skill_ready_acc')}（{sr.get('skill_ready_samples')} 标签样本）")
        print(f"  预处理全表: {sr.get('skill_ready_all')}")
        print("  干员检测（真实战斗截图）:")
        for j in bv.get("operator_detect", []):
            print(f"    HUD {j['hud_score']}  {j['operators']} 个  {j['centers']}")
    print(f"\n基线已写: {out}")


if __name__ == "__main__":
    main()
