#!/usr/bin/env python3
"""从整局录屏里读出「第几秒发生了什么」—— 判据是 HUD 上数字的变化时刻。

为什么（2026-09-20 用户质问"没有录屏的话你怎么知道是为什么失败了"）:
    MAA 的事件流能给「谁在几点上场」，但**不给生命点/击杀数** ⇒ 漏怪发生在第几秒答不了。
    所以每局都录屏，然后用这个脚本把三块静态 HUD 的**变化时刻**打出来：
      生命点（3→2→1→0，= 漏怪时刻 ✓）、击杀数（0/41…，= 输出节奏 ✓）、费用（= DP 曲线 ✓）
    做法不是 OCR，而是**逐帧差分**：这三块区域平时是静止的，只有数字变化时才动
    ⇒ 变化时刻 = 事件时刻，不受 OCR 识别率影响 ✓。

用法:
    python tools/analyze_run_video.py runtime/_ce6/v3.mp4 [--events runtime/_ce6/run8.log]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

import cv2
import numpy as np

# HUD 区域（1280x720 实测坐标；x0,x1,y0,y1）
# ⚠️ 2026-09-20 深夜修正：原来的「生命点 (556,664,12,68)」其实落在**顶部中央的击杀计数**上
#    （和「击杀数 (470,700,8,46)」几乎完全重叠）⇒ 把击杀变化误读成漏怪 ✗。
#    生命点(3 个盾形图标)在**左上角**；中间那组是 击杀 x/41。
REGIONS = {
    "生命点": (8, 170, 4, 58),        # 左上角三个盾形图标（漏 1 个就少一个）
    "击杀数": (470, 700, 8, 46),      # 顶部中央 0/41 计数
    "费用":   (1130, 1230, 600, 660),  # 右下角部署费用
    "投放位": (1290, 1290, 1290, 1290) if False else (1130, 1270, 520, 570),  # 「剩余可放置角色：N」
}


def changes(path: pathlib.Path, region, thr: float = 6.0, min_gap: float = 1.5):
    """返回 [(秒, 变化幅度)] —— 该区域发生变化的时刻（间隔小于 min_gap 的合并）。"""
    x0, x1, y0, y1 = region
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    prev, out, last_t = None, [], -99.0
    i = 0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        crop = cv2.cvtColor(fr[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
        crop = cv2.resize(crop, (crop.shape[1] // 2, crop.shape[0] // 2))   # 抗噪
        if prev is not None:
            d = float(np.abs(crop.astype(np.int16) - prev.astype(np.int16)).mean())
            t = i / fps
            if d > thr and (t - last_t) >= min_gap:
                out.append((round(t, 1), round(d, 1)))
                last_t = t
        prev = crop
        i += 1
    cap.release()
    return out, round(i / fps, 1)


def events_from_log(p: pathlib.Path):
    """从 MAA 日志抽部署/编队事件（与录像时间轴对齐用）。

    注意（2026-09-20 踩过）：日志里的 JSON 是 `"what": "CopilotAction"`（**冒号后有空格**）
    ⇒ 按字段写正则抠会一个都匹配不到 ✗；正解是 `| <Kind> {json}` 之后**整段 json.loads** ✓。
    """
    if not p.exists():
        return []
    rows = []
    with open(p, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = re.match(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", line)
            if not m:
                continue
            mm = re.search(r"\| (\w+) (\{.*\})\s*$", line.strip())
            if not mm:
                continue
            try:
                d = json.loads(mm.group(2))
            except Exception:
                continue
            what = d.get("what") or ""
            det = d.get("details") or {}
            if what == "BattleFormationSelected":
                rows.append(("编队选定", m.group(1)[-8:], "%s → %s" % (det.get("group_name"), det.get("selected"))))
            elif what == "CopilotAction":
                rows.append(("动作", m.group(1)[-8:], "%s %s" % (det.get("action"), det.get("target") or "")))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="从整局录屏读事件时刻")
    ap.add_argument("video", type=pathlib.Path)
    ap.add_argument("--events", type=pathlib.Path, default=None)
    ap.add_argument("--json", type=pathlib.Path, default=None)
    args = ap.parse_args()

    if not args.video.exists():
        sys.exit("✗ 没有这个视频：%s" % args.video)
    out = {}
    for name, reg in REGIONS.items():
        ch, dur = changes(args.video, reg)
        out[name] = {"变化时刻": ch, "时长秒": dur}
        print("== %s ==（录像 %.1f 秒，共 %d 次变化）" % (name, dur, len(ch)))
        print("   " + ("  ".join("%.1fs(Δ%.0f)" % (t, d) for t, d in ch[:24]) or "（无变化）"))
    if args.events:
        ev = events_from_log(args.events)
        out["事件流"] = ev
        print("\n== MAA 事件流（编队/部署）==")
        for k, t, who in ev:
            print("   %s %-8s %s" % (t, k, who))
    if args.json:
        args.json.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print("\n→ %s" % args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
