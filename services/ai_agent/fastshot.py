"""原始套接字截图通道（screencap | nc）。

对比 `screencap -p`（PNG 编解码+传输，实测 ~590ms）：本通道直灌原始帧缓冲，
实测 194~258ms（约 2.7×）。

格式（已在 MuMu 1280x720 实测）：[w(4B 小端)][h(4B 小端)][RGBA...]，总长 8+w*h*4。
两个必须做对的点（2026-09-16）：
  · 字节序：w/h 是小端，显式用 "little" 解析（换 ARM/跨平台也不会错）
  · 颜色序：游戏/模板用 BGR，screencap 给 RGBA —— 漏了这步颜色反转、模板全错
"""
from __future__ import annotations
import os
import socket
import subprocess
import threading

import numpy as np

_ADB_CANDIDATES = [
    r"D:\Xiach\MuMuPlayer\nx_main\adb.exe",
    r"C:\Program Files\Netease\MuMuPlayer-12.0\shell\adb.exe",
]


def find_adb() -> str:
    for c in _ADB_CANDIDATES:
        if os.path.exists(c):
            return c
    return "adb"


def nc_capture(adb: str, address: str, timeout: float = 6.0) -> np.ndarray | None:
    """取一帧 BGR ndarray；失败返回 None（不抛，交给调用方决定重试/降级）。"""
    srv = socket.socket()
    buf = bytearray()
    try:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def acc() -> None:
            srv.settimeout(timeout)
            try:
                conn, _ = srv.accept()
                while True:
                    chunk = conn.recv(1 << 20)
                    if not chunk:
                        break
                    buf.extend(chunk)
                conn.close()
            except Exception:
                pass

        th = threading.Thread(target=acc, daemon=True)
        th.start()
        subprocess.run([adb, "-s", address, "exec-out", f"screencap | nc -w 3 10.0.2.2 {port}"],
                       capture_output=True, timeout=timeout + 4)
        th.join(timeout)
    except Exception:
        return None
    finally:
        try:
            srv.close()
        except Exception:
            pass
    if len(buf) < 12:
        return None
    w = int.from_bytes(buf[0:4], "little")
    h = int.from_bytes(buf[4:8], "little")
    need = w * h * 4
    if not (0 < w <= 4096 and 0 < h <= 4096) or len(buf) < need + 8:
        return None
    # 头长度按实际字节数反推，别写死：MuMu 12 的 `screencap` 头是 **16** 字节
    # （720p 实测 3686416 = 1280*720*4+16，1080p 实测 8294416 = 1920*1080*4+16），
    # 老代码写死 8 ⇒ 每帧整体错位 8 字节（每行水平错 2 像素）。
    off = len(buf) - need
    rgba = np.frombuffer(bytes(buf[off:off + need]), dtype=np.uint8).reshape(h, w, 4)
    return rgba[:, :, :3][:, :, ::-1].copy()      # RGBA → BGR


if __name__ == "__main__":
    import time
    adb = find_adb()
    for i in range(3):
        t0 = time.time()
        f = nc_capture(adb, "127.0.0.1:16448")
        ms = (time.time() - t0) * 1000
        print(f"  {i + 1}) {ms:.0f} ms  {None if f is None else f.shape}  均值BGR={None if f is None else f.reshape(-1,3).mean(0).round(1)}")
