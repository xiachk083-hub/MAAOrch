"""终端仪表盘（main_web.pyw --console 模式）— htop 风格：
顶部固定状态区（CPU / 内存 / 带宽 / 磁盘，定时刷新不滚动），
下方日志区滚动显示。基于 ANSI VT 序列 + alt screen 全屏重绘，
纯 stdlib（指标采集用 psutil，项目既有依赖）。

stdout 非终端（管道重定向）时自动降级：start() 返回 False，
调用方退回普通 print 日志模式，不产生 ANSI 垃圾字节。
"""
from __future__ import annotations
import shutil, sys, threading, time
from collections import deque

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False

_ALT_ON = "\x1b[?1049h"    # 进入 alt screen（保存原屏幕）
_ALT_OFF = "\x1b[?1049l"   # 退出 alt screen（恢复原屏幕）
_CURS_HIDE = "\x1b[?25l"
_CURS_SHOW = "\x1b[?25h"
_CLR_LINE = "\x1b[K"
_HOME = "\x1b[H"

_STATE_TITLE = 1    # 标题行（含运行时长）
_STATE_METRICS = 4  # CPU / 内存 / 带宽 / 磁盘
_STATE_SEP = 1      # 分隔线
STATE_LINES = _STATE_TITLE + _STATE_METRICS + _STATE_SEP  # 6

# psutil 无时（极端情况）也给出可读的速率
def _fmt_rate(n: float) -> str:
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if n < 1024 or unit == "GB/s":
            return f"{n:.1f} {unit}" if n >= 10 else f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.1f} GB/s"


def _fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _bar(pct: float, width: int = 16) -> str:
    """ASCII 进度条（避免 box-drawing 字符在旧终端字体缺字）。"""
    pct = max(0.0, min(100.0, pct))
    filled = int(round(pct / 100 * width))
    return "#" * filled + "-" * (width - filled)


def _clip(s: str, max_cols: int) -> str:
    """按显示列宽截断（CJK 字符计 2 列），避免折行破坏布局。"""
    cols = 0
    for i, ch in enumerate(s):
        cols += 2 if ord(ch) > 0x2E7F else 1  # CJK/全角/emoji 按 2 列
        if cols > max_cols:
            return s[:i]
    return s


class ConsoleDash:
    """轻量终端仪表盘：状态区固定 + 日志区滚动。

    渲染线程按 refresh_interval 定时重绘（状态刷新）；日志到达时
    唤醒渲染（100ms 节流合并高频日志）。退出时恢复原屏幕。
    """

    def __init__(self, refresh_interval: float = 1.0):
        self._refresh = refresh_interval
        self._logs: deque[str] = deque(maxlen=1000)
        self._lock = threading.Lock()
        self._dirty = False
        self._wake = threading.Event()
        self._active = False
        self._started_ts = time.time()
        self._cpu = 0.0
        self._io = None
        self._io_ts = 0.0

    # ── 生命周期 ──
    def start(self) -> bool:
        """启用 VT 模式 + 进入 alt screen。非 tty / 无 psutil / 失败 → False。"""
        if not sys.stdout.isatty() or not _HAS_PSUTIL:
            return False
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            h = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
            mode = ctypes.c_uint32()
            if not kernel32.GetConsoleMode(h, ctypes.byref(mode)):
                return False
            if not kernel32.SetConsoleMode(h, mode.value | 0x0004):  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
                return False
            sys.stdout.write(_ALT_ON + _CURS_HIDE)
            sys.stdout.flush()
        except Exception:
            return False
        self._active = True
        threading.Thread(target=self._loop, daemon=True, name="console_dash").start()
        return True

    def append(self, line: str) -> None:
        """日志行进缓冲并触发重绘（高频合并）。"""
        with self._lock:
            self._logs.append(line)
            self._dirty = True
        if self._active:
            self._wake.set()

    def stop(self) -> None:
        """退出 alt screen，恢复原屏幕与光标。"""
        self._active = False
        self._wake.set()
        try:
            sys.stdout.write(_CURS_SHOW + _ALT_OFF)
            sys.stdout.flush()
        except Exception:
            pass

    # ── 指标 ──
    def _metrics(self) -> list[str]:
        lines: list[str] = []
        if not _HAS_PSUTIL:
            return lines
        try:
            self._cpu = psutil.cpu_percent(interval=None)  # 首次 0，之后为差值
            mem = psutil.virtual_memory()
            down = up = 0.0
            now = time.monotonic()
            io = psutil.net_io_counters()
            if self._io is not None:
                dt = now - self._io_ts
                if dt > 0:
                    down = max(0.0, io.bytes_recv - self._io.bytes_recv) / dt
                    up = max(0.0, io.bytes_sent - self._io.bytes_sent) / dt
            self._io, self._io_ts = io, now
            cores = psutil.cpu_count() or 1
            lines.append(f" CPU   [{_bar(self._cpu)}] {self._cpu:>5.1f}%  ({cores} 核)")
            lines.append(f" 内存  [{_bar(mem.percent)}] {mem.percent:>5.1f}%  "
                         f"{_fmt_bytes(mem.used)} / {_fmt_bytes(mem.total)}")
            lines.append(f" 带宽  下载 {_fmt_rate(down):>12}    上传 {_fmt_rate(up):>12}")
            disk_txt = "-"
            try:
                disk = psutil.disk_usage(psutil.disk_partitions()[0].mountpoint)
                disk_txt = f"[{_bar(disk.percent)}] {disk.percent:>5.1f}%  " \
                           f"{_fmt_bytes(disk.used)} / {_fmt_bytes(disk.total)}"
            except Exception:
                pass
            lines.append(f" 磁盘  {disk_txt}")
        except Exception:
            pass
        return lines

    # ── 渲染 ──
    def _render(self) -> None:
        if not self._active:
            return
        try:
            size = shutil.get_terminal_size((100, 30))
            width, height = size.columns, size.lines
        except Exception:
            width, height = 100, 30
        log_area = max(5, height - STATE_LINES - 1)
        with self._lock:
            logs = list(self._logs)
        uptime = int(time.time() - self._started_ts)
        hh, mm, ss = uptime // 3600, (uptime % 3600) // 60, uptime % 60
        lines = [_clip(f" MAAOrch 仪表盘   运行 {hh:02d}:{mm:02d}:{ss:02d}", width)]
        lines.extend(self._metrics())
        lines.append("-" * width)
        lines.extend(_clip(ln, width) for ln in logs[-log_area:])
        sys.stdout.write(_HOME + "\r\n".join(lines) + "\r\n" + _CLR_LINE + _CURS_HIDE)
        sys.stdout.flush()

    def _loop(self) -> None:
        """渲染循环：状态按 refresh 间隔刷新；日志到达即时重绘（100ms 合并）。"""
        last = 0.0
        while self._active:
            self._wake.wait(timeout=self._refresh)
            self._wake.clear()
            if not self._active:
                break
            now = time.monotonic()
            if now - last >= 0.1 or self._dirty:
                self._dirty = False
                last = now
                self._render()
