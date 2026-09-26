"""操作日志：data/logs/op.log 追加式，线程安全。"""
from __future__ import annotations
import threading
from datetime import datetime
from pathlib import Path

from .config import DATA_DIR

_LOCK = threading.Lock()
_OP_LOG = DATA_DIR / "logs" / "op.log"


def _ensure() -> None:
    _OP_LOG.parent.mkdir(parents=True, exist_ok=True)


def log(action: str, target: str = "", result: str = "", detail: str = "") -> None:
    """记一行操作日志：时间 | 动作 | 目标 | 结果 | 详情"""
    try:
        _ensure()
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"{ts} | {action} | {target} | {result} | {detail}\n"
        with _LOCK:
            with open(_OP_LOG, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass


def recent(limit: int = 200) -> list[dict]:
    """读取最近操作日志（时间倒序）。"""
    try:
        if not _OP_LOG.exists():
            return []
        lines = _OP_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        out = []
        for ln in lines[-limit:]:
            parts = ln.split(" | ")
            if len(parts) >= 4:
                out.append({
                    "time": parts[0].strip(),
                    "action": parts[1].strip(),
                    "target": parts[2].strip(),
                    "result": parts[3].strip(),
                    "detail": " | ".join(parts[4:]).strip(),
                })
        return out
    except Exception:
        return []
