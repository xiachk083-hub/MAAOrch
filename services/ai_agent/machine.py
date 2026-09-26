"""环境能力 — 启动游戏 / 起停 MAA（agent 的"开机"动作）。

为什么独立子进程（services/stage_probe.py:112-115 的教训）:
  in-process MaaCore（ctypes 直连）会让**项目进程直接崩**（2026-08-12 事故，
  crash.log 空、看门狗拉起）。一律用独立 python 子进程跑 MaaCore，崩了只影响
  它自己。这是仓库既定的做法，别改回去。

用法:
    from services.ai_agent.machine import start_game
    start_game(emu_idx=1, client_type="Official")   # 起游戏并登录到主界面
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import time

from services.ai_agent.percept import PROJ, AdbScreen, log


def instance_dir(instance: int | str = 1) -> pathlib.Path:
    return PROJ / "services" / "maa" / "instances" / str(instance)


def maa_ready(instance: int | str = 1) -> bool:
    d = instance_dir(instance)
    return (d / "MAA.exe").exists() and (d / "MaaCore.dll").exists()


def _run_child(code: str, timeout: float) -> tuple[bool, str]:
    try:
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace",
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return False, f"子进程超时 {timeout}s"
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    return ("DONE" in out), (out[-400:] + ("\n" + err[-400:] if err else ""))


def start_game(emu_idx, instance: int | str = 1, client_type: str = "Official",
               timeout: float = 300.0) -> dict:
    """起游戏（MAA StartUp：启动客户端 + 过公告 + 登录，停在主界面）。

    这也是确认"这台模拟器上装的是哪个客户端"的可靠手段：client_type 错了，
    MAA 会在 StartUp 里报找不到界面/登录卡住 —— 把子进程输出带回来给人看。
    """
    if not maa_ready(instance):
        return {"ok": False, "detail": f"MAA 实例不完整: {instance_dir(instance)}"}
    screen = AdbScreen(emu_idx)
    if not screen.ensure_connected():
        return {"ok": False, "detail": "adb 连不上（见 logs/agent.log）"}
    inst = str(instance_dir(instance))
    py_dir = str(instance_dir(instance) / "Python")
    code = "\n".join([
        "import sys, time",
        "sys.path.insert(0, %r)" % py_dir,
        "from asst.asst import Asst",
        "Asst.load(path=%r)" % inst,
        "asst = Asst()",
        "ok = asst.connect(%r, %r)" % (screen.adb_path, screen.adb_address),
        "if not ok:",
        "    print('CONNECT_FAIL'); sys.exit(1)",
        "asst.append_task('StartUp', {'client_type': %r, 'start_game_enabled': True})" % client_type,
        "asst.start()",
        "dl = time.time() + %r" % timeout,
        "while time.time() < dl and asst.running():",
        "    time.sleep(2)",
        "asst.stop()",
        "print('DONE')",
    ])
    t0 = time.time()
    ok, tail = _run_child(code, timeout + 60)
    log(f"start_game(emu={emu_idx}, client={client_type}) → {ok}（{time.time() - t0:.0f}s）")
    return {"ok": ok, "detail": tail, "elapsed": round(time.time() - t0, 1),
            "adb": screen.adb_address}


def stop_maa(instance: int | str = 1) -> dict:
    """停掉该实例的 MAA.exe（按实例目录匹配命令行，不误杀别的 MAA）。"""
    d = str(instance_dir(instance)).replace("\\", "\\\\")
    ps = (f"Get-CimInstance Win32_Process -Filter \"Name='MAA.exe'\" | "
          f"Where-Object {{ $_.CommandLine -like '*{d}*' }} | "
          f"ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force; $_.ProcessId }}")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=30,
                           encoding="utf-8", errors="replace",
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        pids = [p.strip() for p in (r.stdout or "").splitlines() if p.strip().isdigit()]
        return {"ok": True, "killed": pids}
    except Exception as e:
        return {"ok": False, "detail": str(e)}
