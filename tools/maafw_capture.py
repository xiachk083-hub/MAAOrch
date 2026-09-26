"""MaaFramework Win32 高速取帧（FramePool）→ 直接给 1920×1080 的设备帧。

2026-09-19 实测：这条路 **18 ms/帧 ≈ 55 fps**，而且**不走 adb、不发任何输入**
（`PostMessage` 且我们从不调输入接口）⇒ 与 MAA 自己的通道完全隔离 ✓。

为什么不用 MaaCore 的 `AsstAttachWindow`：3 个窗口 × 6 组截图/输入参数**恒 False** ✗；
MaaFramework 的原生 `Win32Controller` 一次成功 ✓（差别就在"用哪套 API"）。
为什么必须用**顶层窗口**：FramePool 走 Windows.Graphics.Capture，`CreateForWindow`
**不接受子窗口**（实测 `nemuwin`/Qt 窗口都返回 E_INVALIDARG ✗）⇒ 只能抓顶层窗、再裁 chrome ✓。

几何**全部实量**（没有任何魔法数字）：
    窗口高 995 = 客户区 946 + chrome 49      ← GetWindowRect / GetClientRect
    捕获图 1218×720 = 整窗 995 × 0.7236       ← 1218/720 与 1683/995 完全吻合
    游戏区 = 顶部裁掉 chrome×scale → 拉成 1920×1080（16:9 ✓）
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

import cv2
import numpy as np

DEV_W, DEV_H = 1920, 1080          # 我们的量测/坐标全建立在这个尺寸上


def _u32():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)     # 和 MAA 的 WPF 一样按显示器 DPI 感知
    except Exception:                                      # noqa: BLE001
        ctypes.windll.user32.SetProcessDPIAware()
    return ctypes.WinDLL("user32", use_last_error=True)


def find_game_window(exe_names=("mumunxdevice.exe",)) -> int | None:
    """找 MuMu 的**设备窗**（顶层、可见、客户区接近 16:9 的那个）。

    ⚠️ 不是主窗 `MuMu模拟器`：它最小化时 FramePool 必失败（`Window is no longer valid or visible`），
    这正是 2026-09-19 之前一堆失败的来源之一。
    """
    u32 = _u32()
    import subprocess
    out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    pids = {int(r[1]) for ln in out.splitlines()
            if len(r := ln.strip('"').split('","')) > 2 and r[0].lower() in exe_names}
    best = None
    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lp):
        nonlocal best
        pid = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in pids or not u32.IsWindowVisible(hwnd):
            return True
        r = wintypes.RECT(); u32.GetClientRect(hwnd, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        if w < 300 or h < 200:
            return True
        score = abs((w / h) - 16 / 9)                       # 越接近 16:9 越好
        if best is None or score < best[0]:
            best = (score, int(hwnd), w, h)
        return True
    u32.EnumWindows(cb, 0)
    return None if best is None else best[1]


def candidates(exe_names=("mumunxdevice.exe",)) -> list[tuple[int, int, int]]:
    """所有像样的 MuMu 顶层窗（hwnd, 客户区宽, 客户区高），大在前。"""
    u32 = _u32()
    import subprocess
    out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    pids = {int(r[1]) for ln in out.splitlines()
            if len(r := ln.strip('"').split('","')) > 2 and r[0].lower() in exe_names}
    got = []
    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lp):
        pid = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and u32.IsWindowVisible(hwnd):
            r = wintypes.RECT(); u32.GetClientRect(hwnd, ctypes.byref(r))
            w, h = r.right - r.left, r.bottom - r.top
            if w > 300 and h > 200:
                got.append((int(hwnd), w, h))
        return True
    u32.EnumWindows(cb, 0)
    got.sort(key=lambda z: -z[1] * z[2])
    return got


class MaafwCapture:
    """FramePool 控制器；`grab()` 返回 **1920×1080 BGR**（已裁 chrome、已缩放）。

    ⚠️ **选窗口不能看尺寸/宽高比**：MuMu 有多个顶层窗，尺寸好看的那个（`MuMuNxDevice`）
    实测抓出来是**块状垃圾** ✗（2026-09-19 踩）。唯一可靠的判据是**真抓一张看是否合法**：
    原始帧必须"够大 + 宽高比与客户区一致" ⇒ 用 `open()`，别自己挑 hwnd。
    """

    @classmethod
    def open(cls, exe_names=("mumunxdevice.exe",)) -> "MaafwCapture":
        last = ""
        for hwnd, w, h in candidates(exe_names):
            try:
                g = cls(hwnd)
                img = g.raw()
                if img is None:
                    last = f"hwnd={hwnd} 抓不到"; g.close(); continue
                ih, iw = img.shape[:2]
                if min(ih, iw) < 200 or abs(iw / ih - w / h) > 0.05:
                    last = f"hwnd={hwnd} 帧不合法 {iw}x{ih}（客户区 {w}x{h}）"
                    g.close(); continue
                return g
            except Exception as e:                       # noqa: BLE001
                last = f"hwnd={hwnd} {str(e)[:50]}"
        raise RuntimeError("没有可用窗口：" + last)

    def __init__(self, hwnd: int | None = None) -> None:
        import maa.controller as C
        import maa.define as D
        self.hwnd = hwnd or find_game_window()
        if not self.hwnd:
            raise RuntimeError("找不到 MuMu 设备窗（顶层、可见、接近 16:9）")
        self._geo()
        self.c = C.Win32Controller(
            hWnd=self.hwnd,
            screencap_method=D.MaaWin32ScreencapMethodEnum.FramePool,
            mouse_method=D.MaaWin32InputMethodEnum.PostMessage,
            keyboard_method=D.MaaWin32InputMethodEnum.PostMessage)
        self.c.post_connection().wait()
        self._chrome = None                                  # 捕获图里 chrome 的像素高（首帧量）

    def _geo(self) -> None:
        u32 = _u32()
        wr = wintypes.RECT(); u32.GetWindowRect(self.hwnd, ctypes.byref(wr))
        cr = wintypes.RECT(); u32.GetClientRect(self.hwnd, ctypes.byref(cr))
        self.win_h = wr.bottom - wr.top
        self.client_h = cr.bottom - cr.top
        self.client_w = cr.right - cr.left

    def raw(self):
        img = self.c.post_screencap().wait().get()
        return None if img is None else np.asarray(img)

    # MuMu 窗口顶部那条标签栏在 **1080p 映射下** 占的像素数。
    # ⚠️ 它是**窗口内容的一部分**（不是 Win32 的非客户区 ⇒ GetWindowRect−GetClientRect 量不到 ✗），
    # 只能在帧上看出来。换 MuMu 版本/皮肤要重测；裁错会让所有坐标整体上移/下移。
    CHROME_1080 = 50

    def grab(self, img=None):
        """抓一帧并映射成 1920×1080 BGR（裁掉顶部标签栏）。"""
        if img is None:
            img = self.raw()
            if img is None:
                return None
        h = img.shape[0]
        cut = int(round(self.CHROME_1080 * h / DEV_H))       # 按原始高度换算裁多少
        game = img[cut:, :] if cut and cut < h - 100 else img
        if game.size == 0:
            return None
        return cv2.resize(game, (DEV_W, DEV_H), interpolation=cv2.INTER_AREA)

    def close(self) -> None:
        try:
            del self.c
        except Exception:                                    # noqa: BLE001
            pass


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="MaaFramework FramePool 取帧器")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--out", default="data/_maafw_frame_1080.png")
    a = ap.parse_args()
    g = MaafwCapture.open()
    print(f"窗口 hwnd={g.hwnd}（实抓校验过的）  窗口高 {g.win_h}  客户区 {g.client_w}x{g.client_h}  "
          f"（chrome 待首帧实量）")
    ts = []
    frame = None
    for _ in range(a.n):
        t = time.time(); frame = g.grab(); ts.append(time.time() - t)
    med = sorted(ts)[len(ts) // 2]
    print(f"取帧 {a.n} 次：中位 {med * 1000:.0f} ms/帧 ≈ {1 / med:.0f} fps   "
          f"chrome≈{g._chrome}px   帧={None if frame is None else frame.shape}")
    if frame is not None:
        cv2.imwrite(a.out, frame)
        print("已存", a.out, "（1920×1080，可直接喂给标注工具 ✓）")
    g.close()


if __name__ == "__main__":
    main()
