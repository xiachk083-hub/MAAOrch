# -*- coding: utf-8 -*-
"""稳健地回到某个关卡的详情页（BI-EX-*）。

背景（2026-09-21）：演习/失败局结束后游戏会停在结算页或提示页，我原来的
tools/bi_back_to_stage.py 会去点左上角返回，结果弹出「是否确认退出游戏？」确认框，
后续所有动作全废（取消按钮在 (439,475)）。这个助手只做三类安全动作：
  1) 看到「终端」卡片（大厅）→ 点它；
  2) 看到 BI-EX-* 节点（地图）→ 点目标节点；
  3) 以上都不是 → 点画面中央/底部推进（关掉结算页/提示），绝不点左上角返回。

用法: python tools/bi_goto.py --stage BI-EX-6 [--want 突袭]
"""
from __future__ import annotations

import argparse, pathlib, re, subprocess, sys, time

import cv2
from rapidocr_onnxruntime import RapidOCR

ADB = r"D:\Xiach\MuMuPlayer\nx_main\adb.exe"
DEV = "127.0.0.1:16416"   # #1（官服）
OCR = RapidOCR()
SHOT = pathlib.Path(r"D:\1-2\MAAOrch\runtime\_ce6\_goto.png")


def shot():
    with open(SHOT, "wb") as f:
        subprocess.run([ADB, "-s", DEV, "exec-out", "screencap", "-p"], stdout=f, timeout=60)
    return cv2.imread(str(SHOT))


def read(img):
    r, _ = OCR(img)
    out = []
    for box, txt, score in (r or []):
        xs = [p[0] for p in box]; ys = [p[1] for p in box]
        out.append((txt, int(sum(xs) / 4), int(sum(ys) / 4)))
    return out


def tap(x, y):
    subprocess.run([ADB, "-s", DEV, "shell", "input", "tap", str(x), str(y)], timeout=30)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True)
    ap.add_argument("--want", default="")
    ap.add_argument("--tries", type=int, default=10)
    a = ap.parse_args()
    for i in range(a.tries):
        img = shot()
        items = read(img)
        texts = [t for t, x, y in items]
        # 0) 退出确认框（误触左上角返回会弹它）→ 点左边的 ✗ 取消
        if any("确认退出" in t or "退出游戏" in t for t in texts):
            tap(439, 475)
            print("  [%d] 取消退出确认框" % i)
            time.sleep(2)
            continue
        # 1) 已在目标关卡详情页？详情页左上角会同时出现 OPERATION 与关卡名，
        #    并且底部一定有「演习」或「开始行动/开始突袭」按钮（地图上也能看到 OPERATION+关卡名，
        #    所以必须用按钮来排除"只是地图上看到了节点"这种误判）。
        has_btn = any(("演习" in t) or ("开始行动" in t) or ("开始突袭" in t) for t in texts)
        if any(a.stage in t for t in texts) and any("OPERATION" in t.upper() for t in texts) and has_btn:
            print("✓ 已在 %s 详情页（第 %d 步）" % (a.stage, i))
            if a.want:
                # 2026-09-21 修：以前这里丢弃 bi_state 的返回码 ⇒「难度没切过去」(5)/「认不出」(2)
                # 被整条吞掉，调用方以为切好了。现在如实透传，并且失败时不再返回 0。
                r = subprocess.run([sys.executable, str(pathlib.Path(__file__).parent / "bi_state.py"),
                                    "--stage", a.stage, "--want", a.want], timeout=180)
                if r.returncode != 0:
                    print("✗ bi_state 未能切到「%s」（返回码 %d）⇒ 本轮任务不成立" % (a.want, r.returncode))
                    return r.returncode
            return 0
        # 2) 终端页右侧有「前往上一次作战」快捷卡片 → 直接点它回到上次打的关卡
        last = [(x, y) for t, x, y in items if "上一次作战" in t or "前往上一次" in t]
        if last:
            tap(last[0][0] + 120, last[0][1] + 40)
            print("  [%d] 终端页 ⇒ 点「前往上一次作战」" % i)
            time.sleep(5)
            continue
        # 2.5) 大厅（大卡片「终端」在右上）→ 点它进终端页
        term = [(x, y) for t, x, y in items if "终端" in t and y < 400]
        if term:
            tap(term[0][0], term[0][1] + 20)
            print("  [%d] 大厅 ⇒ 点终端" % i)
            time.sleep(5)
            continue
        # 2.7) 终端页底部导航的「终端」标签 → 点它（此时已在终端页，改点上一次作战那类卡片）
        # 3) 地图 → 点目标节点
        node = [(x, y) for t, x, y in items if a.stage in t]
        if node:
            tap(node[0][0], node[0][1] + 22)
            print("  [%d] 地图 ⇒ 点 %s 节点" % (i, a.stage))
            time.sleep(4)
            continue
        # 4) 其它（结算页/提示）→ 点中央推进
        tap(640, 400)
        print("  [%d] 点中央推进" % i)
        time.sleep(2)
    print("✗ %d 步内没能回到 %s" % (a.tries, a.stage))
    return 1


if __name__ == "__main__":
    sys.exit(main())
