#!/usr/bin/env python3
"""回归测试: _close_mumu_popups 窗口身份判定（本地，不碰目标机）。

用例（基于 2026-08-12 目标机实机监测数据）:
  W1 标题 MuMuNxDevice + 普通类名 + 按钮"确定"            → 真弹窗形态, 应被关
  W2 标题 MuMuNxDevice + 类名 Qt5156QWindowToolSaveBits
     + WS_EX_TOOLWINDOW 无按钮                           → 悬浮窗(384×216 实机形态), 应保留
  W3 标题 MuMuNxDevice + 类名 Qt5156QWindow 无按钮        → 画面窗变体(316×257 实机形态), 应保留

运行: python _win_test.py
"""
import ctypes
import ctypes.wintypes
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000
WS_CHILD = 0x40000000
WS_EX_TOOLWINDOW = 0x80

# x64: LRESULT=INT_PTR(8B) WPARAM=UINT_PTR(8B) LPARAM=LONG_PTR(8B)
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, ctypes.c_void_p, ctypes.c_uint,
                             ctypes.c_void_p, ctypes.c_longlong)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("style", ctypes.c_uint),
                ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", ctypes.c_void_p),
                ("hIcon", ctypes.c_void_p), ("hCursor", ctypes.c_void_p),
                ("hbrBackground", ctypes.c_void_p),
                ("lpszMenuName", ctypes.c_wchar_p),
                ("lpszClassName", ctypes.c_wchar_p), ("hIconSm", ctypes.c_void_p)]


def make_proc(destroy_on_close):
    user32.DefWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                      ctypes.c_void_p, ctypes.c_longlong]
    user32.DefWindowProcW.restype = ctypes.c_longlong

    def wndproc(hwnd, msg, wparam, lparam):
        if msg == 0x0010:  # WM_CLOSE
            if destroy_on_close:
                user32.DestroyWindow(hwnd)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
    return WNDPROC(wndproc)


def register_class(name, proc):
    kernel32.GetModuleHandleW.restype = ctypes.c_void_p
    kernel32.GetModuleHandleW.argtypes = [ctypes.c_void_p]
    wc = WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wc.style = 0
    wc.lpfnWndProc = proc
    wc.hInstance = kernel32.GetModuleHandleW(None)
    wc.lpszClassName = name
    return wc


def create_popup(cls_name, title, exstyle=0):
    user32.CreateWindowExW.restype = ctypes.c_void_p
    user32.CreateWindowExW.argtypes = [
        ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    kernel32.GetModuleHandleW.restype = ctypes.c_void_p
    kernel32.GetModuleHandleW.argtypes = [ctypes.c_void_p]
    return user32.CreateWindowExW(
        exstyle, cls_name, title,
        WS_POPUP | WS_VISIBLE, 100, 100, 300, 200,
        0, 0, kernel32.GetModuleHandleW(None), None)


def main():
    w1_proc = make_proc(True)
    user32.RegisterClassExW(ctypes.byref(register_class("WinIdentTestPopup", w1_proc)))
    w2_proc = make_proc(True)
    user32.RegisterClassExW(ctypes.byref(register_class("Qt5156QWindowToolSaveBits", w2_proc)))
    w3_proc = make_proc(True)
    user32.RegisterClassExW(ctypes.byref(register_class("Qt5156QWindow", w3_proc)))
    w4_proc = make_proc(True)
    user32.RegisterClassExW(ctypes.byref(register_class("WinIdentTestErr", w4_proc)))

    w1 = create_popup("WinIdentTestPopup", "MuMuNxDevice")
    w2 = create_popup("Qt5156QWindowToolSaveBits", "MuMuNxDevice", WS_EX_TOOLWINDOW)
    w3 = create_popup("Qt5156QWindow", "MuMuNxDevice")
    # W4: 大尺寸(600x400 超 _is_popup 阈值) + 按钮 → 异常弹窗, 按钮兜底应关
    user32.CreateWindowExW(0, "WinIdentTestErr", "MuMu 运行异常",
                           WS_POPUP | WS_VISIBLE, 100, 100, 600, 400,
                           0, 0, kernel32.GetModuleHandleW(None), None)
    w4 = user32.FindWindowW("WinIdentTestErr", None)
    user32.CreateWindowExW(0, "Button", "确定",
                           WS_CHILD | WS_VISIBLE | 0x00000001,
                           10, 10, 50, 30, w4, None,
                           kernel32.GetModuleHandleW(None), None)
    # W1 的按钮（真弹窗特征）
    user32.CreateWindowExW(0, "Button", "确定",
                           WS_CHILD | WS_VISIBLE | 0x00000001,
                           10, 10, 50, 30, w1, None,
                           kernel32.GetModuleHandleW(None), None)
    assert w1 and w2 and w3 and w4, "create window failed"
    print(f"W1 弹窗形态 hwnd={w1}  W2 悬浮窗形态 hwnd={w2}  W3 画面窗变体 hwnd={w3}  W4 异常弹窗(大尺寸) hwnd={w4}")

    from services.runner import _close_mumu_popups

    # ---- debug W4 ----
    user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    def ttl(h):
        ln = user32.GetWindowTextLengthW(h) + 1
        b = ctypes.create_unicode_buffer(ln)
        user32.GetWindowTextW(h, b, ln)
        return b.value
    def btns(h):
        out = []
        def walk(par):
            c = user32.GetWindow(par, 5)
            while c:
                t = ttl(c)
                if t: out.append(t)
                walk(c)
                c = user32.GetWindow(c, 2)
        walk(h)
        return out
    import ctypes as _c
    _c.windll.user32.IsWindowVisible.argtypes = [_c.c_void_p]
    print(f"DBG W4: title={ttl(w4)!r} visible={user32.IsWindowVisible(w4)} btns={btns(w4)}")

    _close_mumu_popups()

    # 泵消息: PostMessageW 异步投递, 需要消息循环 wndproc 才处理 WM_CLOSE
    msg = ctypes.wintypes.MSG()
    for _ in range(20):
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.05)

    w1_alive = bool(user32.IsWindow(w1))
    w2_alive = bool(user32.IsWindow(w2))
    w3_alive = bool(user32.IsWindow(w3))
    w4_alive = bool(user32.IsWindow(w4))
    print(f"W1(弹窗) 存活={w1_alive}  期望=关")
    print(f"W2(悬浮窗 ToolSaveBits) 存活={w2_alive}  期望=保留")
    print(f"W3(画面窗变体 Qt5156QWindow) 存活={w3_alive}  期望=保留")
    print(f"W4(异常弹窗 大尺寸+按钮) 存活={w4_alive}  期望=关")

    ok = (not w1_alive) and w2_alive and w3_alive and (not w4_alive)
    print("RESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
