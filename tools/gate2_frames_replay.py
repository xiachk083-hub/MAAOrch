#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""闸2（进战斗闸 `in_battle`）的**离线帧回归**：拿留证帧重放，不连 adb、不跑 MAA、不写 runtime/。

由来（2026-09-24 18:45 审计，`docs/audits/20260924_1845_jt83_gate2_falsepass.md`）：
JT8-3 36786 那局全程停在**编队页**，闸2 却报「t≈48s 起已在战斗界面」——
因为判据原来是 `re.search(r"\d", t)`：**两个 ROI 里出现任意一个数字就算进战斗**，
编队页的费用框 ROI OCR 出噪声串 `类2O`，那个 `2` 就把这局记成了「进过战斗」
（`battle:true / battle_s:47.5`），而 `battle` 字段的用途恰恰是「进过战斗 ⇒ 到不了 3★ 的证据」。

现在判据只认**干净的战斗 HUD token**（见 `stage_attempt.py:hud_roi_hit()`）：
  · ENEMY 计数 `N/M`（顶栏）
  · 费用框：**以 1~3 位数字开头 + 费用图标 `C`**（真机帧实测文本是 `20C` / `330C二` / `99CMAX`）

用法：
  py -3.12 tools/gate2_frames_replay.py            # 固定夹具（期望值＝视觉核证过的真机帧）
  py -3.12 tools/gate2_frames_replay.py --corpus   # 全语料：runtime/_ce6 下每个 *(_seq|*.png) 逐帧，
                                                   # 报告「旧判据真 / 新判据假」的翻转清单 + 真战斗帧是否仍在
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

PROJ = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ / "tools"))

import cv2  # noqa: E402
from stage_attempt import (BATTLE_COST_RECT, BATTLE_ENEMY_RECT,  # noqa: E402
                           _ocr_text, in_battle)

CE6 = PROJ / "runtime" / "_ce6"
JT83 = CE6 / "story_JT83_JT8-3_36786_drill_seq"

# (路径, 期望 in_battle, 备注)  期望值来自视觉核证：真战斗帧有 ENEMY 计数与费用框，编队页/结算页没有
CASES = [
    # 编队页那 45 秒（原判据在这 25 帧里有 1 帧假真：011）
    (JT83 / "011.png", False, "旧判据唯一假真的那帧（费用框 OCR = 类2O）"),
    (JT83 / "010.png", False, ""),
    (JT83 / "012.png", False, ""),
    (JT83 / "024.png", False, ""),
    # 真战斗（MAA 已进战斗界面的帧）
    (CE6 / "mt1raid8_seq" / "030.png", True, ""),
    (CE6 / "mt1raid8_seq" / "043.png", True, ""),
    (CE6 / "mt1raid8_watch.png", True, ""),
    (CE6 / "mt_now2.png", True, ""),
    (CE6 / "20260924_1315_AD-EX-2_real_59767_seq" / "040.png", True, ""),
    (CE6 / "20260924_1315_AD-EX-2_real_59767_seq" / "060.png", True, ""),
    (CE6 / "cvex8_real_41427_seq" / "040.png", True, ""),
    (CE6 / "ea1_real_48182_seq" / "020.png", True, ""),
    # 进战斗**之前**的帧（同一次跑局里）与结算/详情/开局前帧
    (CE6 / "20260924_1315_AD-EX-2_real_59767_seq" / "020.png", False, "进战斗前的加载/详情帧"),
    (CE6 / "mt1raid8_pre.png", False, "开局前自检帧（历史上是大厅）"),
    (CE6 / "mt1copy_result.png", False, "结算页"),
    (CE6 / "mt1raid5_result.png", False, "结算页"),
    (CE6 / "mt_stuck.png", False, "卡住页"),
]

_OLD_RE = re.compile(r"\d")


def _old_in_battle(img) -> bool:
    """改前的判据（`re.search(r"\\d", t)`）—— 只用来在报告里显示「这帧原来是真还是假」。"""
    for rect in (BATTLE_ENEMY_RECT, BATTLE_COST_RECT):
        t = _ocr_text(img, rect).replace(" ", "")
        if _OLD_RE.search(t):
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", action="store_true", help="遍历 runtime/_ce6 全语料，统计旧/新判据差异")
    args = ap.parse_args()

    bad = 0
    if not args.corpus:
        print("%-46s %-6s %-6s %-6s %s" % ("帧", "旧判", "新判", "期望", "备注"))
        print("-" * 96)
        for path, want, note in CASES:
            if not path.exists():
                print("%-46s  MISSING" % path.name)
                bad += 1
                continue
            img = cv2.imread(str(path))
            if img is None:
                print("%-46s  DECODE FAIL" % path.name)
                bad += 1
                continue
            old, new = _old_in_battle(img), in_battle(img)
            flag = "" if new == want else "   <<< ✗ 期望 %s" % want
            if flag:
                bad += 1
            print("%-46s %-6s %-6s %-6s %s%s" % (path.name, old, new, want, note, flag))
        print("-" * 96)
        print("失败用例：%d / %d" % (bad, len(CASES)))
        return 1 if bad else 0

    # ── 全语料 ──
    frames: list[pathlib.Path] = []
    for d in sorted(CE6.glob("*_seq")):
        frames += sorted(d.glob("*.png"))
    frames += sorted(p for p in CE6.glob("*.png"))
    flips, new_true, old_true, missing = [], 0, 0, 0
    for p in frames:
        img = cv2.imread(str(p))
        if img is None:
            missing += 1
            continue
        o, n = _old_in_battle(img), in_battle(img)
        old_true += int(o)
        new_true += int(n)
        if o and not n:
            e = _ocr_text(img, BATTLE_ENEMY_RECT).replace(" ", "")
            c = _ocr_text(img, BATTLE_COST_RECT).replace(" ", "")
            flips.append((p, e, c))
    print("语料帧数：%d（读不出 %d）" % (len(frames), missing))
    print("旧判据判真：%d 帧 ｜ 新判据判真：%d 帧 ｜ 旧真→新假：%d 帧" % (old_true, new_true, len(flips)))
    print("-" * 96)
    for p, e, c in flips:
        print("%-52s enemy=%-14r cost=%-14r" % (p.relative_to(CE6), e, c))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
