#!/usr/bin/env python3
"""实机"看一帧" — 只读感知探针（不发任何输入，抢不到 MAA 的操作）。

这是"教 AI 玩"的第一课：先让它会看。一帧里输出:
  · 当前界面状态（含 OCR 信号）
  · OCR 全部文字与坐标
  · 战斗信号: 费用 / 击杀数 / 部署栏 / 干员存活（ONNX 检测）/ 技能就绪（ONNX 三分类）
  · 标注图落盘（人眼复核 AI 看到了什么）

安全: 只截图 + 本地推理，不 tap/swipe。
    MAA 正在跑同一台模拟器时也可以跑（2026-09-14 起 detect_emu_adb 已修，
    截图走独立锁，不会与 MAA 抢输入通道）。

用法:
    python tools/ai_agent_live_probe.py                     # 默认 #0 / 国服
    python tools/ai_agent_live_probe.py --emu 0 --client YoStarJP
    python tools/ai_agent_live_probe.py --frames 5 --interval 2   # 连续看 5 帧
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from services.ai_agent.battle_vision import BattleVision  # noqa: E402
from services.ai_agent.percept import Perception, ResourcePaths, task_roi, imread  # noqa: E402

OUT = PROJ / "logs" / "agent"


def battle_signals(p: Perception, img, bv: BattleVision) -> dict:
    """读战斗内状态信号（只读）。ROI 来自 MAA 任务定义，分服自动带覆盖。"""
    import cv2
    out: dict = {}

    def _num(task: str) -> dict | None:
        roi = task_roi(task, p.paths.client)
        if not roi:
            return None
        hit = p.ocr.read_region(img, roi, upscale=3, min_score=0.2)
        return {"task": task, "roi": list(roi), "text": hit.label if hit else None,
                "score": round(hit.score, 3) if hit else None,
                "center": list(hit.center) if hit else None}

    out["cost"] = _num("BattleCostData")
    out["kills"] = _num("BattleKills")
    for task in ("BattleOfficiallyBegin", "BattleCostFlag", "BattleOpersFlag", "BattleSpeedUp"):
        h = p.find(task, img=img)
        out[task] = {"score": round(h.score, 3), "center": list(h.center)} if h else None

    ops = bv.operator.detect(img)
    cards = []
    for h in ops:
        x, y, w, hh = h.box
        crop = img[max(0, y):y + hh, max(0, x):x + w]
        item = {"box": list(h.box), "center": list(h.center), "score": round(h.score, 3)}
        # 技能就绪窗: MAA BattleSkillReady 的 rectMove [-28,-140,64,64]（相对干员框）
        wx, wy, ww, wh = bv.skill.window_from_card(h.box)
        win = img[max(0, wy):wy + wh, max(0, wx):wx + ww]
        if win.size and win.shape[:2] == (wh, ww):
            item["skill"] = bv.skill.predict([win])[0]
        if crop.size:
            cv2.imwrite(str(OUT / f"live_op_{int(h.center[0])}_{int(h.center[1])}.png"), crop)
        cards.append(item)
    out["operators"] = cards
    return out


def draw(img, sig: dict, state: str):
    import cv2
    vis = img.copy()
    cv2.putText(vis, f"state: {state}", (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
    for key in ("BattleOfficiallyBegin", "BattleCostFlag", "BattleOpersFlag"):
        v = sig.get(key)
        if v:
            cv2.circle(vis, tuple(v["center"]), 12, (0, 255, 0), 2)
            cv2.putText(vis, f"{key} {v['score']}", (v["center"][0] - 60, v["center"][1] - 16),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
    for name, key in (("cost", "cost"), ("kills", "kills")):
        v = sig.get(key)
        if v and v.get("center"):
            cv2.rectangle(vis, (v["roi"][0], v["roi"][1]),
                          (v["roi"][0] + v["roi"][2], v["roi"][1] + v["roi"][3]), (255, 128, 0), 2)
            cv2.putText(vis, f"{name}={v['text']}", (v["roi"][0] - 40, v["roi"][1] - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 128, 0), 2)
    for o in sig.get("operators", []):
        x, y, w, h = o["box"]
        cv2.rectangle(vis, (x, y), (x + w, y + h), (0, 200, 255), 2)
        tag = f"{o['score']}"
        if o.get("skill"):
            tag += f" skill:{o['skill']['label']}"
        cv2.putText(vis, tag, (x, max(12, y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1)
    return vis


def main() -> None:
    import cv2
    ap = argparse.ArgumentParser(description="实机只读感知探针")
    ap.add_argument("--emu", default="0", help="模拟器索引（MuMuManager -v）")
    ap.add_argument("--instance", default="1", help="MAA 实例（决定资源目录）")
    ap.add_argument("--client", default="Official", help="Official / YoStarJP / YoStarEN / YoStarKR / txwy")
    ap.add_argument("--frames", type=int, default=1)
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--no-onnx", action="store_true", help="跳过战斗 ONNX（干员/技能）")
    ap.add_argument("--no-ocr", action="store_true", help="跳过 OCR 文字（更快）")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    p = Perception.for_emu(args.emu, instance=args.instance, client=args.client)
    bv = BattleVision(ResourcePaths(args.instance, args.client))
    print(f"模拟器 #{args.emu} | adb {p.screen.adb_address} | 客户端 {p.paths.client} "
          f"| rec 模型 {p.paths.paddleocr_rec_dir.parent.parent.name}")

    for i in range(args.frames):
        t0 = time.time()
        img = p.shot()
        st = p.state(img, use_ocr=not args.no_ocr)
        sig = {} if args.no_onnx else battle_signals(p, img, bv)
        vis = draw(img, sig, st["verdict"])
        stamp = time.strftime("%H%M%S")
        path = OUT / f"live_{stamp}.png"
        cv2.imwrite(str(path), vis)

        print(f"\n── 第 {i + 1} 帧（{time.time() - t0:.2f}s）──")
        print(f"状态: {st['verdict']}  ({st['detail'][:90]})")
        if not args.no_ocr:
            texts = sorted(p.texts(img), key=lambda h: -h.score)[:8]
            print("OCR: " + " | ".join(f"{h.label}@{h.center}" for h in texts))
        if sig:
            c, k = sig.get("cost"), sig.get("kills")
            print(f"费用: {c['text'] if c else '-'} | 击杀: {k['text'] if k else '-'} "
                  f"| 战斗标记: {(sig.get('BattleOfficiallyBegin') or {}).get('score', '-')}")
            print(f"干员 {len(sig.get('operators', []))} 个:")
            for o in sig.get("operators", []):
                print(f"   @{o['center']} 检测 {o['score']} 技能 "
                      f"{o['skill']['probs'] if o.get('skill') else '-'}")
        print(f"标注图: {path}")
        if i + 1 < args.frames:
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
