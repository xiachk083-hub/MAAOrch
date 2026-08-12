#!/usr/bin/env python3
"""窗口身份探测 — 枚举窗口并采集完整身份，回答「这个窗口是什么」。

目标: 用本质特征（进程归属/类名/样式/owner/按钮）区分
      模拟器主窗口 / 悬浮画面窗 / 报错弹窗，替代尺寸启发式判定。

采集字段: 标题 / 类名 / PID / 进程名 / style / exstyle / owner /
          宽高比 / 按钮子窗口文本

用法:
    python tools/window_identify.py                    # 全部可见窗口
    python tools/window_identify.py --filter MuMu      # 标题含 MuMu
    python tools/window_identify.py --filter 异常       # 找弹窗
    python tools/window_identify.py --json             # JSON 输出（脚本用）
"""
from __future__ import annotations
import argparse
import ctypes
import ctypes.wintypes
import json
import sys
from pathlib import Path

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
psapi = ctypes.windll.psapi

GWL_STYLE = -16
GWL_EXSTYLE = -20
GW_OWNER = 4
GW_CHILD = 5
GW_HWNDNEXT = 2

# 弹窗按钮关键词（与 runner._close_mumu_popups 保持一致）
BTN_KEYS = ("忽略", "Ignore", "再次启动", "确定", "OK", "Yes", "是", "No")


def _title(hwnd):
    ln = user32.GetWindowTextLengthW(hwnd) + 1
    buf = ctypes.create_unicode_buffer(ln)
    user32.GetWindowTextW(hwnd, buf, ln)
    return buf.value


def _class_name(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _pid(hwnd):
    pid = ctypes.wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _exe_name(pid):
    if not pid:
        return ""
    h = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return f"pid{pid}"
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.wintypes.DWORD(1024)
        # QueryFullProcessImageNameW 在 Win7+ 位于 kernel32（psapi 无此函数）
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return Path(buf.value).name
        return f"pid{pid}"
    finally:
        kernel32.CloseHandle(h)


def _style(hwnd):
    return user32.GetWindowLongW(hwnd, GWL_STYLE) & 0xFFFFFFFF


def _exstyle(hwnd):
    return user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF


def _owner(hwnd):
    return user32.GetWindow(hwnd, GW_OWNER)


def _rect(hwnd):
    r = ctypes.wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def _buttons(hwnd):
    """递归收集子窗口中的文本按钮（弹窗强特征）。"""
    out = []

    def walk(parent):
        child = user32.GetWindow(parent, GW_CHILD)
        while child:
            ln = user32.GetWindowTextLengthW(child) + 1
            buf = ctypes.create_unicode_buffer(ln)
            user32.GetWindowTextW(child, buf, ln)
            t = buf.value
            if t and any(k in t for k in BTN_KEYS):
                out.append(f"{_class_name(child)}:{t}")
            walk(child)
            child = user32.GetWindow(child, GW_HWNDNEXT)

    walk(hwnd)
    return out


def identify(hwnd):
    x1, y1, x2, y2 = _rect(hwnd)
    w, h = x2 - x1, y2 - y1
    exs = _exstyle(hwnd)
    pid = _pid(hwnd)
    return {
        "hwnd": hwnd,
        "title": _title(hwnd),
        "class": _class_name(hwnd),
        "pid": pid,
        "exe": _exe_name(pid),
        "style": f"0x{_style(hwnd):08X}",
        "exstyle": f"0x{exs:08X}",
        "toolwindow": bool(exs & 0x80),       # WS_EX_TOOLWINDOW
        "owner": _owner(hwnd),
        "rect": f"{w}x{h}",
        "ratio": round(w / h, 3) if h else 0,
        "buttons": _buttons(hwnd),
        "visible": bool(user32.IsWindowVisible(hwnd)),
    }


def main():
    ap = argparse.ArgumentParser(description="窗口身份探测")
    ap.add_argument("--filter", help="标题包含关键字")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    ap.add_argument("--all", action="store_true", help="含不可见窗口（默认只显示可见）")
    ap.add_argument("--min-size", type=int, default=0, help="最小尺寸（宽或高）")
    args = ap.parse_args()

    rows = []
    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _lp):
        info = identify(hwnd)
        if args.filter and args.filter not in info["title"]:
            return True
        if not args.all and not info["visible"]:
            return True
        w, h = info["rect"].split("x")
        if args.min_size and max(int(w), int(h)) < args.min_size:
            return True
        rows.append(info)
        return True

    user32.EnumWindows(enum_proc(cb), 0)

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return

    # 按进程名分组输出，模拟器相关排前面
    def key(r):
        exe = r["exe"].lower()
        if "mumu" in exe or "vmm" in exe:
            return (0, r["exe"])
        return (1, r["exe"])

    rows.sort(key=key)
    hdr = f"{'hwnd':>8} {'pid':>6} {'exe':<26} {'class':<30} {'style':<12} {'exstyle':<12} {'rect':<10} {'ratio':>6} btn title"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        btn = "*" if r["buttons"] else " "
        print(f"{r['hwnd']:>8} {r['pid']:>6} {r['exe']:<26} {r['class']:<30} "
              f"{r['style']:<12} {r['exstyle']:<12} {r['rect']:<10} {r['ratio']:>6} "
              f"{btn} {r['title']}")


if __name__ == "__main__":
    main()
