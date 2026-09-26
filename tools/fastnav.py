#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""快导航内核（2026-09-24）—— 把"点一下 / 滑一下 / 等画面"全部压到最快。

三个提速点：
  1. **常驻 adb shell**：`adb shell input tap` 每次要新起 adb 客户端（实测 53ms），
     常驻一个 `adb shell` 往里写命令能省掉握手（实测见 `python tools/fastnav.py --bench`）。
  2. **等画面用轮询，不用固定 sleep**：老代码点完 `time.sleep(3)`；现在每 0.15 秒用
     页面指纹（34ms）看一眼，到了就走 —— 单步从 3s 降到 0.2~0.5s。
  3. **取帧走常驻服务**（MaaCore MuMu 通道，42ms），不再各自 spawn python 截屏。

用法（也可当库 import）:
    python tools/fastnav.py --bench          # 量三种点法/滑法的耗时
    python tools/fastnav.py --page           # 打印当前页面指纹
    python tools/fastnav.py --tap 640 400
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = pathlib.Path(r"D:\1-2\MAAOrch")
sys.path.insert(0, str(ROOT / "tools"))
from _dev import resolve_dev  # noqa: E402

ADB = r"D:\Xiach\MuMuPlayer\nx_main\adb.exe"
SVC = "http://127.0.0.1:8765"


# ── 常驻 adb shell ───────────────────────────────────────────────────────
class Shell:
    _inst = None
    _lock = threading.Lock()

    def __init__(self, dev: str):
        self.dev = dev
        self.p = subprocess.Popen([ADB, "-s", dev, "shell"], stdin=subprocess.PIPE,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  text=True, bufsize=1)

    @classmethod
    def get(cls, dev: str | None = None):
        if cls._inst is None:
            cls._inst = cls(dev or resolve_dev("1"))
        return cls._inst

    def cmd(self, line: str) -> None:
        with self._lock:
            try:
                self.p.stdin.write(line + "\n")
                self.p.stdin.flush()
            except Exception:                       # 掉线就重开
                self.p = subprocess.Popen([ADB, "-s", self.dev, "shell"], stdin=subprocess.PIPE,
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                          text=True, bufsize=1)
                self.p.stdin.write(line + "\n")
                self.p.stdin.flush()

    def tap(self, x: int, y: int) -> None:
        self.cmd(f"input tap {x} {y}")

    def swipe(self, x1: int, y1: int, x2: int, y2: int, ms: int = 300) -> None:
        self.cmd(f"input swipe {x1} {y1} {x2} {y2} {ms}")

    def key(self, code: int = 4) -> None:
        self.cmd(f"input keyevent {code}")


def adb_tap_once(dev: str, x: int, y: int) -> None:
    subprocess.run([ADB, "-s", dev, "shell", "input", "tap", str(x), str(y)],
                   stdout=subprocess.DEVNULL, timeout=30)


def svc(path: str, timeout: float = 8.0):
    with urllib.request.urlopen(SVC + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def frame_path(p: pathlib.Path | None = None) -> pathlib.Path:
    p = p or (ROOT / "runtime" / "_nav.png")
    svc(f"/shot?path={p}")            # 服务端用 MaaCore 通道取帧后落盘（42ms）
    return p


def page(p: pathlib.Path | None = None) -> dict:
    import importlib.util
    import cv2
    spec = importlib.util.spec_from_file_location("pages_mod", ROOT / "tools" / "pages.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)                     # type: ignore
    img = cv2.imread(str(frame_path(p)))
    return mod.identify(img) if img is not None else {"page": "?", "score": 0, "scores": {}}


def wait_page(want: list[str] | set[str], timeout: float = 8.0, interval: float = 0.15) -> tuple[bool, dict]:
    """轮询到出现期望页面为止（替代固定 sleep）。返回 (是否命中, 最后一次判别结果)。

    ⚠️ 2026-09-24 独立审计：这个函数**全仓无人调用**（`mt_nav` / `enter_*` 用的都是
    `tap_wait` / `swipe_wait` / `wait_settle`），而它依赖的 `page()` 以前走的是
    `tools/pages.py` 里那份**自带的、和 service 不一致的**判据（没有"未知"、还有
    `t.shape >= g.shape` 那个 bug）。`pages.py` 已改成 `page_of` 的薄壳，这里留一个
    显式告警，避免以后有人误用一条没人维护的等待路径 —— 要用请直接用
    `svc("/page")` 或 `panel_gate.read_panel()` + 自己的轮询。
    """
    import warnings
    warnings.warn("fastnav.wait_page 已无人维护（见 docstring），请改用服务 /page + 自轮询",
                  DeprecationWarning, stacklevel=2)
    want = set(want)
    deadline = time.time() + timeout
    last = {}
    while True:
        last = page()
        if last.get("page") in want:
            return True, last
        if time.time() >= deadline:
            return False, last
        time.sleep(interval)


# ── 画面签名 / 等画面变化（替代 time.sleep(3)）───────────────────────────
def sig(p: pathlib.Path | None = None) -> int:
    """极便宜的画面签名：把当前帧缩到 32x18 灰度再哈希（判别"画面变了没有"）。"""
    import cv2
    img = cv2.imread(str(frame_path(p or (ROOT / "runtime" / "_sig.png"))))
    if img is None:
        return -1
    small = cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (32, 18), interpolation=cv2.INTER_AREA)
    return hash(small.tobytes())


def wait_change(before: int, max_wait: float = 3.0, interval: float = 0.12) -> float:
    """等到画面签名变化为止；最多 max_wait 秒。返回实际等待秒数。"""
    t0 = time.time()
    while time.time() - t0 < max_wait:
        if sig() != before:
            return time.time() - t0
        time.sleep(interval)
    return time.time() - t0


def wait_settle(max_wait: float = 2.0, interval: float = 0.12, stable: int = 2) -> float:
    """等画面**稳定下来**（连续 stable 次签名相同 = 动画/滚动停了）。返回等待秒数。

    用于替代"滑完 sleep 1.6/2.0 秒"这种瞎等：滚动到位后通常 0.3~0.5s 就返回。
    """
    t0 = time.time()
    last = sig()
    same = 0
    while time.time() - t0 < max_wait:
        time.sleep(interval)
        cur = sig()
        if cur == last:
            same += 1
            if same >= stable:
                return time.time() - t0
        else:
            same = 0
            last = cur
    return time.time() - t0


def tap_wait(x: int, y: int, max_wait: float = 3.0) -> float:
    """点一下并**等画面变化**（不再无脑 sleep 3 秒）。"""
    import os
    sh = Shell.get()
    t0 = time.perf_counter()
    s0 = sig()
    t_sig = (time.perf_counter() - t0) * 1000
    sh.tap(int(x), int(y))
    w = wait_change(s0, max_wait)
    if os.environ.get("FNAV_DEBUG"):
        print(f"[fnav] tap({x},{y}) sig={t_sig:.0f}ms 等了 {w*1000:.0f}ms", file=sys.stderr, flush=True)
    return w


def swipe_wait(x1: int, y1: int, x2: int, y2: int, ms: int = 500, max_wait: float = 2.0) -> float:
    import os
    sh = Shell.get()
    t0 = time.perf_counter()
    s0 = sig()
    t_sig = (time.perf_counter() - t0) * 1000
    sh.swipe(int(x1), int(y1), int(x2), int(y2), int(ms))
    w = wait_change(s0, max_wait)
    if os.environ.get("FNAV_DEBUG"):
        print(f"[fnav] swipe({x1},{y1}->{x2},{y2}) sig={t_sig:.0f}ms 等了 {w*1000:.0f}ms",
              file=sys.stderr, flush=True)
    return w


def bench() -> int:
    dev = resolve_dev("1")
    sh = Shell.get(dev)
    print("【点法耗时】")
    t = []
    for _ in range(5):
        s = time.perf_counter()
        adb_tap_once(dev, 640, 400)
        t.append((time.perf_counter() - s) * 1000)
    print(f"  adb shell input tap（每次新起进程）   中位 {sorted(t)[len(t)//2]:.1f} ms")
    t = []
    for _ in range(5):
        s = time.perf_counter()
        sh.tap(640, 400)
        t.append((time.perf_counter() - s) * 1000)
    print(f"  常驻 shell 写命令                   中位 {sorted(t)[len(t)//2]:.1f} ms")
    print("【看一次画面】")
    t = []
    for _ in range(5):
        s = time.perf_counter()
        svc("/panel")
        t.append((time.perf_counter() - s) * 1000)
    print(f"  /panel（取帧+判面板）                中位 {sorted(t)[len(t)//2]:.1f} ms")
    t = []
    for _ in range(5):
        s = time.perf_counter()
        page()
        t.append((time.perf_counter() - s) * 1000)
    print(f"  页面指纹（含取帧）                   中位 {sorted(t)[len(t)//2]:.1f} ms")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench", action="store_true")
    ap.add_argument("--page", action="store_true")
    ap.add_argument("--tap", nargs=2, type=int)
    ap.add_argument("--swipe", nargs=4, type=int)
    a = ap.parse_args()
    if a.bench:
        return bench()
    if a.page:
        print(json.dumps(page(), ensure_ascii=False))
        return 0
    sh = Shell.get()
    if a.tap:
        sh.tap(*a.tap)
        print("tapped", a.tap)
    if a.swipe:
        sh.swipe(*a.swipe)
        print("swiped", a.swipe)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
