"""把 MaaCore 只当**高速取帧器**（用户的定调）—— 不 append 任何任务，纯粹拿帧。

为什么这么做：MaaCore 自带 `AsstAsyncScreencap`（"现在截一张"）+ `AsstGetImageBgr`（直接给 BGR）。
我们**只用它的取图通道**：不用它的任务调度、不用它的识别（识别用我们自己的量测）。

⚠️ **实测结论（2026-09-19，别重复踩）：这条路对"只取帧"没有收益** ——
    被动模式 `AsyncScreencap + GetImageBgr`：**302 ms/帧 ≈ 3.3 fps，帧是 1280×720** ✗；
    而我们自己的 raw adb 截图（`battle_loop.shot_fast()`，去掉 16 字节头）：**330 ms / 1920×1080** ✓
    ⇒ **同样快、分辨率更高、还不用多起一个 MaaCore 实例** ⇒ 维持 raw adb 那条。
**Peep 的 20 fps 是真的**（外部量过：抓 Peep 显示区 12 秒，内容以 **≥7 次/秒**在变 ✓✓ ——
我一开始猜"只是重绘同一张"，被用户一句"它帧率有 20 诶"顶回来，实测证明用户对 ✓）。
但它是 **"正在干活的实例"的副产品**：帧由任务循环顺手截 ✓。**空闲的被动实例拿不到** ✗：
   · `AsyncScreencap(block=True)` 每次等一张：**302 ms/帧、720p** ✗
   · `AsyncScreencap(block=False)` + 反复取最新：轮询 3.5 次/秒、内容只换 **1.0 次/秒** ✗
   · 对比我们自己的 raw adb（`battle_loop.shot_fast()`）：**330 ms / 1080p** ✓
  ⇒ 旁观模式下**没有收益**（还得跟别人正在跑的任务争同一个截图工人 ✗，这也是被动探针越测越慢的原因）。

**20fps 的机制查清了（2026-09-19，用户"它帧率有 20 诶"顶回来后挖到底）**：
  · 用户 MAA 自己的日志（`debug/asst.log`）实证：`Screencap` 每张 **13~21ms**、时间戳间隔 ~50ms ⇒ **20 张/秒** ✓
  · 配置来源：`config/gui.new.json` 里 **`ScreencapMethod = "FramePool"`** ✓（不是 adb 截图 ✗）
  · FramePool 要 MuMu 的帧池句柄 ⇒ 参数在 GUI 程序集里：`MuMu12ExtrasEnabled` / `MuMu12EmulatorPath` /
    `MuMu12Index` / `MuMu12Display`（MaaCore 里对应 `path`/`index`/`display_id`），还有
    `MuMuBridgeConnection` / `MuMuEmulator12KeepAliveOn` 两个线索键 ✓
  · **但被动实例进不去** ✗（都是 720p）：裸 `{"screencap":"FramePool"}` **234ms** /
    `MuMuExtras` extras **293ms** / `AVMumuExtras` extras **274ms** / 啥都不设 302ms
  ⇒ **FramePool 不是"给个参数就能用"的**，它是 MAA GUI 完整启动流程里建立起来的（多半靠那套 bridge/keep-alive）
    ⇒ 要复刻 = 复刻那段启动逻辑（要投入的活，不擅自铺开）✓

**本工具的正当用途**：等**我们自己用 MaaCore 跑一局**时（不是只读屏），帧与它自己的识别结果都是白拿的 ——
那时"20fps 标注 + 它的眼睛 vs 我们的量测对照"才成立 ✓；在那之前，标注界面继续走 raw adb
（330ms / **1920×1080** ✓，比被动 MaaCore 的 280ms / 720p 还快且更清晰 ✓）。

三条硬规矩（每条都有来历）：
  1. **`kill_on_adb_exit=0` 必须先设** ✗ —— 默认可能为真，MaaCore 退出时会**杀 adb**，
     而 adb 是全项目的命脉（MAA / 推图 / 标注界面都挂在上面）⇒ 不设就是事故；
  2. **绝不 append 任务** ✓ —— 被动实例不会自己点屏幕 ⇒ 不会和正在跑的 MAA 抢操作；
  3. **user_dir 指到我们自己的目录** ✓ —— 否则 MaaCore 的配置/缓存会落到用户的 MAA 安装目录里。

用法：
    python tools/maacore_shot.py --probe          # 连上、连拍 10 帧、报尺寸与帧率，然后断开
"""
from __future__ import annotations

import argparse
import ctypes
import pathlib
import sys
import time

import numpy as np

PROJ = pathlib.Path(__file__).resolve().parent.parent
MAA_DIR = r"D:\Xiach\M-A\MAA\MAA-v6.12.2-win-x64"
ADB = r"D:\Xiach\MuMuPlayer\nx_main\adb.exe"
ADDR = "127.0.0.1:16384"          # 本机 MuMu 实例 0（adb 别名 emulator-5554）
CFG = "MuMuEmulator12"

for _p in (str(pathlib.Path(MAA_DIR) / "Python"), str(PROJ), str(PROJ / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _lib():
    """拿 MaaCore.dll 的句柄（绑定已加载过同一个模块 ⇒ 拿到的是同一份）。"""
    return ctypes.WinDLL(str(pathlib.Path(MAA_DIR) / "MaaCore.dll"))


class Grabber:
    """被动取帧器：connect 之后只做 AsyncScreencap + GetImageBgr。"""

    def __init__(self, adb: str = ADB, addr: str = ADDR, cfg: str = CFG,
                 maa_dir: str = MAA_DIR, user_dir: str | None = None) -> None:
        from asst.asst import Asst                       # noqa: PLC0415
        from asst.utils import InstanceOptionType        # noqa: PLC0415
        self.Asst = Asst
        self.IOT = InstanceOptionType
        Asst.load(path=maa_dir,
                  user_dir=str(user_dir) if user_dir else str(PROJ / "runtime" / "maa_grabber"))
        self.a = Asst()
        # ① **先设这个**，再谈别的：不清 kill_on_adb_exit 就别启 MaaCore
        self.a.set_instance_option(InstanceOptionType.kill_on_adb_exit, "0")
        self.adb, self.addr, self.cfg = adb, addr, cfg
        self.lib = _lib()
        self.lib.AsstAsyncScreencap.argtypes = (ctypes.c_void_p, ctypes.c_bool)
        self.lib.AsstAsyncScreencap.restype = ctypes.c_bool
        self.lib.AsstGetImageBgr.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint64)
        self.lib.AsstGetImageBgr.restype = ctypes.c_uint64
        self.ptr = ctypes.c_void_p(self.a._Asst__ptr)    # 绑定的句柄，复用同一个实例
        self.shape: tuple[int, int] | None = None

    def connect(self) -> bool:
        return bool(self.a.connect(self.adb, self.addr, self.cfg))

    def connected(self) -> bool:
        return bool(self.a.connected()) if hasattr(self.a, "connected") else True

    def grab(self, size: int = 1920 * 1080 * 3) -> np.ndarray | None:
        """截一张并取回 BGR 帧。返回 None = 这一张没成功（调用方自己决定要不要重试）。"""
        if not self.lib.AsstAsyncScreencap(self.ptr, True):
            return None
        buf = ctypes.create_string_buffer(size)
        got = self.lib.AsstGetImageBgr(self.ptr, buf, ctypes.c_uint64(size))
        if not got or got <= 0:
            return None
        got = int(got)
        if got % 3:
            return None
        px = got // 3
        for w, h in ((1920, 1080), (1280, 720), (2560, 1440), (720, 1280)):
            if px == w * h:
                self.shape = (h, w)
                return np.frombuffer(buf.raw[:got], np.uint8).reshape(h, w, 3).copy()
        self.shape = None
        return None

    def close(self) -> None:
        try:
            del self.a                                    # 触发 AsstDestroy
        except Exception:                                 # noqa: BLE001
            pass


def probe(n: int = 10) -> None:
    g = Grabber()
    t0 = time.time()
    if not g.connect():
        print("✗ connect 失败（端口/配置/adb 路径核对一下）")
        return
    print(f"✓ 已连接 {ADDR}（config={CFG}）  耗时 {time.time() - t0:.2f}s")
    times, sizes = [], []
    for i in range(n):
        t = time.time()
        im = g.grab()
        dt = time.time() - t
        if im is None:
            print(f"  第{i + 1}帧 失败")
            continue
        times.append(dt)
        sizes.append(im.shape)
    if times:
        med = sorted(times)[len(times) // 2]
        print(f"取帧 {len(times)}/{n} 成功  中位 {med * 1000:.0f} ms/帧  ≈ {1 / med:.1f} fps")
        print(f"  帧尺寸 {sizes[-1]}   （我们的量测框是 1920×1080 系 ⇒ 不一致就得换算）")
    g.close()
    print("已断开（kill_on_adb_exit=0 ⇒ 不会动 adb）")


def main() -> None:
    ap = argparse.ArgumentParser(description="MaaCore 高速取帧器（被动，不发任务）")
    ap.add_argument("--probe", action="store_true", help="连上连拍 10 帧报帧率")
    ap.add_argument("--n", type=int, default=10)
    args = ap.parse_args()
    if args.probe:
        probe(args.n)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
