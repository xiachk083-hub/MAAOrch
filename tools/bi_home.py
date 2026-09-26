#!/usr/bin/env python3
"""bi_home.py —— 可靠的"回到关卡详情页"流程（2026-09-22 新增）

背景：`bi_goto.py` 在**大厅**直接跑会失败（它找不到路径，10 步超时），
而跑局结束后游戏总停在编队/干员/结算页，我每次手点返回都会过量或点错
（大厅各按钮坐标一度全靠猜，(110,620) 其实是商店）。这个脚本把"回关卡"
固化成一条可复现路线：

  1) 连按 BACK（最多 9 次），**每次截图 + OCR**，识别到大厅（同时出现
     「终端」和「仓库」）就停；遇到"确认退出游戏"弹窗自动点取消 (439,475)；
  2) 点大厅「终端」**(967,160)**；
  3) 调用 `tools/bi_goto.py --stage <关> [--want 难度]`（它会用终端里的
     「前往上一次作战」卡片一步到位）；
  4) 由 bi_goto 自己校验是否落在关卡详情页。

用法：
  python tools/bi_home.py --stage MT-EX-6
  python tools/bi_home.py --stage MT-EX-6 --want 突袭

绝不重启游戏；不做任何会消耗理智的操作。
"""
import argparse
import pathlib
import subprocess
import sys
import time

PROJ = pathlib.Path(r"D:\1-2\MAAOrch")
ADB = r"D:\Xiach\MuMuPlayer\nx_main\adb.exe"
DEV = "127.0.0.1:16416"
TMP = PROJ / "runtime/_ce6/_home.png"


def sh(*args, timeout=60):
    return subprocess.run([ADB, "-s", DEV, *args], capture_output=True, text=True, timeout=timeout)


def shot():
    with open(TMP, "wb") as f:
        subprocess.run([ADB, "-s", DEV, "exec-out", "screencap", "-p"], stdout=f, timeout=60)


def tap(x, y):
    sh("shell", "input", "tap", str(x), str(y))


def back():
    sh("shell", "input", "keyevent", "KEYCODE_BACK")


def ocr_text():
    import cv2
    from rapidocr_onnxruntime import RapidOCR
    img = cv2.imread(str(TMP))
    if img is None:
        return ""
    r, _ = RapidOCR()(img)
    return "".join(t[1] for t in (r or []))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True, help="关卡名，如 MT-EX-6")
    ap.add_argument("--want", default=None, help="要切换到的难度，如 突袭")
    a = ap.parse_args()

    for i in range(9):
        shot()
        t = ocr_text()
        # 2026-09-22 收紧判定：上一版只要同时出现「终端」「仓库」就认作大厅，
        # 结果把关卡地图（上面有 OPERATION/MT- 等字样）误判成大厅，随后点终端无反应 ✗。
        # 现在要求：同时有「终端」「仓库」「基建」，且**不出现**关卡字样。
        if ("终端" in t and "仓库" in t and "基建" in t
                and "OPERATION" not in t and "MT-" not in t):
            print("  [%d] 已识别到大厅" % i)
            break
        if "确认退出" in t or "退出游戏" in t:
            print("  [%d] 退出确认框 -> 点取消 (439,475)" % i)
            tap(439, 475)
        else:
            print("  [%d] 非大厅 -> 按返回" % i)
            back()
        time.sleep(2)

    print("  -> 点终端 (967,160)")
    tap(967, 160)
    time.sleep(4)

    cmd = [sys.executable, str(PROJ / "tools/bi_goto.py"), "--stage", a.stage]
    if a.want:
        cmd += ["--want", a.want]
    subprocess.run(cmd, cwd=str(PROJ))


if __name__ == "__main__":
    main()
