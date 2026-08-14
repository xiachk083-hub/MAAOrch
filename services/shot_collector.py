"""MAA 截图收集器（2026-08-15 用户: 收集每一次的截图，单独视图事后查看）。

MAA 运行中会把识别用的界面原始截图存到实例目录：
  services/maa/instances/<n>/debug/interface/*.png  （界面识别截图）
  services/maa/instances/<n>/debug/drops/*.png      （掉落截图）
本收集器定时扫描实例目录 → 新文件拷贝到 logs/shots/<aid>/（.meta 映射账号），
文件名带源类型前缀，前端「截图」视图按账号/时间浏览。

与 runner 完成截图（logs/shots/<aid>_<ts>.png 平铺）共存，视图递归读取。
"""
from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
INSTANCES = ROOT / "services" / "maa" / "instances"
SHOTS_DIR = ROOT / "logs" / "shots"

_SRC_DIRS = ("debug/interface", "debug/drops")
_PREFIX = {"debug/interface": "i_", "debug/drops": "d_"}


def _collect_once() -> int:
    """扫描一次实例目录，拷贝新截图。返回拷贝数。"""
    n = 0
    try:
        if not INSTANCES.exists():
            return 0
        for inst in INSTANCES.iterdir():
            if not inst.is_dir():
                continue
            meta = ""
            try:
                mf = inst / ".meta"
                if mf.exists():
                    meta = mf.read_text(encoding="utf-8", errors="replace").strip()
                    if "|" in meta:
                        meta = meta.split("|", 1)[0].strip()
            except Exception:
                pass
            for src in _SRC_DIRS:
                d = inst / src
                if not d.is_dir():
                    continue
                for f in d.glob("*.png"):
                    try:
                        # 目标: logs/shots/<aid>/<prefix><原始文件名>
                        dest_dir = SHOTS_DIR / (meta or "unknown")
                        dest_dir.mkdir(parents=True, exist_ok=True)
                        dest = dest_dir / (_PREFIX[src] + f.name)
                        if not dest.exists():
                            shutil.copy2(f, dest)
                            n += 1
                    except Exception:
                        pass
    except Exception:
        pass
    return n


class ShotCollector(threading.Thread):
    """定时收集线程（main_web.pyw 启动一次，60s 扫描）。"""

    def __init__(self, mw) -> None:
        super().__init__(daemon=True, name="shot-collector")
        self.mw = mw

    def run(self) -> None:
        while True:
            try:
                n = _collect_once()
                if n:
                    fn = getattr(self.mw, "_log", None)
                    if fn:
                        fn(f"📸 收集 MAA 截图 {n} 张 → logs/shots/")
            except Exception:
                pass
            time.sleep(60)
