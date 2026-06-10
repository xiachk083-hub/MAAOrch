"""Unified logging — structured, leveled, auto-rotating files."""
from __future__ import annotations
import os, json, threading
from pathlib import Path
from datetime import datetime

_LOG_DIR = Path(__file__).parent.parent
_LOGGER_LOCK = threading.Lock()

_LEVELS = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3}


def _rotate(path: Path, max_bytes: int = 512 * 1024, backup_count: int = 3) -> None:
    """Rotate a single log file when it exceeds max_bytes."""
    try:
        if path.stat().st_size <= max_bytes:
            return
        for i in range(backup_count - 1, 0, -1):
            src = path.with_suffix(f".{i}.log")
            dst = path.with_suffix(f".{i + 1}.log")
            if src.exists():
                src.replace(dst)
        path.replace(path.with_suffix(".1.log"))
    except Exception:
        pass


class Logger:
    """Lightweight logger with levels, source tagging, and auto-rotation.

    Usage:
        log = Logger("runner")
        log.info("账号 V 启动成功")
        log.warn("ADB 重连超时")
        log.error("MAA 异常退出")
    """

    def __init__(self, name: str):
        self.name = name
        self._ui_callback: callable | None = None

    def set_ui_callback(self, cb: callable | None) -> None:
        """Register a callback for UI display (receives formatted line)."""
        self._ui_callback = cb

    def debug(self, msg: str) -> None:
        self._write("DEBUG", msg)

    def info(self, msg: str) -> None:
        self._write("INFO", msg, ui=True)

    def warn(self, msg: str) -> None:
        self._write("WARN", msg, ui=True)

    def error(self, msg: str) -> None:
        self._write("ERROR", msg, ui=True)
        self._write_crash(msg)

    def _write(self, level: str, msg: str, ui: bool = False) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] [{level:<5}] [{self.name}] {msg}"
        safe_msg = str(msg).replace("\n", " ").replace("\r", " ")[:2000]
        with _LOGGER_LOCK:
            # debug.log: all levels, rotated
            dp = _LOG_DIR / "debug.log"
            _rotate(dp)
            try:
                with dp.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:
                pass
            # events.log: INFO+, JSON structured
            if _LEVELS.get(level, 0) >= _LEVELS["INFO"]:
                ep = _LOG_DIR / "events.log"
                _rotate(ep)
                event = {"t": ts, "l": level, "src": self.name, "msg": safe_msg}
                try:
                    with ep.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(event, ensure_ascii=False) + "\n")
                except Exception:
                    pass
        if ui and self._ui_callback:
            try:
                self._ui_callback(line)
            except Exception:
                pass

    def _write_crash(self, msg: str) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with _LOGGER_LOCK:
                cp = _LOG_DIR / "crash.log"
                with cp.open("a", encoding="utf-8") as f:
                    f.write(f"\n[{ts}] [CRASH] [{self.name}]\n{msg}\n")
        except Exception:
            pass
