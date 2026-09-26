"""三台模拟器的**画面监控**：定时抓帧 + OCR 摘要，落盘给人看，也给 agent 查。

为什么需要（2026-09-17 用户批评："你现在好像对于每个账号具体干了什么事一点都不知道，
你需要有画面监控"）: 这一晚每次真正定位问题都是靠**看一眼画面**（SR 图是青色六边形、
MAA 点歪到了「干员培养」页、EX 图是黄点），而看日志只能猜，猜错过好几次
（"编队缺人"其实是"点不到开始行动"、"SR 没过"其实是黄点判据用错了区）。

产物（默认落在 sr54/watch/）:
    <uid>/latest.png        最新一帧（网页直接显示这个）
    <uid>/log.txt           逐行摘要："HH:MM:SS | 界面关键词"，可回溯"那一刻在干什么"
    <uid>/hist/<HHMMSS>.png 历史帧（滚动保留最近 KEEP 张）

用法:
    python screen_watch.py                 # 一直跑，每 5 秒一轮
    python screen_watch.py --once          # 只抓一轮（给 agent 当"看一眼"用）
    python screen_watch.py --interval 3
"""
from __future__ import annotations

import argparse
import io
import json
import pathlib
import subprocess
import sys
import time

PROJ = pathlib.Path(r"D:\1-2\MAAOrch")
HERE = pathlib.Path(__file__).resolve().parent
WATCH = HERE / "watch"
ACCOUNTS = PROJ / "data" / "accounts.json"
KEEP = 80                       # 每个账号保留多少张历史帧（15 秒一张 ≈ 20 分钟可回溯）
sys.path.insert(0, str(PROJ))
sys.path.insert(0, str(PROJ / "services" / "ai_agent"))

ADB = r"D:\Xiach\MuMuPlayer\nx_main\adb.exe"


def shoot(serial: str):
    """抓一帧 PIL 图；黑帧重试（实测 screencap 会间歇给黑帧）。失败返回 None。"""
    for _ in range(3):
        try:
            out = subprocess.run([ADB, "-s", serial, "exec-out", "screencap", "-p"],
                                 capture_output=True, timeout=25).stdout
        except Exception:
            return None
        if len(out) < 10000:
            time.sleep(0.4)
            continue
        try:
            from PIL import Image
            im = Image.open(io.BytesIO(out)).convert("RGB")
        except Exception:
            return None
        if im.convert("L").getextrema()[1] > 20:
            return im
        time.sleep(0.4)
    return None


def digest(im) -> str:
    """把一帧压成一行关键词——查"那一刻在干什么"用这个，不用翻图。"""
    try:
        from infrastructure.ocr import ocr_boxes
        ts = [b["text"] for b in (ocr_boxes(im) or [])]
    except Exception as e:
        return f"(OCR 失败 {type(e).__name__})"
    blob = " ".join(ts)
    keys = [k for k in ("行动结束", "任务失败", "MISSION FAILED", "理智返还", "演习", "跳过",
                        "是否确认退出游戏", "获得", "公告", "干员培养", "编队", "开始行动",
                        "代理指挥", "理智", "终端", "月行", "通学路", "殡仪堂", "ASCENT",
                        "正在", "网络", "更新", "登录", "开始唤醒",
                        # **战斗中的判据（2026-09-17 补）**：不加这几个，战斗画面会摘出空行，
                        # 看起来像"什么都没干"（实测踩到：V 都快打完了摘要还是空的）
                        "剩余可放置角色", "ENEMY", "弱点", "敌人", "2x", "2X", "倍速")
            if k in blob]
    nodes = [t for t in ts if t.strip().startswith(("SR-", "EX-"))]
    prog = next((t for t in ts if "/" in t and any(c.isdigit() for c in t)), "")
    out = "｜".join(keys) + ("  ｜节点:" + ",".join(nodes[:8]) if nodes else "")
    if prog:
        out += f"  ｜{prog}"
    # **兜底：一个关键词都没命中时，直接摘前几段原文**——保证摘要永远不是空的
    if not out.strip("｜ "):
        out = " | ".join(t for t in ts[:6])[:180]
    return out[:200]


def load_accounts() -> list[dict]:
    accs = json.loads(ACCOUNTS.read_text(encoding="utf-8"))["accounts"]
    for a in accs:                      # 实例号 → adb 序列号（本机实测 5554/5556/5558）
        a["serial"] = f"emulator-{5554 + 2 * int(a['emu_index'])}"
    return accs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="只抓一轮")
    ap.add_argument("--interval", type=float, default=5.0)
    # **OCR 不必每帧都做**（2026-09-17 用户问"它占用为什么这么高"）: 抓帧便宜（adb screencap，
    # 零点几秒），贵的是 OCR —— 全屏要跑两个神经网络（文字检测 + 逐框识别），且
    # onnxruntime 是 **CPU 版**（可用执行器只有 CPU/Azure，没有 CUDA），默认吃满物理核。
    # 实测：15 秒 × 3 台这样跑，36 分钟累计烧掉 6578 CPU 秒 ≈ **3 个核一直满**。
    # 降成"每 N 轮 OCR 一次"后，帧的密度不变（查画面证据靠帧），CPU 降到约 1/4。
    ap.add_argument("--ocr-every", type=int, default=4,
                    help="每 N 轮才 OCR 一次（抓帧/存帧仍每轮都做）")
    a = ap.parse_args()
    accs = load_accounts()
    WATCH.mkdir(parents=True, exist_ok=True)
    for acc in accs:
        (WATCH / acc["uid"] / "hist").mkdir(parents=True, exist_ok=True)
    every = max(1, a.ocr_every)
    print(f"画面监控 → {WATCH} ｜ 账号 {[x['uid'] for x in accs]} ｜ 间隔 {a.interval}s"
          f" ｜ 抓帧每轮、OCR 每 {every} 轮", flush=True)

    rnd = 0
    while True:
        t0 = time.time()
        rnd += 1
        do_ocr = a.once or (rnd % every == 1)
        for acc in accs:
            im = shoot(acc["serial"])
            d = WATCH / acc["uid"]
            if im is None:
                with (d / "log.txt").open("a", encoding="utf-8") as fh:
                    fh.write(f"{time.strftime('%H:%M:%S')} | (抓不到帧)\n")
                continue
            stem = time.strftime("%H%M%S")
            im.save(d / "latest.png")                 # 网页直接显示这一张（保持 png 不变）
            im.save(d / "hist" / f"{stem}.jpg", quality=88)   # 历史帧存 jpg：省 6/7 磁盘
            if not do_ocr:
                continue
            line = f"{time.strftime('%H:%M:%S')} | {digest(im)}"
            with (d / "log.txt").open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            print(f"  {acc['name']:5s} {line}", flush=True)
            hist = d / "hist"
            old = sorted(list(hist.glob("*.png")) + list(hist.glob("*.jpg")))
            for p in old[:-KEEP]:
                p.unlink(missing_ok=True)
        if a.once:
            return
        time.sleep(max(0.5, a.interval - (time.time() - t0)))


if __name__ == "__main__":
    main()
