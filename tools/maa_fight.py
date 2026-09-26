#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""maa_fight.py —— 通用 **MAA Fight 任务**跑手（2026-09-23 新写）

用途（两件事）:
  ① **当导航器用**：MAA 的 `Fight` 自带主线寻路（`EpisodeN` + 通用 `Stage` OCR + 左右滑），
     这是目前唯一"不用我自己点 UI 就能到任意老主线关"的路子。
     实测要点：MAA 会在**开打之前**先点「开始行动」⇒ 想只导航的话用 `--stop-at detail`
     （盯 asst.log，看到 `BattleStartPre`/`StartButton1` 立刻 `asst.stop()`，不花理智）。
  ② **当跑局器用**（简单关）：MAA 用自己的编队/代理打，适合"这号练度足够碾压"的便宜关。

⚠️ 与 Copilot 的分工（实测口径）:
  · `Copilot` **不会自动导航**（2026-09-23 实测三次：直接在残留页面上开打并失败）
    ⇒ 抄作业必须先自己导航。
  · `Fight` **会导航**，但用的是当前/自动编队，不适合需要精确轴的难关。

用法:
    python tools/maa_fight.py --emu 1 --stage 0-2-HARD --times 1
    python tools/maa_fight.py --emu 1 --stage 2-3 --stop-at detail     # 只导航到关卡页
    python tools/maa_fight.py --emu 1 --stage 15-20 --times 1 --timeout 900
输出: 一行 JSON {"ok":bool,"sec":float,"stopped":bool,"tail":[...]}
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from infrastructure.task_constants import detect_emu_adb, find_adb  # noqa: E402

# 只当导航器时，看到这些就收手（MAA 已经站在关卡页、正要开打）
DETAIL_MARKS = ("BattleStartPre", "StartButton1", "StartButton2")


def main() -> int:
    ap = argparse.ArgumentParser(description="MAA Fight 通用跑手")
    ap.add_argument("--emu", default="1")
    ap.add_argument("--instance", default="1")
    ap.add_argument("--client", default="Official")
    ap.add_argument("--stage", required=True, help='MAA 关卡名，如 2-3 / 0-2-HARD / CE-6')
    ap.add_argument("--times", type=int, default=1)
    ap.add_argument("--medicine", type=int, default=0, help="最多吃几瓶理智药（0=不吃）")
    ap.add_argument("--stone", type=int, default=0, help="最多吃几颗源石（**默认 0，绝不动石头**）")
    ap.add_argument("--series", type=int, default=1)
    ap.add_argument("--timeout", type=float, default=900.0)
    ap.add_argument("--stop-at", default="", choices=["", "detail"],
                    help="detail = 一到关卡页（还没开打）就停，不花理智")
    a = ap.parse_args()

    inst = (PROJ / "services" / "maa" / "instances" / str(a.instance)).resolve()
    log_path = inst / "debug" / "asst.log"
    adb = find_adb() or "adb"
    addr = detect_emu_adb(a.emu)
    if not addr:
        print(json.dumps({"ok": False, "err": "取不到模拟器 adb 端口"}, ensure_ascii=False))
        return 1

    params = {"stage": a.stage, "times": a.times, "medicine": a.medicine, "stone": a.stone,
              "series": a.series, "client_type": a.client, "report_to_penguin": False}
    child = f'''
import sys, time
sys.path.insert(0, {str(inst / "Python")!r})
from asst.asst import Asst, InstanceOptionType
Asst.load(path={str(inst)!r})
asst = Asst()
# **触控方式必须显式设成 maatouch**（2026-09-23 实测）:
#   本实例 config 里写的是 `minitouch`，而 MuMu 12 上 minitouch 起不来
#   ⇒ `asst.connect()` 直接返回 False（asst.log 里能看到 minitouch push 之后就没有下文了）。
#   `maa_job_fallback.py` 一直是显式设 maatouch 才连上的（用户口径也是"用官方推荐的 MaaTouch"）。
asst.set_instance_option(InstanceOptionType.touch_type, "maatouch")
if not asst.connect({adb!r}, {addr!r}):
    print("CONNECT_FAIL"); sys.exit(1)
asst.append_task("Fight", {params!r})
asst.start()
dl = time.time() + {a.timeout}
while time.time() < dl and asst.running():
    time.sleep(2)
asst.stop()
print("FIGHT_DONE")
'''
    t0 = time.time()
    proc = subprocess.Popen([sys.executable, "-c", child], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    stopped = False
    tail: list[str] = []
    before = log_path.stat().st_size if log_path.exists() else 0
    try:
        while proc.poll() is None:
            if a.stop_at == "detail" and log_path.exists():
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(before)
                    chunk = f.read()
                for ln in chunk.splitlines()[-40:]:
                    if any(m in ln for m in DETAIL_MARKS):
                        tail.append(ln.strip()[:200])
                        stopped = True
                        break
                if stopped:
                    proc.terminate()
                    break
            time.sleep(1.5)
        out = proc.communicate(timeout=60)[0] or ""
    except Exception as e:                                        # noqa: BLE001
        try:
            proc.kill()
        except Exception:                                         # noqa: BLE001
            pass
        out = f"异常：{e}"
    res = {"ok": "FIGHT_DONE" in out, "sec": round(time.time() - t0, 1),
           "stopped": stopped, "stage": a.stage}
    if not res["ok"] and not stopped:
        res["tail"] = (out or "").strip()[-300:]
    if tail:
        res["marks"] = tail[-3:]
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
