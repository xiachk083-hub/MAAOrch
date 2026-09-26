# -*- coding: utf-8 -*-
"""临时：目标机 adb 截图取回本地（base64 走 SSH，避免二进制被破坏）。

用法: python tools/_tw_shot.py 32 <本地输出.png>
"""
import base64
import importlib.util
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

spec = importlib.util.spec_from_file_location(
    "tg", Path(__file__).resolve().parent / "auto_mas_toggle_user.py")
tg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tg)

ADB = "E:/MuMu Player 12/nx_device/12.0/shell/adb.exe"


def ps(script: str, timeout: int = 180) -> str:
    b64 = base64.b64encode(script.encode("utf-16-le")).decode()
    p = subprocess.run(
        ["ssh", "-i", tg.SSH_KEY, "-o", "StrictHostKeyChecking=no",
         "-o", "ConnectTimeout=10", tg.SSH_HOST,
         f"chcp 65001 >nul & powershell -NoProfile -EncodedCommand {b64}"],
        capture_output=True, timeout=timeout,
    )
    return p.stdout.decode("utf-8", "replace")


def shot(idx: str, addr: str, out: Path, launch_pkg: str = "") -> int:
    if launch_pkg:
        print("启动游戏:", ps(f'& "{ADB}" -s {addr} shell monkey -p {launch_pkg} '
                              f'-c android.intent.category.LAUNCHER 1'))
        for s in range(90):
            time.sleep(1)
            fg = ps(f'& "{ADB}" -s {addr} shell dumpsys window | Select-String -Pattern "mCurrentFocus"')
            if launch_pkg in fg:
                print(f"  已在前台（{s+1}s）")
                break
        time.sleep(25)  # 再等加载/登录动画
    print(ps(f'& "{ADB}" -s {addr} shell screencap -p /sdcard/_ver.png; '
             f'& "{ADB}" -s {addr} pull /sdcard/_ver.png E:/temp_ver.png'))
    b64 = ps("[Convert]::ToBase64String([IO.File]::ReadAllBytes('E:/temp_ver.png'))")
    b64 = "".join(c for c in b64 if c.isalnum() or c in "+/=")
    out.write_bytes(base64.b64decode(b64))
    print("本地已存:", out, out.stat().st_size, "bytes")
    return 0


if __name__ == "__main__":
    idx = sys.argv[1]
    out = Path(sys.argv[2])
    pkg = sys.argv[3] if len(sys.argv) > 3 else ""
    addr = "127.0.0.1:" + str(5619 if idx == "32" else 5619)
    sys.exit(shot(idx, addr, out, pkg))
