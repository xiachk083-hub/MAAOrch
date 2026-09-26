"""BI 关卡状态探针：进关卡详情页、看突袭开关能不能切 —— 判断「普通是否已通关」。

为什么（2026-09-21 用户要求）：「有些关卡普通已经打了，那就只需要打突袭；有些普通也没打，
那就普通和突袭都要打」⇒ 先探明状态再决定跑哪一局，别拿理智去试。

判据（实测）：
  * 详情页按钮读作「开始行动」= 当前普通难度；读作「开始突袭」= 当前突袭模式
  * 左下开关读作「突袭模式」= 点它会切到突袭（即当前普通）；读作「普通难度」= 当前已在突袭
  * **突袭没解锁时点开关切不过去**（普通没通关）⇒ 这就是「普通要不要打」的判据
  * 进突袭后会多出一行「附加条件：…」

用法:
    python tools/bi_state.py --stage BI-EX-2              # 只探状态
    python tools/bi_state.py --stage BI-EX-2 --want 突袭   # 探状态并切到突袭（能切就切）
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import time

import cv2
from rapidocr_onnxruntime import RapidOCR

PROJ = pathlib.Path(r"D:\1-2\MAAOrch")
OUT = PROJ / "runtime" / "_ce6"
ADB = r"D:\Xiach\MuMuPlayer\nx_main\adb.exe"
DEV = "127.0.0.1:16416"   # #1（官服）
TOGGLE = (677, 643)          # 左下「突袭模式 / 普通难度」
START = (1123, 642)          # 右下「开始行动 / 开始突袭」

_engine = None


def ocr(img):
    global _engine
    if _engine is None:
        _engine = RapidOCR()
    res, _ = _engine(img)
    out = []
    for box, text, *_ in (res or []):
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        out.append((int(sum(xs) / len(xs)), int(sum(ys) / len(ys)), text))
    return out


def tap(x, y, wait=3.0):
    subprocess.run([ADB, "-s", DEV, "shell", "input", "tap", str(x), str(y)], timeout=30)
    time.sleep(wait)


def shot(name="_bi_state"):
    p = OUT / f"{name}.png"
    with open(p, "wb") as f:
        subprocess.run([ADB, "-s", DEV, "exec-out", "screencap", "-p"], stdout=f, timeout=60)
    return cv2.imread(str(p))


def read_page(tag=""):
    img = shot("_bi_state" + tag)
    hits = ocr(img)
    txt = " ".join(t for _, _, t in hits)
    return img, hits, txt


def analyse(txt: str) -> dict:
    # 难度判据（2026-09-21 视觉核证后重写）：
    #   普通详情页：主按钮「开始行动」+ 左下红块「突袭模式」（=点它可切到突袭）
    #   突袭详情页：主按钮「开始突袭」+ 左下「普通难度」+「附加条件：…」
    # 旧版把「开关=普通难度」当成"当前是突袭"，且普通页走到 else 分支返回 "?" ⇒ 切难度永远走不到。
    d = {"开始行动": "开始行动" in txt, "开始突袭": "开始突袭" in txt,
         "开关=突袭模式": "突袭模式" in txt, "开关=普通难度": "普通难度" in txt,
         "有附加条件": "附加条件" in txt}
    if d["开始突袭"] or d["开关=普通难度"]:
        d["当前"] = "突袭"
    elif d["开始行动"] or d["开关=突袭模式"]:
        d["当前"] = "普通"
    else:
        d["当前"] = "?"
    m = re.search(r"附加条件[：:]\s*(.{0,40})", txt)
    d["附加条件文本"] = m.group(1).strip() if m else ""
    # 理智：页面右上角显示成「NNN/210」。**不要**用 "-N"（那是「开始行动 -15」的消耗数字，
    # 2026-09-21 就是它把 142 读成 8，害我白等十几轮以为理智不够）。
    m2 = re.search(r"(\d{1,3})\s*/\s*210", txt) or re.search(r"理智\D{0,6}(\d{1,3})", txt)
    d["理智"] = m2.group(1) if m2 else "?"
    return d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True, help="关卡代号，如 BI-EX-2")
    ap.add_argument("--want", choices=["突袭", "普通"], default=None, help="想切到的难度")
    ap.add_argument("--back-first", action="store_true", help="先按返回键回关卡地图（从详情页出来）")
    args = ap.parse_args()

    if args.back_first:
        subprocess.run([ADB, "-s", DEV, "shell", "input", "keyevent", "4"], timeout=30)
        time.sleep(2.5)

    # 0) 结算页/弹层先点掉（只在看到结算关键词时才点，避免在地图上误点节点）
    img, hits, txt = read_page("a")
    for _ in range(3):
        if any(k in txt for k in ("行动结束", "MISSION RESULTS", "点击画面", "以继续")):
            tap(640, 640, wait=3.0)
            img, hits, txt = read_page("a")
        else:
            break

    # 1) 找关卡节点（已经在详情页就别点了，否则会误点详情页上的文字）
    already_detail = any(k in txt for k in ("开始行动", "开始突袭", "推荐平均等级", "附加条件"))
    node = None
    if not already_detail:
        for x, y, t in hits:
            if args.stage in t and y > 140:
                node = (x, y, t)
                break
    if node is None and not already_detail:
        print("当前屏文字：%s" % txt[:200])
        print("✗ 这一屏没看到 %s 节点（可能要滚动地图）" % args.stage)
        return 4
    if node:
        print("找到节点「%s」于 (%d,%d) ⇒ 点进去" % (node[2], node[0], node[1]))
        # 节点图标在文字上方，点文字上方一点更稳
        tap(node[0], node[1] - 20, wait=4.0)
    else:
        print("（已经在 %s 的详情页）" % args.stage)

    img, hits, txt = read_page("b")
    st = analyse(txt)
    print("详情页：当前=%s  理智=%s  有附加条件=%s" % (st["当前"], st["理智"], st["有附加条件"]))
    if st["附加条件文本"]:
        print("  附加条件：%s" % st["附加条件文本"])
    if st["当前"] == "?":
        print("? 认不出按钮（文字：%s）" % txt[:220])
        return 2

    if args.want is None or args.want == st["当前"]:
        print("✓ 已在目标难度：%s" % st["当前"])
        return 0 if st["当前"] == args.want or args.want is None else 3

    # 2) 切换难度（2026-09-21 修：以前只有 TOGGLE 死坐标、且认不出普通页 ⇒ 切换从未真正生效）
    #    优先用 OCR 找到那个切换块的坐标，找不到才退回 TOGGLE 死坐标。
    tx, ty = TOGGLE
    for x, y, t in hits:
        if ("突袭模式" in t) or ("普通难度" in t):
            tx, ty = x, y
            break
    print("… 尝试从「%s」切到「%s」⇒ 点开关 (%d,%d)" % (st["当前"], args.want, tx, ty))
    tap(tx, ty, wait=3.5)
    img, hits, txt = read_page("c")
    st2 = analyse(txt)
    if st2["当前"] == args.want:
        print("✓ 切换成功 ⇒ 现在=%s（说明普通已通关、突袭已解锁）" % st2["当前"])
        if st2["附加条件文本"]:
            print("  附加条件：%s" % st2["附加条件文本"])
        return 0
    print("✗ 切不过去（当前仍是 %s）⇒ 普通难度大概还没通关" % st2["当前"])
    print("  屏幕文字：%s" % txt[:220])
    return 5


if __name__ == "__main__":
    raise SystemExit(main())
