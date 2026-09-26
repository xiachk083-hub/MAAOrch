#!/usr/bin/env python3
"""反复跑同一份作业，直到**结算页读出三星**为止（或跑满次数）—— 把"可复现"变成可执行的东西。

为什么（2026-09-21）：MAA 的拖拽部署/朝向手势**每局成败不定**（落地人数与输出表现局局不同），
同一份作业既有通关（170 秒）也有 20~40 秒崩的局。与其继续猜，不如：
**每局跑完立刻自动读星级**（tools/ce6_star_count.py 的逻辑），三星就停、否则重跑（失败不花理智）。

用法:
    python tools/ce6_until_star.py --job data/copilot/CE-6_v41.json --tries 6 --tag s
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
import time

import cv2

PROJ = pathlib.Path(r"D:\1-2\MAAOrch")
OUT = PROJ / "runtime" / "_ce6"
ADB = r"D:\Xiach\MuMuPlayer\nx_main\adb.exe"
DEV = "127.0.0.1:16416"   # #1（官服）

sys.path.insert(0, str(PROJ / "tools"))
from ce6_star_count import page_kind, star_blobs  # noqa: E402


def shot(name: str) -> pathlib.Path:
    p = OUT / f"{name}.png"
    with open(p, "wb") as f:
        subprocess.run([ADB, "-s", DEV, "exec-out", "screencap", "-p"], stdout=f, timeout=60)
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    ap.add_argument("--tries", type=int, default=6)
    ap.add_argument("--tag", default="s")
    ap.add_argument("--record", type=int, default=180)
    args = ap.parse_args()

    for k in range(1, args.tries + 1):
        tag = "%s%d" % (args.tag, k)
        print("\n=== 第 %d/%d 局：%s ===" % (k, args.tries, tag), flush=True)
        r = subprocess.run([sys.executable, str(PROJ / "tools" / "ce6_attempt.py"),
                            "--job", args.job, "--tag", tag, "--record", str(args.record)],
                           cwd=str(PROJ), capture_output=True, text=True, errors="replace")
        for l in (r.stdout or "").strip().splitlines()[-3:]:
            print("   " + l)
        time.sleep(2)
        p = shot(f"{tag}_result")
        img = cv2.imread(str(p))
        if img is None:
            print("   ✗ 拿不到结算页截图")
            continue
        kind = page_kind(img)
        stars = len(star_blobs(img)) if kind in ("clear", "maybe-clear") else 0
        print("   页面类型=%s  星级=%s" % (kind, stars if kind in ("clear", "maybe-clear") else 0))
        if kind in ("clear", "maybe-clear") and stars >= 3:
            print("\n★★★ 三星达成！作业=%s  证据：%s / %s_result.png" % (args.job, OUT / f"{tag}.mp4", tag))
            return 0
        if kind == "fail":
            print("   → 失败局，继续（不花理智）")
        else:
            print("   → 星数不够（%d 星），继续" % stars)
    print("\n跑满 %d 局仍未三星；请换更强的轴或先解决部署手势的稳定性。" % args.tries)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
