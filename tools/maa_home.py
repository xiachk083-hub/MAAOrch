#!/usr/bin/env python3
"""maa_home.py —— 把游戏送回**大厅（主页）**（MAA StartUp，不启动游戏）。

为什么需要它:
    MAA 的 Copilot/Fight 都不保证自己导航（2026-09-23 实测：Copilot 直接在当前页开打，
    结果打的是残留在屏幕上的另一关），而**大厅是唯一确定的起点**：
    从这里出发的导航路径短、可复现，也方便我们截图读「源石/理智」。
    以前每次回大厅都靠手点返回 —— 大厅按返回会弹「是否确认退出游戏」，点错就退游戏。

为什么用 MAA 而不是自己按 BACK:
    红线③「同一模拟器同一时刻只允许一个 MAA」+ 用户口径「跑游戏只能由 MAA/AUTO-MAS 执行」
    ⇒ 回大厅这种会点屏幕的动作交给 MAA（StartUp 的 `start_game_enabled=False` 就只做导航、
    不会拉起客户端，也不会消耗任何游戏资源）。

用法:
    python tools/maa_home.py --emu 1 [--timeout 180] [--shot runtime/_home.png]
输出: 一行 JSON {"ok":bool,"sec":float,"shot":path}
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


def main() -> int:
    ap = argparse.ArgumentParser(description="MAA StartUp → 大厅")
    ap.add_argument("--emu", default="1")
    ap.add_argument("--instance", default="1")
    ap.add_argument("--client", default="Official")
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--shot", default="")
    ap.add_argument("--start-game", action="store_true",
                    help="顺便把客户端拉起来并登录进大厅（默认只导航、不启动客户端）")
    a = ap.parse_args()

    inst = (PROJ / "services" / "maa" / "instances" / str(a.instance)).resolve()
    adb = find_adb() or "adb"
    addr = detect_emu_adb(a.emu)
    if not addr:
        print(json.dumps({"ok": False, "err": "取不到模拟器 adb 端口"}, ensure_ascii=False))
        return 1

    child = f'''
import sys, time
sys.path.insert(0, {str(inst / "Python")!r})
from asst.asst import Asst, InstanceOptionType
Asst.load(path={str(inst)!r})
asst = Asst()
# 触控方式必须显式设 maatouch：配置里的 minitouch 在 MuMu 12 上连不上（2026-09-23 实测）
asst.set_instance_option(InstanceOptionType.touch_type, "maatouch")
ok = asst.connect({adb!r}, {addr!r})
if not ok:
    print("CONNECT_FAIL"); sys.exit(1)
asst.append_task("StartUp", {{"client_type": {a.client!r}, "start_game_enabled": {bool(a.start_game)}}})
asst.start()
dl = time.time() + {a.timeout}
while time.time() < dl and asst.running():
    time.sleep(2)
asst.stop()
print("HOME_DONE")
'''
    t0 = time.time()
    try:
        r = subprocess.run([sys.executable, "-c", child], capture_output=True, text=True,
                           timeout=a.timeout + 120, encoding="utf-8", errors="replace",
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        out = "子进程超时"
    ok = "HOME_DONE" in out
    res = {"ok": ok, "sec": round(time.time() - t0, 1)}
    if a.shot:
        p = pathlib.Path(a.shot)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "wb") as f:
            subprocess.run([adb, "-s", addr, "exec-out", "screencap", "-p"], stdout=f, timeout=60)
        res["shot"] = str(p)
    if not ok:
        res["tail"] = out.strip()[-300:]
    print(json.dumps(res, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
